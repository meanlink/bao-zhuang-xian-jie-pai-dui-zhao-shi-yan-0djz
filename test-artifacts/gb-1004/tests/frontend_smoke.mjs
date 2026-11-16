/**
 * 前端页面冒烟测试：用 jsdom 加载真实前端模块，对运行中的后端走完整交互。
 * 用法：node test-artifacts/gb-1004/tests/frontend_smoke.mjs
 * 依赖只装在 /tmp/fe-harness（与项目源码隔离），NODE_PATH 指向其 node_modules。
 */
const {JSDOM} = await import(process.env.JSDOM_PATH || '/tmp/fe-harness/node_modules/jsdom/lib/api.js');

const BASE = process.env.BASE_URL || 'http://localhost:18100';
const FRONTEND = process.env.FRONTEND_DIR || '/workspace/frontend';
const EMAIL = process.env.EMAIL || 'admin@demo.local';
const PASSWORD = process.env.PASSWORD || 'Demo123!';

const loginResp = await fetch(BASE + '/auth/login', {
  method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({email: EMAIL, password: PASSWORD}),
});
if (!loginResp.ok) throw new Error('后端登录失败: ' + loginResp.status);
const {access_token, user} = await loginResp.json();

const dom = new JSDOM('<!doctype html><html><body><div id="app"></div><div id="toast"></div></body></html>', {
  url: 'http://localhost:18203/#/collection', runScripts: 'outside-only', pretendToBeVisual: true,
});
const {window} = dom;
globalThis.window = window;
globalThis.document = window.document;
globalThis.localStorage = window.localStorage;
globalThis.location = window.location;
globalThis.addEventListener = window.addEventListener.bind(window);
globalThis.history = window.history;
globalThis.Event = window.Event;
globalThis.CustomEvent = window.CustomEvent;
window.HTMLElement.prototype.scrollIntoView = () => {};
// jsdom 未实现 HTMLFormElement -> FormData，按表单控件手工构造最小 FormData
class FakeFormData {
  constructor(form) {
    this._map = new Map();
    if (form) {
      for (const el of form.querySelectorAll('input, textarea, select')) {
        if (!el.name || el.disabled) continue;
        this._map.set(el.name, el.value);
      }
    }
  }
  get(k) { return this._map.has(k) ? this._map.get(k) : null; }
  set(k, v) { this._map.set(k, v); }
}
window.FormData = FakeFormData;
globalThis.FormData = FakeFormData;
const nodeFetch = globalThis.fetch.bind(globalThis);
const appFetch = (path, options = {}) => {
  const url = String(path).startsWith('http') ? String(path) : BASE + String(path).replace(/^\/api/, '');
  return nodeFetch(url, options);
};
globalThis.fetch = appFetch;
Object.defineProperty(window, 'fetch', {value: appFetch, configurable: true, writable: true});
window.localStorage.setItem('token', access_token);
window.localStorage.setItem('user', JSON.stringify(user));

const sleep = ms => new Promise(r => setTimeout(r, ms));
const waitFor = async (fn, tries = 40) => {
  for (let i = 0; i < tries; i++) {
    const v = fn();
    if (v) return v;
    await sleep(150);
  }
  throw new Error('等待元素超时');
};
let failures = 0;
const check = (cond, msg) => {
  if (!cond) { failures++; console.log('  ✗ ' + msg); }
  else console.log('  ✓ ' + msg);
};

await import('file://' + FRONTEND + '/src/main.js');
await waitFor(() => window.document.querySelector('#coll-form'));
console.log('— 采集页初始渲染 —');
check(window.document.querySelector('nav a[href="#/collection"]'), '导航出现「秒表采集」入口');
check(window.document.querySelector('#coll-form'), '显示设备名单登记表单');
check(window.document.querySelector('#coll-detail'), '渲染采集详情区');

console.log('— 登记新采集批次 —');
const name = 'jsdom 冒烟批次 ' + Date.now();
window.document.querySelector('#coll-form [name=name]').value = name;
window.document.querySelector('#coll-form [name=devices]').value = 'JS-1,一号表\nJS-2,二号表:3';
window.document.querySelector('#coll-form button[type=submit]').dispatchEvent(new window.Event('submit', {cancelable: true, bubbles: true}));
await waitFor(() => [...window.document.querySelectorAll('.coll-batch strong')].some(el => el.textContent === name));
console.log('  ✓ 新批次出现在清单并自动打开详情');

console.log('— 设备表格与确认位置 —');
const rows = [...window.document.querySelectorAll('#coll-detail tbody tr')];
check(rows.length === 2, '展示 2 台设备（实际 ' + rows.length + '）');
check(rows.some(r => r.textContent.includes('JS-1')) && rows.some(r => r.textContent.includes('JS-2')), '设备编号正确');
check(window.document.querySelector('#coll-seal[disabled]'), '未齐套时封批按钮禁用');

console.log('— 运行本地秒表「缺段补传」场景 —');
const gapBtn = [...window.document.querySelectorAll('[data-scenario]')].find(b => b.dataset.scenario === 'gap');
check(!!gapBtn, '存在缺段补传场景按钮');
gapBtn.dispatchEvent(new window.Event('click', {bubbles: true}));
await waitFor(() => {
  const log = window.document.querySelector('#sim-log');
  return log && log.textContent.includes('封批成功');
}, 60);
const logText = window.document.querySelector('#sim-log').textContent;
check(logText.includes('断网丢失'), '日志重现断网丢包');
check(logText.includes('缺号'), '日志显示缺号');
check(logText.includes('封批成功'), '补传后成功封批生成唯一分析批次');

console.log('— 运行「同号异值冲突」场景 —');
const conflictBtn = [...window.document.querySelectorAll('[data-scenario]')].find(b => b.dataset.scenario === 'conflict');
conflictBtn.dispatchEvent(new window.Event('click', {bubbles: true}));
await waitFor(() => window.document.querySelector('.conflict-card.open'), 60);
const card = window.document.querySelector('.conflict-card.open');
check(!!card, '页面列出未决冲突卡片');
check(card.textContent.includes('已留存值') && card.textContent.includes('冲突上报值'), '冲突双方原值均保留展示');
check(card.textContent.includes('99.9'), '冲突值 99.9 可见');
const resolveBtn = card.querySelector('[data-keep="existing"]');
check(!!resolveBtn, 'admin 可见「保留原值并解除阻断」操作');
resolveBtn.dispatchEvent(new window.Event('click', {bubbles: true}));
await waitFor(() => !window.document.querySelector('.conflict-card.open'));
console.log('  ✓ 处理后未决冲突清零，设备解除阻断');

console.log('— 状态查询完整性 —');
const summaryText = window.document.querySelector('#coll-detail').textContent;
check(summaryText.includes('连续确认') && summaryText.includes('待补缺号') && summaryText.includes('未决冲突'),
  '页面可查接收中/待补段/冲突/成批汇总与各设备确认位置');

console.log('— 普通导入入口仍在数据接入页 —');
window.location.hash = '#/datasets';
window.dispatchEvent(new window.Event('hashchange'));
await waitFor(() => window.document.querySelector('#dataset-form'));
check(!!window.document.querySelector('#file-input'), '普通 CSV/JSON 导入表单照常可用');

if (failures) { console.log('\n前端冒烟失败 ' + failures + ' 项'); process.exit(1); }
console.log('\n前端冒烟全部通过');
