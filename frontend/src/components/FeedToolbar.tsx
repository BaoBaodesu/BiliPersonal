import type { ReactNode } from 'react'
import { RefreshCw, ShieldAlert, Loader2, AlertCircle } from 'lucide-react'
import type { FeedPage } from '../types'
import { useSystemStatus } from '../hooks/queries'

interface Props {
  first?: FeedPage
  onRefresh: () => void
  refreshing: boolean
  children?: ReactNode
}

// 分类与换批共用一行，只有需要关注时才展示状态。
export function FeedToolbar({ first, onRefresh, refreshing, children }: Props) {
  const { data: status } = useSystemStatus()
  const notices: { icon: typeof AlertCircle; text: string; tone?: string }[] = []

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
  } else if (status?.model === 'error') {
    notices.push({ icon: AlertCircle, text: '模型暂时不可用，可在设置中查看状态或重试训练', tone: 'text-danger' })
  } else if (first && !first.ranked && first.items.length > 0) {
    notices.push({ icon: Loader2, text: '模型准备中，暂按热度排序' })
  }

  return (
    <div>
      <div className="flex min-h-11 items-center gap-3">
        <div className="min-w-0 flex-1">{children}</div>
        <button
          onClick={onRefresh}
          disabled={refreshing}
          aria-busy={refreshing}
          title="换一批 (R)"
          className="flex h-11 shrink-0 items-center gap-2 rounded-full bg-surface px-3 text-sm font-medium hover:bg-surface-hover disabled:opacity-60 sm:px-4"
        >
          <RefreshCw size={16} className={refreshing ? 'animate-spin' : ''} />
          换一批
        </button>
      </div>
      {notices.length > 0 && (
        <div role="status" className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1 pt-2 text-xs text-muted">
          {notices.map((n) => (
            <span key={n.text} className={`flex items-center gap-1.5 ${n.tone || ''}`}>
              <n.icon size={14} className={n.icon === Loader2 ? 'animate-spin' : ''} />
              {n.text}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
