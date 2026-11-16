"""本地秒表端：对运行中的后端重现断网缺段、补传、ACK 丢失重发与同号异值冲突。

仅使用标准库，调用正式 /collection 接口，不绕过任何服务端规则。

用法（backend/ 目录下）：
  BASE_URL=http://localhost:8000 EMAIL=admin@demo.local PASSWORD=Demo123! \
      python -m scripts.stopwatch_client --scenario gap
  场景：gap（缺段补传） / ack（确认丢失重发） / conflict（同号异值阻断） / all

环境变量：BASE_URL（默认 http://localhost:8000）、EMAIL、PASSWORD（默认演示管理员）
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.getenv('BASE_URL', 'http://localhost:8000').rstrip('/')
EMAIL = os.getenv('EMAIL', 'admin@demo.local')
PASSWORD = os.getenv('PASSWORD', 'Demo123!')


class StopwatchClient:
    def __init__(self):
        self.token = None

    def _request(self, method, path, body=None):
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        req = urllib.request.Request(BASE_URL + path, data=data, method=method)
        req.add_header('Content-Type', 'application/json')
        if self.token:
            req.add_header('Authorization', 'Bearer ' + self.token)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode())
            except json.JSONDecodeError:
                detail = {'detail': e.reason}
            return e.code, detail

    def login(self):
        status, body = self._request('POST', '/auth/login',
                                     {'email': EMAIL, 'password': PASSWORD})
        if status != 200:
            raise SystemExit(f'登录失败 {status}: {body}')
        self.token = body['access_token']
        print(f'已登录 {EMAIL}')

    def open_batch(self, name, device_codes):
        status, body = self._request('POST', '/collection/batches', {
            'name': name,
            'devices': [{'code': c, 'label': '本地秒表 ' + c} for c in device_codes],
        })
        if status != 201:
            raise SystemExit(f'登记采集批次失败 {status}: {body}')
        print(f"采集批次 #{body['id']} {body['name']} 已登记，设备 {device_codes}")
        return body

    def declare_end(self, device_id, end_seq):
        status, body = self._request('POST', f'/collection/devices/{device_id}/end-seq',
                                     {'end_seq': end_seq})
        if status != 200:
            raise SystemExit(f'声明结束序号失败 {status}: {body}')
        print(f'设备 {device_id} 声明结束序号 {end_seq}')

    def upload(self, device_id, records, allow_conflict=False):
        status, body = self._request('POST', f'/collection/devices/{device_id}/records',
                                     {'records': records})
        if status == 409 and allow_conflict:
            print('  ! 服务端阻断：' + json.dumps(body.get('detail', body), ensure_ascii=False))
            return body
        if status not in (200, 202):
            raise SystemExit(f'上报失败 {status}: {body}')
        print(f"  接收 {body['accepted']} 条，幂等去重 {body['duplicates']} 条，"
              f"确认位置 {body['confirmed_seq']}，状态 {body['status']}")
        return body

    def device(self, device_id):
        status, body = self._request('GET', f'/collection/devices/{device_id}')
        if status != 200:
            raise SystemExit(f'读取设备失败 {status}: {body}')
        return body

    def seal(self, batch_id):
        status, body = self._request('POST', f'/collection/batches/{batch_id}/seal', {})
        if status != 200:
            print(f'封批被拒绝 {status}: {body.get("detail")}')
            return None
        print(f"封批成功：分析批次 #{body['dataset_id']}，状态 {body['status']}")
        return body


def make_payload(device_code, seq):
    arm = 'A' if seq % 2 else 'B'
    shift = '早班' if seq % 2 else '晚班'
    h = 7
    for ch in device_code + ':' + str(seq):
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    seconds = round(15 + (h % 600) / 100, 3)
    return {'run': f'{device_code}-{seq:03d}', 'arm': arm, 'shift': shift, 'seconds': seconds}


def scenario_gap(c: StopwatchClient):
    print('\n=== 场景一：断网缺段与补传 ===')
    batch = c.open_batch('本地重现 · 缺段补传', ['LW-1', 'LW-2'])
    d1, d2 = batch['devices']
    c.declare_end(d1['id'], 6)
    lost = {2, 5}  # 断网期间这两包没发出去
    for seq in [3, 1, 2, 4, 5, 6]:
        if seq in lost:
            print(f'  x 序号 {seq} 断网未送达，秒表本地留存')
            continue
        c.upload(d1['id'], [{'seq': seq, 'payload': make_payload(d1['code'], seq)}])
    info = c.device(d1['id'])
    print(f"首轮：确认位置 {info['confirmed_seq']}，缺号 {info['missing_seqs']}，缓存 {info['buffered_seqs']}")
    for seq in info['missing_seqs']:
        c.upload(d1['id'], [{'seq': seq, 'payload': make_payload(d1['code'], seq)}])
    info = c.device(d1['id'])
    print(f"补传后：状态 {info['status']}，确认位置 {info['confirmed_seq']}/{info['end_seq']}")
    # 第二台设备正常完成，便于演示封批
    c.declare_end(d2['id'], 2)
    c.upload(d2['id'], [{'seq': s, 'payload': make_payload(d2['code'], s)} for s in (1, 2)])
    c.seal(batch['id'])


def scenario_ack(c: StopwatchClient):
    print('\n=== 场景二：确认丢失（ACK 丢失）重发，不新增样本 ===')
    batch = c.open_batch('本地重现 · 确认丢失', ['LW-3'])
    d = batch['devices'][0]
    c.declare_end(d['id'], 4)
    c.upload(d['id'], [{'seq': s, 'payload': make_payload(d['code'], s)} for s in (1, 2, 3, 4)])
    before = c.device(d['id'])
    print(f"全部送达：库内样本 {before['record_count']}，确认位置 {before['confirmed_seq']}")
    # 秒表没收到 ACK，整段原样重发
    c.upload(d['id'], [{'seq': s, 'payload': make_payload(d['code'], s)} for s in (1, 2, 3, 4)])
    after = c.device(d['id'])
    print(f"重发后：库内样本 {after['record_count']}（应等于 {before['record_count']}），"
          f"确认位置 {after['confirmed_seq']}")
    assert after['record_count'] == before['record_count'], '重发不得新增样本'
    c.seal(batch['id'])


def scenario_conflict(c: StopwatchClient):
    print('\n=== 场景三：同号异值冲突阻断，冲突保留 ===')
    batch = c.open_batch('本地重现 · 冲突阻断', ['LW-4', 'LW-5'])
    d, other = batch['devices']
    c.declare_end(d['id'], 3)
    c.upload(d['id'], [{'seq': s, 'payload': make_payload(d['code'], s)} for s in (1, 2)])
    bad = dict(make_payload(d['code'], 1))
    bad['seconds'] = 99.9
    c.upload(d['id'], [{'seq': 1, 'payload': bad}], allow_conflict=True)
    info = c.device(d['id'])
    print(f"冲突后设备状态 {info['status']}，未决冲突 {info['open_conflict_count']} 条")
    # 阻断期间新样本被拒
    c.upload(d['id'], [{'seq': 3, 'payload': make_payload(d['code'], 3)}], allow_conflict=True)
    conflict_id = info['conflicts'][0]['id']
    status, body = c._request('POST', f'/collection/conflicts/{conflict_id}/resolve',
                              {'keep': 'existing', 'note': '本地重现：保留原表读数'})
    print(f"处理冲突（保留原值）：{status} {body.get('state')}")
    c.upload(d['id'], [{'seq': 3, 'payload': make_payload(d['code'], 3)}])
    c.declare_end(other['id'], 1)
    c.upload(other['id'], [{'seq': 1, 'payload': make_payload(other['code'], 1)}])
    c.seal(batch['id'])


def main():
    parser = argparse.ArgumentParser(description='本地秒表断网补传重现客户端')
    parser.add_argument('--scenario', choices=['gap', 'ack', 'conflict', 'all'], default='all')
    args = parser.parse_args()
    c = StopwatchClient()
    c.login()
    if args.scenario in ('gap', 'all'):
        scenario_gap(c)
    if args.scenario in ('ack', 'all'):
        scenario_ack(c)
    if args.scenario in ('conflict', 'all'):
        scenario_conflict(c)
    print('\n全部场景执行完毕。')


if __name__ == '__main__':
    main()
