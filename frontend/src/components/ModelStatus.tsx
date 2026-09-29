import { Brain, Loader2, AlertCircle, ShieldAlert } from 'lucide-react'
import { useSystemStatus } from '../hooks/queries'
import { timeAgo } from '../hooks/format'

// Header 中的模型状态指示
export function ModelStatus({ compact = false }: { compact?: boolean }) {
  const { data } = useSystemStatus()
  if (!data) return null

  let icon = <Brain size={18} />
  let label = '模型就绪'
  let tone = 'text-muted'
  if (data.training) {
    icon = <Loader2 size={18} className="animate-spin" />
    label =
      data.stage === 'collecting'
        ? '正在读取历史'
        : `训练中 ${data.progress.epoch}/${data.progress.total || 30}`
    tone = 'text-accent'
  } else if (data.model === 'error') {
    icon = <AlertCircle size={18} />
    label = data.error === 'login_expired' ? '登录失效' : '模型异常'
    tone = 'text-danger'
  } else if (data.model !== 'ready') {
    icon = <Loader2 size={18} className="animate-spin" />
    label = '准备模型'
    tone = 'text-muted'
  }
  if (data.rate_limited && !data.training) {
    icon = <ShieldAlert size={18} />
    label = 'B 站限流中'
    tone = 'text-danger'
  }

  const title = [
    label,
    data.model_version && `模型版本 ${data.model_version}`,
    data.trained_at && `训练于 ${timeAgo(data.trained_at)}`,
    `历史样本 ${data.history_samples}`,
    `候选池 ${data.pool_size}`,
  ]
    .filter(Boolean)
    .join('\n')

  return (
    <div
      title={title}
      aria-live="polite"
      className={`flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs whitespace-nowrap ${tone} ${
        data.training ? 'bg-surface' : ''
      }`}
    >
      {icon}
      {!compact && <span className="hidden lg:inline">{label}</span>}
    </div>
  )
}
