import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { Header } from '../components/Header'
import { Sidebar } from '../components/Sidebar'
import { Toast } from '../components/Toast'
import { DebugPanel } from '../components/DebugPanel'
import { OfflineState } from '../components/ErrorState'
import { useAuth, useOnline } from '../hooks/queries'
import { useUi } from '../stores/ui'

export function AppShell() {
  const expanded = useUi((s) => s.sidebarExpanded)
  const auth = useAuth()
  const online = useOnline()
  const location = useLocation()

  // 登录态检查：未登录 / 登录失效 → 登录页
  if (auth.data && !auth.data.logged_in) {
    return <Navigate to="/login" replace state={{ from: location.pathname, expired: auth.data.expired }} />
  }

  return (
    <div className="min-h-full">
      <Header />
      <Sidebar />
      <main className={`pt-14 ${expanded ? 'lg:pl-60' : 'lg:pl-[72px]'}`}>
        <div className="mx-auto max-w-[2200px] px-4 pb-10 sm:px-6">
          {auth.isError && (auth.error as { code?: string }).code === 'offline' ? (
            <OfflineState
              title="无法连接本地服务"
              description="请确认 python run.py 正在运行（http://127.0.0.1:8345）。"
              action={{ label: '重试', onClick: () => auth.refetch() }}
            />
          ) : (
            <>
              {!online && (
                <div className="mt-2 rounded-lg bg-surface px-4 py-2 text-sm text-muted">当前处于离线状态，显示的是已缓存内容。</div>
              )}
              <Outlet />
            </>
          )}
        </div>
      </main>
      <Toast />
      <DebugPanel />
    </div>
  )
}
