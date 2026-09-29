import { Link, useSearchParams } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Monitor, Moon, Sun, RotateCw } from 'lucide-react'
import { api } from '../api/client'
import { FilterEditor, type FilterTab } from '../components/FilterEditor'
import { keys, useSystemStatus } from '../hooks/queries'
import { formatDateTime } from '../hooks/format'
import { useUi, type Theme } from '../stores/ui'

export function FiltersPage() {
  const [params, setParams] = useSearchParams()
  const raw = params.get('tab')
  const tab: FilterTab = raw === 'ups' ? 'ups' : raw === 'uploader' ? 'uploader' : 'title'
  const tabs: [FilterTab, string][] = [
    ['title', '标题关键词'],
    ['uploader', 'UP 主关键词'],
    ['ups', '精确屏蔽 UP'],
  ]
  return (
    <div className="max-w-2xl">
      <h1 className="mb-6 pt-4 text-2xl font-bold">过滤规则</h1>
      <div className="mb-6 flex gap-6 border-b border-line" role="tablist">
        {tabs.map(([id, name]) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            onClick={() => setParams({ tab: id })}
            className={`-mb-px border-b-2 pb-3 text-sm font-medium ${
              tab === id ? 'border-fg text-fg' : 'border-transparent text-muted hover:text-fg'
            }`}
          >
            {name}
          </button>
        ))}
      </div>
      <FilterEditor tab={tab} />
      <p className="mt-8 text-xs text-muted">
        过滤在推荐排序之前执行（精确屏蔽 UP → UP 关键词 → 标题关键词），同样作用于搜索结果。
        规则变更会立即让已生成的 Feed 缓存失效。AI 内容过滤（LLM）将在后续版本作为可选模块加入。
      </p>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-b border-line py-6 last:border-0">
      <h2 className="mb-4 font-medium">{title}</h2>
      {children}
    </section>
  )
}

function Toggle({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return (
    <label className="flex cursor-pointer items-center justify-between gap-6 py-2">
      <span>
        <span className="text-sm">{label}</span>
        {hint && <span className="block text-xs text-muted">{hint}</span>}
      </span>
      <span className="relative inline-flex shrink-0 items-center">
        <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="peer sr-only" />
        <span className="h-5 w-9 rounded-full bg-surface-hover transition peer-checked:bg-accent" />
        <span className="absolute left-0.5 h-4 w-4 rounded-full bg-white transition peer-checked:translate-x-4" />
      </span>
    </label>
  )
}

export function SettingsPage() {
  const ui = useUi()
  const qc = useQueryClient()
  const { data: status } = useSystemStatus()
  const retrain = useMutation({
    mutationFn: api.system.retrain,
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.status }),
  })
  const themes: { id: Theme; label: string; icon: typeof Sun }[] = [
    { id: 'system', label: '跟随系统', icon: Monitor },
    { id: 'light', label: '浅色', icon: Sun },
    { id: 'dark', label: '深色', icon: Moon },
  ]
  const m = status?.summary?.final_metrics

  return (
    <div className="max-w-2xl">
      <h1 className="pt-4 text-2xl font-bold">设置</h1>

      <Section title="外观">
        <div className="flex gap-3">
          {themes.map((t) => (
            <button
              key={t.id}
              onClick={() => ui.setTheme(t.id)}
              aria-pressed={ui.theme === t.id}
              className={`flex flex-1 flex-col items-center gap-2 rounded-xl border py-4 text-sm ${
                ui.theme === t.id ? 'border-accent' : 'border-line hover:bg-surface'
              }`}
            >
              <t.icon size={22} />
              {t.label}
            </button>
          ))}
        </div>
      </Section>

      <Section title="推荐模型">
        <dl className="grid grid-cols-[8rem_1fr] gap-y-2 text-sm">
          <dt className="text-muted">状态</dt>
          <dd>
            {status?.training
              ? status.stage === 'collecting'
                ? '正在读取历史'
                : `训练中 ${status.progress.epoch}/${status.progress.total}`
              : status?.model === 'ready'
                ? '就绪'
                : status?.model === 'error'
                  ? `异常：${status.error}`
                  : '准备中'}
          </dd>
          <dt className="text-muted">模型版本</dt>
          <dd className="font-mono">{status?.model_version || '-'}</dd>
          <dt className="text-muted">训练时间</dt>
          <dd>{formatDateTime(status?.trained_at)}</dd>
          <dt className="text-muted">历史样本</dt>
          <dd>
            {status?.history_samples ?? '-'}
            {status?.summary && ` 条（正样本 ${status.summary.positives}）`}
          </dd>
          <dt className="text-muted">数据更新</dt>
          <dd>{formatDateTime(status?.history_updated_at)}</dd>
          {m && (
            <>
              <dt className="text-muted">训练集指标</dt>
              <dd className="font-mono text-xs">
                AUC {m['AUC-ROC']?.toFixed(3)} · AP {m['Average Precision']?.toFixed(3)} · loss{' '}
                {status?.summary?.best_loss.toFixed(4)}
              </dd>
            </>
          )}
        </dl>
        <button
          onClick={() => retrain.mutate()}
          disabled={status?.training || retrain.isPending}
          className="mt-4 flex items-center gap-2 rounded-full bg-surface px-4 py-2 text-sm font-medium hover:bg-surface-hover disabled:opacity-50"
        >
          <RotateCw size={16} className={status?.training ? 'animate-spin' : ''} />
          重新抓取历史并训练
        </button>
        <p className="mt-2 text-xs text-muted">
          历史数据没有变化时会直接加载已保存的模型；训练在后台进行，期间继续使用旧模型推荐。
        </p>
      </Section>

      <Section title="过滤">
        <Link to="/settings/filters" className="text-sm text-accent hover:underline">
          管理屏蔽关键词与 UP →
        </Link>
      </Section>

      <Section title="调试">
        <Toggle
          checked={ui.showRating}
          onChange={ui.setShowRating}
          label="显示推荐评分"
          hint="在视频卡片上显示 Raw Rating、候选来源、匹配标签与排名"
        />
        <Toggle
          checked={ui.debugPanel}
          onChange={ui.setDebugPanel}
          label="Debug 面板"
          hint="右下角显示模型、候选池、served、API 错误等信息"
        />
      </Section>
    </div>
  )
}
