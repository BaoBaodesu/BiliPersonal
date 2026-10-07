import type {
  AuthStatus,
  ReviewCandidate, ReviewList, PolicyReview, BlindBatch, BlindItem, ReviewReport,
  InterestSettings, PolicySettings, PolicyTrial, EntityDictionary, SearchResult, FeedReadiness,
  Category,
  DebugInfo,
  Explain,
  FeedbackAction,
  FeedbackReason,
  SourceSettings,
  SourceStatus,
  FeedbackRecord,
  FeedPage,
  FeedType,
  FilterAction,
  FilterImportResult,
  FilterMatchMode,
  FilterRule,
  FilterSummary,
  FilterTarget,
  Interests,
  RecommendationRecord,
  SystemStatus,
  UpRule,
  UserProfile,
  Video,
} from '../types'

// 统一 API 客户端：只与本地 Flask 通信，前端永远拿不到 Cookie
export class ApiError extends Error {
  status: number
  code: string
  retryCursor?: string
  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

const BASE = '/api/v1'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(BASE + path, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    })
  } catch {
    throw new ApiError(0, 'offline', '无法连接本地服务')
  }
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    if (data.error === 'trial_locked' && init?.method && typeof window !== 'undefined') {
      if (window.confirm('这次修改会结束当前策略试用。是否恢复试用前状态，再应用本次修改？取消将继续当前试用。')) {
        await request('/system/policy-trial', { method: 'POST', body: JSON.stringify({ action: 'stop' }) })
        window.dispatchEvent(new Event('policy-trial-changed'))
        return request<T>(path, init)
      }
    }
    const error = new ApiError(res.status, data.error || 'error', data.message || `请求失败 ${res.status}`)
    error.retryCursor = data.retry_cursor ?? undefined
    throw error
  }
  return data as T
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) })
const del = <T>(path: string) => request<T>(path, { method: 'DELETE' })

export const api = {
  reviews: {
    list: () => request<ReviewList>('/system/policy-reviews'),
    create: (candidate: ReviewCandidate, request_id: string) => post<PolicyReview>('/system/policy-reviews', { candidate, request_id }),
    detail: (id: string) => request<PolicyReview>(`/system/policy-reviews/${id}`),
    prepare: (id: string) => post<PolicyReview>(`/system/policy-reviews/${id}/prepare`),
    abort: (id: string, reason: string) => post<PolicyReview>(`/system/policy-reviews/${id}/abort`, { reason }),
    batch: (id: string, batch: string) => request<BlindBatch>(`/system/policy-reviews/${id}/batches/${batch}`),
    rate: (id: string, batch: string, item: string, score: number, revision: number, request_id: string) => request<Pick<BlindItem, 'blind_item_id' | 'score' | 'revision'>>(`/system/policy-reviews/${id}/batches/${batch}/items/${item}/rating`, { method: 'PUT', body: JSON.stringify({ score, revision, request_id }) }),
    submit: (id: string, batch: string) => post<PolicyReview>(`/system/policy-reviews/${id}/batches/${batch}/submit`),
    report: (id: string) => request<ReviewReport>(`/system/policy-reviews/${id}/report`),
    trial: (id: string, request_id: string, confirm_restore: boolean) => post<PolicyTrial>(`/system/policy-reviews/${id}/trial`, { request_id, confirm_restore }),
  },
  auth: {
    status: () => request<AuthStatus>('/auth/status'),
    qrcode: () => post<{ qrcode_key: string; image: string }>('/auth/qrcode'),
    qrcodeStatus: (key: string) =>
      request<{ status: 'waiting' | 'scanned' | 'expired' | 'success' | 'error' }>(
        `/auth/qrcode/status?qrcode_key=${encodeURIComponent(key)}`,
      ),
    logout: () => post<{ success: boolean }>('/auth/logout'),
  },
  feed: {
    readiness: (streamId: string) => request<FeedReadiness>(`/feed/readiness?stream_id=${encodeURIComponent(streamId)}`),
    get: (type: FeedType, category: string, cursor?: string | null, limit = 12, view_id?: string, signal?: AbortSignal) => {
      const q = new URLSearchParams({ type, category, limit: String(limit) })
      if (cursor) q.set('cursor', cursor)
      if (view_id && !cursor) q.set('view_id', view_id)
      return request<FeedPage>(`/feed?${q}`, { signal })
    },
    refresh: (type: FeedType, category: string, limit = 12, view_id: string = crypto.randomUUID()) =>
      post<FeedPage>('/feed/refresh', { type, category, limit, view_id }),
    categories: () => request<{ items: Category[] }>('/feed/categories'),
    explain: (bvid: string, recommendationId?: number) => request<Explain>(`/feed/explain/${bvid}${recommendationId ? `?recommendation_id=${recommendationId}` : ""}`),
    history: (offset = 0, type?: string) =>
      request<{ items: RecommendationRecord[]; total: number }>(
        `/feed/history?limit=50&offset=${offset}${type ? `&type=${type}` : ''}`,
      ),
  },
  search: (q: string, page: number | string = 1, signal?: AbortSignal, entityId?: string) =>
    request<SearchResult>(
      `/search?q=${encodeURIComponent(q)}&${typeof page === 'string' ? `cursor=${encodeURIComponent(page)}` : `page=${page}`}${entityId ? `&entity_id=${encodeURIComponent(entityId)}` : ''}`, { signal },
    ),
  feedback: {
    send: (bvid: string, action: FeedbackAction, video?: Partial<Video>, event_id = crypto.randomUUID(), reason?: FeedbackReason) =>
      post<{ ok?: boolean; undone?: boolean }>('/feedback', { bvid, action, video, event_id, reason,
        recommendation_id: video?.recommendation_id, view_id: video?.view_id,
        exposure_id: video?.recommendation_id && video.view_id ? `${video.view_id}:${video.recommendation_id}` : undefined }),
    history: (action?: string) =>
      request<{ items: FeedbackRecord[] }>(`/feedback/history${action ? `?action=${action}` : ''}`),
    remove: (id: number) => del<{ success: boolean }>(`/feedback/${id}`),
  },
  filters: {
    get: () => request<{ keywords: FilterRule[]; ups: UpRule[] }>('/filters'),
    addKeyword: (keyword: string) => post<FilterRule>('/filters/keywords', { keyword }),
    toggleKeyword: (id: number, enabled: boolean) =>
      request<{ success: boolean }>(`/filters/keywords/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ enabled }),
      }),
    deleteKeyword: (id: number) => del<{ success: boolean }>(`/filters/keywords/${id}`),
    addUp: (name: string, mid?: number | null) => post<UpRule>('/filters/ups', { name, mid }),
    deleteUp: (id: number) => del<{ success: boolean }>(`/filters/ups/${id}`),
    // v0.2.1 统一过滤规则
    rules: (params: {
      target_type?: FilterTarget
      enabled?: boolean
      q?: string
    } = {}) => {
      const q = new URLSearchParams()
      if (params.target_type) q.set('target_type', params.target_type)
      if (params.enabled !== undefined) q.set('enabled', String(params.enabled))
      if (params.q) q.set('q', params.q)
      const qs = q.toString()
      return request<{ items: FilterRule[]; summary: FilterSummary }>(
        `/filters/rules${qs ? `?${qs}` : ''}`,
      )
    },
    createRule: (body: {
      keyword: string
      target_type?: FilterTarget
      match_mode?: FilterMatchMode
      action?: FilterAction
      enabled?: boolean
    }) => post<FilterRule & { created: boolean }>('/filters/rules', body),
    updateRule: (
      id: number,
      body: { enabled?: boolean; action?: FilterAction; match_mode?: FilterMatchMode; keyword?: string },
    ) =>
      request<FilterRule>(`/filters/rules/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      }),
    deleteRule: (id: number) => del<{ success: boolean }>(`/filters/rules/${id}`),
    importRules: (body: { text: string; target_type?: FilterTarget; action?: FilterAction }) =>
      post<FilterImportResult>('/filters/rules/import', body),
    summary: () => request<FilterSummary>('/filters/summary'),
  },
  user: {
    entities: () => request<EntityDictionary>('/user/entities'),
    setEntities: (value: EntityDictionary) => request<EntityDictionary>('/user/entities', { method: 'PUT', body: JSON.stringify(value) }),
    videoPreferences: () => request<Record<string, { purpose: string; allow_replay: boolean; replay_days: number }>>('/user/video-preferences'),
    interestSettings: () => request<InterestSettings>('/user/interest-settings'),
    setInterestSettings: (value: Partial<InterestSettings>) => request<InterestSettings>('/user/interest-settings', { method: 'PUT', body: JSON.stringify(value) }),
    setVideoPreferences: (bvid: string, value: { purpose?: string; allow_replay?: boolean; replay_days?: number }) => request<{ purpose: string; allow_replay: boolean; replay_days: number }>(`/user/video-preferences/${bvid}`, { method: 'PUT', body: JSON.stringify(value) }),
    recordSearch: (query: string, event_id: string) => post('/user/search-history', { query, event_id }),
    searchHistory: () => request<{ enabled: boolean; items: { query: string; event_id: string; at: number }[] }>('/user/search-history'),
    deleteSearch: (eventId?: string) => del(`/user/search-history${eventId ? `?event_id=${encodeURIComponent(eventId)}` : ''}`),
    profile: () => request<UserProfile>('/user/profile'),
    interests: (days = 7) => request<Interests>(`/user/interests?days=${days}`),
    history: (cursor?: { max: number; view_at: number }) =>
      request<{ items: Video[]; cursor: { max: number; view_at: number }; has_more: boolean }>(
        `/user/history${cursor ? `?max=${cursor.max}&view_at=${cursor.view_at}` : ''}`,
      ),
    favorites: (mediaId?: number, pn = 1) =>
      request<{
        folders: { id: number; title: string; count: number }[]
        media_id: number
        items: Video[]
        has_more: boolean
      }>(`/user/favorites?pn=${pn}${mediaId ? `&media_id=${mediaId}` : ''}`),
    watchLater: () => request<{ items: (Video & { feedback_id: number; saved_at: number })[] }>('/user/watch-later'),
  },
  system: {
    recommendationSettings: () => request<PolicySettings>('/system/recommendation-settings'),
    setRecommendationSettings: (value: Partial<PolicySettings>) => request<PolicySettings>('/system/recommendation-settings', { method: 'PUT', body: JSON.stringify(value) }),
    policyTrial: () => request<PolicyTrial>('/system/policy-trial'),
    setPolicyTrial: (action: string) => post<PolicyTrial>('/system/policy-trial', { action }),
    sources: () => request<SourceStatus>('/system/sources'),
    setSources: (settings: Partial<SourceSettings>) => request<SourceStatus>('/system/sources', { method: 'PUT', body: JSON.stringify(settings) }),
    status: () => request<SystemStatus>('/system/status'),
    retrain: () => post<{ started: boolean }>('/system/retrain'),
    debug: () => request<DebugInfo>('/system/debug'),
  },
}
