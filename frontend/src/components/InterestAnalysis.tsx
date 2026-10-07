import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { DualProfile, InterestAnalysis as Analysis } from '../types'
import { formatDateTime } from '../hooks/format'

const rate = (v: number | null) => v == null ? '—' : `${(v * 100).toFixed(1)}%`
export function InterestAnalysis({ profile, analysis }: { profile?: DualProfile; analysis?: Analysis }) {
  const qc = useQueryClient()
  const [topicQuery, setTopicQuery] = useState('')
  const [limit, setLimit] = useState(24)
  const topics = (profile?.topics || []).filter((t) => t.name.includes(topicQuery.trim().toLocaleLowerCase())).sort((a, b) => b.L - a.L || b.S - a.S)
  const save = useMutation({ mutationFn: api.user.setInterestSettings, onSuccess: () => { qc.invalidateQueries({ queryKey: ['interests'] }); qc.invalidateQueries({ queryKey: ['interest-settings'] }) } })
  return <div className="space-y-6 md:col-span-2">
    <h2 className="font-medium">双画像 · {profile?.version || '等待后台发布'}</h2>
    <p className="text-xs text-muted">长期资格按真实日期计算；短期14天窗口，3天半衰。未知时间不参与日期资格。</p>
    <label className="block text-sm">查找主题<input className="mt-2 min-h-11 w-full rounded border border-line bg-bg px-3" value={topicQuery} onChange={(e) => { setTopicQuery(e.target.value); setLimit(24) }} /></label>
    <p className="text-xs text-muted">匹配 {topics.length} 个主题，优先显示长期与近期分数较高的主题。</p>
    {topics.slice(0, limit).map((topic) => <details key={topic.name} className="rounded-lg border border-line p-3">
      <summary className="cursor-pointer text-sm">{topic.name} · 长期 {topic.L.toFixed(2)} · 短期 {topic.S.toFixed(2)} · {topic.state} · {topic.qualified ? '自动长期资格已满足' : '自动长期资格未满足'}</summary>
      <p className="my-3 text-xs text-muted">观看 {topic.watch_bvids} BV／{topic.watch_days} 日／跨度 {topic.watch_span_days.toFixed(1)} 天；点赞收藏 {topic.explicit_bvids} BV／跨度 {topic.explicit_span_days.toFixed(1)} 天。最近活动：{formatDateTime(topic.last_activity)}。无新增行为时短期分数三天后约为当前的一半。</p>
      <select aria-label={`${topic.name} 人工状态`} className="min-h-11 rounded border border-line bg-bg px-3 text-sm" value={topic.state} disabled={save.isPending} onChange={(e) => { if (profile) save.mutate({ themes: { ...profile.config.themes, [topic.name]: { ...profile.config.themes[topic.name], state: e.target.value as 'auto' | 'fixed' | 'paused' | 'excluded' } } }) }}>
        <option value="auto">自动</option><option value="fixed">固定</option><option value="paused">暂停</option><option value="excluded">排除</option>
      </select>
      <ul className="mt-3 max-h-48 overflow-auto text-xs text-muted">{topic.evidence.map((v, index) => <li key={index}>{v.bvid} · {v.kind} · {v.weight} · {formatDateTime(v.at)}</li>)}</ul>
    </details>)}
    {topics.length > limit && <button className="min-h-11 rounded border border-line px-3 text-sm" onClick={() => setLimit(limit + 24)}>显示更多主题</button>}
    {save.isError && <p className="text-danger" role="alert">{save.error.message}</p>}
    {analysis && <>
      {analysis.diagnostics && <section className="space-y-3" aria-labelledby="recommendation-diagnostics"><h2 id="recommendation-diagnostics" className="font-medium">推荐诊断 · 最近 {analysis.days} 天</h2>
        <p className="text-sm">生成 {analysis.diagnostics.generated}／真实曝光 {analysis.diagnostics.exposed}</p>
        {Object.entries(analysis.diagnostics.repetition).map(([key, value]) => <p key={key} className="text-sm">{key === 'publisher' ? '投稿UP' : '明确UP主体'}：同页重复 {rate(value.same_page_rate)}／跨页重复 {rate(value.cross_page_rate)} · 可归因曝光 {value.attributed}</p>)}
        <div className="overflow-x-auto"><table className="w-full text-left text-xs"><caption className="py-2 text-left">新鲜度分布（推荐发生时年龄）</caption><thead><tr><th>年龄</th><th>生成</th><th>真实曝光</th></tr></thead><tbody>{analysis.diagnostics.freshness.map((value) => <tr key={value.name} className="border-t border-line"><td className="py-2">{value.name}</td><td>{value.generated}</td><td>{value.exposed}</td></tr>)}</tbody></table></div>
        <p className="text-xs text-muted">实体证据：{analysis.diagnostics.aliases.map((value) => `${({ account: '账号', title: '标题', tag: '标签' } as Record<string, string>)[value.field]} ${value.generated}／${value.exposed}`).join(' · ')}（生成／曝光，可重叠）</p>
        {analysis.diagnostics.readiness.map((value, index) => <p key={index} className="text-xs text-muted">候选准备：{value.status} · {value.at_least ? '至少' : '已验证'} {value.complete_pages} 个完整页，首个可展示页 {value.available} 条</p>)}
        <p className="text-xs text-muted">{analysis.diagnostics.limitations}</p>
      </section>}
      <h2 className="font-medium">最近 {analysis.days} 天来源表现</h2>
      <div className="overflow-x-auto"><table className="w-full text-left text-xs"><thead><tr><th>来源</th><th>生成／曝光</th><th>CTR 代理指标</th><th>不感兴趣</th><th>屏蔽</th></tr></thead><tbody>{analysis.sources.map((v) => <tr key={v.source} className="border-t border-line"><td className="py-3">{v.source}</td><td>{v.generated}／{v.exposed}</td><td>{rate(v.ctr_proxy)}</td><td>{rate(v.not_interested_rate)}</td><td>{rate(v.blocked_rate)}</td></tr>)}</tbody></table></div>
      <h2 className="font-medium">UP 状态 · 特别关注同步 {analysis.special_sync.status}</h2>
      <div className="max-h-56 overflow-auto text-sm">{analysis.ups.map((up) => <p key={up.key}>{up.name} · {up.special ? '特别关注 · ' : ''}{up.following ? '已关注 · ' : ''}{up.regular ? '常看' : up.familiar ? '熟悉' : '陌生'}{up.downrank ? ' · 降权' : ''}{up.blocked ? ' · 屏蔽' : ''} · 作者频率影响 −{up.creator_penalty.toFixed(2)}</p>)}</div>
      <h2 className="font-medium">查询贡献</h2>
      {analysis.queries.map((v) => <details key={v.query} className="rounded border border-line p-3 text-xs"><summary className="cursor-pointer">{v.query.slice(0, 24)} · 当前完整候选 {v.complete_candidates}／窗口贡献 {v.complete_contributions} · 主归因曝光 {v.exposed}</summary><p className="my-2 break-all">{v.query}</p><input aria-label={`修改查询 ${v.query}`} defaultValue={v.query} className="min-h-11 w-full rounded border border-line bg-bg px-2" disabled={save.isPending || !profile} onBlur={(e) => {
        const query = e.target.value.trim()
        if (!profile || !query || query === v.query) return
        const config = profile.config
        const changes = { queries: { ...config.queries, [v.query]: { ...config.queries[v.query], paused: true } } }
        if (v.state.special_mid) {
          const key = v.state.special_mid
          save.mutate({ ...changes, special_ups: { ...config.special_ups, [key]: { ...config.special_ups[key], queries: [...(config.special_ups[key]?.queries || []).filter((q) => q !== v.query), query] } } })
        } else if (v.state.theme) {
          const key = v.state.theme
          save.mutate({ ...changes, themes: { ...config.themes, [key]: { ...config.themes[key], queries: [...(config.themes[key]?.queries || []).filter((q) => q !== v.query), query] } } })
        }
      }} /><p>召回 {v.state.recalled || 0}／去重 {v.state.deduplicated || 0} · 辅助曝光 {v.assisted_exposures} · 主归因点击 {v.clicks}／CTR {rate(v.ctr_proxy)}{v.state.error_code != null && ` · 接口失败 ${v.state.error_code}`}</p><button className="mt-2 min-h-11 rounded border border-line px-3" disabled={save.isPending || !profile} onClick={() => profile && save.mutate({ queries: { ...profile.config.queries, [v.query]: { ...profile.config.queries[v.query], paused: !profile.config.queries[v.query]?.paused } } })}>{profile?.config.queries[v.query]?.paused ? '恢复查询' : '暂停查询'}</button></details>)}
      <p className="text-xs text-muted">{analysis.limitations}</p>
    </>}
  </div>
}
