import { useState } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import { MousePointerClick } from 'lucide-react'
import { api } from '../api/client'
import { formatDateTime, thumb, videoUrl } from '../hooks/format'
import { LoadMoreTrigger } from '../components/LoadMoreTrigger'
import { EmptyState } from '../components/EmptyState'
import type { FeedType } from '../types'

const TYPE_LABEL: Record<FeedType, string> = { for_you: '首页', hot: '热门', explore: '探索' }
const FEEDBACK_LABEL: Record<string, string> = {
  not_interested: '不感兴趣',
  block_up: '屏蔽 UP',
  watched: '看过了',
  watch_later: '稍后再看',
  like: '喜欢',
}

// 推荐历史：用于以后对比不同推荐算法版本
export function RecommendationHistoryPage() {
  const [type, setType] = useState<string>('')
  const query = useInfiniteQuery({
    queryKey: ['rec-history', type],
    queryFn: ({ pageParam }) => api.feed.history(pageParam, type || undefined),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((n, p) => n + p.items.length, 0)
      return loaded < last.total ? loaded : undefined
    },
  })
  const items = query.data?.pages.flatMap((p) => p.items) ?? []
  const total = query.data?.pages[0]?.total ?? 0

  return (
    <div className="max-w-5xl">
      <h1 className="mb-2 pt-4 text-2xl font-bold">推荐历史</h1>
      <p className="mb-6 text-sm text-muted">共 {total} 条推荐记录，记录模型版本、评分、是否点击与反馈。</p>
      <div className="mb-4 flex gap-3">
        {[['', '全部'], ['for_you', '首页'], ['hot', '热门'], ['explore', '探索']].map(([id, name]) => (
          <button
            key={id}
            onClick={() => setType(id)}
            className={`h-8 rounded-lg px-3 text-sm font-medium ${
              type === id ? 'bg-active text-on-active' : 'bg-surface hover:bg-surface-hover'
            }`}
          >
            {name}
          </button>
        ))}
      </div>
      {query.isPending ? (
        <div className="space-y-3">
          {Array.from({ length: 6 }, (_, i) => (
            <div key={i} className="skeleton h-16 rounded-lg" />
          ))}
        </div>
      ) : !items.length ? (
        <EmptyState title="还没有推荐记录" />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-sm">
            <thead className="text-left text-xs text-muted">
              <tr className="border-b border-line">
                <th className="py-2 font-normal">视频</th>
                <th className="w-20 font-normal">Feed</th>
                <th className="w-16 font-normal">排名</th>
                <th className="w-20 font-normal">Rating</th>
                <th className="w-28 font-normal">推荐时间</th>
                <th className="w-24 font-normal">反馈</th>
              </tr>
            </thead>
            <tbody>
              {items.map((r) => (
                <tr key={r.id} className="border-b border-line hover:bg-surface">
                  <td className="py-2 pr-4">
                    <a href={videoUrl(r.bvid)} target="_blank" rel="noreferrer" className="flex items-center gap-3">
                      <img
                        src={thumb(r.pic, 160, 90)}
                        referrerPolicy="no-referrer"
                        loading="lazy"
                        alt=""
                        className="aspect-video w-24 shrink-0 rounded-md bg-skeleton object-cover"
                      />
                      <span className="min-w-0">
                        <span className="line-clamp-1 font-medium">{r.title}</span>
                        <span className="text-xs text-muted">{r.author}</span>
                      </span>
                    </a>
                  </td>
                  <td className="text-muted">{TYPE_LABEL[r.feed_type] ?? r.feed_type}</td>
                  <td className="font-mono text-muted">#{r.rank}</td>
                  <td className="font-mono">{r.rating != null ? r.rating.toFixed(4) : '-'}</td>
                  <td className="text-muted">{formatDateTime(r.served_at)}</td>
                  <td>
                    <span className="flex items-center gap-2">
                      {!!r.clicked && <MousePointerClick size={16} className="text-accent" aria-label="已点击" />}
                      {r.feedback && <span className="text-xs text-muted">{FEEDBACK_LABEL[r.feedback] ?? r.feedback}</span>}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <LoadMoreTrigger onLoad={() => !query.isFetchingNextPage && query.fetchNextPage()} disabled={!query.hasNextPage} />
        </div>
      )}
    </div>
  )
}
