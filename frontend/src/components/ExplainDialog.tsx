import { useQuery } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { useRef, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api/client'
import type { Video } from '../types'
import { videoUrl } from '../hooks/format'
import { useDialogFocus } from '../hooks/useDialogFocus'
import { useUi } from '../stores/ui'

const SOURCE_LABEL: Record<string, string> = { hot: '热门列表', rcmd: 'B 站推荐流', search: '搜索', follow: '关注新作', up_archive: '常看 UP 旧作', related: '相关视频' }

// “为什么推荐给我”：基于标签交集与相同 UP 的简单解释
export function ExplainDialog({ video, onClose, returnFocus }: { video: Video; onClose: () => void; returnFocus?: RefObject<HTMLElement | null> }) {
  const dialog = useRef<HTMLDivElement>(null)
  const showRating = useUi((s) => s.showRating)
  useDialogFocus(dialog, true, onClose, returnFocus)
  const { data, isLoading, isError } = useQuery({
    queryKey: ['explain', video.bvid, video.recommendation_id],
    queryFn: () => api.feed.explain(video.bvid, video.recommendation_id),
  })

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div ref={dialog} role="dialog" aria-modal="true" aria-labelledby="explain-title" tabIndex={-1} className="fade-in max-h-[calc(100dvh-32px)] w-full max-w-md overflow-y-auto rounded-2xl bg-elevated p-6 shadow-pop">
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h2 id="explain-title" className="text-lg font-medium">为什么推荐给我</h2>
            <p className="mt-1 line-clamp-2 text-sm text-muted">{video.title}</p>
          </div>
          <button onClick={onClose} aria-label="关闭" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full hover:bg-surface-hover">
            <X size={20} />
          </button>
        </div>
        {isLoading && <div className="skeleton h-24 rounded-lg" />}
        {isError && <p className="text-sm text-muted">暂时无法获取推荐原因。</p>}
        {data && (
          <div className="space-y-4 text-sm">
            {data.historical ? <div className="space-y-2 text-muted"><p>按推荐生成时的快照解释；画像和参数之后的修改不会改写这条记录。</p>{data.policy?.profile_version ? <><p>主主题：{data.policy.primary_theme || '无'} · 长期 {(data.policy.L || 0).toFixed(2)}／短期 {(data.policy.S || 0).toFixed(2)}</p><p>画像版本：{data.policy.profile_version} · 策略：{data.policy.version}</p><p>规则分：{data.policy.rule_score == null ? '未评分' : data.policy.rule_score.toFixed(3)}（不是概率） · 作者频率惩罚 {(data.policy.creator_penalty || 0).toFixed(2)}</p>{data.policy.primary_query && <p className="break-all">主查询：{data.policy.primary_query}</p>}</> : <p>这条记录没有双画像证据快照，不能补造历史兴趣解释。</p>}</div> : data.related_history.length > 0 ? (
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
            {showRating && (
              <div className="space-y-2 border-t border-line pt-3 text-xs text-muted">
                <p>{video.source_reason || data.source_reason}</p>
                {(video.seed_bvid || data.seed_video?.bvid) && <p>种子视频：<a className="text-accent hover:underline" href={videoUrl(video.seed_bvid || data.seed_video!.bvid)} target="_blank" rel="noreferrer">{video.seed_title || data.seed_video?.title || video.seed_bvid}</a></p>}
                {data.up_affinity && <p>UP：{data.up_affinity.level === 'regular' ? '常看' : data.up_affinity.level === 'familiar' ? '熟悉' : '陌生'} · 看过至少一半 {data.up_affinity.watched_count} 个视频{data.up_affinity.following ? ' · 已关注' : ''}</p>}
              </div>
            )}
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}
