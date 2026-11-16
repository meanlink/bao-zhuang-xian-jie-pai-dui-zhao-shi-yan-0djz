"""秒表断网补传全链路测试：连续确认、乱序留存、缺号补传、ACK 丢失、同号冲突、封批并发。

运行：cd backend && python -m pytest ../test-artifacts/gb-1004/tests/test_collection.py -v
（也可直接 python test_collection.py，基于 unittest，无需 pytest）
"""
import os
import sys
import tempfile
import threading
import unittest

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'backend'))
sys.path.insert(0, _BACKEND)

_TMP = tempfile.mkdtemp(prefix='coll-test-')
os.environ.setdefault('DATABASE_URL', 'sqlite:///' + os.path.join(_TMP, 'test.db'))
os.environ.setdefault('SEED_DEMO', 'false')
os.environ.setdefault('JWT_SECRET', 'test-secret-key-at-least-32-bytes-long-xx')

from fastapi.testclient import TestClient  # noqa: E402

from migrations.initial import migrate  # noqa: E402
from src.main import app  # noqa: E402
from src.config.database import Session  # noqa: E402
from src.models import (  # noqa: E402
    User, CollectionBatch, CollectionDevice, CollectionRecord, CollectionConflict, Dataset,
)
from src.utils.security import hash_password  # noqa: E402

migrate()


def _payload(code, seq, seconds=None):
    return {
        'run': f'{code}-{seq:03d}',
        'arm': 'A' if seq % 2 else 'B',
        'shift': '早班' if seq % 2 else '晚班',
        'seconds': round(10 + seq + (seq * 7 % 5) / 10, 3) if seconds is None else seconds,
    }


class CollectionTestBase(unittest.TestCase):
    client: TestClient = None
    admin_headers: dict = None
    analyst_headers: dict = None
    viewer_headers: dict = None

    @classmethod
    def setUpClass(cls):
        with Session() as db:
            for role, name in [('admin', '负责人'), ('analyst', '分析员'), ('viewer', '观察员')]:
                email = f'{role}@t.local'
                if not db.query(User).filter_by(email=email).first():
                    db.add(User(email=email, name=name, role=role,
                                password=hash_password('Demo123!')))
            db.commit()
        cls.client = TestClient(app)
        cls.admin_headers = cls._login('admin@t.local')
        cls.analyst_headers = cls._login('analyst@t.local')
        cls.viewer_headers = cls._login('viewer@t.local')

    @classmethod
    def _login(cls, email):
        r = cls.client.post('/auth/login', json={'email': email, 'password': 'Demo123!'})
        assert r.status_code == 200, r.text
        return {'Authorization': 'Bearer ' + r.json()['access_token']}

    def setUp(self):
        # 每个测试方法用独立采集批次，避免相互干扰
        self._seq = id(self) % 100000

    def create_batch(self, name, devices, headers=None):
        body = {'name': name, 'devices': devices}
        return self.client.post('/collection/batches', headers=headers or self.analyst_headers, json=body)

    def open_two_devices(self, name=None):
        name = name or f'测试批次 {self._seq}-{self._next_id()}'
        r = self.create_batch(name, [
            {'code': 'SW-A', 'label': '甲表'},
            {'code': 'SW-B', 'label': '乙表', 'end_seq': 3},
        ])
        self.assertEqual(r.status_code, 201)
        b = r.json()
        return b, next(d for d in b['devices'] if d['code'] == 'SW-A'), \
            next(d for d in b['devices'] if d['code'] == 'SW-B')

    def _next_id(self):
        with Session() as db:
            return db.query(CollectionBatch).count()

    def upload(self, device_id, records, headers=None, allow_error=False):
        r = self.client.post(f'/collection/devices/{device_id}/records',
                             headers=headers or self.analyst_headers,
                             json={'records': records})
        if not allow_error:
            self.assertIn(r.status_code, (200, 202), r.text)
        return r


class TestRosterAndPermissions(CollectionTestBase):
    def test_roster_registered_first(self):
        r = self.create_batch(f'名单测试 {self._next_id()}', [
            {'code': 'D1'}, {'code': 'D2', 'end_seq': 5}, {'code': 'D3', 'label': '三号'}])
        self.assertEqual(r.status_code, 201)
        codes = [d['code'] for d in r.json()['devices']]
        self.assertEqual(codes, ['D1', 'D2', 'D3'])
        d2 = next(d for d in r.json()['devices'] if d['code'] == 'D2')
        self.assertEqual(d2['end_seq'], 5)
        self.assertEqual(d2['confirmed_seq'], 0)

    def test_duplicate_device_code_rejected(self):
        r = self.create_batch(f'重复设备 {self._next_id()}', [{'code': 'X'}, {'code': 'X'}])
        self.assertEqual(r.status_code, 422)

    def test_duplicate_batch_name_rejected(self):
        name = f'同名批次 {self._next_id()}'
        self.assertEqual(self.create_batch(name, [{'code': 'X'}]).status_code, 201)
        self.assertEqual(self.create_batch(name, [{'code': 'Y'}]).status_code, 409)

    def test_viewer_read_only(self):
        r = self.create_batch(f'权限批次 {self._next_id()}', [{'code': 'X'}], headers=self.viewer_headers)
        self.assertEqual(r.status_code, 403)
        # 可读
        self.assertEqual(self.client.get('/collection/batches',
                                         headers=self.viewer_headers).status_code, 200)

    def test_anonymous_rejected(self):
        self.assertEqual(self.client.get('/collection/batches').status_code, 401)


class TestContinuousConfirmation(CollectionTestBase):
    def test_out_of_order_buffered_then_continuous(self):
        _, d1, _ = self.open_two_devices()
        r = self.upload(d1['id'], [
            {'seq': 3, 'payload': _payload('SW-A', 3)},
            {'seq': 1, 'payload': _payload('SW-A', 1)},
        ])
        self.assertEqual(r.json()['confirmed_seq'], 1)  # 只确认连续到 1，seq3 留存
        info = self.client.get(f"/collection/devices/{d1['id']}",
                               headers=self.analyst_headers).json()
        self.assertEqual(info['buffered_seqs'], [3])
        r = self.upload(d1['id'], [{'seq': 2, 'payload': _payload('SW-A', 2)}])
        self.assertEqual(r.json()['confirmed_seq'], 3)  # 1,2,3 全部连续
        info = self.client.get(f"/collection/devices/{d1['id']}",
                               headers=self.analyst_headers).json()
        self.assertEqual(info['buffered_seqs'], [])
        with Session() as db:
            states = {x.seq: x.state for x in db.query(CollectionRecord).filter_by(device_id=d1['id'])}
        self.assertEqual(set(states.values()), {'confirmed'})

    def test_ack_loss_retry_is_idempotent(self):
        _, d1, _ = self.open_two_devices()
        self.upload(d1['id'], [{'seq': s, 'payload': _payload('SW-A', s)} for s in (1, 2, 3)])
        before = self.client.get(f"/collection/devices/{d1['id']}",
                                 headers=self.analyst_headers).json()
        # 秒表未收到 ACK，原样重发
        r = self.upload(d1['id'], [{'seq': s, 'payload': _payload('SW-A', s)} for s in (1, 2, 3)])
        self.assertEqual(r.json()['accepted'], 0)
        self.assertEqual(r.json()['duplicates'], 3)
        after = self.client.get(f"/collection/devices/{d1['id']}",
                                headers=self.analyst_headers).json()
        self.assertEqual(after['record_count'], before['record_count'])  # 不新增样本
        self.assertEqual(after['confirmed_seq'], 3)

    def test_interruption_resumes_same_progress(self):
        b, d1, _ = self.open_two_devices()
        # 断网状态下只到了 seq2：留存在缓冲区，连续确认位置仍为 0
        self.upload(d1['id'], [{'seq': 2, 'payload': _payload('SW-A', 2)}])
        # 模拟客户端/网络中断后重新接入：重新拉取即恢复服务端持久化进度
        fresh = self.client.get(f"/collection/devices/{d1['id']}",
                                headers=self.analyst_headers).json()
        self.assertEqual(fresh['confirmed_seq'], 0)
        self.assertEqual(fresh['next_expected'], 1)
        self.assertEqual(fresh['buffered_seqs'], [2])
        # 恢复后补 seq1，缓存中的 2 自动接上，进度从原位置继续而不是从头
        r = self.upload(d1['id'], [{'seq': 1, 'payload': _payload('SW-A', 1)}])
        self.assertEqual(r.json()['confirmed_seq'], 2)
        r = self.upload(d1['id'], [{'seq': 3, 'payload': _payload('SW-A', 3)}])
        self.assertEqual(r.json()['confirmed_seq'], 3)

    def test_payload_validation_rejected(self):
        _, d1, _ = self.open_two_devices()
        bad = _payload('SW-A', 1)
        bad['seconds'] = 'not-a-number'
        r = self.upload(d1['id'], [{'seq': 1, 'payload': bad}], allow_error=True)
        self.assertEqual(r.status_code, 422)
        info = self.client.get(f"/collection/devices/{d1['id']}",
                               headers=self.analyst_headers).json()
        self.assertEqual(info['record_count'], 0)  # 整批校验失败，不留垃圾

    def test_bulk_upload_atomic_on_mid_batch_failure(self):
        _, d1, _ = self.open_two_devices()
        good = _payload('SW-A', 1)
        bad = _payload('SW-A', 2)
        bad['seconds'] = None
        # 同批中先合法后非法：整批不落库，seq1 也不能留下
        r = self.upload(d1['id'], [
            {'seq': 1, 'payload': good},
            {'seq': 2, 'payload': bad},
        ], allow_error=True)
        self.assertEqual(r.status_code, 422)
        info = self.client.get(f"/collection/devices/{d1['id']}",
                               headers=self.analyst_headers).json()
        self.assertEqual(info['record_count'], 0)
        self.assertEqual(info['confirmed_seq'], 0)

    def test_seq_beyond_end_rejected(self):
        _, _, d2 = self.open_two_devices()
        r = self.upload(d2['id'], [{'seq': 4, 'payload': _payload('SW-B', 4)}], allow_error=True)
        self.assertEqual(r.status_code, 422)


class TestMissingAndConflict(CollectionTestBase):
    def test_gap_listed_and_filled(self):
        _, d1, _ = self.open_two_devices()
        self.client.post(f"/collection/devices/{d1['id']}/end-seq",
                         headers=self.analyst_headers, json={'end_seq': 5})
        self.upload(d1['id'], [{'seq': 1, 'payload': _payload('SW-A', 1)},
                               {'seq': 2, 'payload': _payload('SW-A', 2)},
                               {'seq': 4, 'payload': _payload('SW-A', 4)}])
        info = self.client.get(f"/collection/devices/{d1['id']}",
                               headers=self.analyst_headers).json()
        self.assertEqual(info['status'], 'waiting')
        self.assertEqual(info['missing_seqs'], [3, 5])
        # 补 seq3 后缓存的 4 自动接上，但 5 仍缺
        self.upload(d1['id'], [{'seq': 3, 'payload': _payload('SW-A', 3)}])
        info = self.client.get(f"/collection/devices/{d1['id']}",
                               headers=self.analyst_headers).json()
        self.assertEqual(info['confirmed_seq'], 4)
        self.assertEqual(info['missing_seqs'], [5])
        self.upload(d1['id'], [{'seq': 5, 'payload': _payload('SW-A', 5)}])
        info = self.client.get(f"/collection/devices/{d1['id']}",
                               headers=self.analyst_headers).json()
        self.assertEqual(info['status'], 'done')

    def test_end_seq_cannot_shrink_below_confirmed(self):
        _, d1, _ = self.open_two_devices()
        self.upload(d1['id'], [{'seq': 1, 'payload': _payload('SW-A', 1)},
                               {'seq': 2, 'payload': _payload('SW-A', 2)}])
        r = self.client.post(f"/collection/devices/{d1['id']}/end-seq",
                             headers=self.analyst_headers, json={'end_seq': 1})
        self.assertEqual(r.status_code, 422)

    def test_same_seq_different_value_blocks_and_keeps_conflict(self):
        _, d1, _ = self.open_two_devices()
        self.upload(d1['id'], [{'seq': 1, 'payload': _payload('SW-A', 1, seconds=12.5)}])
        r = self.upload(d1['id'], [{'seq': 1, 'payload': _payload('SW-A', 1, seconds=77.7)}],
                        allow_error=True)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()['detail']['seq'], 1)
        info = self.client.get(f"/collection/devices/{d1['id']}",
                               headers=self.analyst_headers).json()
        self.assertEqual(info['status'], 'blocked')
        self.assertEqual(info['open_conflict_count'], 1)
        # 阻断期间任何上报都被拒绝
        r = self.upload(d1['id'], [{'seq': 2, 'payload': _payload('SW-A', 2)}], allow_error=True)
        self.assertEqual(r.status_code, 409)
        with Session() as db:
            c = db.query(CollectionConflict).filter_by(device_id=d1['id']).one()
            self.assertEqual(c.existing_payload['seconds'], 12.5)
            self.assertEqual(c.incoming_payload['seconds'], 77.7)
            self.assertEqual(c.state, 'open')
            # 原样本未被覆盖
            rec = db.query(CollectionRecord).filter_by(device_id=d1['id'], seq=1).one()
            self.assertEqual(rec.payload['seconds'], 12.5)
        self._blocked_device, self._open_conflict = d1, c

    def test_resolve_conflict_unblocks_and_resumes(self):
        self.test_same_seq_different_value_blocks_and_keeps_conflict()
        d1, c = self._blocked_device, self._open_conflict
        # analyst 不能处理冲突
        r = self.client.post(f"/collection/conflicts/{c.id}/resolve",
                             headers=self.analyst_headers, json={'keep': 'existing'})
        self.assertEqual(r.status_code, 403)
        r = self.client.post(f"/collection/conflicts/{c.id}/resolve",
                             headers=self.admin_headers,
                             json={'keep': 'existing', 'note': '保留秒表原值'})
        self.assertEqual(r.status_code, 200)
        info = self.client.get(f"/collection/devices/{d1['id']}",
                               headers=self.analyst_headers).json()
        self.assertNotEqual(info['status'], 'blocked')
        self.assertEqual(info['confirmed_seq'], 1)  # 原进度保留
        # 冲突记录仍保留可追溯
        self.assertEqual(info['conflicts'][0]['state'], 'resolved_keep_existing')
        # 解除后可以继续采集
        r = self.upload(d1['id'], [{'seq': 2, 'payload': _payload('SW-A', 2)}])
        self.assertEqual(r.json()['confirmed_seq'], 2)

    def test_resolve_keep_incoming_replaces_value(self):
        _, d1, _ = self.open_two_devices()
        self.upload(d1['id'], [{'seq': 1, 'payload': _payload('SW-A', 1, seconds=12.5)}])
        self.upload(d1['id'], [{'seq': 1, 'payload': _payload('SW-A', 1, seconds=77.7)}],
                    allow_error=True)
        with Session() as db:
            cid = db.query(CollectionConflict).filter_by(device_id=d1['id']).one().id
        r = self.client.post(f'/collection/conflicts/{cid}/resolve',
                             headers=self.admin_headers, json={'keep': 'incoming'})
        self.assertEqual(r.status_code, 200)
        with Session() as db:
            rec = db.query(CollectionRecord).filter_by(device_id=d1['id'], seq=1).one()
            self.assertEqual(rec.payload['seconds'], 77.7)
            self.assertEqual(rec.state, 'confirmed')  # 连续前缀仍确认
        info = self.client.get(f"/collection/devices/{d1['id']}",
                               headers=self.analyst_headers).json()
        self.assertEqual(info['conflicts'][0]['state'], 'resolved_keep_incoming')


class TestSealing(CollectionTestBase):
    def _complete(self, device, end):
        self.client.post(f"/collection/devices/{device['id']}/end-seq",
                         headers=self.analyst_headers, json={'end_seq': end})
        self.upload(device['id'], [
            {'seq': s, 'payload': _payload(device['code'], s)} for s in range(1, end + 1)])

    def test_seal_requires_all_ended_no_gap_no_conflict(self):
        b, d1, d2 = self.open_two_devices()
        # 未声明结束
        r = self.client.post(f"/collection/batches/{b['id']}/seal",
                             headers=self.analyst_headers)
        self.assertEqual(r.status_code, 409)
        self._complete(d1, 4)
        # d2 已声明 end_seq=3，但只发 1,3 -> 缺 2
        self.upload(d2['id'], [{'seq': 1, 'payload': _payload('SW-B', 1)},
                               {'seq': 3, 'payload': _payload('SW-B', 3)}])
        r = self.client.post(f"/collection/batches/{b['id']}/seal",
                             headers=self.analyst_headers)
        self.assertEqual(r.status_code, 409)
        self.assertIn('缺号', r.json()['detail'])
        self.upload(d2['id'], [{'seq': 2, 'payload': _payload('SW-B', 2)}])
        r = self.client.post(f"/collection/batches/{b['id']}/seal",
                             headers=self.analyst_headers)
        self.assertEqual(r.status_code, 200)
        sealed = r.json()
        self.assertEqual(sealed['status'], 'sealed')
        self.assertIsNotNone(sealed['dataset_id'])
        # 唯一 Dataset，记录数为各设备 end_seq 之和，顺序按设备与序号
        ds = self.client.get(f"/datasets/{sealed['dataset_id']}",
                             headers=self.analyst_headers).json()
        self.assertEqual(ds['rows'], 4 + 3)
        full = self.client.get(f"/datasets/{sealed['dataset_id']}",
                               headers=self.analyst_headers).json()
        self.assertEqual(full['records'][0]['run'], 'SW-A-001')
        self.assertEqual(full['records'][-1]['run'], 'SW-B-003')

    def test_seal_idempotent_and_blocks_late_upload(self):
        b, d1, d2 = self.open_two_devices()
        self._complete(d1, 2)
        self.upload(d2['id'], [{'seq': s, 'payload': _payload('SW-B', s)} for s in (1, 2, 3)])
        r1 = self.client.post(f"/collection/batches/{b['id']}/seal",
                              headers=self.analyst_headers)
        r2 = self.client.post(f"/collection/batches/{b['id']}/seal",
                              headers=self.analyst_headers)
        self.assertEqual(r1.json()['dataset_id'], r2.json()['dataset_id'])  # 不重复成批
        with Session() as db:
            self.assertEqual(db.query(Dataset).count(), 1)
        # 成批后接样、改结束序号都被拒绝
        r = self.upload(d1['id'], [{'seq': 9, 'payload': _payload('SW-A', 9)}], allow_error=True)
        self.assertEqual(r.status_code, 409)
        r = self.client.post(f"/collection/devices/{d1['id']}/end-seq",
                             headers=self.analyst_headers, json={'end_seq': 9})
        self.assertEqual(r.status_code, 409)

    def test_seal_rejected_with_open_conflict(self):
        b, d1, d2 = self.open_two_devices()
        self._complete(d1, 2)
        self.upload(d2['id'], [{'seq': 1, 'payload': _payload('SW-B', 1)},
                               {'seq': 2, 'payload': _payload('SW-B', 2)},
                               {'seq': 3, 'payload': _payload('SW-B', 3)}])
        self.upload(d2['id'], [{'seq': 1, 'payload': _payload('SW-B', 1, seconds=42.0)}],
                    allow_error=True)
        r = self.client.post(f"/collection/batches/{b['id']}/seal",
                             headers=self.analyst_headers)
        self.assertEqual(r.status_code, 409)
        self.assertIn('冲突', r.json()['detail'])

    def test_duplicate_run_across_devices_rejected_at_seal(self):
        b, d1, d2 = self.open_two_devices()
        self._complete(d1, 2)
        # 故意让 d2 使用与 d1 相同的 run
        self.upload(d2['id'], [
            {'seq': 1, 'payload': {**_payload('SW-B', 1), 'run': 'SW-A-001'}},
            {'seq': 2, 'payload': _payload('SW-B', 2)},
            {'seq': 3, 'payload': _payload('SW-B', 3)},
        ])
        r = self.client.post(f"/collection/batches/{b['id']}/seal",
                             headers=self.analyst_headers)
        self.assertEqual(r.status_code, 409)
        self.assertIn('run', r.json()['detail'])


class TestConcurrency(CollectionTestBase):
    def test_concurrent_seal_single_batch(self):
        b, d1, d2 = self.open_two_devices()
        self.client.post(f"/collection/devices/{d1['id']}/end-seq",
                         headers=self.analyst_headers, json={'end_seq': 3})
        self.upload(d1['id'], [{'seq': s, 'payload': _payload('SW-A', s)} for s in (1, 2, 3)])
        self.upload(d2['id'], [{'seq': s, 'payload': _payload('SW-B', s)} for s in (1, 2, 3)])
        results, barrier = [], threading.Barrier(2)

        def seal():
            barrier.wait()
            r = self.client.post(f"/collection/batches/{b['id']}/seal",
                                 headers=self.analyst_headers)
            results.append(r.status_code)

        t1 = threading.Thread(target=seal)
        t2 = threading.Thread(target=seal)
        t1.start(); t2.start(); t1.join(); t2.join()
        self.assertEqual(sorted(results), [200, 200])  # 一成批一幂等
        with Session() as db:
            batch = db.get(CollectionBatch, b['id'])
            self.assertEqual(batch.status, 'sealed')
            self.assertEqual(db.query(Dataset).filter(Dataset.id == batch.dataset_id).count(), 1)
            # 只有一个成批 Dataset 来自该批次
            self.assertEqual(
                db.query(Dataset).filter(Dataset.name.like(b['name'] + '%')).count(), 1)

    def test_concurrent_upload_same_seq_same_value_no_duplicate(self):
        _, d1, _ = self.open_two_devices()
        payload = _payload('SW-A', 1)
        errors, ok = [], []

        def upload():
            r = self.upload(d1['id'], [{'seq': 1, 'payload': payload}], allow_error=True)
            (ok if r.status_code in (200, 202) else errors).append(r.status_code)

        threads = [threading.Thread(target=upload) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        with Session() as db:
            # 唯一约束保证同设备同序号只有一条
            self.assertEqual(
                db.query(CollectionRecord).filter_by(device_id=d1['id'], seq=1).count(), 1)

    def test_upload_while_sealing_does_not_lose_or_double_batch(self):
        # TestClient 经同一 portal 串行执行请求；写事务用 BEGIN IMMEDIATE 排队，
        # 因此只存在两种确定顺序，逐一验证“不漏段、不重复成批”的不变量。
        for order in ('seal_first', 'upload_first'):
            b, d1, d2 = self.open_two_devices()
            self.client.post(f"/collection/devices/{d1['id']}/end-seq",
                             headers=self.analyst_headers, json={'end_seq': 3})
            self.upload(d1['id'], [{'seq': 1, 'payload': _payload('SW-A', 1)},
                                   {'seq': 2, 'payload': _payload('SW-A', 2)}])
            self.upload(d2['id'], [{'seq': s, 'payload': _payload('SW-B', s)} for s in (1, 2, 3)])

            def seal():
                return self.client.post(f"/collection/batches/{b['id']}/seal",
                                        headers=self.analyst_headers)

            def late_upload():
                return self.upload(d1['id'], [{'seq': 3, 'payload': _payload('SW-A', 3)}],
                                   allow_error=True)
            if order == 'seal_first':
                # 封批时 seq3 缺失 -> 先拒绝；补传后再封成功
                r1 = seal()
                self.assertEqual(r1.status_code, 409)
                self.assertIn('缺号', r1.json()['detail'])
                r2 = late_upload()
                self.assertEqual(r2.status_code, 202)
                r3 = seal()
                self.assertEqual(r3.status_code, 200)
            else:
                # 补传先到 -> 设备齐套，封批一次成功
                r2 = late_upload()
                self.assertEqual(r2.status_code, 202)
                r3 = seal()
                self.assertEqual(r3.status_code, 200)
                # 成批后再来的迟到样本必须被拒绝
                late = self.upload(d1['id'], [{'seq': 3, 'payload': _payload('SW-A', 3)}],
                                   allow_error=True)
                self.assertEqual(late.status_code, 409)
            with Session() as db:
                batch = db.get(CollectionBatch, b['id'])
                self.assertEqual(batch.status, 'sealed')
                ds = db.query(Dataset).filter_by(id=batch.dataset_id).one()
                self.assertEqual(len(ds.records), 6)
                runs = [r['run'] for r in ds.records]
                self.assertEqual(len(runs), len(set(runs)))  # 无重复
                self.assertEqual(
                    db.query(Dataset).filter(Dataset.name.like(b['name'] + '%')).count(), 1)
                self.assertEqual(
                    db.query(CollectionRecord).filter_by(
                        device_id=d1['id'], seq=3).count(), 1)

    def test_seal_serialized_across_real_sessions(self):
        # 独立 DB 连接上的真实线程并发：行锁/IMMEDIATE 串行化，最多成批一次
        from src.services import collection as svc
        from src.schemas.collection import RecordItem
        b, d1, d2 = self.open_two_devices()
        with Session() as db:
            dev_a = db.get(CollectionDevice, d1['id'])
            dev_b = db.get(CollectionDevice, d2['id'])
            dev_a_id, dev_b_id, batch_id = dev_a.id, dev_b.id, b['id']
        outcomes = []

        def worker():
            import time as _time
            _time.sleep(0.01)
            session = Session()
            try:
                batch = session.get(CollectionBatch, batch_id)
                _, ds, idem = svc.seal_batch(session, batch)
                session.commit()
                outcomes.append(ds.id)
            except Exception as exc:  # 缺号失败也算合法结果，记录状态
                session.rollback()
                outcomes.append('409:' + str(getattr(exc, 'status_code', '?')))
            finally:
                session.close()
        # 先把两台设备补齐
        self.client.post(f"/collection/devices/{d1['id']}/end-seq",
                         headers=self.analyst_headers, json={'end_seq': 3})
        self.upload(d1['id'], [{'seq': s, 'payload': _payload('SW-A', s)} for s in (1, 2, 3)])
        self.upload(d2['id'], [{'seq': s, 'payload': _payload('SW-B', s)} for s in (1, 2, 3)])
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(outcomes), 4)
        ds_ids = {x for x in outcomes if isinstance(x, int)}
        self.assertEqual(len(ds_ids), 1)  # 只有一个 Dataset 被创建
        with Session() as db:
            self.assertEqual(db.get(CollectionBatch, batch_id).status, 'sealed')


class TestNormalImportUnaffected(CollectionTestBase):
    def test_ordinary_dataset_import_still_works(self):
        rows = [_payload('IMP', s) for s in range(1, 7)]
        r = self.client.post('/datasets', headers=self.analyst_headers,
                             json={'name': f'普通导入 {self._next_id()}',
                                   'source': 'manual.json', 'records': rows})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()['rows'], 6)
        # 普通批次与采集批次互不影响，均可在列表中查看
        ds_list = self.client.get('/datasets', headers=self.viewer_headers).json()
        self.assertTrue(any(d['source'] == 'manual.json' for d in ds_list))


if __name__ == '__main__':
    unittest.main(verbosity=2)
