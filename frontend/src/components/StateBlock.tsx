import { AlertTriangle, RefreshCw, WifiOff, Inbox, LogIn } from 'lucide-react'

export interface StateProps {
  title: string
  description?: string
  action?: { label: string; onClick: () => void }
  icon?: 'empty' | 'error' | 'offline' | 'login'
}

const ICONS = { empty: Inbox, error: AlertTriangle, offline: WifiOff, login: LogIn }

export function StateBlock({ title, description, action, icon = 'empty' }: StateProps) {
  const Icon = ICONS[icon]
  return (
    <div className="flex flex-col items-center justify-center px-6 py-20 text-center">
      <Icon size={48} strokeWidth={1.25} className="mb-4 text-subtle" />
      <h2 className="text-lg font-medium">{title}</h2>
      {description && <p className="mt-2 max-w-md text-sm text-muted">{description}</p>}
      {action && (
        <button
          onClick={action.onClick}
          className="mt-6 flex items-center gap-2 rounded-full bg-active px-4 py-2 text-sm font-medium text-on-active hover:opacity-90"
        >
          <RefreshCw size={16} /> {action.label}
        </button>
      )}
    </div>
  )
}
