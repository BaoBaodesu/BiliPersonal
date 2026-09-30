// Node 的 TypeScript 转换配合假浏览器/时钟，不新增测试依赖。
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const { stripTypeScriptTypes } = require('node:module')

function browser(failFirst = false) {
  let now = 1700000000000, sequence = 0, effect, observer
  const timers = new Map(), events = new Map(), requests = [], storage = new Map()
  const base = {
    exports: {},
    setTimeout: (fn, ms) => { const id = ++sequence; timers.set(id, { fn, at: now + ms }); return id },
    clearTimeout: id => timers.delete(id),
    Date: class extends Date { static now() { return now } },
    document: { hidden: false, addEventListener: (name, fn) => events.set(name, fn), removeEventListener: name => events.delete(name) },
    window: { addEventListener: (name, fn) => events.set(name, fn) },
    sessionStorage: { setItem: (k, v) => storage.set(k, v), getItem: k => storage.get(k) },
    crypto: { randomUUID: () => `event-${++sequence}` },
    fetch: async (path, init) => { requests.push({ path, body: init.body }); return { ok: !(failFirst && requests.length === 1), status: failFirst && requests.length === 1 ? 500 : 200 } },
    IntersectionObserver: class { constructor(fn) { observer = fn } observe() {} disconnect() {} },
  }
  function load(file, require) {
    const context = vm.createContext({ ...base, exports: {}, require })
    let source = stripTypeScriptTypes(fs.readFileSync(file, 'utf8'), { mode: 'transform' })
    const names = [...source.matchAll(/export (?:class|function) (\w+)/g)].map(match => match[1])
    source = source.replace(/import (\{[^}]+\}) from (['"][^'"]+['"]);?/g, 'const $1 = require($2)')
      .replace(/export (?=class|function)/g, '')
    vm.runInContext(source + '\n' + names.map(name => `exports.${name} = ${name}`).join('\n'), context)
    return context.exports
  }
  const clock = load('src/hooks/visibilityClock.ts', () => {})
  const hook = load('src/hooks/useExposure.ts', name => name === 'react' ? {
    useRef: () => ({ current: {} }), useEffect: fn => { effect = fn() },
  } : clock)
  const tick = async ms => {
    const end = now + ms
    while ([...timers.values()].some(t => t.at <= end)) {
      const [id, timer] = [...timers.entries()].sort((a, b) => a[1].at - b[1].at)[0]
      now = timer.at; timers.delete(id); timer.fn()
      for (let i = 0; i < 6; i++) await Promise.resolve()
    }
    now = end
    for (let i = 0; i < 6; i++) await Promise.resolve()
  }
  return { hook, requests, tick, show: ratio => observer([{ intersectionRatio: ratio }]),
    hide: value => { base.document.hidden = value; events.get('visibilitychange')() }, cleanup: () => effect() }
}

async function main() {
  const video = { bvid: 'fixture', recommendation_id: 1, view_id: 'view' }
  const continuous = browser()
  continuous.hook.useExposure(video, true)
  continuous.show(.5)
  await continuous.tick(999); assert.equal(continuous.requests.length, 0)
  await continuous.tick(1); assert.equal(continuous.requests.length, 1)
  continuous.show(1); await continuous.tick(2000); assert.equal(continuous.requests.length, 1)

  const interrupted = browser()
  interrupted.hook.useExposure(video, true)
  interrupted.show(.8); await interrupted.tick(700)
  interrupted.show(.49); await interrupted.tick(700); assert.equal(interrupted.requests.length, 0)
  interrupted.show(.8); await interrupted.tick(700)
  interrupted.hide(true); await interrupted.tick(2000); assert.equal(interrupted.requests.length, 0)
  interrupted.hide(false); await interrupted.tick(999); assert.equal(interrupted.requests.length, 0)
  await interrupted.tick(1); assert.equal(interrupted.requests.length, 1)

  const fast = browser()
  fast.hook.useExposure(video, true)
  fast.show(1); await fast.tick(100)
  fast.hook.recordClick(video)
  fast.cleanup(); await fast.tick(2000)
  assert.equal(fast.requests.length, 1)
  assert.equal(fast.requests[0].path, '/api/v1/feedback')
  assert.equal(JSON.parse(fast.requests[0].body).action, 'click')
  assert.equal(JSON.parse(fast.requests[0].body).visible_at, undefined)

  const retry = browser(true)
  retry.hook.recordClick(video)
  await retry.tick(0)
  await retry.tick(3000)
  assert.equal(retry.requests.length, 2)
  assert.equal(retry.requests[0].body, retry.requests[1].body)
  assert.ok(JSON.parse(retry.requests[0].body).event_id)

  const exposureRetry = browser(true)
  exposureRetry.hook.useExposure(video, true); exposureRetry.show(1)
  await exposureRetry.tick(4000)
  assert.equal(exposureRetry.requests.length, 2)
  assert.equal(exposureRetry.requests[0].body, exposureRetry.requests[1].body)
  console.log('5 个曝光/点击计时及重试场景通过')
}
main().catch(error => { console.error(error); process.exitCode = 1 })
