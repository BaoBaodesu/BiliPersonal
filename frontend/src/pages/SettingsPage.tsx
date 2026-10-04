import { PolicyControls } from '../components/PolicyControls'
import { DiscreteSlider } from '../components/DiscreteSlider'
import { Link, useSearchParams } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Monitor, Moon, Sun, RotateCw } from 'lucide-react'
import { api } from '../api/client'
import { FilterEditor, type FilterTab } from '../components/FilterEditor'
import { keys, useSystemStatus, useSources } from '../hooks/queries'
import { formatDateTime } from '../hooks/format'
import { useUi, type Theme } from '../stores/ui'
import type { SourceSettings, SourceLevel } from '../types'

export function FiltersPage() {
  const [params, setParams] = useSearchParams()
  const raw = params.get('tab')
  const tab: FilterTab = raw === 'ups' || raw === 'uploader' || raw === 'tag' || raw === 'zone' ? raw : 'title'
  const tabs: [FilterTab, string][] = [
    ['title', '标题关键词'],
    ['uploader', 'UP 主关键词'],
    ['tag', '标签'],
    ['zone', '分区'],
    ['ups', '精确屏蔽 UP'],
  ]
  return (
    <div className="max-w-2xl">
      <h1 className="mb-6 pt-4 text-2xl font-bold">过滤规则</h1>
      <div className="mb-6 flex flex-wrap gap-4 border-b border-line" role="tablist">
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
        过滤在推荐排序之前执行（精确屏蔽 UP → UP 关键词 → 标题关键词 → 标签 → 分区），同样作用于搜索结果。
        硬屏蔽立即作用于缓存页；降权从新生成的页面开始生效。AI 内容过滤（LLM）将在后续版本作为可选模块加入。
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

function Toggle({ checked, onChange, label, hint, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string; disabled?: boolean }) {
  return (
    <label className={`flex min-h-11 cursor-pointer items-center justify-between gap-6 py-2 ${disabled ? 'opacity-50' : ''}`}>
      <span>
        <span className="text-sm">{label}</span>
        {hint && <span className="block text-xs text-muted">{hint}</span>}
      </span>
      <span className="relative inline-flex shrink-0 items-center">
        <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} className="peer sr-only" />
        <span className="h-5 w-9 rounded-full bg-surface-hover transition peer-checked:bg-accent peer-focus-visible:ring-2 peer-focus-visible:ring-accent" />
        <span className="absolute left-0.5 h-4 w-4 rounded-full bg-white transition peer-checked:translate-x-4" />
      </span>
    </label>
  )
}

export function SettingsPage() {
  const ui = useUi()
  const qc = useQueryClient()
  const { data: status } = useSystemStatus()
  const sources = useSources()
  const saveSources = useMutation({
    mutationFn: (value: Partial<SourceSettings>) => api.system.setSources(value),
    onSuccess: (value) => qc.setQueryData(keys.sources, value),
  })
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

      <Section title="推荐来源">
        {sources.isPending ? <div className="skeleton h-32 rounded-lg" /> : sources.isError ? (
          <button onClick={() => sources.refetch()} className="min-h-11 text-sm text-accent">读取失败，点击重试</button>
        ) : sources.data && (
          <div className="space-y-3">
            {([['hot', '热门榜'], ['rcmd', 'B 站推荐流'], ['vertical_search', '兴趣垂类搜索']] as const).map(([key, label]) => (
              <DiscreteSlider key={key} label={label} value={(['off', 'fallback', 'small', 'standard'] as SourceLevel[]).indexOf(sources.data.settings[key])}
                labels={['关闭', '仅兜底', '少量1条', '标准3条']} disabled={saveSources.isPending}
                onCommit={(index) => saveSources.mutateAsync({ [key]: (['off', 'fallback', 'small', 'standard'] as SourceLevel[])[index] })} />
            ))}
            <Toggle checked={sources.data.settings.classic} onChange={(classic) => saveSources.mutate({ classic })} disabled={saveSources.isPending} label="经典首页" hint="使用原来的热门与推荐流排序路径" />
            <p className="text-xs text-muted" role="status">{saveSources.isPending ? '正在保存…' : '修改后从下一次换一批开始生效；当前批次保持原设置。'}</p>
            {saveSources.isError && <p className="text-sm text-danger" role="alert">保存失败：{saveSources.error.message}</p>}
            <div className="pt-2">
              <h3 className="mb-2 text-sm font-medium">最近 7 天 · 推荐来源</h3>
              {sources.data.report.total ? (
                <>
                  <p className="mb-3 text-xs text-muted">共 {sources.data.report.total} 条；热门 + 推荐流占比 {(sources.data.report.sources.filter((s) => s.source === 'hot' || s.source === 'rcmd').reduce((sum, s) => sum + s.share, 0) * 100).toFixed(1)}%</p>
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-xs tabular-nums">
                      <thead><tr className="text-muted"><th className="py-2 font-normal">来源</th><th className="py-2 font-normal">生成／曝光</th><th className="py-2 font-normal">占比</th><th className="py-2 font-normal">点击率</th><th className="py-2 font-normal">不感兴趣率</th><th className="py-2 font-normal">屏蔽率</th></tr></thead>
                      <tbody>{sources.data.report.sources.map((s) => <tr key={s.source} className="border-t border-line"><td className="py-3">{{ follow: '关注新作', related: '相关视频', up_archive: '常看旧作', rcmd: '推荐流', hot: '热门', legacy: '旧记录' }[s.source] || s.source}</td><td>{s.served}／{s.exposed}</td><td>{(s.share * 100).toFixed(1)}%</td><td>{s.click_rate == null ? '—' : `${(s.click_rate * 100).toFixed(1)}%`}</td><td>{s.not_interested_rate == null ? '—' : `${(s.not_interested_rate * 100).toFixed(1)}%`}</td><td>{s.blocked_rate == null ? '—' : `${(s.blocked_rate * 100).toFixed(1)}%`}</td></tr>)}</tbody>
                    </table>
                  </div>
                  <p className="mt-2 text-xs text-muted">占比按生成条目计算；点击率与不感兴趣率按实际曝光计算。没有曝光时显示 —。</p>
                </>
              ) : <p className="text-sm text-muted">还没有来源统计，浏览几批推荐后再来查看。</p>}
            </div>
          </div>
        )}
      </Section>

      <Section title="推荐策略"><PolicyControls /></Section>
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
        <Link to="/settings/filters" viewTransition className="text-sm text-accent hover:underline">
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
