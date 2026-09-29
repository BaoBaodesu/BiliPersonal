import { useEffect } from 'react'
import { useToast } from '../stores/ui'

export function Toast() {
  const { toast, hide } = useToast()
  useEffect(() => {
    if (!toast) return
    const t = setTimeout(hide, toast.action ? 6000 : 3000)
    return () => clearTimeout(t)
  }, [toast, hide])
  if (!toast) return null
  return (
    <div
      key={toast.id}
      role="status"
      className="fade-in fixed bottom-6 left-6 z-50 flex max-w-[calc(100vw-3rem)] items-center gap-6 rounded-lg bg-active px-4 py-3 text-sm text-on-active shadow-pop"
    >
      <span>{toast.message}</span>
      {toast.action && (
        <button
          className="font-medium text-accent"
          onClick={() => {
            toast.action!.onClick()
            hide()
          }}
        >
          {toast.action.label}
        </button>
      )}
    </div>
  )
}
