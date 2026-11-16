from sqlalchemy import String, JSON, Double, Integer, ForeignKey, Boolean, UniqueConstraint, Text
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class CollectionBatch(Base):
    """一次多设备秒表连续采集的批次（先登记设备名单，再逐设备连续确认）。"""
    __tablename__ = 'collection_batches'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    status: Mapped[str] = mapped_column(String(20), default='open')
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey('datasets.id'), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    sealed_at: Mapped[float | None] = mapped_column(Double, nullable=True)


class CollectionDevice(Base):
    """采集名单中的一台秒表设备，各自从 1 编号，声明结束序号。"""
    __tablename__ = 'collection_devices'
    __table_args__ = (UniqueConstraint('batch_id', 'code', name='uq_collection_device_code'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey('collection_batches.id'))
    code: Mapped[str] = mapped_column(String(60))
    label: Mapped[str] = mapped_column(String(160), default='')
    end_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmed_seq: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default='receiving')
    created_at: Mapped[float] = mapped_column(Double)


class CollectionRecord(Base):
    """设备上报记录。乱序先 buffered 留存；只有连续段才转 confirmed 落库确认。"""
    __tablename__ = 'collection_records'
    __table_args__ = (UniqueConstraint('device_id', 'seq', name='uq_collection_record_seq'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey('collection_devices.id'))
    seq: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(20), default='buffered')
    received_at: Mapped[float] = mapped_column(Double)
    confirmed_at: Mapped[float | None] = mapped_column(Double, nullable=True)


class CollectionConflict(Base):
    """同号异值冲突：冲突记录原样保留，设备阻断直到处理。"""
    __tablename__ = 'collection_conflicts'
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey('collection_devices.id'))
    seq: Mapped[int] = mapped_column(Integer)
    existing_payload: Mapped[dict] = mapped_column(JSON)
    incoming_payload: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(40), default='open')
    created_at: Mapped[float] = mapped_column(Double)
    resolved_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    note: Mapped[str] = mapped_column(Text, default='')
