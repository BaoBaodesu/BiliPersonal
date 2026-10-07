import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { ReviewRatingQueue, reviewShortcut } from '../hooks/reviewRating'
import { formatDuration, thumb } from '../hooks/format'
import type { BlindBatch } from '../types'

export const reviewButton = 'min-h-11 rounded-lg border border-line px-4 py-2 text-sm disabled:opacity-50 hover:bg-surface focus-visible:outline-2 focus-visible:outline-accent'
const choices = [{ score: 3, text: '非常想看' }, { score: 2, text: '想看' }, { score: 1, text: '一般' }, { score: 0, text: '不想看' }, { score: -1, text: '强烈排斥' }]

export function BlindRating({ experiment, batch, changed }: { experiment: string; batch: BlindBatch; changed: () => void }) {
  const qc = useQueryClient()
  const key = ['policy-review-batch', experiment, batch.id]
  const [index, setIndex] = useState(() => {
    const saved = Number(localStorage.getItem(`blind-position:${batch.id}`) ?? -1)
    return Number.isInteger(saved) && saved >= 0 && saved < batch.items.length ? saved : Math.max(0, batch.items.findIndex((v) => v.score === null))
  })
  const [automatic, setAutomatic] = useState(localStorage.getItem('blind-auto-next') === 'true')
  const [, render] = useState(0)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [failedImage, setFailedImage] = useState(0)
  const [reload, setReload] = useState(0)
  const [confirmSubmit, setConfirmSubmit] = useState(false)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const queue = useMemo(() => new ReviewRatingQueue((id, score, revision, requestId) => api.reviews.rate(experiment, batch.id, id, score, revision, requestId), () => { if (mounted.current) render((v) => v + 1) }), [experiment, batch.id, reload])
  const item = batch.items[index]
  const submit = useMutation({ mutationFn: () => api.reviews.submit(experiment, batch.id), onSuccess: changed })
  useEffect(() => { localStorage.setItem(`blind-position:${batch.id}`, String(index)); setFailedImage(0) }, [index, batch.id])
  function save(score: number, retry = false) {
    if (!item || !['ready', 'rating'].includes(batch.status) || submit.isPending || confirmSubmit) return
    const current = item
    setErrors((old) => ({ ...old, [current.blind_item_id]: '' }))
    queue.save(current, score, retry).then((result) => {
      qc.setQueryData<BlindBatch>(key, (old) => old && ({ ...old, items: old.items.map((v) => v.blind_item_id === result.blind_item_id ? { ...v, ...result } : v) }))
      if (mounted.current && automatic) setIndex((position) => Math.min(position + 1, batch.items.length - 1))
    }).catch((error: Error) => { if (mounted.current) setErrors((old) => ({ ...old, [current.blind_item_id]: error.message })) })
  }
  useEffect(() => {
    if (!['ready', 'rating'].includes(batch.status) || submit.isPending || confirmSubmit) return
    const handler = (event: KeyboardEvent) => {
      const action = reviewShortcut(event)
      if (action === null) return
      event.preventDefault()
      if (action === 'previous') setIndex((v) => Math.max(0, v - 1))
      else if (action === 'next') setIndex((v) => Math.min(batch.items.length - 1, v + 1))
      else save(action)
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  })
  if (!item) return <p role="status">本批因新增屏蔽作废，正在重新准备；旧评分草稿保留。</p>
  const rated = batch.items.filter((v) => v.score !== null).length
  const locked = !['ready', 'rating'].includes(batch.status)
  return <section className="space-y-4" aria-label={`第${batch.number}批评分`}>
    <h2 className="text-lg font-medium">第 {batch.number} 批 · {rated}/{batch.items.length} 已评分 {locked && '· 已封存'}</h2>
    <div className="flex flex-wrap gap-2" aria-label="视频导航">{batch.items.map((v, i) => <button key={v.blind_item_id} aria-label={`第${i + 1}条${v.score === null ? '未评分' : '已评分'}`} aria-current={i === index ? 'step' : undefined} onClick={() => setIndex(i)} className={`${reviewButton} ${i === index ? 'border-accent text-accent' : ''}`}>{i + 1}{v.score !== null && ' ✓'}</button>)}</div>
    <article className="max-w-3xl space-y-3">
      <a href={item.video.url} target="_blank" rel="noreferrer" className="flex aspect-video items-center justify-center overflow-hidden rounded-xl bg-surface" aria-label={`打开视频：${item.video.title || '标题未知'}`}>
        {item.video.pic && failedImage < 2 ? <img alt="" referrerPolicy="no-referrer" src={failedImage ? `/api/v1/img?url=${encodeURIComponent(thumb(item.video.pic, 960, 540))}` : thumb(item.video.pic, 960, 540)} onError={() => setFailedImage((v) => v + 1)} className="h-full w-full object-cover" /> : <span className="text-muted">封面暂不可用</span>}
      </a>
      <h3 className="text-xl font-medium">{item.video.title || '标题未知'}</h3>
      <p className="text-sm text-muted">{item.video.author || 'UP未知'} · 时长 {formatDuration(item.video.duration) || '未知'} · 发布 {item.video.pubdate ? new Date(item.video.pubdate * 1000).toLocaleString('zh-CN') : '未知'}</p>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">{choices.map((choice) => <button key={choice.score} disabled={locked || submit.isPending} aria-pressed={item.score === choice.score} onClick={() => save(choice.score)} className={`${reviewButton} ${item.score === choice.score ? 'border-accent bg-surface text-accent' : ''}`}>{choice.score} · {choice.text}</button>)}</div>
      <p role="status" className="min-h-6 text-sm text-muted">{queue.pending ? '保存中，请等待确认…' : errors[item.blind_item_id] ? '保存失败，本次选择尚未确认' : item.score !== null ? '已保存' : '尚未评分'}</p>
      {errors[item.blind_item_id] && <div role="alert" className="space-y-2 text-sm text-danger"><p>{errors[item.blind_item_id]}</p><button className={reviewButton} onClick={() => save(item.score ?? 2, true)}>重试保存</button><button className={reviewButton} disabled={!!queue.pending} onClick={() => { api.reviews.batch(experiment, batch.id).then((value) => { qc.setQueryData(key, value); setErrors({}); setReload((v) => v + 1) }).catch((error: Error) => setErrors((value) => ({ ...value, [item.blind_item_id]: error.message }))) }}>重新读取评分</button></div>}
    </article>
    <div className="flex items-center gap-3"><button className={reviewButton} disabled={index === 0} onClick={() => setIndex((v) => v - 1)}>← 上一条</button><span className="text-sm">{index + 1}/{batch.items.length}</span><button className={reviewButton} disabled={index === batch.items.length - 1} onClick={() => setIndex((v) => v + 1)}>下一条 →</button></div>
    {!locked && <><label className="flex min-h-11 items-center gap-2 text-sm"><input type="checkbox" checked={automatic} onChange={(event) => { setAutomatic(event.target.checked); localStorage.setItem('blind-auto-next', String(event.target.checked)) }} />保存后自动下一条（默认关闭）</label><p className="text-xs text-muted">快捷键 3／2／1／0／- 评分，←／→ 切换；提交前可修改，提交后永久锁定。</p><button className={`${reviewButton} text-accent`} disabled={rated !== batch.items.length || !!queue.pending || queue.hasFailures() || submit.isPending} onClick={() => setConfirmSubmit(true)}>{submit.isPending ? '正在提交…' : '提交并封存本批'}</button></>}
    {confirmSubmit && !locked && <div role="dialog" aria-label="确认封存" className="space-y-3 rounded-xl border border-line p-4"><p className="text-sm">提交后本批评分永久锁定，不能再修改。确认提交？</p><div className="flex gap-3"><button autoFocus className={reviewButton} disabled={submit.isPending} onClick={() => setConfirmSubmit(false)}>返回检查</button><button className={`${reviewButton} text-accent`} disabled={rated !== batch.items.length || !!queue.pending || queue.hasFailures() || submit.isPending} onClick={() => submit.mutate()}>确认永久封存</button></div></div>}
    {submit.isError && <p role="alert" className="text-sm text-danger">{submit.error.message}</p>}
  </section>
}
