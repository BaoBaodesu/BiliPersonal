import { useEffect, useId, useState, useRef } from 'react'

// 原生离散滑杆：移动只更新草稿，释放或键盘操作结束才保存。
export function DiscreteSlider({ label, value, labels, disabled, onCommit }: {
  label: string; value: number; labels: readonly string[]; disabled?: boolean
  onCommit: (value: number) => Promise<unknown>
}) {
  const id = useId()
  const [draft, setDraft] = useState(value)
  const [saving, setSaving] = useState(false)
  const pending = useRef(false)
  const cancelled = useRef(false)
  const input = useRef<HTMLInputElement>(null)
  const keyboard = useRef(false)
  useEffect(() => { if (!saving && keyboard.current && document.activeElement === document.body) input.current?.focus() }, [saving])
  useEffect(() => setDraft(value), [value])
  const cancel = () => { cancelled.current = true; setDraft(value) }
  const commit = async (next: number) => {
    if (pending.current || cancelled.current || disabled || next === value) return
    pending.current = true
    setSaving(true)
    try { await onCommit(next) } catch { setDraft(value) } finally { pending.current = false; setSaving(false) }
  }
  return <div className="w-full max-w-sm py-2">
    <div className="flex justify-between gap-4 text-sm"><label htmlFor={id}>{label}</label><output htmlFor={id}>{labels[draft]}</output></div>
    <div className={`relative mt-1 h-11 ${disabled || saving ? 'opacity-50' : ''}`}>
      <div aria-hidden="true" className="pointer-events-none absolute inset-x-0 top-1/2 h-6 -translate-y-1/2 overflow-hidden rounded-full border border-line bg-surface-hover">
        <div className="absolute inset-y-0 left-0 rounded-full bg-accent" style={{ width: `calc(${draft / (labels.length - 1) * 100}% + ${16 - 32 * draft / (labels.length - 1)}px)` }} />
        {labels.map((name, index) => <span key={name} className={`absolute top-1/2 h-1.5 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full ${index <= draft ? 'bg-white/50' : 'bg-subtle/50'}`} style={{ left: `calc(${index / (labels.length - 1) * 100}% + ${16 - 32 * index / (labels.length - 1)}px)` }} />)}
      </div>
    <input ref={input} id={id} type="range" min={0} max={labels.length - 1} step={1} value={draft}
      aria-valuetext={labels[draft]} disabled={disabled || saving}
      onChange={(e) => setDraft(Number(e.target.value))}
      onPointerDown={(e) => { cancelled.current = false; e.currentTarget.setPointerCapture(e.pointerId) }}
      onPointerUp={(e) => void commit(Number(e.currentTarget.value))} onPointerCancel={cancel}
      onKeyDown={(e) => { keyboard.current = true; cancelled.current = false; if (e.key === 'Escape') { e.preventDefault(); cancel() } }}
      onKeyUp={(e) => { if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown'].includes(e.key)) void commit(Number(e.currentTarget.value)) }}
      onBlur={() => { if (!pending.current) cancel() }} className="discrete-slider relative h-11 w-full" />
    </div>
    <div aria-hidden="true" className="flex justify-between gap-2 text-xs text-muted">{labels.map((name) => <span key={name}>{name}</span>)}</div>
  </div>
}
