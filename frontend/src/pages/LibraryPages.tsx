import { useState } from 'react'
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { Trash2 } from 'lucide-react'
import { api } from '../api/client'
import { VideoCard } from '../components/VideoCard'
import { GRID } from '../components/VideoGrid'
import { SkeletonCards, SkeletonGrid } from '../components/VideoSkeleton'
import { LoadMoreTrigger } from '../components/LoadMoreTrigger'
import { EmptyState } from '../components/EmptyState'
import { ErrorState } from '../components/ErrorState'
import { timeAgo } from '../hooks/format'

function PageTitle({ children }: { children: React.ReactNode }) {
  return <h1 className="mb-6 pt-4 text-2xl font-bold">{children}</h1>
}

export function WatchHistoryPage() {
  const query = useInfiniteQuery({
    queryKey: ['watch-history'],
    queryFn: ({ pageParam }) => api.user.history(pageParam),
    initialPageParam: undefined as { max: number; view_at: number } | undefined,
    getNextPageParam: (last) => (last.has_more && last.cursor.max ? last.cursor : undefined),
  })
  const items = query.data?.pages.flatMap((p) => p.items) ?? []
  return (
    <div>
      <PageTitle>观看历史</PageTitle>
      {query.isPending ? (
        <SkeletonGrid />
      ) : query.isError ? (
        <ErrorState title="观看历史加载失败" description={query.error.message} action={{ label: '重试', onClick: () => query.refetch() }} />
      ) : !items.length ? (
        <EmptyState title="还没有观看历史" />
      ) : (
        <>
          <div className={GRID}>
            {items.map((v) => (
              <VideoCard key={`${v.bvid}-${v.view_at}`} video={v} inFeed={false} meta={`${v.tname ? v.tname + ' · ' : ''}${timeAgo(v.view_at)}观看`} />
            ))}
            {query.isFetchingNextPage && <SkeletonCards count={4} />}
          </div>
          <LoadMoreTrigger onLoad={() => !query.isFetchingNextPage && query.fetchNextPage()} disabled={!query.hasNextPage} />
        </>
      )}
    </div>
  )
}

export function FavoritesPage() {
  const [folder, setFolder] = useState<number | undefined>()
  const query = useInfiniteQuery({
    queryKey: ['favorites', folder],
    queryFn: ({ pageParam }) => api.user.favorites(folder, pageParam),
    initialPageParam: 1,
    getNextPageParam: (last, pages) => (last.has_more ? pages.length + 1 : undefined),
  })
  const folders = query.data?.pages[0]?.folders ?? []
  const current = folder ?? query.data?.pages[0]?.media_id
  const items = query.data?.pages.flatMap((p) => p.items) ?? []
  return (
    <div>
      <PageTitle>收藏</PageTitle>
      {folders.length > 0 && (
        <div className="no-scrollbar mb-6 flex gap-3 overflow-x-auto">
          {folders.map((f) => (
            <button
              key={f.id}
              onClick={() => setFolder(f.id)}
              className={`h-8 shrink-0 rounded-lg px-3 text-sm font-medium ${
                f.id === current ? 'bg-active text-on-active' : 'bg-surface hover:bg-surface-hover'
              }`}
            >
              {f.title} <span className="opacity-60">{f.count}</span>
            </button>
          ))}
        </div>
      )}
      {query.isPending ? (
        <SkeletonGrid />
      ) : query.isError ? (
        <ErrorState title="收藏加载失败" description={query.error.message} action={{ label: '重试', onClick: () => query.refetch() }} />
      ) : !items.length ? (
        <EmptyState title="这个收藏夹是空的" />
      ) : (
        <>
          <div className={GRID}>
            {items.map((v) => (
              <VideoCard key={v.bvid} video={v} inFeed={false} />
            ))}
            {query.isFetchingNextPage && <SkeletonCards count={4} />}
          </div>
          <LoadMoreTrigger onLoad={() => !query.isFetchingNextPage && query.fetchNextPage()} disabled={!query.hasNextPage} />
        </>
      )}
    </div>
  )
}

export function WatchLaterPage() {
  const qc = useQueryClient()
  const { data, isPending } = useQuery({ queryKey: ['watch-later'], queryFn: api.user.watchLater })
  const remove = async (id: number) => {
    await api.feedback.remove(id)
    qc.invalidateQueries({ queryKey: ['watch-later'] })
  }
  return (
    <div>
      <PageTitle>稍后再看</PageTitle>
      <p className="-mt-4 mb-6 text-sm text-muted">保存在本地的稍后再看列表（不会同步到 B 站账号）。</p>
      {isPending ? (
        <SkeletonGrid count={4} />
      ) : !data?.items.length ? (
        <EmptyState title="列表是空的" description="在视频卡片菜单中选择“稍后再看”即可添加。" />
      ) : (
        <div className={GRID}>
          {data.items.map((v) => (
            <div key={v.bvid} className="relative">
              <VideoCard video={v} inFeed={false} menu={false} meta={`添加于 ${timeAgo(v.saved_at)}`} />
              <button
                onClick={() => remove(v.feedback_id)}
                aria-label="移除"
                className="absolute top-2 right-2 rounded-full bg-black/70 p-2 text-white hover:bg-black/90"
              >
                <Trash2 size={16} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export function NotInterestedPage() {
  const qc = useQueryClient()
  const { data, isPending } = useQuery({
    queryKey: ['feedback', 'not_interested'],
    queryFn: () => api.feedback.history('not_interested'),
  })
  const remove = async (id: number) => {
    await api.feedback.remove(id)
    qc.invalidateQueries({ queryKey: ['feedback', 'not_interested'] })
  }
  return (
    <div>
      <PageTitle>不感兴趣</PageTitle>
      <p className="-mt-4 mb-6 text-sm text-muted">这些视频不会再出现在推荐中。移除后可能重新出现。</p>
      {isPending ? (
        <SkeletonGrid count={4} />
      ) : !data?.items.length ? (
        <EmptyState title="没有标记为不感兴趣的视频" />
      ) : (
        <div className={GRID}>
          {data.items.map((r) => (
            <div key={r.id} className="relative">
              <VideoCard
                video={{ title: '', pic: '', author: '', ...r.video, bvid: r.bvid }}
                inFeed={false}
                menu={false}
                meta={`标记于 ${timeAgo(r.created_at)}`}
              />
              <button
                onClick={() => remove(r.id)}
                className="absolute top-2 right-2 rounded-full bg-black/70 px-3 py-1.5 text-xs text-white hover:bg-black/90"
              >
                移除
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
