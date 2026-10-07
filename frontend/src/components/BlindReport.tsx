import { useRef, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { PolicyReview, ReviewGroup, ReviewReport } from '../types'
import { reviewButton } from './BlindRating'

export const reviewPercent = (value: number | null | undefined) => value == null ? '—／证据不足' : `${(value * 100).toFixed(1)}%`
const settingNames: Record<string, string> = { interest: '兴趣增强', exploration: '探索上限', archive: '旧作权重', freshness: '新鲜度', discovery: '新作者发现', third_party: '第三方关联', related: '关联视频', vertical: '主题搜索', vertical_search: '主题搜索', up_archive: '常看旧作', rcmd: 'B站推荐流', hot: '热门', follow: '关注投稿', special_follow: '特别关注', trending: '热门', recommend: '推荐流', classic: '经典模式' }
const settingValues: Record<string, string> = { off: '关闭', weak: '弱', light: '轻度', fallback: '仅补位', standard: '标准', strong: '强', conservative: '保守', balanced: '均衡', active: '积极', small: '少量', enhanced: '增强', true: '开启', false: '关闭' }
export const reviewSetting = (key: string, value: unknown) => `${settingNames[key] || key}：${settingValues[String(value)] || String(value)}`

function Metrics({ groups }: { groups: Record<string, ReviewGroup> }) {
  const a = groups.baseline, b = groups.strategy
  const rows = [['Top12 想看率', a.top12.wanted, b.top12.wanted], ['Top4 想看率', a.top4.wanted, b.top4.wanted], ['Top12 强烈排斥率', a.top12.repelled, b.top12.repelled], ['Top4 强烈排斥率', a.top4.repelled, b.top4.repelled], ['长期兴趣覆盖', a.long_coverage, b.long_coverage]] as const
  return <div className="overflow-x-auto"><table className="w-full text-left text-sm tabular-nums"><thead><tr><th className="py-3 font-medium">指标</th><th>基础规则</th><th>待评增强</th><th>差异</th></tr></thead><tbody>{rows.map(([name, base, candidate]) => <tr key={name} className="border-t border-line"><th className="py-3 pr-3 font-normal">{name}</th><td>{reviewPercent(base)}</td><td>{reviewPercent(candidate)}</td><td>{base == null || candidate == null ? '—' : `${((candidate - base) * 100).toFixed(1)}pp`}</td></tr>)}<tr className="border-t border-line"><th className="py-3 font-normal">平均评分</th><td>{a.average?.toFixed(2) ?? '—'}</td><td>{b.average?.toFixed(2) ?? '—'}</td><td>{a.average == null || b.average == null ? '—' : (b.average - a.average).toFixed(2)}</td></tr></tbody></table></div>
}

export function BlindReport({ report, experiment, changed, running }: { report: ReviewReport; experiment: PolicyReview; changed: () => void; running: boolean }) {
  const settings = useQuery({ queryKey: ['policy-settings'], queryFn: api.system.recommendationSettings })
  const sources = useQuery({ queryKey: ['system', 'sources'], queryFn: api.system.sources })
  const requestId = useRef(crypto.randomUUID())
  const [confirmed, setConfirmed] = useState(false)
  const trial = useMutation({ mutationFn: () => api.reviews.trial(experiment.id, requestId.current, confirmed), onSuccess: () => { requestId.current = crypto.randomUUID(); changed() } })
  if (!report.complete || !report.groups) return <p role="status">{report.status || '证据不足，五批提交前不揭盲'}</p>
  const differences = [
    ...Object.entries({ ...experiment.config.candidate, ...experiment.config.controls }).filter(([key, value]) => settings.data && settings.data[key as keyof typeof settings.data] !== value).map(([key, value]) => `${reviewSetting(key, settings.data?.[key as keyof typeof settings.data])} → ${settingValues[String(value)] || String(value)}`),
    ...Object.entries({ ...experiment.config.sources, classic: false }).filter(([key, value]) => sources.data && sources.data.settings[key as keyof typeof sources.data.settings] !== value).map(([key, value]) => `${reviewSetting(key, sources.data?.settings[key as keyof typeof sources.data.settings])} → ${settingValues[String(value)] || String(value)}`),
  ]
  return <section className="space-y-5"><h2 className="text-xl font-medium">离线盲评报告</h2><Metrics groups={report.groups} /><p className="text-sm text-muted">每组 Top12 为 {report.groups.baseline.top12.samples} 个排序位置，Top4 为 {report.groups.baseline.top4.samples} 个位置；累计 {report.rated_occurrences} 次视频评分，{report.unique_bvids} 个不同 BV。</p><div className="rounded-xl border border-line p-4"><h3 className="mb-3 font-medium">试用准入 · {report.passed ? '通过' : '未通过'}</h3>{report.guards?.map((guard) => <p key={guard.metric} className={`py-2 text-sm ${guard.passed ? '' : 'text-danger'}`}>{guard.passed ? '✓' : '✗'} {guard.label} · 差异 {(guard.difference * 100).toFixed(1)}pp · 要求：{guard.requirement}</p>)}</div><p className="text-sm text-muted">{report.limitations}</p>{report.batches?.map((batch) => <details key={batch.number} className="rounded-lg border border-line p-4"><summary className="cursor-pointer text-sm">第 {batch.number} 批差异 · 新视频 {batch.change.new_bvids}/{batch.change.union}</summary><Metrics groups={batch.groups} /></details>)}{report.passed && !running && <div className="space-y-3 rounded-xl border border-line p-4"><h3 className="font-medium">阶段 2：真实使用至少七天</h3><p className="text-sm text-muted">盲评通过只是准入，不代表已经证明更好。开始后下一正常批次采用已评参数，随时可恢复启动前状态。</p>{differences.length > 0 && <div className="space-y-1 text-sm"><p>以下参数会恢复为本轮快照：</p>{differences.map((value) => <p key={value}>{value}</p>)}</div>}<label className="flex min-h-11 items-center gap-2 text-sm"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />确认使用已评配置；保存当前状态以便恢复</label><button className={`${reviewButton} text-accent`} disabled={!confirmed || !settings.data || !sources.data || trial.isPending} onClick={() => trial.mutate()}>{trial.isPending ? '正在开始…' : '开始至少七天试用'}</button>{trial.isError && <p role="alert" className="text-sm text-danger">{trial.error.message}</p>}</div>}</section>
}
