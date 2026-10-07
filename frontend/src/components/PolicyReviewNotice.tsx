import { useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import { usePolicyReviews } from '../hooks/usePolicyReview'
import { useToast } from '../stores/ui'
import { useQueryClient } from '@tanstack/react-query'

export function PolicyReviewNotice() {
  const qc = useQueryClient()
  const reviews = usePolicyReviews()
  const current = reviews.data?.current
  const batch = current?.batches.find((v) => v.status === 'ready' || v.status === 'rating')
  const announced = useRef(new Set<string>())
  useEffect(() => {
    const update = () => { for (const key of [['policy-trial'], ['policy-settings'], ['system', 'sources'], ['entities']]) qc.invalidateQueries({ queryKey: key }) }
    window.addEventListener('policy-trial-changed', update)
    return () => window.removeEventListener('policy-trial-changed', update)
  }, [qc])
  useEffect(() => {
    if (batch && !announced.current.has(batch.id) && !sessionStorage.getItem(`blind-announced:${batch.id}`)) {
      announced.current.add(batch.id)
      sessionStorage.setItem(`blind-announced:${batch.id}`, 'true')
      useToast.getState().show(`第 ${batch.number} 批策略盲评已准备好，可稍后在设置中评分`)
    }
  }, [batch])
  return batch ? <Link to={`/settings/policy-review?experiment=${current?.id}`} className="mt-3 block rounded-lg border border-line bg-surface px-4 py-3 text-sm text-accent">策略盲评 · 第 {batch.number} 批可评分，已完成 {current?.completed_batches}/5 批 →</Link> : null
}
