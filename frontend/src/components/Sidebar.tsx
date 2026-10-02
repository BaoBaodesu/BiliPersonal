import { useEffect, useRef } from 'react'
import { NavLink, useLocation, useSearchParams } from 'react-router-dom'
import { useDialogFocus } from '../hooks/useDialogFocus'
import {
  Ban,
  Clock,
  Compass,
  Flame,
  History,
  Home,
  ListVideo,
  Settings,
  Sparkles,
  Star,
  UserX,
  ChartColumn,
  ThumbsDown,
  X,
  Users,
} from 'lucide-react'
import { useUi } from '../stores/ui'
import { Logo } from './Logo'
import { useSources } from '../hooks/queries'

const SECTIONS = [
  [
    { to: '/', label: '首页', icon: Home, end: true },
    { to: '/following', label: '关注', icon: Users },
    { to: '/trending', label: '热门', icon: Flame },
    { to: '/explore', label: '兴趣探索', icon: Compass },
    { to: '/history/recommendation', label: '推荐历史', icon: ListVideo },
  ],
  [
    { to: '/history', label: '观看历史', icon: History, end: true },
    { to: '/favorites', label: '收藏', icon: Star },
    { to: '/watch-later', label: '稍后再看', icon: Clock },
  ],
  [
    { to: '/feedback/not-interested', label: '不感兴趣', icon: ThumbsDown },
    { to: '/settings/filters?tab=ups', label: '屏蔽的 UP', icon: UserX },
    { to: '/settings/filters?tab=title', label: '屏蔽关键词', icon: Ban },
  ],
  [
    { to: '/profile/interests', label: '兴趣画像', icon: ChartColumn },
    { to: '/settings', label: '设置', icon: Settings, end: true },
  ],
]

// 收起状态只显示主要入口
const MINI = [
  { to: '/', label: '首页', icon: Home, end: true },
  { to: '/following', label: '关注', icon: Users },
  { to: '/trending', label: '热门', icon: Flame },
  { to: '/explore', label: '探索', icon: Sparkles },
  { to: '/history', label: '历史', icon: History, end: true },
  { to: '/profile/interests', label: '画像', icon: ChartColumn },
]

function FullNav({ onNavigate }: { onNavigate?: () => void }) {
  const { data: sources } = useSources()
  const location = useLocation()
  const [params] = useSearchParams()
  return (
    <nav className="thin-scroll h-full overflow-y-auto px-3 pb-6">
      {SECTIONS.map((section, i) => (
        <div key={i} className="border-b border-line py-3 last:border-0">
          {section.filter((item) => !(item.to === '/trending' && sources?.settings.hot === 'off') && !(item.to === '/explore' && sources?.settings.rcmd === 'off')).map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              viewTransition
              end={item.end}
              aria-current={item.to.includes('?') ? (location.pathname === '/settings/filters' && (item.to.endsWith('ups') ? params.get('tab') === 'ups' : params.get('tab') !== 'ups') ? 'page' : false) : undefined}
              onClick={onNavigate}
              className={({ isActive }) =>
                `sidebar-link flex h-11 items-center gap-6 rounded-lg px-3 text-sm ${
                  (item.to.includes('?') ? location.pathname === '/settings/filters' && (item.to.endsWith('ups') ? params.get('tab') === 'ups' : params.get('tab') !== 'ups') : isActive) ? 'bg-surface font-medium' : 'hover:bg-surface'
                }`
              }
            >
              <item.icon size={22} strokeWidth={1.75} />
              <span className="truncate">{item.label}</span>
            </NavLink>
          ))}
        </div>
      ))}
      <p className="px-3 pt-2 text-xs leading-5 text-subtle">
        BiliPersonal v0.3.1 · 本地个性化推荐客户端
        <br />
        仅用于学习与测试
      </p>
    </nav>
  )
}

export function Sidebar() {
  const { data: sources } = useSources()
  const expanded = useUi((s) => s.sidebarExpanded)
  const mobileOpen = useUi((s) => s.mobileSidebar)
  const setMobile = useUi((s) => s.setMobileSidebar)
  const drawer = useRef<HTMLElement>(null)
  useDialogFocus(drawer, mobileOpen, () => setMobile(false))

  useEffect(() => {
    const desktop = window.matchMedia('(min-width: 1024px)')
    const closeOnBreakpoint = () => setMobile(false)
    desktop.addEventListener('change', closeOnBreakpoint)
    return () => desktop.removeEventListener('change', closeOnBreakpoint)
  }, [setMobile])

  return (
    <>
      {/* 桌面端 */}
      <aside
        className={`sidebar-desktop fixed top-14 bottom-0 left-0 z-30 hidden overflow-hidden bg-bg lg:block ${expanded ? 'w-60' : 'w-[72px]'}`}
      >
        <div className={`sidebar-nav absolute inset-y-0 left-0 w-60 ${expanded ? 'is-visible' : ''}`} inert={!expanded} aria-hidden={!expanded}>
          <FullNav />
        </div>
        <div className={`sidebar-nav absolute inset-y-0 left-0 w-[72px] ${!expanded ? 'is-visible' : ''}`} inert={expanded} aria-hidden={expanded}>
          <nav className="flex flex-col px-1 pt-1">
            {MINI.filter((item) => !(item.to === '/trending' && sources?.settings.hot === 'off') && !(item.to === '/explore' && sources?.settings.rcmd === 'off')).map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                viewTransition
                end={item.end}
                className={({ isActive }) =>
                  `sidebar-link flex flex-col items-center gap-1.5 rounded-lg py-4 text-[10px] hover:bg-surface ${
                    isActive ? 'bg-surface font-medium' : ''
                  }`
                }
              >
                <item.icon size={22} strokeWidth={1.75} />
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </aside>

      {/* 平板 / 手机：抽屉 */}
      <div className={`sidebar-mobile fixed inset-0 z-50 lg:hidden ${mobileOpen ? 'is-visible' : ''}`} inert={!mobileOpen} aria-hidden={!mobileOpen}>
        <div className="sidebar-backdrop absolute inset-0 bg-black/50" onClick={() => setMobile(false)} />
        <aside ref={drawer} role="dialog" aria-modal={mobileOpen} aria-label="主导航" tabIndex={-1} className="sidebar-drawer absolute top-0 bottom-0 left-0 w-60 bg-bg">
          <div className="flex h-14 items-center gap-2 px-4">
            <button aria-label="关闭菜单" onClick={() => setMobile(false)} className="flex h-11 w-11 items-center justify-center rounded-full hover:bg-surface-hover">
              <X size={22} />
            </button>
            <Logo />
          </div>
          <div className="h-[calc(100%-56px)]">
            <FullNav onNavigate={() => setMobile(false)} />
          </div>
        </aside>
      </div>
    </>
  )
}
