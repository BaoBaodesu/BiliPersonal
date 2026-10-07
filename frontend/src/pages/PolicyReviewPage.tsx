import { useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { DiscreteSlider } from '../components/DiscreteSlider'
import { BlindRating, reviewButton as button } from '../components/BlindRating'
import { BlindReport, reviewPercent, reviewSetting } from '../components/BlindReport'
import { usePolicyReviews, useReviewVisibility } from '../hooks/usePolicyReview'
import type { ReviewCandidate } from '../types'

const levels = [
  { key: 'interest' as const, label: '兴趣增强', values: ['off', 'weak', 'standard', 'strong'], labels: ['不增强', '弱', '标准', '强'] },
  { key: 'exploration' as const, label: '探索上限', values: ['off', 'conservative', 'balanced', 'active'], labels: ['关闭', '保守1条', '均衡2条', '积极3条'] },
  { key: 'archive' as const, label: '旧作相对权重', values: ['off', 'small', 'standard', 'enhanced'], labels: ['关闭', '少量', '标准', '增强'] },
]
const statusNames: Record<string, string> = { preparing: '准备中', batch_ready: '可评分', rating: '评分中', completed: '已完成', aborted: '已放弃', failed: '准备失败' }

export function PolicyReviewPage() {
  const qc = useQueryClient()
  const [params, setParams] = useSearchParams()
  const list = usePolicyReviews()
  const visible = useReviewVisibility()
  const settings = useQuery({ queryKey: ['policy-settings'], queryFn: api.system.recommendationSettings })
  const [draft, setDraft] = useState<ReviewCandidate | null>(null)
  const requestId = useRef(crypto.randomUUID())
  const selected = params.get('experiment') || list.data?.current?.id || list.data?.experiments[0]?.id
  const experiment = useQuery({ queryKey: ['policy-review', selected], queryFn: () => api.reviews.detail(selected!), enabled: !!selected && visible, refetchInterval: visible ? 10000 : false })
  const current = experiment.data
  const activeBatch = current?.batches.find((v) => v.status === 'ready' || v.status === 'rating')
  const [selectedBatch, setSelectedBatch] = useState<string | null>(null)
  const [confirmStop, setConfirmStop] = useState(false)
  const [abortReason, setAbortReason] = useState<string | null>(null)
  const batchId = selectedBatch || activeBatch?.id
  const batch = useQuery({ queryKey: ['policy-review-batch', selected, batchId], queryFn: () => api.reviews.batch(selected!, batchId!), enabled: !!selected && !!batchId && visible, refetchInterval: visible ? 10000 : false })
  const report = useQuery({ queryKey: ['policy-review-report', selected], queryFn: () => api.reviews.report(selected!), enabled: current?.status === 'completed' })
  const trial = useQuery({ queryKey: ['policy-trial'], queryFn: api.system.policyTrial, refetchInterval: visible ? 10000 : false })
  function changed() { for (const key of [['policy-reviews'], ['policy-review'], ['policy-review-batch'], ['policy-review-report'], ['policy-trial'], ['policy-settings'], ['system', 'sources']]) qc.invalidateQueries({ queryKey: key }) }
  const create = useMutation({ mutationFn: () => api.reviews.create(draft || { interest: settings.data!.interest, exploration: settings.data!.exploration, archive: settings.data!.archive }, requestId.current), onSuccess: (value) => { requestId.current = crypto.randomUUID(); setSelectedBatch(null); setParams({ experiment: value.id }); changed() } })
  const prepare = useMutation({ mutationFn: () => api.reviews.prepare(selected!), onSuccess: changed })
  const abort = useMutation({ mutationFn: (reason: string) => api.reviews.abort(selected!, reason), onSuccess: () => { requestId.current = crypto.randomUUID(); changed() } })
  const trialAction = useMutation({ mutationFn: api.system.setPolicyTrial, onSuccess: () => { setConfirmStop(false); changed() } })
  const candidate = draft || (settings.data ? { interest: settings.data.interest, exploration: settings.data.exploration, archive: settings.data.archive } : null)
  const canCreate = !list.data?.current && trial.data?.status !== 'running'
  return <div className="max-w-5xl space-y-6 pt-4">
    <Link className="inline-flex min-h-11 items-center text-sm text-muted" to="/settings">← 返回设置</Link>
    <header className="space-y-2"><h1 className="text-2xl font-bold">策略盲评与试用</h1><p className="text-sm text-muted">阶段 1：为冻结候选评分 → 阶段 2：真实使用至少七天。对照为基础规则，不一定是你目前已保留的增强策略。</p></header>
    {(list.isError || experiment.isError || batch.isError || report.isError) && <div role="alert" className="space-y-2 text-sm text-danger"><p>{(list.error || experiment.error || batch.error || report.error)?.message}</p><button className={button} onClick={changed}>重新读取</button></div>}
    {list.isPending && <p role="status">正在读取实验…</p>}
    {canCreate && candidate && <details open={!current} className="rounded-xl border border-line p-4"><summary className="cursor-pointer font-medium">开始新一轮五批盲评</summary><div className="mt-4 space-y-4">{levels.map((level) => <DiscreteSlider key={level.key} label={level.label} labels={level.labels} value={level.values.indexOf(candidate[level.key])} onCommit={async (index) => { setDraft({ ...candidate, [level.key]: level.values[index] }); requestId.current = crypto.randomUUID() }} />)}<p className="text-sm text-muted">这里只编辑待评草稿，不改变普通推荐。创建后锁定本轮模型、公共设置、词典及兴趣排序依据。</p><button className={`${button} text-accent`} disabled={create.isPending || list.isPending} onClick={() => create.mutate()}>{create.isPending ? '正在创建…' : '开始五批盲评'}</button>{create.isError && <p role="alert" className="text-sm text-danger">{create.error.message}</p>}</div></details>}
    {current && <><section className="space-y-3 rounded-xl border border-line p-4"><h2 className="font-medium">本轮进度 · {current.completed_batches}/5 批完成 · {statusNames[current.status]}</h2><div className="grid grid-cols-2 gap-2 sm:grid-cols-5">{Array.from({ length: 5 }, (_, i) => { const b = [...current.batches].reverse().find((v) => v.number === i + 1 && v.status !== 'invalidated'); return <button key={i} className={button} disabled={!b} onClick={() => setSelectedBatch(b?.id || null)}>第 {i + 1} 批<br /><span className="text-xs text-muted">{b?.status === 'sealed' ? '已封存 ✓' : b ? `${b.rated}/${b.total} 已评分` : i === current.completed_batches ? '等待候选' : '尚未开始'}</span></button> })}</div><details><summary className="cursor-pointer text-sm text-muted">查看本轮参数快照</summary><div className="mt-3 space-y-2 text-sm"><p>基础规则：关闭待评增强；两组共用公共控制。</p>{levels.map((level) => <p key={level.key}>{level.label}：{level.labels[level.values.indexOf(current.config.candidate[level.key])]}</p>)}<p>模型：{current.config.model_version} · 词典版本：{current.config.dictionary_version} · {current.protocol}</p><p>新鲜度／新作者／第三方：{Object.entries(current.config.controls).map(([key, value]) => reviewSetting(key, value)).join(' · ')}</p><p>来源档位：{Object.entries(current.config.sources).map(([key, value]) => reviewSetting(key, value)).join(' · ')}</p></div></details></section>
      {current.batches.some((v) => v.status === 'invalidated') && <p className="text-sm text-muted">新增屏蔽使未提交批次作废；旧草稿保留，替代批次需要重新评分。</p>}
      {['preparing', 'failed'].includes(current.status) && <section className="space-y-3 rounded-xl border border-line p-4" aria-live="polite"><h2 className="font-medium">第 {current.completed_batches + 1} 批准备中</h2><p className="text-sm">{current.preparation.reason || '后台正在检查候选；你可以继续使用首页、搜索和关注。'}</p>{current.preparation.qualified != null && <p className="text-sm text-muted">合格候选 {current.preparation.qualified} 条 · 基础组 {current.preparation.groups?.baseline ?? '—'}/12 · 待评组 {current.preparation.groups?.strategy ?? '—'}/12</p>}<div className="flex flex-wrap gap-3 text-sm text-muted">{Object.entries(current.preparation.sources || {}).map(([source, count]) => <span key={source}>{reviewSetting(source, count)}</span>)}</div><p className="text-xs text-muted">来源候选有重叠，不能相加。每批间隔至少一小时，至少50%视频本轮此前未出现；不保证准备时间。</p><button className={button} disabled={prepare.isPending} onClick={() => prepare.mutate()}>刷新状态</button></section>}
      {batch.data && <BlindRating key={batch.data.id + (batch.data.status === 'invalidated' ? ':invalidated' : '')} experiment={current.id} batch={batch.data} changed={() => { setSelectedBatch(null); changed() }} />}
      {report.data && current.status === 'completed' && <BlindReport report={report.data} experiment={current} changed={changed} running={trial.data?.status === 'running'} />}
      {current.status === 'aborted' && <p className="text-sm text-muted">本轮已放弃：{current.abort_reason}。配置、批次和评分历史保留。</p>}
      {['preparing', 'batch_ready', 'rating', 'failed'].includes(current.status) && <button className={`${button} text-danger`} disabled={abort.isPending} onClick={() => setAbortReason('')}>放弃本轮／重新开始</button>}
    </>}
    {trial.data && ['running', 'kept'].includes(trial.data.status) && <section className="space-y-3 rounded-xl border border-line p-4"><h2 className="font-medium">阶段 2 · {trial.data.status === 'kept' ? '已保留增强策略' : `真实试用 ${(trial.data.days || 0).toFixed(1)} / 7 天`}</h2><p className="text-sm text-muted">至少七个完整24小时后可保留；缺少样本不能推断收益。</p>{trial.data.metrics && <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-3"><span>曝光 {trial.data.metrics.exposed}</span><span>点击 {trial.data.metrics.clicks ?? '—'}</span><span>CTR {reviewPercent(trial.data.metrics.ctr_proxy)}</span><span>每页 Top4 CTR {reviewPercent(trial.data.metrics.top4_ctr)}</span><span>不感兴趣 {reviewPercent(trial.data.metrics.not_interested_rate)}</span><span>屏蔽 {reviewPercent(trial.data.metrics.blocked_rate)}</span><span>短页 {trial.data.metrics.short_pages}/{trial.data.metrics.pages}</span><span>候选请求 {trial.data.metrics.candidate_attempts}</span><span>风控 {trial.data.metrics.rate_limited ? '退避中' : '未触发'}</span></div>}<div className="flex flex-wrap gap-3"><button className={button} disabled={trialAction.isPending || trial.data.status !== 'running' || (trial.data.days || 0) < 7 || trial.data.requires_revalidation} onClick={() => trialAction.mutate('keep')}>保留新策略</button><button className={button} disabled={trialAction.isPending} onClick={() => setConfirmStop(true)}>恢复试用前状态</button></div>{trial.data.requires_revalidation && <p className="text-sm text-danger">配置或模型已变化，不能继续保留本次试用。</p>}</section>}
    {confirmStop && <div role="dialog" aria-label="确认恢复" className="space-y-3 rounded-xl border border-line p-4"><p className="text-sm">恢复本次试用开始前的策略和公共参数，并保留证据？</p><button autoFocus className={button} onClick={() => setConfirmStop(false)}>继续当前试用</button><button className={button} disabled={trialAction.isPending} onClick={() => trialAction.mutate('stop')}>确认恢复试用前状态</button></div>}
    {abortReason !== null && <div role="dialog" aria-label="确认放弃" className="space-y-3 rounded-xl border border-line p-4"><label className="block text-sm">取消原因<input autoFocus value={abortReason} maxLength={500} onChange={(event) => setAbortReason(event.target.value)} className="mt-2 block w-full rounded-lg border border-line bg-base p-3" /></label><p className="text-sm text-muted">放弃本轮后保留配置、批次和评分历史。</p><button className={button} onClick={() => setAbortReason(null)}>继续本轮</button><button className={button} disabled={!abortReason.trim() || abort.isPending} onClick={() => abort.mutate(abortReason, { onSuccess: () => setAbortReason(null) })}>确认放弃并保留历史</button></div>}
    {[prepare.error, abort.error, trialAction.error].filter(Boolean).map((error, i) => <p key={i} role="alert" className="text-sm text-danger">{error?.message}</p>)}
    {(list.data?.experiments.length || list.data?.legacy.length) ? <details className="border-t border-line pt-4"><summary className="cursor-pointer text-sm">实验历史</summary><div className="mt-3 space-y-2">{list.data?.experiments.map((v) => <button key={v.id} className={`${button} block`} onClick={() => { setSelectedBatch(null); setParams({ experiment: v.id }) }}>{new Date(v.created_at * 1000).toLocaleString('zh-CN')} · {v.completed_batches}/5 · {statusNames[v.status]}</button>)}{list.data?.legacy.map((v) => <p key={v.id} className="text-sm text-muted">旧协议 {v.protocol} · {v.id} · 仅保留历史，不取得新版准入</p>)}</div></details> : null}
  </div>
}
