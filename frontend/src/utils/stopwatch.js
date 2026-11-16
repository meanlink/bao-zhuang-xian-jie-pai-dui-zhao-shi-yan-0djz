// 本地秒表端：模拟断网补传、缺段、重发与确认丢失。仅调用正式采集 API，不绕过任何服务端规则。
import {collection} from '../api/entities.js';

// 确定性生成一条合格业务记录（同序号同设备永远同值，便于重现）
export function makePayload(deviceCode, seq) {
  const arm = seq % 2 === 1 ? 'A' : 'B';
  const shift = seq % 2 === 0 ? '晚班' : '早班';
  const h = [...String(deviceCode + ':' + seq)].reduce((a, ch) => (a * 31 + ch.charCodeAt(0)) >>> 0, 7);
  const seconds = Math.round((15 + (h % 600) / 100) * 1000) / 1000;
  return {run: deviceCode + '-' + String(seq).padStart(3, '0'), arm, shift, seconds};
}

// 每个场景开一个独立演示批次，保证可反复重现、互不干扰
async function createDemoBatch(key, codes) {
  const stamp = new Date().toLocaleTimeString('zh-CN', {hour12: false});
  const b = await collection.create({
    name: '本地重现 · ' + key + ' · ' + stamp,
    devices: codes.map(code => ({code, label: '模拟秒表 ' + code})),
  });
  return b;
}

async function sendWithLoss(device, seq, lostSeqs, log) {
  if (lostSeqs.has(seq)) {
    log('✗ 序号 ' + seq + ' 断网丢失，秒表本地留存待补', true);
    return {lost: true};
  }
  const r = await collection.upload(device.id, [{seq, payload: makePayload(device.code, seq)}]);
  log('✓ 序号 ' + seq + ' 已发送，设备确认位置 → ' + r.confirmed_seq + '（幂等去重 ' + r.duplicates + ' 条）');
  return r;
}

// 场景一：乱序到达 + 断网缺段 + 补传
export async function scenarioGapFill(log) {
  const b = await createDemoBatch('缺段补传', ['SIM-G']);
  const device = b.devices[0];
  const end = 6;
  await collection.endSeq(device.id, end);
  log('设备 ' + device.code + ' 声明结束序号 ' + end);
  const lost = new Set([2, 5]); // 断网时丢两包
  for (const seq of [3, 1, 2, 4, 5, 6]) {
    await sendWithLoss(device, seq, lost, log);
  }
  let d = await collection.device(device.id);
  log('首轮结束：确认位置 ' + d.confirmed_seq + '，缺号 [' + d.missing_seqs.join(',') + ']，缓存 [' + d.buffered_seqs.join(',') + ']', true);
  for (const seq of [...d.missing_seqs]) {
    const r = await collection.upload(device.id, [{seq, payload: makePayload(device.code, seq)}]);
    log('补传序号 ' + seq + ' → 确认位置 ' + r.confirmed_seq);
  }
  d = await collection.device(device.id);
  log('补传完成：状态 ' + d.status + '，确认位置 ' + d.confirmed_seq + '/' + d.end_seq);
  const sealed = await collection.seal(b.id);
  log('封批成功：唯一分析批次 #' + sealed.dataset_id);
  return sealed;
}

// 场景二：确认丢失（ACK 丢失）重试，不新增样本
export async function scenarioAckLoss(log) {
  const b = await createDemoBatch('确认丢失重发', ['SIM-A']);
  const device = b.devices[0];
  await collection.endSeq(device.id, 4);
  for (let seq = 1; seq <= 4; seq++) {
    await collection.upload(device.id, [{seq, payload: makePayload(device.code, seq)}]);
  }
  const before = await collection.device(device.id);
  log('四包全部送达，确认位置 ' + before.confirmed_seq + '，库内样本 ' + before.record_count);
  // 秒表未收到 ACK，原样重发 1..4
  const r = await collection.upload(device.id, [1, 2, 3, 4].map(seq => ({seq, payload: makePayload(device.code, seq)})));
  log('ACK 丢失后整段重发：接收 ' + r.accepted + ' 条，幂等去重 ' + r.duplicates + ' 条，确认位置仍为 ' + r.confirmed_seq);
  const after = await collection.device(device.id);
  log('重发未新增样本：' + before.record_count + ' → ' + after.record_count, before.record_count === after.record_count);
  const sealed = await collection.seal(b.id);
  log('封批成功：唯一分析批次 #' + sealed.dataset_id);
  return sealed;
}

// 场景三：同号异值冲突阻断并保留（admin 可在页面冲突清单处理）
export async function scenarioConflict(log) {
  const b = await createDemoBatch('同号异值冲突', ['SIM-C']);
  const device = b.devices[0];
  await collection.endSeq(device.id, 3);
  await collection.upload(device.id, [1, 2].map(seq => ({seq, payload: makePayload(device.code, seq)})));
  const tampered = {...makePayload(device.code, 1), seconds: 99.9};
  try {
    await collection.upload(device.id, [{seq: 1, payload: tampered}]);
    log('异常：冲突未被阻断', true);
  } catch (e) {
    log('同号异值被服务端阻断：' + e.message, true);
  }
  const d = await collection.device(device.id);
  log('设备状态 ' + d.status + '，未决冲突 ' + d.open_conflict_count + ' 条；序号 3 在处理冲突前不能接收');
  try {
    await collection.upload(device.id, [{seq: 3, payload: makePayload(device.code, 3)}]);
  } catch (e) {
    log('阻断期间 seq3 上报被拒绝：' + e.message, true);
  }
  log('演示批次 #' + b.id + ' 已保留冲突原值与冲突值，请在上方冲突清单处理后再封批。');
  return b;
}

// 场景四：中断后恢复原进度（进度由服务端持久化，重新拉取即恢复）
export async function scenarioResume(log) {
  const b = await createDemoBatch('中断恢复', ['SIM-R']);
  const device = b.devices[0];
  await collection.endSeq(device.id, 5);
  // 断网期间只到了 seq2（乱序，留存在缓冲区）
  await collection.upload(device.id, [{seq: 2, payload: makePayload(device.code, 2)}]);
  log('断网前仅 seq2 到达（留存），模拟秒表掉线…');
  let d = await collection.device(device.id);
  log('重新接入：服务端恢复确认位置 ' + d.confirmed_seq + '，下一期待确认 ' + d.next_expected + '，缓存 [' + d.buffered_seqs.join(',') + ']');
  for (const seq of [1, 3, 4, 5]) {
    const r = await collection.upload(device.id, [{seq, payload: makePayload(device.code, seq)}]);
    log('续传序号 ' + seq + ' → 确认位置 ' + r.confirmed_seq);
  }
  d = await collection.device(device.id);
  log('恢复后状态 ' + d.status + '，确认位置 ' + d.confirmed_seq + '/5（从原进度继续，未重头）');
  const sealed = await collection.seal(b.id);
  log('封批成功：唯一分析批次 #' + sealed.dataset_id);
  return sealed;
}

export const scenarios = [
  {key: 'gap', label: '缺段与补传（含断网丢包）', run: scenarioGapFill},
  {key: 'ack', label: '确认丢失重发（幂等不新增）', run: scenarioAckLoss},
  {key: 'conflict', label: '同号异值冲突阻断', run: scenarioConflict},
  {key: 'resume', label: '中断恢复原进度', run: scenarioResume},
];
