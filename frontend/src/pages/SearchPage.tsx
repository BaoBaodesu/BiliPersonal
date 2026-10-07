import { useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useInfiniteQuery } from '@tanstack/react-query'
import { api, type ApiError } from '../api/client'
import { VideoGrid } from '../components/VideoGrid'
import { SkeletonCards, SkeletonGrid } from '../components/VideoSkeleton'
import { LoadMoreTrigger } from '../components/LoadMoreTrigger'
import { EmptyState } from '../components/EmptyState'
import { ErrorState } from '../components/ErrorState'

export function SearchPage() {
  const [params, setParams] = useSearchParams()
  const q = params.get('q') || ''
  const entityId = params.get('entity_id') || undefined
  const retryCursor = useRef<{ key: string; cursor?: string }>({ key: '' })
  const query = useInfiniteQuery({
    queryKey: ['search', q, entityId],
    queryFn: async ({ pageParam, signal }) => {
      try {
        const result = await api.search(q, typeof pageParam === 'number' && retryCursor.current.key === `${q}:${entityId}` ? retryCursor.current.cursor || pageParam : pageParam, signal, entityId)
        retryCursor.current = { key: `${q}:${entityId}` }
        return result
      } catch (error) {
        retryCursor.current = { key: `${q}:${entityId}`, cursor: (error as ApiError).retryCursor }
        throw error
      }
    },
    initialPageParam: 1 as number | string,
    getNextPageParam: (last) => last.retry_cursor || last.next_cursor || (last.page < (last.num_pages ?? 0) ? last.page + 1 : undefined),
    enabled: !!q,
    staleTime: 5 * 60_000,
    retry: false,
  })
  const items = (query.data?.pages.flatMap((p) => p.items) ?? []).filter((v, index, all) => all.findIndex((item) => item.bvid === v.bvid) === index)

  useEffect(() => {
    document.title = `${q} - 搜索 - BiliPersonal`
  }, [q])

  if (!q) return <EmptyState title="输入关键词开始搜索" description="按 / 键可以快速聚焦搜索框。" />
  if (query.isPending) return <SkeletonGrid />
  if (query.isError && !items.length) {
    const err = query.error as ApiError
    return (
      <ErrorState
        title={err.code === 'rate_limited' ? 'Bilibili 暂时限制了搜索请求' : '搜索失败'}
        description={err.message}
        action={{ label: '重试', onClick: () => query.refetch() }}
      />
    )
  }
  if (query.data?.pages[0]?.entity_choices?.length) return <section className="space-y-4 pt-4"><h1 className="text-base font-medium">选择要展开的实体</h1><p className="text-sm text-muted">一次展开一个实体，其他查询词保留。</p>{query.data.pages[0].entity_choices.map((entity) => <button key={entity.id} className="mr-3 min-h-11 rounded-lg border border-line px-4 text-sm" onClick={() => setParams({ q, entity_id: entity.id })}>{entity.name} · {{ up: 'UP主', character: '角色', work: '作品' }[entity.type]}</button>)}</section>
  const last = query.data?.pages.at(-1)

  return (
    <div className="pt-4">
      <h1 className="mb-6 text-sm text-muted">“{q}” 的搜索结果</h1>
      {query.data?.pages[0]?.entity && <div className="mb-4 space-y-2 text-sm"><p>实体：{query.data.pages[0].entity.name} · 最多三个固定查询</p>{query.data.pages[0].entity.accounts.map((account) => <a key={account.mid} className="mr-4 inline-flex min-h-11 items-center text-accent" target="_blank" rel="noreferrer" href={`https://space.bilibili.com/${account.mid}`}>{account.name || query.data!.pages[0].entity!.name}（MID {account.mid}）</a>)}</div>}
      {!items.length && <p className="py-8 text-sm text-muted">这一页没有合格结果{query.hasNextPage ? '，可继续搜索。' : '，试试其他关键词。'}</p>}
      {last?.errors?.length ? <p role="alert" className="mb-3 text-sm text-danger">部分查询失败，已保留结果。重试只补失败的查询。</p> : null}
      <VideoGrid videos={items} inFeed={false}>
        {query.isFetchingNextPage && <SkeletonCards count={4} />}
      </VideoGrid>
      <LoadMoreTrigger onLoad={() => !query.isFetchingNextPage && query.fetchNextPage()} disabled={!query.hasNextPage || query.isFetchNextPageError || !!last?.retry_cursor || !last?.items.length} />
      {(query.hasNextPage || query.isFetchNextPageError) && <button disabled={query.isFetchingNextPage} className="mt-4 min-h-11 rounded-lg border border-line px-4 text-sm" onClick={() => query.fetchNextPage()}>{query.isFetchingNextPage ? '搜索中…' : last?.retry_cursor || query.isFetchNextPageError ? '重试失败查询' : '继续搜索'}</button>}
    </div>
  )
}
