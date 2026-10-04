import { api } from '../api/client'
import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Menu, RefreshCw, Search, X } from 'lucide-react'
import { useIsMutating, useQueryClient } from '@tanstack/react-query'
import { useUi, useToast } from '../stores/ui'
import { ModelStatus } from './ModelStatus'
import { UserAvatar } from './UserAvatar'
import { Logo } from './Logo'

export const FEED_PATHS: Record<string, 'for_you' | 'hot' | 'explore' | 'following'> = {
  '/': 'for_you',
  '/trending': 'hot',
  '/explore': 'explore',
  '/following': 'following',
}

export function Header() {
  const toggleSidebar = useUi((s) => s.toggleSidebar)
  const setMobileSidebar = useUi((s) => s.setMobileSidebar)
  const sidebarOpen = useUi((s) => window.innerWidth < 1024 ? s.mobileSidebar : s.sidebarExpanded)
  const navigate = useNavigate()
  const location = useLocation()
  const [params] = useSearchParams()
  const [q, setQ] = useState(location.pathname === '/search' ? params.get('q') || '' : '')
  const [mobileSearch, setMobileSearch] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const qc = useQueryClient()
  const refreshing = useIsMutating({ mutationKey: ['refresh-feed'] }) > 0
  const onFeed = location.pathname in FEED_PATHS

  useEffect(() => {
    if (location.pathname === '/search') setQ(params.get('q') || '')
  }, [location.pathname, params])

  useEffect(() => {
    if (mobileSearch) input.current?.focus()
  }, [mobileSearch])

  // 键盘操作：/ 聚焦搜索
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (e.key === '/' && !e.defaultPrevented && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey && !t.isContentEditable && !t.closest('[role="dialog"], [role="menu"]') && !['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName)) {
        e.preventDefault()
        if (window.innerWidth < 640) setMobileSearch(true)
        else input.current?.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    const v = q.trim()
    if (!v) return
    void api.user.recordSearch(v, crypto.randomUUID()).catch(() => useToast.getState().show('搜索记忆保存失败，搜索仍可继续'))
    window.scrollTo({ top: 0, behavior: 'instant' })
    navigate(`/search?q=${encodeURIComponent(v)}`, { viewTransition: true })
    input.current?.blur()
    setMobileSearch(false)
  }

  const refresh = () => {
    if (onFeed) {
      window.dispatchEvent(new CustomEvent('feed:refresh'))
    } else {
      window.scrollTo({ top: 0, behavior: 'instant' })
      qc.invalidateQueries()
    }
  }

  const searchForm = (
    <form onSubmit={submit} role="search" className="header-search flex h-11 w-full max-w-[640px] items-center rounded-full border border-line bg-bg focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/15">
      <div className="flex h-full min-w-0 flex-1 items-center gap-1 pl-4 pr-1">
        <input
          ref={input}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索"
          aria-label="搜索"
          className="h-full min-w-0 w-full bg-transparent text-base text-fg outline-none placeholder:text-subtle"
        />
        {q && (
          <button type="button" aria-label="清除" onClick={() => { setQ(''); input.current?.focus() }} className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-muted hover:bg-surface-hover hover:text-fg">
            <X size={18} />
          </button>
        )}
      </div>
      <button
        type="submit"
        aria-label="搜索"
        className="flex h-full w-14 shrink-0 items-center justify-center rounded-r-full border-l border-line bg-surface hover:bg-surface-hover sm:w-16"
      >
        <Search size={20} />
      </button>
    </form>
  )

  if (mobileSearch) {
    return (
      <header className="fixed inset-x-0 top-0 z-40 flex h-14 items-center gap-2 bg-bg px-2">
        <button aria-label="返回" onClick={() => setMobileSearch(false)} className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full hover:bg-surface-hover">
          <ArrowLeft size={22} />
        </button>
        {searchForm}
      </header>
    )
  }

  return (
    <header className="fixed inset-x-0 top-0 z-40 flex h-14 items-center justify-between gap-2 bg-bg px-2 sm:gap-4 sm:px-4">
      <div className="flex shrink-0 items-center gap-1 sm:gap-2">
        <button
          aria-label="菜单"
          aria-expanded={sidebarOpen}
          onClick={() => (window.innerWidth < 1024 ? setMobileSidebar(true) : toggleSidebar())}
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full hover:bg-surface-hover"
        >
          <Menu size={22} />
        </button>
        <Logo />
      </div>
      <div className="hidden flex-1 justify-center sm:flex">{searchForm}</div>
      <div className="flex shrink-0 items-center gap-1">
        <button
          aria-label="搜索"
          onClick={() => setMobileSearch(true)}
          className="flex h-11 w-11 items-center justify-center rounded-full hover:bg-surface-hover sm:hidden"
        >
          <Search size={22} />
        </button>
        {!onFeed && <div className="hidden md:block"><ModelStatus /></div>}
        {!onFeed && <button
          onClick={refresh}
          title="刷新"
          aria-label="刷新当前页面"
          disabled={refreshing}
          className="flex h-11 w-11 items-center justify-center rounded-full hover:bg-surface-hover disabled:opacity-60"
        >
          <RefreshCw size={20} className={refreshing ? 'animate-spin' : ''} />
        </button>}
        <div className="ml-1">
          <UserAvatar />
        </div>
      </div>
    </header>
  )
}
