import { RefreshCw, ShieldAlert, Loader2, History } from 'lucide-react'
import type { FeedPage } from '../types'
import { useSystemStatus } from '../hooks/queries'
import { timeAgo } from '../hooks/format'

interface Props {
  first?: FeedPage
  onRefresh: () => void
  refreshing: boolean
}

// Feed 顶部的状态条：缓存 / 限流 / 训练中 + 换一批
export function FeedToolbar({ first, onRefresh, refreshing }: Props) {
  const { data: status } = useSystemStatus()
  const notices: { icon: typeof History; text: string; tone?: string }[] = []

  if (status?.rate_limited || first?.notice === 'rate_limited') {
    notices.push({ icon: ShieldAlert, text: 'Bilibili 暂时限制了候选请求，正在使用缓存推荐', tone: 'text-danger' })
  }
  if (status?.training) {
    notices.push({
      icon: Loader2,
      text:
        status.stage === 'collecting'
          ? '正在读取观看历史，完成后自动更新推荐'
          : `模型训练中（${status.progress.epoch}/${status.progress.total || 30}），完成后自动更新`,
    })
  } else if (first && !first.ranked && first.items.length > 0) {
    notices.push({ icon: Loader2, text: '模型准备中，暂按热度排序' })
  }
  if (first?.from_cache && first.generated_at) {
    notices.push({ icon: History, text: `上次推荐 · ${timeAgo(first.generated_at)}` })
  }

  return (
    <div className="flex min-h-9 flex-wrap items-center justify-between gap-2">
      <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
        {notices.map((n) => (
          <span key={n.text} className={`flex items-center gap-1.5 ${n.tone || ''}`}>
            <n.icon size={14} className={n.icon === Loader2 ? 'animate-spin' : ''} />
            {n.text}
          </span>
        ))}
      </div>
      <button
        onClick={onRefresh}
        disabled={refreshing}
        title="换一批 (R)"
        className="flex h-9 items-center gap-2 rounded-full bg-surface px-4 text-sm font-medium hover:bg-surface-hover disabled:opacity-60"
      >
        <RefreshCw size={16} className={refreshing ? 'animate-spin' : ''} />
        换一批
      </button>
    </div>
  )
}
