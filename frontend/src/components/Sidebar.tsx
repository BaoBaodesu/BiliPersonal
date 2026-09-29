import { NavLink } from 'react-router-dom'
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
} from 'lucide-react'
import { useUi } from '../stores/ui'
import { Logo } from './Logo'

const SECTIONS = [
  [
    { to: '/', label: '首页', icon: Home, end: true },
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
    { to: '/settings/filters?tab=keywords', label: '屏蔽关键词', icon: Ban },
  ],
  [
    { to: '/profile/interests', label: '兴趣画像', icon: ChartColumn },
    { to: '/settings', label: '设置', icon: Settings, end: true },
  ],
]

// 收起状态只显示主要入口
const MINI = [
  { to: '/', label: '首页', icon: Home, end: true },
  { to: '/trending', label: '热门', icon: Flame },
  { to: '/explore', label: '探索', icon: Sparkles },
  { to: '/history', label: '历史', icon: History, end: true },
  { to: '/profile/interests', label: '画像', icon: ChartColumn },
]

function FullNav({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className="thin-scroll h-full overflow-y-auto px-3 pb-6">
      {SECTIONS.map((section, i) => (
        <div key={i} className="border-b border-line py-3 last:border-0">
          {section.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              onClick={onNavigate}
              className={({ isActive }) =>
                `flex h-10 items-center gap-6 rounded-lg px-3 text-sm ${
                  isActive && !item.to.includes('?') ? 'bg-surface font-medium' : 'hover:bg-surface'
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
        BiliFeed v0.2 · 本地个性化推荐客户端
        <br />
        仅用于学习与测试
      </p>
    </nav>
  )
}

export function Sidebar() {
  const expanded = useUi((s) => s.sidebarExpanded)
  const mobileOpen = useUi((s) => s.mobileSidebar)
  const setMobile = useUi((s) => s.setMobileSidebar)

  return (
    <>
      {/* 桌面端 */}
      <aside
        className={`fixed top-14 bottom-0 left-0 z-30 hidden bg-bg lg:block ${expanded ? 'w-60' : 'w-[72px]'}`}
      >
        {expanded ? (
          <FullNav />
        ) : (
          <nav className="flex flex-col px-1 pt-1">
            {MINI.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `flex flex-col items-center gap-1.5 rounded-lg py-4 text-[10px] hover:bg-surface ${
                    isActive ? 'font-medium' : ''
                  }`
                }
              >
                <item.icon size={22} strokeWidth={1.75} />
                {item.label}
              </NavLink>
            ))}
          </nav>
        )}
      </aside>

      {/* 平板 / 手机：抽屉 */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 bg-black/50" onClick={() => setMobile(false)} />
          <aside className="fade-in absolute top-0 bottom-0 left-0 w-60 bg-bg">
            <div className="flex h-14 items-center gap-2 px-4">
              <button aria-label="关闭菜单" onClick={() => setMobile(false)} className="rounded-full p-2 hover:bg-surface-hover">
                <X size={22} />
              </button>
              <Logo />
            </div>
            <div className="h-[calc(100%-56px)]">
              <FullNav onNavigate={() => setMobile(false)} />
            </div>
          </aside>
        </div>
      )}
    </>
  )
}
