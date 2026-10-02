import { useQuery } from '@tanstack/react-query'
import { Bug, X } from 'lucide-react'
import { useState } from 'react'
import { api } from '../api/client'
import { formatDateTime } from '../hooks/format'
import { useUi } from '../stores/ui'

// 调试面板：设置中开启后在右下角显示
export function DebugPanel() {
  const enabled = useUi((s) => s.debugPanel)
  const [open, setOpen] = useState(false)
  const { data } = useQuery({
    queryKey: ['system', 'debug'],
    queryFn: api.system.debug,
    enabled: enabled && open,
    refetchInterval: 3000,
  })
  if (!enabled) return null

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="fixed right-4 bottom-4 z-40 flex items-center gap-1.5 rounded-full bg-active px-3 py-2 text-xs font-medium text-on-active shadow-pop"
      >
        <Bug size={14} /> Debug
      </button>
    )
  }

  const row = (k: string, v: React.ReactNode) => (
    <div className="flex justify-between gap-4 py-0.5">
      <span className="text-muted">{k}</span>
      <span className="text-right font-mono">{v}</span>
    </div>
  )

  return (
    <div className="fixed right-4 bottom-4 z-40 max-h-[70vh] w-80 overflow-y-auto rounded-xl bg-elevated p-4 text-xs shadow-pop">
      <div className="mb-2 flex items-center justify-between">
        <span className="flex items-center gap-1.5 font-medium">
          <Bug size={14} /> Debug
        </span>
        <button aria-label="关闭" onClick={() => setOpen(false)} className="rounded-full p-1 hover:bg-surface-hover">
          <X size={16} />
        </button>
      </div>
      {!data ? (
        <div className="skeleton h-40 rounded" />
      ) : (
        <>
          {row('模型状态', `${data.model.model} / ${data.model.stage}`)}
          {row('模型版本', data.model.model_version || '-')}
          {row('训练进度', `${data.model.progress.epoch}/${data.model.progress.total}`)}
          {row('历史样本', data.model.history_samples)}
          {row('正样本', data.model.summary?.positives ?? '-')}
          {row('Tag / UP 词表', `${data.model.summary?.num_tags ?? '-'} / ${data.model.summary?.num_authors ?? '-'}`)}
          {row('候选池 hot / rcmd', `${data.pool.hot} / ${data.pool.rcmd}`)}
          {row('关注 / 旧作 / 相关', `${data.pool.follow} / ${data.pool.up_archive} / ${data.pool.related}`)}
          {row('有效候选（有已知 Tag）', data.valid_candidates)}
          {row(
            'served',
            Object.entries(data.served)
              .map(([k, v]) => `${k}:${v}`)
              .join(' ') || '0',
          )}
          {row('-352 次数', data.rate_limit_count)}
          {row(
            '限流至',
            data.rate_limited_until * 1000 > Date.now() ? formatDateTime(data.rate_limited_until) : '-',
          )}
          <div className="mt-3 mb-1 text-muted">Feed Cache（最近）</div>
          {data.recent_streams.map((s, i) => (
            <div key={i} className="font-mono">
              {formatDateTime(s.last)} {s.feed_type}/{s.category} ×{s.pages}
            </div>
          ))}
          <div className="mt-3 mb-1 text-muted">最近 API 错误</div>
          {data.recent_errors.length === 0 && <div className="text-subtle">无</div>}
          {data.recent_errors.slice(0, 8).map((e, i) => (
            <div key={i} className="font-mono break-all">
              {formatDateTime(e.time)} {e.code} {e.path}
            </div>
          ))}
        </>
      )}
    </div>
  )
}
