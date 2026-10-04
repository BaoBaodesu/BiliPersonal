import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { InterestSettings, InterestTheme } from '../types'

const inputClass = 'min-h-11 rounded-lg border border-line bg-bg px-3 text-sm'
export function InterestControls() {
  const qc = useQueryClient()
  const data = useQuery({ queryKey: ['interest-settings'], queryFn: api.user.interestSettings })
  const history = useQuery({ queryKey: ['search-history'], queryFn: api.user.searchHistory })
  const [name, setName] = useState('')
  const [words, setWords] = useState('')
  const [alias, setAlias] = useState('')
  const [mid, setMid] = useState('')
  const [queries, setQueries] = useState('')
  const save = useMutation({ mutationFn: api.user.setInterestSettings, onSuccess: (v) => { qc.setQueryData(['interest-settings'], v); qc.invalidateQueries({ queryKey: ['interests'] }); qc.invalidateQueries({ queryKey: ['search-history'] }) } })
  const remove = useMutation({ mutationFn: api.user.deleteSearch, onSuccess: () => qc.invalidateQueries({ queryKey: ['search-history'] }) })
  const split = (s: string) => s.split(/[,，]/).map((v) => v.trim()).filter(Boolean)
  if (!data.data) return <p className="text-sm text-muted">{data.isError ? '兴趣设置读取失败' : '正在读取兴趣设置…'}</p>
  const config = data.data
  const theme = (name: string, value: Partial<InterestTheme>) => save.mutate({ themes: { ...config.themes, [name]: { ...config.themes[name], ...value } } })
  return <div className="space-y-4 md:col-span-2">
    <h2 className="font-medium">人工兴趣控制</h2>
    <p className="text-xs text-muted">固定立即成为长期兴趣；暂停保留证据；排除禁止自动恢复。匹配词仅执行明确的标题包含匹配。</p>
    <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); if (name.trim()) theme(name.trim(), { state: 'fixed', words: split(words) }) }}>
      <input aria-label="规范主题" placeholder="规范主题" className={inputClass} value={name} onChange={(e) => setName(e.target.value)} />
      <input aria-label="标题匹配词" placeholder="标题匹配词，逗号分隔" className={inputClass} value={words} onChange={(e) => setWords(e.target.value)} />
      <button disabled={save.isPending} className={inputClass}>固定主题</button>
    </form>
    {Object.entries(config.themes).map(([key, value]) => <div key={key} className="flex flex-wrap items-center gap-2 text-sm"><span>{key}</span>
      <select aria-label={`${key} 状态`} className={inputClass} disabled={save.isPending} value={value.state || 'auto'} onChange={(e) => theme(key, { state: e.target.value as InterestTheme['state'] })}>{Object.entries({ auto: '自动', fixed: '固定', paused: '暂停', excluded: '排除' }).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
      <input aria-label={`${key} 标题匹配词`} className={inputClass} defaultValue={(value.words || []).join(',')} onBlur={(e) => { if (e.target.value !== (value.words || []).join(',')) theme(key, { words: split(e.target.value) }) }} />
      <input aria-label={`${key} 查询词`} placeholder="查询词，逗号分隔" className={inputClass} defaultValue={(value.queries || []).join(',')} onBlur={(e) => { if (e.target.value !== (value.queries || []).join(',')) theme(key, { queries: split(e.target.value) }) }} />
    </div>)}
    <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); if (alias.trim() && name.trim()) save.mutate({ aliases: { ...config.aliases, [alias.trim()]: name.trim() } }) }}><input aria-label="同义词" placeholder="同义词 → 上方规范主题" className={inputClass} value={alias} onChange={(e) => setAlias(e.target.value)} /><button disabled={save.isPending} className={inputClass}>添加明确别名</button></form>
    {Object.entries(config.aliases).map(([a, n]) => <div key={a} className="flex items-center gap-3 text-sm">{a} → {n}<button className={inputClass} disabled={save.isPending} onClick={() => save.mutate({ aliases: Object.fromEntries(Object.entries(config.aliases).filter(([key]) => key !== a)) })}>删除</button></div>)}
    <h3 className="font-medium">特别关注本地补充</h3>
    <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); if (/^\d+$/.test(mid)) save.mutate({ special_ups: { ...config.special_ups, [mid]: { enabled: true, queries: split(queries) } } }) }}><input aria-label="UP 的 MID" placeholder="UP 的 MID" className={inputClass} value={mid} onChange={(e) => setMid(e.target.value)} /><input aria-label="特别关注扩展查询词" placeholder="作品／切片查询词，逗号分隔" className={inputClass} value={queries} onChange={(e) => setQueries(e.target.value)} /><button className={inputClass} disabled={save.isPending}>添加特别关注</button></form>
    {Object.entries(config.special_ups).map(([key, value]) => <div key={key} className="flex flex-wrap items-center gap-2 text-sm">MID {key}<select className={inputClass} aria-label={`MID ${key} 特别关注`} disabled={save.isPending} value={value.enabled == null ? 'sync' : String(value.enabled)} onChange={(e) => save.mutate({ special_ups: { ...config.special_ups, [key]: { ...value, enabled: e.target.value === 'sync' ? null : e.target.value === 'true' } } })}><option value="sync">使用同步值</option><option value="true">特别关注</option><option value="false">关闭加权</option></select><input aria-label={`MID ${key} 扩展查询词`} className={inputClass} defaultValue={(value.queries || []).join(',')} onBlur={(e) => { if (e.target.value !== (value.queries || []).join(',')) save.mutate({ special_ups: { ...config.special_ups, [key]: { ...value, queries: split(e.target.value) } } }) }} /></div>)}
    <label className="flex min-h-11 items-center gap-3 text-sm"><input type="checkbox" checked={config.search_memory} disabled={save.isPending} onChange={(e) => save.mutate({ search_memory: e.target.checked })} />记忆主动搜索（默认关闭，保留30天／最多200条）</label>
    {history.data?.items.map((item) => <div key={item.event_id} className="flex items-center justify-between gap-3 text-sm"><span>{item.query}</span><button disabled={remove.isPending} className={inputClass} onClick={() => remove.mutate(item.event_id)}>删除</button></div>)}
    <label className="block text-sm">查询黑名单<input aria-label="查询黑名单" className={`${inputClass} mt-2 w-full`} defaultValue={config.query_blacklist.join(',')} onBlur={(e) => { if (e.target.value !== config.query_blacklist.join(',')) save.mutate({ query_blacklist: split(e.target.value) } as Partial<InterestSettings>) }} /></label>
    {(save.error || remove.error) && <p role="alert" className="text-sm text-danger">{(save.error || remove.error)?.message}</p>}
  </div>
}
