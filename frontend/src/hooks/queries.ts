import { useEffect } from 'react'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type InfiniteData } from '@tanstack/react-query'
import { api } from '../api/client'
import { useToast, useUi } from '../stores/ui'
import type { FeedbackAction, FeedbackReason, FeedPage, FeedType, Video } from '../types'

export const keys = {
  auth: ['auth'] as const,
  status: ['system', 'status'] as const,
  feed: (type: FeedType, category: string, viewId?: string) => ['feed', type, category, viewId] as const,
  categories: ['feed', 'categories'] as const,
  filters: ['filters'] as const,
  interests: ['interests'] as const,
  sources: ['system', 'sources'] as const,
}

export function useAuth() {
  return useQuery({ queryKey: keys.auth, queryFn: api.auth.status, staleTime: 5 * 60_000, retry: 1 })
}

export function useSystemStatus() {
  return useQuery({
    queryKey: keys.status,
    queryFn: api.system.status,
    // 训练中每 2 秒轮询一次，空闲时 30 秒一次
    refetchInterval: (q) => (q.state.data?.training || q.state.data?.model !== 'ready' ? 2000 : 30000),
  })
}

export function useSources() {
  const { data: auth } = useAuth()
  return useQuery({ queryKey: keys.sources, queryFn: api.system.sources, enabled: auth?.logged_in === true, staleTime: 30_000 })
}

export function useFeed(type: FeedType, category: string, viewId?: string) {
  const qc = useQueryClient()
  return useInfiniteQuery({
    queryKey: keys.feed(type, category, viewId),
    queryFn: ({ pageParam }) => api.feed.get(type, category, pageParam, 12,
      qc.getQueryData<InfiniteData<FeedPage>>(keys.feed(type, category, viewId))?.pages[0]?.view_id ?? viewId),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => (last.has_more ? last.next_cursor : undefined),
    staleTime: Infinity,
    retry: (count, err) => count < 2 && (err as { status?: number }).status !== 401,
  })
}

// 换一批：新建 stream，替换当前 Feed 的第一页
export function useRefreshFeed(type: FeedType, category: string, viewId?: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationKey: ['refresh-feed'],
    mutationFn: () => api.feed.refresh(type, category),
    onSuccess: (page) => {
      qc.setQueryData<InfiniteData<FeedPage, string | null>>(keys.feed(type, category, viewId), {
        pages: [page],
        pageParams: [null],
      })
      window.scrollTo({ top: 0, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' })
    },
    onError: () => useToast.getState().show('换批失败，已保留当前推荐，请稍后重试'),
  })
}

// 模型从未就绪变为就绪时，自动刷新当前 Feed（未排序 → 模型排序）
export function useAutoUpgradeFeed(type: FeedType, category: string, ranked: boolean | undefined, viewId?: string) {
  const { data: status } = useSystemStatus()
  const refresh = useRefreshFeed(type, category, viewId)
  const ready = status?.model === 'ready'
  useEffect(() => {
    if (type !== 'following' && ready && ranked === false && !refresh.isPending) refresh.mutate()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, ranked])
}

// 从所有 Feed 缓存中移除某些视频，仅在反馈成功后调用。
function removeFromFeeds(qc: ReturnType<typeof useQueryClient>, predicate: (v: Video) => boolean) {
  qc.setQueriesData<InfiniteData<FeedPage>>({ queryKey: ['feed'] }, (data) => {
    if (!data?.pages) return data
    return { ...data, pages: data.pages.map((p) => ({ ...p, items: p.items.filter((v) => !predicate(v)) })) }
  })
}

export function useFeedback() {
  const qc = useQueryClient()
  const toast = useToast((s) => s.show)
  return useMutation({
    mutationFn: ({ video, action, reason }: { video: Video; action: FeedbackAction; reason?: FeedbackReason }) =>
      api.feedback.send(video.bvid, action, video, crypto.randomUUID(), reason),
    onSuccess: (_, { video, action }) => {
      if (action === 'not_interested' || action === 'watched') {
        removeFromFeeds(qc, (v) => v.bvid === video.bvid)
      } else if (action === 'block_up') {
        removeFromFeeds(qc, (v) => v.author === video.author || (!!video.mid && v.mid === video.mid))
      }
      const messages: Partial<Record<FeedbackAction, string>> = {
        not_interested: '已减少此类推荐',
        block_up: `已屏蔽 UP：${video.author}`,
        watched: '已标记为看过',
        watch_later: '已添加到稍后再看',
        like: '已记录喜欢',
      }
      const msg = messages[action]
      if (!msg) return
      const undoable = action === 'not_interested' || action === 'block_up' || action === 'watched'
      toast(
        msg,
        undoable
          ? {
              label: '撤销',
              onClick: () =>
                api.feedback.send(video.bvid, 'undo', video).then(() => {
                  qc.invalidateQueries({ queryKey: keys.filters })
                  toast('已撤销，下一批推荐中生效')
                }).catch(() => toast('撤销失败，请稍后重试')),
            }
          : undefined,
      )
      if (action === 'block_up') qc.invalidateQueries({ queryKey: keys.filters })
    },
    onError: () => toast('操作失败，请稍后重试'),
  })
}

export function useBlockKeyword() {
  const qc = useQueryClient()
  const toast = useToast((s) => s.show)
  return useMutation({
    mutationFn: (keyword: string) => api.filters.addKeyword(keyword),
    onSuccess: (rule) => {
      const k = rule.keyword.toLowerCase()
      removeFromFeeds(qc, (v) => `${v.title} ${(v.tags || []).join(' ')}`.toLowerCase().includes(k))
      qc.invalidateQueries({ queryKey: keys.filters })
      toast(`已屏蔽关键词：${rule.keyword}`)
    },
    onError: () => toast('屏蔽关键词失败，请稍后重试'),
  })
}

export function useThemeEffect() {
  const theme = useUi((s) => s.theme)
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const apply = () =>
      document.documentElement.classList.toggle('dark', theme === 'dark' || (theme === 'system' && mq.matches))
    apply()
    mq.addEventListener('change', apply)
    return () => mq.removeEventListener('change', apply)
  }, [theme])
}

export function useOnline() {
  const query = useQuery({
    queryKey: ['online'],
    queryFn: () => navigator.onLine,
    staleTime: Infinity,
  })
  const qc = useQueryClient()
  useEffect(() => {
    const on = () => qc.setQueryData(['online'], true)
    const off = () => qc.setQueryData(['online'], false)
    window.addEventListener('online', on)
    window.addEventListener('offline', off)
    return () => {
      window.removeEventListener('online', on)
      window.removeEventListener('offline', off)
    }
  }, [qc])
  return query.data ?? true
}
