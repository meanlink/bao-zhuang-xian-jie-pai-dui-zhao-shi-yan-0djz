import hashlib, json
from sqlalchemy import select, func
def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
def dataset_dict(d):
    return {'id':d.id, 'name':d.name, 'source':d.source, 'rows':len(d.records), 'checksum':d.checksum, 'created_at':d.created_at}
def job_dict(j):
    return {k:getattr(j,k) for k in ['id','name','dataset_id','params','status','result','error','approved','created_at','finished_at']}
def report_dict(r):
    return {k:getattr(r,k) for k in ['id','name','job_id','destination','interval_seconds','next_run','enabled']}
def delivery_dict(d):
    return {k:getattr(d,k) for k in ['id','report_id','created_at','checksum','filename','destination']}
def conflict_dict(c):
    return {'id':c.id,'device_id':c.device_id,'seq':c.seq,'existing_payload':c.existing_payload,
            'incoming_payload':c.incoming_payload,'state':c.state,'created_at':c.created_at,
            'resolved_at':c.resolved_at,'note':c.note}
def device_dict(db, d, include_records=False, limit=200):
    from src.models import CollectionRecord, CollectionConflict
    buffered = list(db.scalars(select(CollectionRecord.seq).where(
        CollectionRecord.device_id == d.id, CollectionRecord.state == 'buffered').order_by(CollectionRecord.seq)))
    have = set(db.scalars(select(CollectionRecord.seq).where(CollectionRecord.device_id == d.id)))
    end = d.end_seq if d.end_seq is not None else d.confirmed_seq
    missing = [s for s in range(d.confirmed_seq + 1, end + 1) if s not in have]
    out = {'id': d.id, 'batch_id': d.batch_id, 'code': d.code, 'label': d.label,
           'end_seq': d.end_seq, 'confirmed_seq': d.confirmed_seq, 'status': d.status,
           'buffered_seqs': buffered, 'missing_seqs': missing,
           'next_expected': d.confirmed_seq + 1, 'created_at': d.created_at}
    conflicts = list(db.scalars(select(CollectionConflict).where(
        CollectionConflict.device_id == d.id).order_by(CollectionConflict.id.desc())))
    out['conflicts'] = [conflict_dict(c) for c in conflicts]
    out['open_conflict_count'] = sum(1 for c in conflicts if c.state == 'open')
    if include_records:
        rows = list(db.scalars(select(CollectionRecord).where(
            CollectionRecord.device_id == d.id).order_by(CollectionRecord.seq).limit(limit)))
        out['records'] = [{'seq': r.seq, 'state': r.state, 'payload': r.payload,
                           'received_at': r.received_at, 'confirmed_at': r.confirmed_at} for r in rows]
        out['record_count'] = db.scalar(select(func.count()).select_from(CollectionRecord).where(CollectionRecord.device_id == d.id))
    return out
def batch_dict(db, b, detail=False):
    from src.models import CollectionDevice
    devices = list(db.scalars(select(CollectionDevice).where(
        CollectionDevice.batch_id == b.id).order_by(CollectionDevice.id)))
    out = {'id': b.id, 'name': b.name, 'status': b.status, 'dataset_id': b.dataset_id,
           'created_at': b.created_at, 'sealed_at': b.sealed_at,
           'devices': [device_dict(db, d, include_records=detail) for d in devices]}
    out['device_count'] = len(devices)
    out['confirmed_count'] = sum(d.confirmed_seq for d in devices)
    out['buffered_count'] = sum(len(x['buffered_seqs']) for x in out['devices'])
    out['missing_count'] = sum(len(x['missing_seqs']) for x in out['devices'])
    out['open_conflict_count'] = sum(x['open_conflict_count'] for x in out['devices'])
    return out

