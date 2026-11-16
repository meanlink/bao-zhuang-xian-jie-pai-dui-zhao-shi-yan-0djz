from enum import Enum
class Role(str, Enum):
    admin = 'admin'
    analyst = 'analyst'
    viewer = 'viewer'
class JobStatus(str, Enum):
    queued = 'queued'
    running = 'running'
    completed = 'completed'
    failed = 'failed'
# 秒表断网补传：采集批次与设备状态
class CollectionStatus(str, Enum):
    open = 'open'                 # 接收中
    waiting = 'waiting'           # 待补段（设备已声明结束但仍有缺号）
    conflict = 'conflict'         # 存在同号异值冲突
    sealed = 'sealed'             # 已成批
class DeviceStatus(str, Enum):
    receiving = 'receiving'       # 接收中，尚未声明结束序号
    waiting = 'waiting'           # 已声明结束，存在缺号待补
    blocked = 'blocked'           # 同号异值，阻断
    done = 'done'                 # 1..end_seq 全部连续确认
class RecordState(str, Enum):
    buffered = 'buffered'         # 乱序先留存，尚未连续落库确认
    confirmed = 'confirmed'       # 连续段，已确认给设备
class ConflictState(str, Enum):
    open = 'open'                 # 待处理冲突
    resolved_keep_existing = 'resolved_keep_existing'
    resolved_keep_incoming = 'resolved_keep_incoming'
