import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { Entity } from '../types'

const inputClass = 'min-h-11 w-full rounded-lg border border-line bg-bg px-3 text-sm'
const empty = { id: '', type: 'up' as Entity['type'], name: '', aliases: '', mids: '' }

export function EntityControls() {
  const qc = useQueryClient()
  const data = useQuery({ queryKey: ['entities'], queryFn: api.user.entities })
  const [draft, setDraft] = useState(empty)
  const save = useMutation({ mutationFn: api.user.setEntities, onSuccess: (value) => { qc.setQueryData(['entities'], value); setDraft(empty) } })
  if (!data.data) return <p className="text-sm text-muted">{data.isError ? '实体词典读取失败' : '正在读取实体词典…'}</p>
  const split = (value: string) => [...new Set(value.split(/[,，]/).map((word) => word.trim()).filter(Boolean))]
  const duplicate = data.data.entities.filter((entity) => entity.id !== draft.id && [entity.name, ...entity.aliases].some((word) => [draft.name, ...split(draft.aliases)].some((value) => value.toLocaleLowerCase() === word.toLocaleLowerCase())))
  return <section className="space-y-3" aria-labelledby="entity-heading">
    <h3 id="entity-heading" className="font-medium">实体词典</h3>
    <p className="text-xs text-muted">人工确认名称、别名和本人账号；不会自动猜测绑定。相同别名可以属于不同实体，搜索时选择，推荐中保守识别。保存后下一批推荐生效。</p>
    <form className="grid gap-3 sm:grid-cols-2" onSubmit={(event) => {
      event.preventDefault()
      const entity: Entity = { id: draft.id || crypto.randomUUID(), type: draft.type, name: draft.name.trim(), aliases: split(draft.aliases), accounts: draft.type === 'up' ? split(draft.mids).map((mid) => ({ mid, name: data.data?.entities.find((value) => value.id === draft.id)?.accounts.find((account) => account.mid === mid)?.name || '' })) : [] }
      save.mutate({ version: data.data!.version, entities: [...data.data!.entities.filter((value) => value.id !== entity.id), entity] })
    }}>
      <label className="space-y-1 text-sm">规范名称<input required maxLength={100} className={inputClass} value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
      <label className="space-y-1 text-sm">实体类型<select className={inputClass} value={draft.type} onChange={(event) => setDraft({ ...draft, type: event.target.value as Entity['type'] })}><option value="up">UP主</option><option value="character">角色</option><option value="work">作品</option></select></label>
      <label className="space-y-1 text-sm">别名，逗号分隔<input className={inputClass} value={draft.aliases} onChange={(event) => setDraft({ ...draft, aliases: event.target.value })} /></label>
      {draft.type === 'up' && <label className="space-y-1 text-sm">本人账号MID，逗号分隔<input inputMode="numeric" pattern="[0-9,， ]*" className={inputClass} value={draft.mids} onChange={(event) => setDraft({ ...draft, mids: event.target.value })} /></label>}
      {!!duplicate.length && <p role="status" className="text-xs text-muted sm:col-span-2">别名与 {duplicate.map((entity) => entity.name).join('、')} 重叠，将保留为多义实体。</p>}
      <div className="flex gap-3 sm:col-span-2"><button disabled={save.isPending} className="min-h-11 rounded-lg border border-line px-4 text-sm disabled:opacity-50">{save.isPending ? '保存中…' : draft.id ? '保存修改' : '添加实体'}</button>{draft.id && <button type="button" disabled={save.isPending} className="min-h-11 px-3 text-sm" onClick={() => setDraft(empty)}>取消编辑</button>}</div>
    </form>
    {save.error && <div role="alert" className="text-sm text-danger">{save.error.message}<button className="min-h-11 px-3 underline" onClick={() => data.refetch()}>重新读取词典</button></div>}
    {!data.data.entities.length && <p className="text-sm text-muted">词典为空。添加实体后可使用别名搜索和第三方主体识别。</p>}
    {data.data.entities.map((entity) => <div key={entity.id} className="flex flex-wrap items-center justify-between gap-2 border-t border-line py-2 text-sm">
      <div><p>{entity.name} · {{ up: 'UP主', character: '角色', work: '作品' }[entity.type]}</p><p className="text-xs text-muted">{entity.aliases.join('、') || '无别名'}{entity.accounts.length > 0 && ` · MID ${entity.accounts.map((account) => account.mid).join('、')}`}</p></div>
      <div className="flex gap-2"><button disabled={save.isPending} className="min-h-11 px-3" onClick={() => setDraft({ ...entity, aliases: entity.aliases.join(','), mids: entity.accounts.map((account) => account.mid).join(',') })}>编辑</button><button disabled={save.isPending} className="min-h-11 px-3 text-danger" onClick={() => save.mutate({ version: data.data!.version, entities: data.data!.entities.filter((value) => value.id !== entity.id) })}>删除</button></div>
    </div>)}
  </section>
}
