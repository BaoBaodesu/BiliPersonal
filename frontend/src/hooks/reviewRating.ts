import type { BlindItem } from '../types'

// 每条视频串行保存。响应丢失时重试同一请求，多标签冲突交由用户重新读取。
export class ReviewRatingQueue {
  private tails = new Map<string, Promise<void>>()
  private versions = new Map<string, number>()
  private failures = new Map<string, { score: number; revision: number; requestId: string }>()
  pending = 0
  constructor(private send: (id: string, score: number, revision: number, requestId: string) => Promise<Pick<BlindItem, 'blind_item_id' | 'score' | 'revision'>>, private notify: () => void) {}
  save(item: BlindItem, score: number, retry = false) {
    const id = item.blind_item_id
    this.pending++
    this.notify()
    const task = (this.tails.get(id) || Promise.resolve()).then(async () => {
      if (this.failures.has(id) && !retry) throw new Error('上次评分尚未保存，请先重试或重新读取')
      const request = retry && this.failures.get(id) || { score, revision: this.versions.get(id) ?? item.revision, requestId: crypto.randomUUID() }
      try {
        const result = await this.send(id, request.score, request.revision, request.requestId)
        this.versions.set(id, result.revision)
        this.failures.delete(id)
        return result
      } catch (error) {
        this.failures.set(id, request)
        throw error
      }
    }).finally(() => { this.pending--; this.notify() })
    this.tails.set(id, task.then(() => {}, () => {}))
    return task
  }
  hasFailures() { return this.failures.size > 0 }
}

export function reviewShortcut(event: KeyboardEvent): number | 'previous' | 'next' | null {
  const target = event.target as HTMLElement | null
  if (event.repeat || event.ctrlKey || event.altKey || event.metaKey || event.shiftKey || target?.closest('input, textarea, select, [contenteditable="true"], [role="dialog"]')) return null
  if (event.key === 'ArrowLeft') return 'previous'
  if (event.key === 'ArrowRight') return 'next'
  return event.key === '-' ? -1 : /^[0-3]$/.test(event.key) ? Number(event.key) : null
}
