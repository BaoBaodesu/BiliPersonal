import { create } from 'zustand'
import { persist } from 'zustand/middleware'

// 只保存少量全局 UI 状态；服务端数据全部交给 TanStack Query
export type Theme = 'system' | 'light' | 'dark'

interface UiState {
  theme: Theme
  sidebarExpanded: boolean
  showRating: boolean
  debugPanel: boolean
  mobileSidebar: boolean
  setTheme: (theme: Theme) => void
  toggleSidebar: () => void
  setMobileSidebar: (open: boolean) => void
  setShowRating: (v: boolean) => void
  setDebugPanel: (v: boolean) => void
}

export const useUi = create<UiState>()(
  persist(
    (set) => ({
      theme: 'system',
      sidebarExpanded: true,
      showRating: false,
      debugPanel: false,
      mobileSidebar: false,
      setTheme: (theme) => set({ theme }),
      toggleSidebar: () => set((s) => ({ sidebarExpanded: !s.sidebarExpanded })),
      setMobileSidebar: (mobileSidebar) => set({ mobileSidebar }),
      setShowRating: (showRating) => set({ showRating }),
      setDebugPanel: (debugPanel) => set({ debugPanel }),
    }),
    {
      name: 'bili-ui',
      partialize: (s) => ({
        theme: s.theme,
        sidebarExpanded: s.sidebarExpanded,
        showRating: s.showRating,
        debugPanel: s.debugPanel,
      }),
    },
  ),
)

interface ToastState {
  toast: { id: number; message: string; action?: { label: string; onClick: () => void } } | null
  show: (message: string, action?: { label: string; onClick: () => void }) => void
  hide: () => void
}

export const useToast = create<ToastState>((set) => ({
  toast: null,
  show: (message, action) => set({ toast: { id: Date.now(), message, action } }),
  hide: () => set({ toast: null }),
}))
