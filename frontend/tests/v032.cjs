// 使用现有 TypeScript CLI 产物和 React 元素测试，不安装测试依赖。
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const path = require('node:path')
const output = path.resolve('../.tmp/v032/frontend-test')
function load(file, require) {
  const exports = {}
  vm.runInNewContext(fs.readFileSync(path.join(output, file), 'utf8'), { exports, require, DOMException, crypto: { randomUUID: () => 'event' }, window: { scrollTo() {} } })
  return exports
}
function sliders(onCommit) {
  let states = [], refs = [], index = 0, refIndex = 0
  const react = {
    useId: () => 'slider', useEffect: () => {},
    useState: initial => { const key = index++; if (states[key] === undefined) states[key] = initial; return [states[key], value => { states[key] = value }] },
    useRef: initial => { const key = refIndex++; return refs[key] ||= { current: initial } },
  }
  const { DiscreteSlider } = load('components/DiscreteSlider.js', name => name === 'react' ? react : require(name))
  const render = (value = 1, disabled = false) => {
    index = refIndex = 0
    const tree = DiscreteSlider({ label: '热门榜', value, labels: ['关闭', '仅兜底', '少量1条', '标准3条'], disabled, onCommit })
    return tree.props.children[1].props.children.find(child => child.type === 'input').props
  }
  return { render }
}
const event = value => ({ target: { value: String(value) }, currentTarget: { value: String(value), setPointerCapture() {} }, pointerId: 1, preventDefault() {} })
const flush = () => new Promise(resolve => setImmediate(resolve))
async function main() {
  let commits = []
  const slider = sliders(async value => { commits.push(value) })
  let input = slider.render()
  input.onPointerDown(event(1)); input.onChange(event(3))
  assert.equal(commits.length, 0, '拖动不发送保存')
  input = slider.render(); assert.equal(input['aria-valuetext'], '标准3条')
  input.onPointerUp(event(3)); await flush(); assert.deepEqual(commits, [3])
  input = slider.render(); input.onPointerDown(event(3)); input.onChange(event(2))
  input = slider.render(); input.onKeyDown({ key: 'Escape', preventDefault() {} }); input.onPointerUp(event(2))
  await flush(); assert.equal(commits.length, 1, '取消不保存')
  input = slider.render(); input.onKeyDown({ key: 'ArrowLeft' }); input.onChange(event(0))
  input = slider.render(); input.onKeyUp({ ...event(0), key: 'ArrowLeft' }); await flush()
  assert.deepEqual(commits, [3, 0], '键盘结束提交四档值')
  const failure = sliders(async () => { throw new Error('保存失败') })
  input = failure.render(); input.onPointerDown(event(1)); input.onChange(event(3))
  input = failure.render(); input.onPointerUp(event(3)); await flush()
  assert.equal(failure.render().value, 1, '失败恢复确认值')
  let release
  const saving = sliders(value => { commits.push(value); return new Promise(resolve => { release = resolve }) })
  input = saving.render(); input.onPointerDown(event(1)); input.onChange(event(2))
  input = saving.render(); input.onPointerUp(event(2)); input.onPointerUp(event(2))
  assert.equal(saving.render().disabled, true)
  assert.deepEqual(commits, [3, 0, 2], '保存中不重复提交')
  release(); await flush()

  let infinite, mutation, page = { view_id: 'old' }, cancelled = false, get
  const qc = { getQueryData: () => ({ pages: [page] }), cancelQueries: async () => { cancelled = true }, setQueryData: (_, data) => { page = data.pages[0] } }
  const api = { feed: { get: async () => new Promise(resolve => { get = resolve }), refresh: async () => { assert.equal(cancelled, true); return { view_id: 'new' } } } }
  const hooks = load('hooks/queries.js', name => name === '@tanstack/react-query' ? {
    useQueryClient: () => qc, useInfiniteQuery: value => { infinite = value; return value }, useMutation: value => { mutation = value; return value }, useQuery: () => {},
  } : name === '../api/client' ? { api } : name === '../stores/ui' ? { useToast: { getState: () => ({ show() {} }) } } : { useEffect() {} })
  hooks.useFeed('for_you', 'all', 'view')
  const late = infinite.queryFn({ pageParam: 'old:1', signal: new AbortController().signal })
  hooks.useRefreshFeed('for_you', 'all', 'view')
  await mutation.onMutate(); mutation.onSuccess(await mutation.mutationFn())
  get({ view_id: 'old' })
  await assert.rejects(late, /批次已更换/)
  const abort = new AbortController()
  const aborted = infinite.queryFn({ pageParam: null, signal: abort.signal })
  abort.abort(); get({ view_id: 'new' })
  await assert.rejects(aborted, /已取消旧分页/)
  console.log('v0.3.2 UI: 拖动/键盘提交、取消、失败恢复、并发保存及旧 stream 竞态通过')
}
main().catch(error => { console.error(error); process.exitCode = 1 })
