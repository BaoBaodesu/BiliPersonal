import { useQuery } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api/client'
import type { Video } from '../types'
import { videoUrl } from '../hooks/format'

const SOURCE_LABEL: Record<string, string> = { hot: '热门列表', rcmd: 'B 站推荐流', search: '搜索' }

// “为什么推荐给我”：基于标签交集与相同 UP 的简单解释
export function ExplainDialog({ video, onClose }: { video: Video; onClose: () => void }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['explain', video.bvid],
    queryFn: () => api.feed.explain(video.bvid),
  })

  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [onClose])

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div role="dialog" aria-modal="true" className="fade-in w-full max-w-md rounded-2xl bg-elevated p-6 shadow-pop">
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-medium">为什么推荐给我</h2>
            <p className="mt-1 line-clamp-2 text-sm text-muted">{video.title}</p>
          </div>
          <button onClick={onClose} aria-label="关闭" className="rounded-full p-2 hover:bg-surface-hover">
            <X size={20} />
          </button>
        </div>
        {isLoading && <div className="skeleton h-24 rounded-lg" />}
        {isError && <p className="text-sm text-muted">暂时无法获取推荐原因。</p>}
        {data && (
          <div className="space-y-4 text-sm">
            {data.related_history.length > 0 ? (
              <div>
                <div className="mb-2 text-muted">因为你最近观看或收藏了：</div>
                <ul className="space-y-1.5">
                  {data.related_history.map((h) => (
                    <li key={h.bvid} className="flex gap-2">
                      <span className="text-subtle">·</span>
                      <a href={videoUrl(h.bvid)} target="_blank" rel="noreferrer" className="line-clamp-1 hover:underline">
                        {h.title}
                      </a>
                      {h.same_author && <span className="shrink-0 text-xs text-accent">同一 UP</span>}
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="text-muted">这条内容与你的历史记录关联较弱，属于探索推荐。</p>
            )}
            {data.matched_tags.length > 0 && (
              <div>
                <div className="mb-2 text-muted">匹配标签：</div>
                <div className="flex flex-wrap gap-2">
                  {data.matched_tags.map((t) => (
                    <span key={t} className="rounded-md bg-surface px-2 py-1 text-xs">
                      {t}
                    </span>
                  ))}
                </div>
              </div>
            )}
            <div className="border-t border-line pt-3 text-xs text-subtle">
              候选来源：{SOURCE_LABEL[data.source] || data.source || '未知'}
              {data.rcmd_reason && ` · ${data.rcmd_reason}`}
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}
