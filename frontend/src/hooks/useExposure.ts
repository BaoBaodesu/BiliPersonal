import { useEffect, useRef } from 'react'
import type { Video } from '../types'
import { VisibilityClock } from './visibilityClock'

type Event = { id: string; path: string; body: unknown; createdAt: number; attempts: number }
const key = 'bilipersonal-telemetry-v3'
const pending = new Map<string, Event>()
const visible = new Set<string>()
let sending = false
let wake: ReturnType<typeof setTimeout> | undefined

function save() {
  try { sessionStorage.setItem(key, JSON.stringify([...pending.values()])) } catch { /* 隐私模式仍保留内存重试。 */ }
}

async function flush() {
  if (sending) return
  sending = true
  for (const event of [...pending.values()]) {
    if (Date.now() - event.createdAt > 3 * 86400_000) { pending.delete(event.id); continue }
    try {
      const response = await fetch(`/api/v1/${event.path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(event.body) })
      if (response.ok || response.status >= 400 && response.status < 500 && response.status !== 429) pending.delete(event.id)
      else event.attempts++
    } catch { event.attempts++ }
  }
  sending = false
  save()
  if (pending.size && wake === undefined) {
    wake = setTimeout(() => { wake = undefined; void flush() }, Math.min(60_000, 1000 * 2 ** Math.min(6, Math.max(...[...pending.values()].map(e => e.attempts)))))
  }
}

function queue(event: Event) {
  pending.set(event.id, event)
  save()
  void flush()
}

try {
  for (const event of JSON.parse(sessionStorage.getItem(key) || '[]') as Event[]) pending.set(event.id, event)
} catch { /* 损坏的本地队列不影响浏览。 */ }
window.addEventListener('online', () => void flush())
window.addEventListener('pagehide', save)
void flush()

export function recordClick(video: Video) {
  if (!video.recommendation_id || !video.view_id) return
  const id = crypto.randomUUID()
  queue({ id, path: 'feedback', createdAt: Date.now(), attempts: 0,
    body: { bvid: video.bvid, action: 'click', event_id: id, recommendation_id: video.recommendation_id,
      view_id: video.view_id, exposure_id: `${video.view_id}:${video.recommendation_id}` } })
}

export function useExposure(video: Video, enabled: boolean) {
  const ref = useRef<HTMLElement>(null)
  useEffect(() => {
    if (!enabled || !ref.current || !video.recommendation_id || !video.view_id) return
    let ratio = 0
    const clock = new VisibilityClock(() => {
      if (document.hidden || ratio < .5) return false
      const id = `${video.view_id}:${video.recommendation_id}`
      if (visible.has(id) || pending.has(id)) return
      visible.add(id)
      queue({ id, path: 'feed/exposures', createdAt: Date.now(), attempts: 0,
        body: { items: [{ id, recommendation_id: video.recommendation_id, view_id: video.view_id,
          visible_at: Date.now() / 1000, visible_ratio: ratio, duration_ms: 1000 }] } })
    })
    const foreground = () => clock.update(ratio, !document.hidden)
    const observer = new IntersectionObserver(entries => {
      ratio = entries[0].intersectionRatio
      foreground()
    }, { threshold: [0, .5, 1] })
    observer.observe(ref.current)
    document.addEventListener('visibilitychange', foreground)
    return () => { observer.disconnect(); clock.cancel(); document.removeEventListener('visibilitychange', foreground) }
  }, [enabled, video.recommendation_id, video.view_id])
  return ref
}
