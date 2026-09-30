import { useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useInfiniteQuery } from '@tanstack/react-query'
import { api, type ApiError } from '../api/client'
import { VideoGrid } from '../components/VideoGrid'
import { SkeletonCards, SkeletonGrid } from '../components/VideoSkeleton'
import { LoadMoreTrigger } from '../components/LoadMoreTrigger'
import { EmptyState } from '../components/EmptyState'
import { ErrorState } from '../components/ErrorState'

export function SearchPage() {
  const [params] = useSearchParams()
  const q = params.get('q') || ''
  const query = useInfiniteQuery({
    queryKey: ['search', q],
    queryFn: ({ pageParam }) => api.search(q, pageParam),
    initialPageParam: 1,
    getNextPageParam: (last) => (last.page < last.num_pages ? last.page + 1 : undefined),
    enabled: !!q,
    staleTime: 5 * 60_000,
  })
  const items = query.data?.pages.flatMap((p) => p.items) ?? []

  useEffect(() => {
    document.title = `${q} - 搜索 - BiliPersonal`
  }, [q])

  if (!q) return <EmptyState title="输入关键词开始搜索" description="按 / 键可以快速聚焦搜索框。" />
  if (query.isPending) return <SkeletonGrid />
  if (query.isError) {
    const err = query.error as ApiError
    return (
      <ErrorState
        title={err.code === 'rate_limited' ? 'Bilibili 暂时限制了搜索请求' : '搜索失败'}
        description={err.message}
        action={{ label: '重试', onClick: () => query.refetch() }}
      />
    )
  }
  if (!items.length) return <EmptyState title={`没有找到“${q}”相关的视频`} description="试试其他关键词。" />

  return (
    <div className="pt-4">
      <h1 className="mb-6 text-sm text-muted">“{q}” 的搜索结果</h1>
      <VideoGrid videos={items} inFeed={false}>
        {query.isFetchingNextPage && <SkeletonCards count={4} />}
      </VideoGrid>
      <LoadMoreTrigger onLoad={() => !query.isFetchingNextPage && query.fetchNextPage()} disabled={!query.hasNextPage} />
    </div>
  )
}
