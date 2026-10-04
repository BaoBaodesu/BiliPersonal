import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'

export function VideoPreferences({ bvid }: { bvid: string }) {
  const qc = useQueryClient()
  const values = useQuery({ queryKey: ['video-preferences'], queryFn: api.user.videoPreferences })
  const save = useMutation({ mutationFn: (value: { purpose?: string; allow_replay?: boolean; replay_days?: number }) => api.user.setVideoPreferences(bvid, value), onSuccess: (value) => { qc.setQueryData(['video-preferences'], { ...values.data, [bvid]: value }); qc.invalidateQueries({ queryKey: ['interests'] }) } })
  const current = values.data?.[bvid] ?? { purpose: 'normal', allow_replay: false, replay_days: 30 }
  return <div className="mt-3 flex flex-wrap gap-2 text-xs">
    <select aria-label={`${bvid} 收藏用途`} value={current.purpose} disabled={!values.data || save.isPending} onChange={(e) => save.mutate({ purpose: e.target.value })} className="min-h-11 rounded border border-line bg-bg px-2"><option value="normal">普通收藏</option><option value="utility">工具学习</option><option value="support">支持作者</option></select>
    <label className="flex min-h-11 items-center gap-2"><input type="checkbox" disabled={!values.data || save.isPending} checked={current.allow_replay} onChange={(e) => save.mutate({ allow_replay: e.target.checked })} />允许回看</label>
    {current.allow_replay && <select aria-label={`${bvid} 回看周期`} value={current.replay_days} disabled={save.isPending} onChange={(e) => save.mutate({ replay_days: Number(e.target.value) })} className="min-h-11 rounded border border-line bg-bg px-2"><option value={30}>30天</option><option value={7}>7天</option></select>}
    {(save.error || values.error) && <p role="alert" className="w-full text-danger">{(save.error || values.error)?.message}</p>}
  </div>
}
