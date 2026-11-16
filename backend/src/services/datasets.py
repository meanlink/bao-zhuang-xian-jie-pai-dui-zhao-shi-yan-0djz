from fastapi import HTTPException
from sqlalchemy import select
from src.models import Dataset
from src.services.business import validate_row
from src.utils.serialization import checksum
import time
def create(db, data):
    name=data.name.strip()
    if len(name)<2: raise HTTPException(422, '批次名称去除空格后至少需要两个字符')
    if db.scalar(select(Dataset).where(Dataset.name==name)):
        raise HTTPException(409, '该数据批次名已存在，请修改名称')
    for index,row in enumerate(data.records):
        try:
            validate_row(row, index)
        except ValueError as e:
            raise HTTPException(422, str(e))
    d=Dataset(name=name, source=data.source, records=data.records, checksum=checksum(data.records), created_at=time.time())
    db.add(d); db.flush()
    return d
