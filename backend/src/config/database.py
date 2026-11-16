from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .settings import DATABASE_URL

class Base(DeclarativeBase):
    pass

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=1800)

if DATABASE_URL.startswith('sqlite'):
    # 关闭 pysqlite 隐式事务，改由 SQLAlchemy 显式开启；接样与封批并发时
    # BEGIN IMMEDIATE 让写事务排队，避免旧快照下重复成批或唯一约束竞争。
    @event.listens_for(engine, 'connect')
    def _sqlite_pragmas(dbapi_conn, _record):
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        cur.execute('PRAGMA journal_mode=WAL')
        cur.execute('PRAGMA busy_timeout=8000')
        cur.execute('PRAGMA foreign_keys=ON')
        cur.close()

    @event.listens_for(engine, 'begin')
    def _sqlite_begin_immediate(conn):
        conn.exec_driver_sql('BEGIN IMMEDIATE')

Session = sessionmaker(engine, expire_on_commit=False)

def get_db():
    with Session() as db:
        yield db
