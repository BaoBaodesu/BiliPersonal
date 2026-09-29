import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { LogOut, Monitor, Moon, Settings, Sun, SlidersHorizontal, UserRound } from 'lucide-react'
import { api } from '../api/client'
import { useAuth } from '../hooks/queries'
import { thumb } from '../hooks/format'
import { useUi, type Theme } from '../stores/ui'

export function UserAvatar() {
  const { data } = useAuth()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const { theme, setTheme } = useUi()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const user = data?.user

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false)
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', esc)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', esc)
    }
  }, [open])

  const logout = async () => {
    await api.auth.logout()
    qc.clear()
    navigate('/login', { replace: true })
  }

  const themes: { id: Theme; label: string; icon: typeof Sun }[] = [
    { id: 'system', label: '跟随系统', icon: Monitor },
    { id: 'light', label: '浅色', icon: Sun },
    { id: 'dark', label: '深色', icon: Moon },
  ]
  const item = 'flex w-full items-center gap-4 px-4 py-2 text-sm hover:bg-surface-hover'

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-label="账户菜单"
        className="flex h-9 w-9 items-center justify-center overflow-hidden rounded-full bg-surface"
      >
        {user?.face ? (
          <img src={thumb(user.face, 72, 72)} referrerPolicy="no-referrer" alt="" className="h-full w-full object-cover" />
        ) : (
          <UserRound size={20} />
        )}
      </button>
      {open && (
        <div className="fade-in absolute top-11 right-0 z-50 w-72 overflow-hidden rounded-xl bg-elevated py-2 shadow-pop">
          {user && (
            <div className="flex gap-3 border-b border-line px-4 pt-2 pb-4">
              <img src={thumb(user.face, 80, 80)} referrerPolicy="no-referrer" alt="" className="h-10 w-10 rounded-full" />
              <div className="min-w-0">
                <div className="truncate font-medium">{user.name}</div>
                <div className="text-xs text-muted">
                  LV{user.level}
                  {user.vip && ' · 大会员'}
                </div>
              </div>
            </div>
          )}
          <div className="border-b border-line py-2">
            <div className="px-4 pb-1 text-xs text-muted">外观</div>
            {themes.map((t) => (
              <button key={t.id} className={item} onClick={() => setTheme(t.id)}>
                <t.icon size={20} />
                <span className="flex-1 text-left">{t.label}</span>
                {theme === t.id && <span className="h-2 w-2 rounded-full bg-accent" />}
              </button>
            ))}
          </div>
          <div className="py-2">
            <Link to="/settings/filters" className={item} onClick={() => setOpen(false)}>
              <SlidersHorizontal size={20} /> 过滤规则
            </Link>
            <Link to="/settings" className={item} onClick={() => setOpen(false)}>
              <Settings size={20} /> 设置
            </Link>
            <button className={item} onClick={logout}>
              <LogOut size={20} /> 退出登录
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
