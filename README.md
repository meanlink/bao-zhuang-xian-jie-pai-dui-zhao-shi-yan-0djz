# 包装线节拍对照实验

Welch 实验检验的真实业务分析小型项目。完整模拟客户、痛点、上下游、算法公式与一天工作剧本见 [BUSINESS.md](BUSINESS.md)。

## 模块与业务流程
数据接入（JSON/CSV、字段验证、批次指纹）→ 参数化分析（异步 Python worker）→ 结果复核（指标、图形、明细、解释、批准）→ 定时分发（本地收件箱、CSV/JSON、SHA256）；另有业务总览和审计轨迹。
三个贯穿全栈的实体：Dataset 数据批次、Job 分析任务、Report 分发计划；辅助 User、Audit、Delivery。

### 秒表断网补传（连续采集）
多台秒表断网后补传时，不能用“已收到的最大序号”掩盖中间漏段。系统在「秒表采集」页提供独立于普通导入的连续采集链路：
- **先登记设备名单再开批**：每台设备各自从序号 1 开始，并可声明结束序号（end_seq）。
- **乱序先留存、连续才确认**：未接上连续段的记录以 `buffered` 留存；只有从 `confirmed_seq+1` 起连续的段才转 `confirmed` 落库，确认位置即设备的可确认进度。
- **重试幂等**：ACK 丢失后同号同值重发只计数去重，不新增样本；中断后重新拉取设备即恢复原进度。
- **缺号可补传**：设备声明结束后，1..end_seq 中的缺口在页面标红，补齐后缓存段自动连续确认。
- **同号异值阻断**：同序号不同值会原样保留双方载荷生成冲突，设备立即阻断；仅 admin 可在冲突清单选择保留原值或采用新值后解除。
- **唯一成批**：全部设备声明结束、无缺号、无未决冲突才允许封批，生成一个普通 `Dataset`（普通导入与分析照常使用）；封批与接样并发由批次行锁与条件复核保护，不漏段、不重复成批。
- **本地重现**：页面内置断网丢包、确认丢失重发、冲突阻断、中断恢复四个场景；后端另附纯标准库 CLI：`python -m scripts.stopwatch_client --scenario all`。


## 快速启动
```bash
cp .env.example .env
docker compose up -d --build --wait
docker compose exec -T backend python -m scripts.seed_demo
```
访问 http://localhost:18203；后端文档 http://localhost:19203/docs；数据库宿主端口 17703。
`SEED_DEMO=true` 自动幂等 seed；不覆盖用户修改。手动 seed 命令可重复执行。

## 演示账号与权限
|账号|密码|真实权限|
|---|---|---|
|admin@demo.local|Demo123!|接入、执行、批准、分发、暂停、读取与下载|
|analyst@demo.local|Demo123!|接入、执行、读取与下载；不能批准或安排分发|
|viewer@demo.local|Demo123!|只读与下载|

## 技术与本地开发
Python 3.12 / FastAPI / SQLAlchemy / NumPy / SciPy / scikit-learn；MySQL 8.0；Redis 7；Nginx；原生 HTML CSS JavaScript ES modules（用户指定）。后端所有校验、持久化和算法在服务端。无前端打包步骤。
后端：安装 backend/requirements.txt，设置 DATABASE_URL、REDIS_URL、JWT_SECRET、ARTIFACT_DIR 后在 backend/ 执行 `python -m migrations.initial`、`python -m scripts.seed_demo`、`uvicorn src.main:app`，另开 `python -m src.worker`。前端使用带 /api 反向代理的 Nginx，直接打开 index.html 不支持 API。

## 目录与工程约定
backend/src/{models,schemas,services,routes,controllers,middlewares,constants,config,utils}；backend/scripts/seed_demo.py、backend/scripts/stopwatch_client.py；backend/migrations/initial.py。
frontend/src/{api,stores,types,components/common,hooks,pages,router,utils,constants}。
秒表采集相关：模型 CollectionBatch/CollectionDevice/CollectionRecord/CollectionConflict（models/collection.py）、服务 services/collection.py、路由 routes/collection.py、页面 pages/collection.js 与 utils/stopwatch.js。
Role 枚举：admin/analyst/viewer；JobStatus：queued/running/completed/failed；采集批次 CollectionStatus：open/waiting/conflict/sealed，设备 DeviceStatus：receiving/waiting/blocked/done。后端 backend/src/constants/enums.py，前端 frontend/src/constants/enums.js；路由权限在 middlewares/auth.py；页面按相同角色控制操作入口。

## 环境变量与 Docker
COMPOSE_PROJECT_NAME=pack-welch；FRONTEND_PORT=18203；BACKEND_PORT=19203；DB_PORT=17703；DB_PASSWORD、JWT_SECRET 见 .env.example；SEED_DEMO 默认 true。共享命名产物卷供后端下载与 worker 写入，数据库/Redis 各持久卷。
恰好五服务均有健康检查。Redis 队列加 SQL 排队状态兜底；worker 按 next_run 生成本地收件箱文件。周期 60 秒至 30 天。分发结果为批准分析的固定快照；若需新数据重新计算并建立新计划。当前不连接邮件或企业聊天。
错误响应、请求编号、Redis 每 IP 每分钟 240 次限流、JWT 和数据库操作审计。演示密钥与密码仅供本机模拟；生产部署前配置独立密钥与账号。

## 数据与验收
所有数据为固定随机种子生成的模拟业务数据，不能作为实际生产决策证据。项目独立算法、输入边界及业务局限详见 BUSINESS.md。测试与截图统一存放 ../../test-artifacts/gb-1004/，源码不含日志/截图/测试报告。
秒表采集验收：后端 `python -m pytest ../test-artifacts/gb-1004/tests/test_collection.py -v`（25 项，覆盖名单登记、乱序留存、连续确认、ACK 丢失幂等、中断恢复、缺号补传、同号冲突阻断/解除、封批条件、并发不重复成批、普通导入不受影响）；前端 jsdom 冒烟 `node test-artifacts/gb-1004/tests/frontend_smoke.mjs`（需先启动后端）。
清理本项目演示数据：`docker compose down -v --remove-orphans`（会删除本项目命名卷）。

## License
MIT，见 LICENSE。
