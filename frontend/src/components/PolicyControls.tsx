import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useSources } from '../hooks/queries'
import { api } from '../api/client'
import type { PolicySettings } from '../types'
import { DiscreteSlider } from './DiscreteSlider'

export function PolicyControls() {
  const qc = useQueryClient()
  const sources = useSources()
  const settings = useQuery({ queryKey: ['policy-settings'], queryFn: api.system.recommendationSettings })
  const trial = useQuery({ queryKey: ['policy-trial'], queryFn: api.system.policyTrial })
  const save = useMutation({ mutationFn: api.system.setRecommendationSettings, onSuccess: (value) => { qc.setQueryData(['policy-settings'], value); qc.invalidateQueries({ queryKey: ['policy-trial'] }) } })
  const action = useMutation({ mutationFn: api.system.setPolicyTrial, onSuccess: (value) => qc.setQueryData(['policy-trial'], value) })
  const controls: { key: keyof PolicySettings; label: string; values: string[]; labels: string[] }[] = [
    { key: 'interest', label: '兴趣增强', values: ['off', 'weak', 'standard', 'strong'], labels: ['不增强', '弱', '标准', '强'] },
    { key: 'exploration', label: '探索上限', values: ['off', 'conservative', 'balanced', 'active'], labels: ['关闭', '保守1条', '均衡2条', '积极3条'] },
    { key: 'archive', label: '旧作相对权重', values: ['off', 'small', 'standard', 'enhanced'], labels: ['关闭0', '少量1', '标准3', '增强5'] },
  ]
  if (!settings.data) return <p className="text-sm text-muted">{settings.isError ? '策略设置读取失败' : '正在读取策略设置…'}</p>
  const weight = ({ off: 0, small: 1, standard: 3, enhanced: 5 } as Record<string, number>)[settings.data.archive]
  const reserved = ['rcmd', 'hot', 'vertical_search'].reduce((n, key) => Math.min(12, n + (({ small: 1, standard: 3 } as Record<string, number>)[sources.data?.settings[key as keyof typeof sources.data.settings] as string] ?? 0)), 0)
  const rest = 12 - reserved
  const allocation = [4, 4, weight].map((w) => Math.floor(rest * w / (8 + weight)))
  const remainders = [4, 4, weight].map((w, i) => ({ i, remainder: rest * w % (8 + weight) })).sort((a, b) => b.remainder - a.remainder)
  for (const entry of remainders.slice(0, rest - allocation.reduce((n, v) => n + v, 0))) allocation[entry.i]++
  return <div className="space-y-3">
    {controls.map((c) => <DiscreteSlider key={c.key} label={c.label} labels={c.labels} value={c.values.indexOf(settings.data[c.key])} disabled={save.isPending} onCommit={(index) => save.mutateAsync({ [c.key]: c.values[index] })} />)}
    <p className="text-xs text-muted">旧作是相对权重，余下名额按关注 4／相关 4／旧作权重分配。预计每12条：关注 {allocation[0]}／相关 {allocation[1]}／旧作 {allocation[2]}。来源和覆盖目标不足时会保留短页。</p>
    <p className="text-sm">策略：{trial.data?.status ?? '读取中'}{trial.data?.days != null && ` · 已试用 ${trial.data.days.toFixed(1)} 天`}</p>
    {trial.data?.requires_revalidation && <p className="text-sm text-danger">模型或核心参数已改变，新批次已停止使用旧试用策略，需要重新盲评。</p>}
    <div className="flex flex-wrap gap-3">{([['start', '启动试用'], ['keep', '保留策略'], ['stop', '关闭策略']] as const).map(([key, label]) => <button key={key} disabled={action.isPending || (key === 'keep' && (trial.data?.days ?? 0) < 7)} onClick={() => action.mutate(key)} className="min-h-11 rounded-lg border border-line px-3 text-sm disabled:opacity-50">{label}</button>)}</div>
    <p className="text-xs text-muted">默认关闭。先完成五批策略盲评，再手动试用至少七天；继续运行即延长试用。不切换模型。</p>
    {(save.isError || action.isError) && <p role="alert" className="text-sm text-danger">{(save.error || action.error)?.message}</p>}
    {trial.data?.metrics && <p className="text-xs text-muted">样本：生成 {trial.data.metrics.generated}／曝光 {trial.data.metrics.exposed} · CTR 代理指标 {trial.data.metrics.ctr_proxy == null ? '—' : `${(trial.data.metrics.ctr_proxy * 100).toFixed(1)}%`}</p>}
    {trial.data?.metrics && <div className="text-xs text-muted space-y-1">
      <p>不感兴趣率 {trial.data.metrics.not_interested_rate == null ? '—' : `${(trial.data.metrics.not_interested_rate * 100).toFixed(1)}%`} · 屏蔽率 {trial.data.metrics.blocked_rate == null ? '—' : `${(trial.data.metrics.blocked_rate * 100).toFixed(1)}%`} · 长期覆盖 {trial.data.metrics.long_coverage == null ? '—' : `${(trial.data.metrics.long_coverage * 100).toFixed(1)}%`}</p>
      <p>短页 {trial.data.metrics.short_pages}／{trial.data.metrics.pages} · 候选请求 {trial.data.metrics.candidate_attempts}，其中搜索 {trial.data.metrics.search_attempts} · 风控 {trial.data.metrics.rate_limited ? '退避中' : '未触发'}</p>
      <p>{trial.data.metrics.limitations}</p>
    </div>}
  </div>
}
