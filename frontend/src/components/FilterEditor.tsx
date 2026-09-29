import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Search, Trash2, Upload } from 'lucide-react'
import { api } from '../api/client'
import { keys } from '../hooks/queries'
import type { FilterAction, FilterRule, FilterTarget, FilterMatchMode } from '../types'
import { useToast } from '../stores/ui'

export type FilterTab = 'title' | 'uploader' | 'ups'

const TARGET_OF_TAB: Record<Exclude<FilterTab, 'ups'>, FilterTarget> = {
  title: 'title',
  uploader: 'uploader',
}

const MATCH_LABEL: Record<FilterMatchMode, string> = {
  contains: '包含',
  exact: '完全匹配',
  regex: '正则',
}

const ACTION_LABEL: Record<FilterAction, string> = {
  hard_block: '直接屏蔽',
  downrank: '降权',
}

const SOURCE_LABEL: Record<string, string> = {
  manual: '手动添加',
  legacy_blocked_keywords: '旧数据迁移',
  biliblock_import: 'BiliBlock 导入',
  system: '系统',
}

const PLACEHOLDER: Record<Exclude<FilterTab, 'ups'>, string> = {
  title: '标题关键词，如：大冰、广告、考研',
  uploader: 'UP 主名称关键词，如：数码、差评君',
}

// 过滤规则编辑：标题关键词 / UP 主关键词 / 精确屏蔽 UP 三类管理
export function FilterEditor({ tab }: { tab: FilterTab }) {
  const qc = useQueryClient()
  const toast = useToast((s) => s.show)
  const [input, setInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [importOpen, setImportOpen] = useState(false)
  const [importText, setImportText] = useState('')

  const isRulesTab = tab !== 'ups'
  const targetType = isRulesTab ? TARGET_OF_TAB[tab] : undefined

  const rulesQuery = useQuery({
    queryKey: [...keys.filters, 'rules', tab, keyword],
    queryFn: () => api.filters.rules({ target_type: targetType, q: keyword || undefined }),
    enabled: isRulesTab,
  })
  const legacyQuery = useQuery({ queryKey: keys.filters, queryFn: api.filters.get })

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: keys.filters })
    // 规则变化后旧 Feed 缓存已失效，前端同步重新拉取
    qc.invalidateQueries({ queryKey: ['feed'] })
  }

  // 两个 Tab 的返回值类型不同，拆成两个 mutation，避免联合类型
  const addRule = useMutation({
    mutationFn: (value: string) => api.filters.createRule({ keyword: value, target_type: targetType }),
    onSuccess: () => {
      setInput('')
      invalidate()
    },
    onError: (e: Error) => toast(e.message),
  })

  const addUp = useMutation({
    mutationFn: (value: string) => api.filters.addUp(value),
    onSuccess: () => {
      setInput('')
      invalidate()
    },
    onError: (e: Error) => toast(e.message),
  })

  const importRules = useMutation({
    mutationFn: () => api.filters.importRules({ text: importText, target_type: targetType }),
    onSuccess: (r) => {
      toast(`导入完成：新增 ${r.inserted}，重复 ${r.duplicate}，无效 ${r.invalid}`)
      setImportText('')
      setImportOpen(false)
      invalidate()
    },
    onError: (e: Error) => toast(e.message),
  })

  const submitRule = (e: React.FormEvent) => {
    e.preventDefault()
    if (input.trim()) addRule.mutate(input.trim())
  }

  const submitUp = (e: React.FormEvent) => {
    e.preventDefault()
    if (input.trim()) addUp.mutate(input.trim())
  }

  const rules = rulesQuery.data?.items ?? []
  const ups = legacyQuery.data?.ups ?? []
  const pending = isRulesTab ? rulesQuery.isPending : legacyQuery.isPending

  return (
    <div>
      {isRulesTab ? (
        <form onSubmit={submitRule} className="mb-4 flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            maxLength={60}
            placeholder={PLACEHOLDER[tab]}
            className="h-10 flex-1 rounded-lg border border-line bg-bg px-3 outline-none focus:border-accent"
          />
          <button
            type="submit"
            disabled={!input.trim() || addRule.isPending}
            className="flex h-10 items-center gap-1.5 rounded-lg bg-active px-4 text-sm font-medium text-on-active disabled:opacity-50"
          >
            <Plus size={16} /> 添加
          </button>
        </form>
      ) : (
        <form onSubmit={submitUp} className="mb-4 flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            maxLength={60}
            placeholder="输入要精确屏蔽的 UP 主名称"
            className="h-10 flex-1 rounded-lg border border-line bg-bg px-3 outline-none focus:border-accent"
          />
          <button
            type="submit"
            disabled={!input.trim() || addUp.isPending}
            className="flex h-10 items-center gap-1.5 rounded-lg bg-active px-4 text-sm font-medium text-on-active disabled:opacity-50"
          >
            <Plus size={16} /> 添加
          </button>
        </form>
      )}

      {isRulesTab && (
        <>
          <div className="mb-4 flex items-center gap-2">
            <div className="relative flex-1">
              <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-subtle" />
              <input
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                placeholder="搜索关键词"
                className="h-9 w-full rounded-lg border border-line bg-bg pl-9 pr-3 text-sm outline-none focus:border-accent"
              />
            </div>
            <button
              type="button"
              onClick={() => setImportOpen((v) => !v)}
              className="flex h-9 items-center gap-1.5 rounded-lg border border-line px-3 text-sm hover:bg-surface"
            >
              <Upload size={15} /> 批量导入
            </button>
          </div>

          {importOpen && (
            <div className="mb-4 rounded-xl border border-line p-3">
              <p className="mb-2 text-xs text-muted">
                每行一条，或用 | 分隔。例如：关键词1|关键词2|关键词3
              </p>
              <textarea
                value={importText}
                onChange={(e) => setImportText(e.target.value)}
                rows={5}
                placeholder={'大冰\n广告\n考研'}
                className="w-full rounded-lg border border-line bg-bg p-3 text-sm outline-none focus:border-accent"
              />
              <div className="mt-2 flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => importRules.mutate()}
                  disabled={!importText.trim() || importRules.isPending}
                  className="rounded-lg bg-active px-4 py-2 text-sm font-medium text-on-active disabled:opacity-50"
                >
                  导入
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setImportOpen(false)
                    setImportText('')
                  }}
                  className="rounded-lg px-3 py-2 text-sm text-muted hover:bg-surface"
                >
                  取消
                </button>
              </div>
            </div>
          )}
        </>
      )}

      {!isRulesTab && (
        <p className="-mt-1 mb-4 text-xs text-muted">
          按 mid 或完整名称精确屏蔽某一个 UP。若只想按名称关键字屏蔽（如「数码」），请用「UP 主关键词」。
        </p>
      )}

      {pending ? (
        <div className="skeleton h-32 rounded-lg" />
      ) : isRulesTab ? (
        !rules.length ? (
          <p className="py-10 text-center text-sm text-muted">
            {keyword ? '没有匹配的规则' : '还没有规则'}
          </p>
        ) : (
          <ul className="divide-y divide-line rounded-xl border border-line">
            {rules.map((r) => (
              <RuleRow key={r.id} rule={r} onChanged={invalidate} />
            ))}
          </ul>
        )
      ) : !ups.length ? (
        <p className="py-10 text-center text-sm text-muted">还没有精确屏蔽的 UP 主</p>
      ) : (
        <ul className="divide-y divide-line rounded-xl border border-line">
          {ups.map((u) => (
            <li key={u.id} className="flex items-center gap-4 px-4 py-3">
              <span className="flex-1">{u.name}</span>
              {u.mid && <span className="font-mono text-xs text-subtle">mid {u.mid}</span>}
              <button
                aria-label={`取消屏蔽 ${u.name}`}
                onClick={() => api.filters.deleteUp(u.id).then(invalidate)}
                className="rounded-full p-2 text-muted hover:bg-surface-hover hover:text-danger"
              >
                <Trash2 size={16} />
              </button>
            </li>
          ))}
        </ul>
      )}

      {isRulesTab && rulesQuery.data?.summary && (
        <p className="mt-4 text-xs text-muted">
          {tab === 'title' ? '标题' : 'UP'} 规则共{' '}
          {rulesQuery.data.summary.by_target.find((s) => s.target_type === targetType)?.total ?? 0} 条 ·
          规则版本 v{rulesQuery.data.summary.version}
        </p>
      )}
    </div>
  )
}

function RuleRow({ rule, onChanged }: { rule: FilterRule; onChanged: () => void }) {
  const toast = useToast((s) => s.show)
  const update = useMutation({
    mutationFn: (body: Parameters<typeof api.filters.updateRule>[1]) =>
      api.filters.updateRule(rule.id, body),
    onSuccess: onChanged,
    onError: (e: Error) => toast(e.message),
  })
  const remove = useMutation({
    mutationFn: () => api.filters.deleteRule(rule.id),
    onSuccess: onChanged,
    onError: (e: Error) => toast(e.message),
  })

  return (
    <li className="px-4 py-3">
      <div className="flex items-center gap-4">
        <span className={`flex-1 ${rule.enabled ? '' : 'text-subtle line-through'}`}>
          {rule.keyword}
        </span>
        <label
          className="relative inline-flex cursor-pointer items-center"
          title={rule.enabled ? '已启用' : '已禁用'}
        >
          <input
            type="checkbox"
            checked={rule.enabled}
            onChange={(e) => update.mutate({ enabled: e.target.checked })}
            className="peer sr-only"
            aria-label={`启用 ${rule.keyword}`}
          />
          <span className="h-5 w-9 rounded-full bg-surface-hover transition peer-checked:bg-accent" />
          <span className="absolute left-0.5 h-4 w-4 rounded-full bg-white transition peer-checked:translate-x-4" />
        </label>
        <button
          aria-label={`删除 ${rule.keyword}`}
          onClick={() => remove.mutate()}
          disabled={remove.isPending}
          className="rounded-full p-2 text-muted hover:bg-surface-hover hover:text-danger disabled:opacity-50"
        >
          <Trash2 size={16} />
        </button>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-subtle">
        <span>{MATCH_LABEL[rule.match_mode]}</span>
        <button
          type="button"
          onClick={() =>
            update.mutate({ action: rule.action === 'hard_block' ? 'downrank' : 'hard_block' })
          }
          title="点击切换行为"
          className={`rounded px-1.5 py-0.5 ${
            rule.action === 'hard_block' ? 'bg-danger/10 text-danger' : 'bg-surface-hover'
          }`}
        >
          {ACTION_LABEL[rule.action]}
        </button>
        <span>{SOURCE_LABEL[rule.source] ?? rule.source}</span>
        <span>命中 {rule.hit_count}</span>
        {rule.last_hit_at && <span>最近命中 {rule.last_hit_at}</span>}
      </div>
    </li>
  )
}
