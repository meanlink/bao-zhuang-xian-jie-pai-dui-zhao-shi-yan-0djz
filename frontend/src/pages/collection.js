import {collection} from '../api/entities.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {esc, notify, date} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
import {CollectionStatus, DeviceStatus, ConflictState, RecordState} from '../constants/enums.js';
import {scenarios} from '../utils/stopwatch.js';

const canWrite = () => session.user.role !== 'viewer';
const badge = (text, cls) => '<span class="coll-badge ' + cls + '">' + esc(text) + '</span>';

function statusBadge(status) {
  const map = {open: 'b-open', waiting: 'b-waiting', conflict: 'b-conflict', sealed: 'b-sealed',
    receiving: 'b-open', blocked: 'b-conflict', done: 'b-sealed'};
  const label = CollectionStatus[status] || DeviceStatus[status] || status;
  return badge(label, map[status] || 'b-open');
}

// 封批就绪：每台设备都已声明结束序号且连续确认完毕，且无未决冲突
function sealReady(b) {
  return b.open_conflict_count === 0
    && b.devices.length > 0
    && b.devices.every(d => d.end_seq != null && d.status === 'done');
}

function batchCard(b, active) {
  return '<button class="batch-item coll-batch ' + (active ? 'active' : '') + '" data-batch="' + b.id + '">'
    + '<strong>' + esc(b.name) + '</strong>'
    + '<span>' + b.device_count + ' 台设备 · 已确认 ' + b.confirmed_count + ' · 留存 ' + b.buffered_count + '</span>'
    + '<small>' + statusBadge(b.status) + (b.dataset_id ? ' · 批次 #' + b.dataset_id : '') + '</small></button>';
}

function deviceRow(d) {
  const progress = d.end_seq ? d.confirmed_seq + '/' + d.end_seq : d.confirmed_seq + '/—';
  return '<tr><td><button class="text-button" data-device="' + d.id + '"><strong>' + esc(d.code) + '</strong></button>'
    + (d.label ? '<br><small>' + esc(d.label) + '</small>' : '') + '</td>'
    + '<td>' + statusBadge(d.status) + '</td><td>' + progress + '</td>'
    + '<td>' + (d.buffered_seqs.length ? '[' + d.buffered_seqs.join(', ') + ']' : '—') + '</td>'
    + '<td>' + (d.missing_seqs.length ? '<span class="gap">' + d.missing_seqs.join(', ') + '</span>' : '—') + '</td>'
    + '<td>' + (d.open_conflict_count ? badge(d.open_conflict_count + ' 条未决', 'b-conflict') : '—') + '</td></tr>';
}

function conflictPanel(d) {
  if (!d.conflicts || !d.conflicts.length) return '';
  return '<div class="section-title"><h2>同号异值冲突</h2><span>冲突原样保留 · 仅业务负责人可处理</span></div>'
    + d.conflicts.map(c => '<article class="conflict-card ' + (c.state === 'open' ? 'open' : 'closed') + '">'
      + '<div class="conflict-head"><strong>序号 ' + c.seq + '</strong>' + badge(ConflictState[c.state] || c.state, c.state === 'open' ? 'b-conflict' : 'b-sealed')
      + '<small>' + date(c.created_at) + '</small></div>'
      + '<div class="conflict-payload"><div><p>已留存值</p><pre>' + esc(JSON.stringify(c.existing_payload, null, 2)) + '</pre></div>'
      + '<div><p>冲突上报值</p><pre>' + esc(JSON.stringify(c.incoming_payload, null, 2)) + '</pre></div></div>'
      + (c.note ? '<p class="hint">处理说明：' + esc(c.note) + '</p>' : '')
      + (c.state === 'open' && session.user.role === 'admin'
        ? '<div class="conflict-actions"><button data-resolve="' + c.id + '" data-keep="existing" class="small">保留原值并解除阻断</button>'
          + '<button data-resolve="' + c.id + '" data-keep="incoming" class="small primary">采用新值并解除阻断</button></div>'
        : '') + '</article>').join('');
}

function deviceDetail(d) {
  const columns = [{key: 'seq', label: '序号'}, {key: 'state', label: '状态'},
    {key: 'run', label: '片段'}, {key: 'arm', label: '实验组'}, {key: 'shift', label: '班次'}, {key: 'seconds', label: '节拍/秒'}];
  const rows = (d.records || []).map(r => ({seq: r.seq, state: RecordState[r.state] || r.state, ...r.payload}));
  return '<div class="section-title"><h2>设备 ' + esc(d.code) + ' · 明细（前 200 条）</h2><span>下一期待确认序号 ' + d.next_expected + '</span></div>'
    + '<div class="table-wrap"><table><thead><tr>' + columns.map(c => '<th>' + c.label + '</th>').join('') + '</tr></thead><tbody>'
    + rows.map(r => '<tr class="' + (r.state && r.state.indexOf('留存') >= 0 ? 'row-buffered' : '') + '">'
      + columns.map(c => '<td>' + esc(r[c.key] ?? '—') + '</td>').join('') + '</tr>').join('')
    + '</tbody></table></div>'
    + (d.record_count > rows.length ? '<p class="hint">仅展示前 ' + rows.length + ' 条，设备共 ' + d.record_count + ' 条。</p>' : '');
}

export async function collectionPage() {
  const batches = await collection.list();
  const first = batches[0];
  const detail = first ? await collection.get(first.id) : null;
  return heading('03 / STOPWATCH COLLECTION', '秒表断网补传与连续确认',
    '先登记设备名单，各设备从 1 连续编号并声明结束序号；乱序先留存，仅连续段落库确认。缺号可补传，同号异值阻断并保留冲突，全部结束且无冲突才封批。')
    + '<div class="dataset-grid"><section class="panel"><div class="section-title"><h2>采集批次</h2><span>' + batches.length + ' 批</span></div>'
    + '<div class="batch-list">' + (batches.length ? batches.map(b => batchCard(b, detail && b.id === detail.id)).join('') : '<div class="empty">尚无采集批次。</div>') + '</div>'
    + (canWrite() ? '<div class="section-title"><h2>登记本次采集</h2></div><form id="coll-form">'
      + '<label>采集批次名称<input name="name" required minlength="2" placeholder="例如：11 月 16 日车间秒表采集"></label>'
      + '<label>设备名单（每行一台：编号,名称，可选 :结束序号）<textarea name="devices" rows="5" placeholder="SW-01,一号秒表&#10;SW-02,二号秒表:6&#10;SW-03,三号秒表" required></textarea></label>'
      + '<button type="submit" class="primary">登记设备名单并开批</button></form>' : '<p class="hint">观察员只读。</p>')
    + '</section><section class="panel" id="coll-detail">' + (detail ? detailView(detail) : '<div class="empty">登记设备名单后开始连续采集。</div>') + '</section></div>';
}

function detailView(b) {
  const sealed = b.status === 'sealed';
  return '<div class="section-title"><h2>' + esc(b.name) + '</h2><span>' + statusBadge(b.status) + ' 建批 ' + date(b.created_at) + '</span></div>'
    + '<div class="coll-summary">'
    + [['设备数', b.device_count], ['连续确认', b.confirmed_count], ['乱序留存', b.buffered_count], ['待补缺号', b.missing_count], ['未决冲突', b.open_conflict_count]]
      .map(([k, v]) => '<div><span>' + k + '</span><strong>' + v + '</strong></div>').join('') + '</div>'
    + (b.dataset_id ? '<p class="seal-ok">已成批为唯一分析批次 <a class="text-link" href="#/datasets">数据批次 #' + b.dataset_id + ' ↗</a>，普通导入与分析照常使用。</p>' : '')
    + '<div class="table-wrap"><table><thead><tr><th>设备</th><th>状态</th><th>确认位置</th><th>留存序号</th><th>缺号</th><th>冲突</th></tr></thead><tbody>'
    + b.devices.map(deviceRow).join('') + '</tbody></table></div>'
    + (canWrite() && !sealed ? '<div class="coll-actions">'
      + '<button id="coll-seal" class="primary" ' + (sealReady(b) ? '' : 'disabled')
      + ' title="' + (sealReady(b) ? '全部设备齐套，可封批' : '全部设备声明结束且无缺号、无未决冲突后可封批') + '">校验并封批生成唯一分析批次</button>'
      + '<p class="hint">封批条件：全部设备声明结束序号、1..结束序号无缺号、无未决冲突。封批与接样并发时由行锁与条件复核保证不漏段、不重复成批。</p></div>'
      : sealed ? '<p class="hint">该批次已封批，接样与结束序号修改均被拒绝。</p>' : '')
    + '<div id="coll-conflicts">' + b.devices.map(conflictPanel).join('') + '</div>'
    + (canWrite() && !sealed ? simulatorPanel() : '')
    + '<div id="coll-device"></div>';
}

function simulatorPanel() {
  return '<div class="section-title"><h2>本地秒表端重现</h2><span>调用正式接口模拟断网、重发与冲突</span></div>'
    + '<div class="simulator"><p class="hint">每个场景自动开一个独立演示批次，可反复运行；重发样本经服务端幂等去重，冲突批次需在上方清单处理后才能封批。</p>'
    + '<div class="sim-buttons">' + scenarios.map(s => '<button class="small" data-scenario="' + s.key + '">' + esc(s.label) + '</button>').join('') + '</div>'
    + '<pre id="sim-log" class="sim-log">等待场景运行…</pre></div>';
}

export function bindCollection(refresh) {
  const qa = s => [...document.querySelectorAll(s)];
  const reloadDetail = async id => {
    const b = await collection.get(id);
    document.querySelector('#coll-detail').innerHTML = detailView(b);
    bindDetail(b.id, refresh);
  };
  qa('[data-batch]').forEach(btn => btn.onclick = () => reloadDetail(Number(btn.dataset.batch)).catch(e => notify(e.message, true)));
  const form = document.querySelector('#coll-form');
  if (form) submit(form, async f => {
    const devices = f.get('devices').split('\n').map(line => line.trim()).filter(Boolean).map(line => {
      const [head, tail] = line.split(':');
      const [code, ...labelParts] = head.split(',');
      const item = {code: code.trim(), label: labelParts.join(',').trim()};
      if (tail) item.end_seq = Number(tail.trim());
      return item;
    });
    await collection.create({name: f.get('name'), devices});
    notify('设备名单已登记，采集批次开启');
    await refresh();
  });
  const active = document.querySelector('.coll-batch.active');
  if (active) bindDetail(Number(active.dataset.batch), refresh);
}

// 详情区事件绑定（每次重渲染后调用）
function bindDetail(batchId, refresh) {
  const q = s => document.querySelector(s);
  const qa = s => [...document.querySelectorAll(s)];
  const reload = () => collection.get(batchId).then(b => {
    q('#coll-detail').innerHTML = detailView(b);
    bindDetail(batchId, refresh);
  }).catch(e => notify(e.message, true));
  qa('[data-device]').forEach(btn => btn.onclick = async () => {
    const d = await collection.device(Number(btn.dataset.device));
    q('#coll-device').innerHTML = deviceDetail(d);
    q('#coll-device').scrollIntoView({behavior: 'smooth'});
  });
  qa('[data-resolve]').forEach(btn => btn.onclick = async () => {
    try {
      await collection.resolve(Number(btn.dataset.resolve), btn.dataset.keep, '页面手工处理冲突');
      notify('冲突已处理，设备阻断解除');
      reload();
    } catch (e) { notify(e.message, true); }
  });
  const sealBtn = q('#coll-seal');
  if (sealBtn) sealBtn.onclick = async () => {
    try {
      const b = await collection.seal(batchId);
      notify('封批成功，已生成唯一分析批次 #' + b.dataset_id);
      reload();
    } catch (e) { notify(e.message, true); }
  };
  qa('[data-scenario]').forEach(btn => btn.onclick = async () => {
    const logEl = q('#sim-log');
    const lines = [];
    const log = (msg, warn = false) => { lines.push((warn ? '⚠ ' : '· ') + msg); logEl.textContent = lines.join('\n'); };
    btn.disabled = true;
    try {
      const scenario = scenarios.find(s => s.key === btn.dataset.scenario);
      log('运行场景：' + scenario.label);
      await scenario.run(log);
      log('场景结束，正在刷新批次清单…');
      await refresh();
    } catch (e) {
      log('场景中断：' + e.message, true);
      notify(e.message, true);
      refresh();
    } finally { btn.disabled = false; }
  });
}
