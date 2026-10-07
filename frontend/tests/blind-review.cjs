// 测试实际串行保存代码，不新增测试依赖。
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const { stripTypeScriptTypes } = require('node:module')
const context = vm.createContext({ crypto: { randomUUID: () => `request-${++sequence}` } })
let sequence = 0
const code = fs.readFileSync('src/hooks/reviewRating.ts', 'utf8').replace(/^import .*\n/gm, '').replace(/export /g, '')
vm.runInContext(stripTypeScriptTypes(code, { mode: 'transform' }) + '\nglobalThis.Queue = ReviewRatingQueue; globalThis.shortcut = reviewShortcut;', context)

async function test() {
  const item = { blind_item_id: 'one', score: null, revision: 0 }
  const calls = []
  let release, version = 0, notifications = 0
  const queue = new context.Queue(async (id, score, revision, requestId) => {
    calls.push({ id, score, revision, requestId })
    if (calls.length === 1) await new Promise((resolve) => { release = resolve })
    assert.equal(revision, version)
    return { blind_item_id: id, score, revision: ++version }
  }, () => notifications++)
  const first = queue.save(item, 2), second = queue.save(item, 3)
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(calls.length, 1); assert.equal(queue.pending, 2)
  release(); await Promise.all([first, second])
  assert.equal(calls[1].revision, 1); assert.equal(calls[1].score, 3)
  assert.equal(queue.pending, 0); assert.equal(queue.hasFailures(), false)
  assert.equal(notifications, 4)

  let failed = true
  const retries = []
  const retryQueue = new context.Queue(async (id, score, revision, requestId) => {
    retries.push({ id, score, revision, requestId })
    if (failed) throw new Error('response lost')
    return { blind_item_id: id, score, revision: 1 }
  }, () => {})
  await assert.rejects(retryQueue.save(item, -1))
  assert.equal(retryQueue.hasFailures(), true)
  await assert.rejects(retryQueue.save(item, 3), /重试/)
  failed = false
  await retryQueue.save(item, 2, true)
  assert.equal(retries[0].requestId, retries[1].requestId)
  assert.equal(retries[1].score, -1)
  assert.equal(retryQueue.hasFailures(), false)

  const shortcut = (key, extra = {}) => context.shortcut({ key, target: { closest: () => null }, ...extra })
  assert.equal(shortcut('3'), 3); assert.equal(shortcut('-'), -1)
  assert.equal(shortcut('ArrowLeft'), 'previous'); assert.equal(shortcut('ArrowRight'), 'next')
  assert.equal(shortcut('2', { repeat: true }), null)
  assert.equal(shortcut('2', { ctrlKey: true }), null)
  assert.equal(shortcut('2', { target: { closest: () => ({}) } }), null)
  assert.equal(shortcut('5'), null)
  console.log('8 个盲评保存、乱序、响应丢失、失败阻断和键盘场景通过')
}
test().catch((error) => { console.error(error); process.exitCode = 1 })
