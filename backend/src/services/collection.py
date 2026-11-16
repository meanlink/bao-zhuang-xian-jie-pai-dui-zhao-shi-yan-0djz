"""秒表断网补传：按设备连续采集、乱序留存、连续确认、缺号补传与同号冲突阻断。

核心不变量：
- 每台设备从 1 编号，confirmed_seq 表示 1..confirmed_seq 全部连续确认；
- 乱序记录先以 buffered 留存，只有接上连续段才转 confirmed；
- 同号重发且值一致 -> 幂等忽略（ACK 丢失重试不新增样本）；
- 同号异值 -> 记录冲突并阻断设备，冲突原样保留；
- 全部设备声明结束、无缺号、无未决冲突才封批生成唯一 Dataset；
- 封批与接样并发由行锁 + 条件复核保护，不漏段也不重复成批。
"""
import json
import time
from fastapi import HTTPException
from sqlalchemy import select

from src.models import (
    CollectionBatch, CollectionDevice, CollectionRecord,
    CollectionConflict, Dataset,
)
from src.services.business import validate_row
from src.utils.serialization import checksum


def _lock_device(db, device_id):
    device = db.get(CollectionDevice, device_id)
    if device is None:
        raise HTTPException(404, '采集设备不存在')
    if db.bind.dialect.name != 'sqlite':
        db.execute(select(CollectionDevice).where(CollectionDevice.id == device_id).with_for_update())
    return device


def _lock_batch(db, batch_id):
    batch = db.get(CollectionBatch, batch_id)
    if batch is None:
        raise HTTPException(404, '采集批次不存在')
    if db.bind.dialect.name != 'sqlite':
        db.execute(select(CollectionBatch).where(CollectionBatch.id == batch_id).with_for_update())
    return batch


def _canonical(payload):
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False)


def recompute_status(db, batch):
    """根据设备与冲突实时汇总批次状态：sealed 是终态，不回退。"""
    if batch.status == 'sealed':
        return batch.status
    devices = list(db.scalars(select(CollectionDevice).where(CollectionDevice.batch_id == batch.id)))
    open_conflict = db.scalar(
        select(CollectionConflict.id)
        .join(CollectionDevice, CollectionDevice.id == CollectionConflict.device_id)
        .where(CollectionDevice.batch_id == batch.id, CollectionConflict.state == 'open')
    )
    if open_conflict:
        batch.status = 'conflict'
    elif any(d.status == 'waiting' for d in devices):
        batch.status = 'waiting'
    else:
        batch.status = 'open'
    return batch.status


def create_batch(db, data, user):
    name = data.name.strip()
    if len(name) < 2:
        raise HTTPException(422, '采集批次名称去除空格后至少需要两个字符')
    if db.scalar(select(CollectionBatch).where(CollectionBatch.name == name)):
        raise HTTPException(409, '该采集批次名已存在，请修改名称')
    codes = [item.code.strip() for item in data.devices]
    if any(not c for c in codes) or len(set(codes)) != len(codes):
        raise HTTPException(422, '设备名单不能为空且设备编号必须唯一')
    now = time.time()
    batch = CollectionBatch(name=name, status='open', created_by=user.id, created_at=now)
    db.add(batch)
    db.flush()
    for item in data.devices:
        if item.end_seq is not None and item.end_seq < 1:
            raise HTTPException(422, '结束序号必须为正整数')
        db.add(CollectionDevice(
            batch_id=batch.id, code=item.code.strip(), label=item.label.strip(),
            end_seq=item.end_seq, status='receiving', created_at=now,
        ))
    db.flush()
    return batch


def declare_end(db, device, end_seq):
    if device.status == 'blocked':
        raise HTTPException(409, '设备存在同号异值冲突，已阻断；请先处理冲突')
    if end_seq < device.confirmed_seq:
        raise HTTPException(422, f'结束序号不能小于已连续确认位置 {device.confirmed_seq}')
    device.end_seq = end_seq
    if end_seq == device.confirmed_seq:
        device.status = 'done'
    else:
        device.status = 'waiting'
    return device


def upload_records(db, device, items):
    """返回 (device, accepted, duplicates, conflict)。冲突时由路由提交后抛 409。

    整批先校验后写入：任一记录非法则整批不落库，避免半截请求污染连续段。
    """
    if device.status == 'blocked':
        raise HTTPException(409, '设备已被同号异值冲突阻断，请先在冲突清单处理')
    # 第一遍：纯校验，不产生任何写入
    normalized = []
    for index, item in enumerate(items):
        seq = item.seq
        if device.end_seq is not None and seq > device.end_seq:
            raise HTTPException(422, f'序号 {seq} 超出本设备声明的结束序号 {device.end_seq}')
        try:
            payload = validate_row(dict(item.payload), index)
        except ValueError as e:
            raise HTTPException(422, str(e))
        normalized.append((seq, payload))
    conflict = None
    accepted = 0
    duplicates = 0
    now = time.time()
    for seq, payload in normalized:
        existing = db.scalar(select(CollectionRecord).where(
            CollectionRecord.device_id == device.id, CollectionRecord.seq == seq))
        if existing is not None:
            if _canonical(existing.payload) == _canonical(payload):
                duplicates += 1  # ACK 丢失重发：幂等，不新增样本
                continue
            conflict = CollectionConflict(
                device_id=device.id, seq=seq,
                existing_payload=existing.payload, incoming_payload=payload,
                state='open', created_at=now,
            )
            db.add(conflict)
            device.status = 'blocked'
            db.flush()
            break
        db.add(CollectionRecord(
            device_id=device.id, seq=seq, payload=payload,
            state='buffered', received_at=now,
        ))
        accepted += 1
    db.flush()
    _advance_confirmed(db, device)
    return device, accepted, duplicates, conflict


def _advance_confirmed(db, device):
    """把从 confirmed_seq+1 开始连续存在的缓存段转为 confirmed。"""
    if device.status == 'blocked':
        return
    while True:
        nxt = db.scalar(select(CollectionRecord).where(
            CollectionRecord.device_id == device.id,
            CollectionRecord.seq == device.confirmed_seq + 1,
        ))
        if nxt is None:
            break
        nxt.state = 'confirmed'
        nxt.confirmed_at = time.time()
        device.confirmed_seq += 1
    if device.end_seq is not None:
        device.status = 'done' if device.confirmed_seq >= device.end_seq else 'waiting'
    else:
        device.status = 'receiving'
    db.flush()


def resolve_conflict(db, conflict, device, keep, note, actor):
    if conflict.state != 'open':
        raise HTTPException(409, '该冲突已处理，请勿重复操作')
    record = db.scalar(select(CollectionRecord).where(
        CollectionRecord.device_id == device.id, CollectionRecord.seq == conflict.seq))
    if keep == 'incoming':
        if record is None:
            db.add(CollectionRecord(
                device_id=device.id, seq=conflict.seq, payload=conflict.incoming_payload,
                state='buffered', received_at=time.time(),
            ))
        else:
            # 原位替换载荷：连续前缀的连续性不受影响，confirmed 记录保持 confirmed
            was_confirmed = record.state == 'confirmed'
            record.payload = conflict.incoming_payload
            if not was_confirmed:
                record.state = 'buffered'
                record.confirmed_at = None
    conflict.state = ('resolved_keep_existing' if keep == 'existing'
                      else 'resolved_keep_incoming')
    conflict.resolved_at = time.time()
    conflict.note = note
    db.flush()
    still_open = db.scalar(select(CollectionConflict.id).where(
        CollectionConflict.device_id == device.id,
        CollectionConflict.state == 'open',
    ))
    if not still_open:
        # 解除阻断并按现有缓存重新推进连续确认，恢复原进度
        device.status = 'receiving' if device.end_seq is None else 'waiting'
        _advance_confirmed(db, device)
    return conflict


def _duplicate_runs(db, batch_id):
    devices = list(db.scalars(select(CollectionDevice).where(CollectionDevice.batch_id == batch_id)))
    seen, dupes = set(), set()
    for device in devices:
        for r in db.scalars(select(CollectionRecord).where(
                CollectionRecord.device_id == device.id,
                CollectionRecord.state == 'confirmed').order_by(CollectionRecord.seq)):
            run = str(r.payload.get('run'))
            if run in seen:
                dupes.add(run)
            seen.add(run)
    return sorted(dupes)


def seal_batch(db, batch):
    """封批：全部设备结束、无缺号、无未决冲突 -> 生成唯一 Dataset。幂等。

    调用方须先持有批次行锁（_lock_batch），保证并发封批串行、成批唯一。
    """
    if batch.status == 'sealed':
        # 已在锁内复核：重复封批直接返回同一 Dataset，绝不二次成批
        existing = db.get(Dataset, batch.dataset_id) if batch.dataset_id else None
        if existing is not None:
            return batch, existing, True
        raise HTTPException(409, '批次状态为已成批但分析批次缺失，请联系管理员核对')
    device_stmt = select(CollectionDevice).where(CollectionDevice.batch_id == batch.id)
    if db.bind.dialect.name != 'sqlite':
        device_stmt = device_stmt.with_for_update()
    devices = list(db.scalars(device_stmt))
    undeclared = [d.code for d in devices if d.end_seq is None]
    if undeclared:
        raise HTTPException(409, '仍有设备未声明结束序号：' + '、'.join(undeclared))
    blocked = [d.code for d in devices if d.status == 'blocked']
    if blocked:
        raise HTTPException(409, '设备存在同号异值冲突未处理：' + '、'.join(blocked))
    # 1..end_seq 中库里尚不存在的序号即为待补缺号（缓冲与确认都算已留存）
    gaps = {}
    for d in devices:
        rows = set(db.scalars(select(CollectionRecord.seq).where(CollectionRecord.device_id == d.id)))
        gaps[d.code] = [s for s in range(d.confirmed_seq + 1, d.end_seq + 1) if s not in rows]
    missing = {c: g for c, g in gaps.items() if g}
    if missing:
        raise HTTPException(409, '仍有缺号待补传：' + '；'.join(f'{c} 缺 {g[:8]}{"…" if len(g) > 8 else ""}' for c, g in missing.items()))
    open_conflict = db.scalar(
        select(CollectionConflict.id)
        .join(CollectionDevice, CollectionDevice.id == CollectionConflict.device_id)
        .where(CollectionDevice.batch_id == batch.id, CollectionConflict.state == 'open'))
    if open_conflict:
        raise HTTPException(409, '存在未处理的同号异值冲突')
    dupes = _duplicate_runs(db, batch.id)
    if dupes:
        raise HTTPException(409, '跨设备存在重复片段号 run：' + '、'.join(dupes[:10]))
    records, source_parts = [], []
    for d in devices:
        rows = list(db.scalars(select(CollectionRecord).where(
            CollectionRecord.device_id == d.id,
            CollectionRecord.state == 'confirmed').order_by(CollectionRecord.seq)))
        if len(rows) != d.end_seq:
            raise HTTPException(409, f'设备 {d.code} 确认数 {len(rows)} 与结束序号 {d.end_seq} 不一致')
        source_parts.append(f'{d.code}:{d.end_seq}')
        records.extend(r.payload for r in rows)
    name = f'{batch.name} · 秒表采集成批'
    if db.scalar(select(Dataset).where(Dataset.name == name)):
        raise HTTPException(409, '同名分析批次已存在，无法重复成批')
    dataset = Dataset(
        name=name, source='stopwatch://' + '|'.join(source_parts),
        records=records, checksum=checksum(records), created_at=time.time(),
    )
    db.add(dataset)
    db.flush()
    batch.dataset_id = dataset.id
    batch.status = 'sealed'
    batch.sealed_at = time.time()
    return batch, dataset, False
