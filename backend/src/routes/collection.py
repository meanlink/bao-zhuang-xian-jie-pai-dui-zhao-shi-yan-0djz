from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from src.config.database import get_db
from src.models import CollectionBatch, CollectionDevice, CollectionConflict
from src.schemas.collection import (
    CollectionBatchInput, EndSeqInput, RecordUpload, ConflictResolve,
)
from src.middlewares.auth import current_user, can_write, admin_only
from src.services import collection as service
from src.services.audit import record
from src.utils.serialization import batch_dict, device_dict, conflict_dict

router = APIRouter(prefix='/collection')


@router.get('/batches')
def list_batches(db=Depends(get_db), u=Depends(current_user)):
    rows = db.scalars(select(CollectionBatch).order_by(CollectionBatch.id.desc()))
    return [batch_dict(db, b) for b in rows]


@router.post('/batches', status_code=201)
def create_batch(data: CollectionBatchInput, db=Depends(get_db), u=Depends(can_write)):
    b = service.create_batch(db, data, u)
    record(db, u.email, 'collection.create', {'id': b.id, 'devices': len(data.devices)})
    db.commit()
    return batch_dict(db, b, detail=True)


@router.get('/batches/{batch_id}')
def get_batch(batch_id: int, db=Depends(get_db), u=Depends(current_user)):
    b = db.get(CollectionBatch, batch_id)
    if not b:
        raise HTTPException(404, '采集批次不存在')
    return batch_dict(db, b, detail=True)


@router.get('/devices/{device_id}')
def get_device(device_id: int, db=Depends(get_db), u=Depends(current_user)):
    d = db.get(CollectionDevice, device_id)
    if not d:
        raise HTTPException(404, '采集设备不存在')
    return device_dict(db, d, include_records=True)


@router.post('/devices/{device_id}/end-seq')
def declare_end(device_id: int, data: EndSeqInput, db=Depends(get_db), u=Depends(can_write)):
    d = db.get(CollectionDevice, device_id)
    if not d:
        raise HTTPException(404, '采集设备不存在')
    # 与接样、封批统一“批次 -> 设备”的加锁顺序，避免数据库死锁
    b = service._lock_batch(db, d.batch_id)
    d = service._lock_device(db, device_id)
    if b.status == 'sealed':
        raise HTTPException(409, '采集批次已成批，不能修改结束序号')
    service.declare_end(db, d, data.end_seq)
    service.recompute_status(db, b)
    record(db, u.email, 'collection.end_seq', {'device': d.id, 'end_seq': data.end_seq})
    db.commit()
    return device_dict(db, d)


@router.post('/devices/{device_id}/records', status_code=202)
def upload(device_id: int, data: RecordUpload, db=Depends(get_db), u=Depends(can_write)):
    d = db.get(CollectionDevice, device_id)
    if not d:
        raise HTTPException(404, '采集设备不存在')
    b = service._lock_batch(db, d.batch_id)
    d = service._lock_device(db, device_id)
    if b.status == 'sealed':
        raise HTTPException(409, '采集批次已成批，拒绝接样')
    d, accepted, duplicates, conflict = service.upload_records(db, d, data.records)
    service.recompute_status(db, b)
    record(db, u.email, 'collection.upload',
           {'device': d.id, 'accepted': accepted, 'duplicates': duplicates,
            'conflict': conflict.id if conflict else None})
    db.commit()
    if conflict is not None:
        raise HTTPException(409, {
            'message': f'序号 {conflict.seq} 同号异值，设备已阻断；冲突已保留',
            'conflict_id': conflict.id, 'seq': conflict.seq,
            'existing': conflict.existing_payload, 'incoming': conflict.incoming_payload,
        })
    return {'accepted': accepted, 'duplicates': duplicates,
            'confirmed_seq': d.confirmed_seq, 'status': d.status,
            'next_expected': d.confirmed_seq + 1}


@router.post('/batches/{batch_id}/seal')
def seal(batch_id: int, db=Depends(get_db), u=Depends(can_write)):
    b = service._lock_batch(db, batch_id)
    b, dataset, idempotent = service.seal_batch(db, b)
    record(db, u.email, 'collection.seal',
           {'id': b.id, 'dataset': dataset.id, 'idempotent': idempotent})
    db.commit()
    return batch_dict(db, b, detail=True)


@router.post('/conflicts/{conflict_id}/resolve')
def resolve_conflict(conflict_id: int, data: ConflictResolve,
                     db=Depends(get_db), u=Depends(admin_only)):
    c = db.get(CollectionConflict, conflict_id)
    if not c:
        raise HTTPException(404, '冲突不存在')
    d0 = db.get(CollectionDevice, c.device_id)
    b = service._lock_batch(db, d0.batch_id)
    d = service._lock_device(db, c.device_id)
    service.resolve_conflict(db, c, d, data.keep, data.note, u.email)
    service.recompute_status(db, b)
    record(db, u.email, 'collection.conflict_resolve',
           {'conflict': c.id, 'keep': data.keep})
    db.commit()
    return conflict_dict(c)
