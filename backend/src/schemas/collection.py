from pydantic import BaseModel, Field


class DeviceRosterItem(BaseModel):
    code: str = Field(min_length=1, max_length=60)
    label: str = Field(default='', max_length=160)
    end_seq: int | None = Field(default=None, ge=1, le=1_000_000)


class CollectionBatchInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    devices: list[DeviceRosterItem] = Field(min_length=1, max_length=200)


class EndSeqInput(BaseModel):
    end_seq: int = Field(ge=1, le=1_000_000)


class RecordItem(BaseModel):
    seq: int = Field(ge=1, le=1_000_000)
    payload: dict


class RecordUpload(BaseModel):
    records: list[RecordItem] = Field(min_length=1, max_length=5000)


class ConflictResolve(BaseModel):
    keep: str = Field(default='existing', pattern='^(existing|incoming)$')
    note: str = Field(default='', max_length=500)
