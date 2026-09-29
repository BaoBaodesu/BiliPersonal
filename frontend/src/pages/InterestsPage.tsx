import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { WeightedItem } from '../types'
import { EmptyState } from '../components/EmptyState'

// 条形权重列表（代替首页的 3D Tag Cloud）
function WeightList({ items, max = 12 }: { items: WeightedItem[]; max?: number }) {
  if (!items.length) return <p className="text-sm text-subtle">暂无数据</p>
  return (
    <ul className="space-y-2">
      {items.slice(0, max).map((t) => (
        <li key={t.name} className="grid grid-cols-[minmax(0,9rem)_1fr_2.5rem] items-center gap-3 text-sm">
          <span className="truncate" title={t.name}>
            {t.name}
          </span>
          <span className="h-2 overflow-hidden rounded-full bg-surface">
            <span className="block h-full rounded-full bg-accent" style={{ width: `${Math.max(4, t.score)}%` }} />
          </span>
          <span className="text-right font-mono text-xs text-muted">{t.score}</span>
        </li>
      ))}
    </ul>
  )
}

function Panel({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-line p-5">
      <h2 className="font-medium">{title}</h2>
      {hint && <p className="mt-0.5 mb-4 text-xs text-muted">{hint}</p>}
      {!hint && <div className="mb-4" />}
      {children}
    </section>
  )
}

export function InterestPanel() {
  const { data, isPending } = useQuery({ queryKey: ['interests'], queryFn: api.user.interests })
  if (isPending) {
    return (
      <div className="grid gap-4 md:grid-cols-2">
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="skeleton h-72 rounded-2xl" />
        ))}
      </div>
    )
  }
  if (!data?.samples) return <EmptyState title="还没有足够的历史数据" description="模型训练完成后这里会显示你的兴趣画像。" />

  const maxDuration = Math.max(1, ...data.durations.map((d) => d.count))

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Panel title="长期兴趣 · Top Tags" hint={`基于 ${data.samples} 条历史 / 收藏样本，权重 = 收藏点赞与观看进度`}>
        <WeightList items={data.top_tags} max={15} />
      </Panel>
      <Panel title="近期兴趣" hint="最近观看的视频中的标签">
        <WeightList items={data.recent_tags} />
      </Panel>
      <Panel title="常看 UP">
        <WeightList items={data.top_ups} max={10} />
      </Panel>
      <div className="space-y-4">
        <Panel title="常看分区">
          <WeightList items={data.zones} max={8} />
        </Panel>
        <Panel title="视频时长分布">
          <div className="flex h-28 items-end gap-3">
            {data.durations.map((d) => (
              <div key={d.name} className="flex flex-1 flex-col items-center gap-2">
                <span className="font-mono text-xs text-muted">{d.count}</span>
                <span
                  className="w-full rounded-t-md bg-accent"
                  style={{ height: `${Math.max(3, (d.count / maxDuration) * 72)}px` }}
                />
                <span className="text-xs whitespace-nowrap text-muted">{d.name}</span>
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  )
}

export function InterestsPage() {
  return (
    <div className="max-w-5xl">
      <h1 className="mb-6 pt-4 text-2xl font-bold">兴趣画像</h1>
      <InterestPanel />
    </div>
  )
}
