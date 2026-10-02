import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import { QueryClient, QueryClientProvider, QueryCache, MutationCache } from '@tanstack/react-query'
import './styles/index.css'
import { ApiError } from './api/client'
import { keys, useThemeEffect } from './hooks/queries'
import { AppShell } from './layouts/AppShell'
import { FeedPage } from './pages/FeedPage'
import { SearchPage } from './pages/SearchPage'
import { FavoritesPage, NotInterestedPage, WatchHistoryPage, WatchLaterPage } from './pages/LibraryPages'
import { RecommendationHistoryPage } from './pages/RecommendationHistoryPage'
import { InterestsPage } from './pages/InterestsPage'
import { FiltersPage, SettingsPage } from './pages/SettingsPage'
import { LoginPage } from './pages/LoginPage'
import { EmptyState } from './components/EmptyState'

// 任意请求返回登录失效时，刷新登录态，AppShell 会跳转到登录页
const onError = (err: unknown) => {
  if (err instanceof ApiError && (err.code === 'login_expired' || err.code === 'not_logged_in')) {
    queryClient.invalidateQueries({ queryKey: keys.auth })
  }
}

const queryClient = new QueryClient({
  queryCache: new QueryCache({ onError }),
  mutationCache: new MutationCache({ onError }),
  defaultOptions: {
    queries: { refetchOnWindowFocus: false, retry: 1 },
  },
})

const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  {
    element: <AppShell />,
    children: [
      { path: '/', element: <FeedPage key="for_you" type="for_you" /> },
      { path: '/following', element: <FeedPage key="following" type="following" /> },
      { path: '/trending', element: <FeedPage key="hot" type="hot" /> },
      { path: '/explore', element: <FeedPage key="explore" type="explore" /> },
      { path: '/search', element: <SearchPage /> },
      { path: '/history', element: <WatchHistoryPage /> },
      { path: '/history/recommendation', element: <RecommendationHistoryPage /> },
      { path: '/favorites', element: <FavoritesPage /> },
      { path: '/watch-later', element: <WatchLaterPage /> },
      { path: '/feedback/not-interested', element: <NotInterestedPage /> },
      { path: '/profile/interests', element: <InterestsPage /> },
      { path: '/settings/filters', element: <FiltersPage /> },
      { path: '/settings', element: <SettingsPage /> },
      { path: '*', element: <EmptyState title="页面不存在" /> },
    ],
  },
])

function App() {
  useThemeEffect()
  return <RouterProvider router={router} />
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
