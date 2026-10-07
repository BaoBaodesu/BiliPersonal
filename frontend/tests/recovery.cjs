// 使用真实恢复 Hook 配合假 React 状态，验证可见性和响应丢失幂等。
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const { stripTypeScriptTypes } = require('node:module')
let now = 100000, visible = true, mutating = 0, sequence = 0, readiness, options
const attempt = { current: null }, calls = [], events = new Map()
const context = vm.createContext({
  document: { hidden: false, addEventListener: (name, fn) => events.set(name, fn), removeEventListener: () => {} },
  Date: class extends Date { static now() { return now } },
  crypto: { randomUUID: () => `view-${++sequence}` },
  useRef: () => attempt, useState: () => [visible, (value) => { visible = value }],
  useEffect: (fn) => fn(), useIsMutating: () => mutating,
  useQueryClient: () => ({ invalidateQueries: () => {} }),
  useQuery: (value) => { options = value; return { data: readiness, dataUpdatedAt: now } },
  useRefreshFeed: () => ({ isPending: false, mutate: (id, callbacks) => calls.push({ id, callbacks }) }),
  api: { feed: { readiness: () => {} } },
})
const source = fs.readFileSync('src/hooks/queries.ts', 'utf8').split('export function useFeedRecovery')[1].split('// 模型从未就绪')[0]
vm.runInContext(stripTypeScriptTypes('function useFeedRecovery'+source, { mode: 'transform' }), context)
const render = (empty = true, short = true) => context.useFeedRecovery('for_you', 'all', { stream_id: 'stream' }, empty, short, 'original')
readiness = { status: 'checking', version: null, available: 0 }
render(); assert.equal(calls.length, 0)
readiness = { status: 'ready', version: 'v1', available: 1 }
render(false); assert.equal(calls.length, 0); assert.equal(options.enabled, true)
render(); assert.equal(calls.length, 1)
calls[0].callbacks.onError()
render(); assert.equal(calls.length, 1)
now += 10000; render(); assert.equal(calls.length, 2); assert.equal(calls[1].id, calls[0].id)
calls[1].callbacks.onSuccess()
now += 10000; render(); assert.equal(calls.length, 2)
readiness.version = 'v2'; mutating = 1; render(); assert.equal(calls.length, 2)
mutating = 0; context.document.hidden = true; events.get('visibilitychange')()
render(); assert.equal(options.enabled, false); assert.equal(options.refetchInterval, false); assert.equal(calls.length, 2)
context.document.hidden = false; events.get('visibilitychange')()
render(); assert.equal(calls.length, 3); assert.notEqual(calls[2].id, calls[0].id)
console.log('7 个空页恢复、短页稳定、可见性和幂等场景通过')
