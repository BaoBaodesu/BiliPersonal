import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { keys, useAutoUpgradeFeed, useFeed, useRefreshFeed } from '../hooks/queries'
import type { Category, FeedType } from '../types'
import { CategoryChips } from '../components/CategoryChips'
import { FeedToolbar } from '../components/FeedToolbar'
import { VideoGrid } from '../components/VideoGrid'
import { SkeletonCards, SkeletonGrid } from '../components/VideoSkeleton'
import { LoadMoreTrigger } from '../components/LoadMoreTrigger'
import { EmptyState } from '../components/EmptyState'
import { ErrorState } from '../components/ErrorState'
import type { ApiError } from '../api/client'

const TITLES: Record<FeedType, string> = { for_you: '为你推荐', hot: '热门', explore: '兴趣探索', following: '关注' }
const DEFAULT_CATEGORIES: Category[] = [{ id: 'all', name: '全部' }]

export function FeedPage({ type }: { type: FeedType }) {
  const [category, setCategory] = useState('all')
  const viewId = useMemo(() => crypto.randomUUID(), [type, category])
  const { data: cats } = useQuery({ queryKey: keys.categories, queryFn: api.feed.categories, staleTime: 10 * 60_000, enabled: type !== 'following' })
  const feed = useFeed(type, category, viewId)
  const refresh = useRefreshFeed(type, category, viewId)
  const first = feed.data?.pages[0]
  const items = feed.data?.pages.flatMap((p) => p.items) ?? []

  useAutoUpgradeFeed(type, category, first ? first.ranked || first.items.length === 0 : undefined, viewId)

  useEffect(() => {
    document.title = `${TITLES[type]} - BiliPersonal`
  }, [type])

  // Header 刷新按钮 / 键盘 R：换一批
  useEffect(() => {
    const doRefresh = () => !refresh.isPending && refresh.mutate()
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (e.key.toLowerCase() === 'r' && !e.defaultPrevented && !e.repeat && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey && !t.isContentEditable && !t.closest('[role="dialog"], [role="menu"]') && !['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName)) {
        doRefresh()
      }
    }
    window.addEventListener('feed:refresh', doRefresh)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('feed:refresh', doRefresh)
      window.removeEventListener('keydown', onKey)
    }
  }, [refresh])

  const error = feed.error as ApiError | null
  let body: React.ReactNode
  if (feed.isPending) {
    body = <SkeletonGrid count={12} />
  } else if (error && !items.length) {
    body =
      error.code === 'rate_limited' ? (
        <ErrorState
          title="Bilibili 暂时限制了候选请求"
          description="本地还没有可用的缓存推荐，请稍等一两分钟后重试。"
          action={{ label: '重试', onClick: () => feed.refetch() }}
        />
      ) : (
        <ErrorState title="推荐加载失败" description={error.message} action={{ label: '重试', onClick: () => feed.refetch() }} />
      )
  } else if (!items.length) {
    body = (
      <EmptyState
        title={category === 'all' ? '暂时没有新的推荐' : '这个分类下暂时没有推荐'}
        description={
          category === 'all'
            ? first?.notice === 'accumulating'
              ? '个性化候选仍在积累，或已被观看、冷却和屏蔽规则过滤。稍后换一批，或在设置中调整推荐来源。'
              : type === 'following' ? '关注动态暂时没有可用的视频，稍后刷新试试。' : '候选池中的视频都已经展示过或被过滤了。稍后换一批，会重新抓取新的候选。'
            : '换个分类看看，或者稍后再来。'
        }
        action={{ label: '换一批', onClick: () => refresh.mutate() }}
      />
    )
  } else {
    body = (
      <div className={refresh.isPending ? 'pointer-events-none opacity-50 transition-opacity' : 'transition-opacity'}>
        <VideoGrid videos={items}>{feed.isFetchingNextPage && <SkeletonCards count={4} />}</VideoGrid>
        <LoadMoreTrigger onLoad={() => feed.fetchNextPage()} disabled={!feed.hasNextPage || feed.isFetchingNextPage || feed.isFetchNextPageError || refresh.isPending} />
        {feed.hasNextPage && !feed.isFetchNextPageError && (
          <div className="py-6 text-center">
            <button disabled={feed.isFetchingNextPage || refresh.isPending} onClick={() => feed.fetchNextPage()} className="h-11 rounded-full bg-surface px-5 text-sm hover:bg-surface-hover disabled:opacity-60">
              {feed.isFetchingNextPage ? '加载中…' : '加载更多'}
            </button>
          </div>
        )}
        {feed.isFetchNextPageError && (
          <div className="py-8 text-center text-sm text-muted">
            加载失败{' '}
            <button disabled={feed.isFetchingNextPage} className="min-h-11 px-3 text-accent disabled:opacity-60" onClick={() => feed.fetchNextPage()}>
              {feed.isFetchingNextPage ? '重试中…' : '重试'}
            </button>
          </div>
        )}
        {!feed.hasNextPage && (
          <div className="py-10 text-center text-sm text-muted">
            已经到底了 ·{' '}
            <button disabled={refresh.isPending} className="min-h-11 px-3 text-accent disabled:opacity-60" onClick={() => refresh.mutate()}>
              换一批
            </button>
          </div>
        )}
      </div>
    )
  }

  return (
    <div>
      <h1 className="sr-only">{TITLES[type]}</h1>
      <div className="sticky top-14 z-20 -mx-4 mb-4 bg-bg px-4 py-1.5 sm:-mx-6 sm:px-6">
        <FeedToolbar first={first} onRefresh={() => refresh.mutate()} refreshing={refresh.isPending}>
          {type === 'following' ? <span className="text-sm font-medium">关注 · 最新发布</span> : <CategoryChips categories={cats?.items ?? DEFAULT_CATEGORIES} value={category} onChange={setCategory} />}
        </FeedToolbar>
      </div>
      {refresh.isPending && (
        <div className="progress-bar fixed top-14 right-0 left-0 z-30 h-0.5 overflow-hidden" aria-hidden />
      )}
      {body}
    </div>
  )
}
