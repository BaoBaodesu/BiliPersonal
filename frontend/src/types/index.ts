export type FeedType = 'for_you' | 'hot' | 'explore' | 'following'

export interface Video {
  recommendation_id?: number
  view_id?: string
  model_version?: string
  bvid: string
  title: string
  pic: string
  author: string
  mid?: number | null
  face?: string
  view?: number
  like?: number
  duration?: number
  pubdate?: number
  tname?: string
  tags?: string[]
  source?: string
  rcmd_reason?: string
  source_reason?: string
  seed_bvid?: string
  seed_title?: string
  rating?: number | null
  matched_tags?: string[]
  rank?: number
  progress?: number
  view_at?: number
}

export interface FeedPage {
  view_id?: string
  model_version?: string
  items: Video[]
  next_cursor: string | null
  has_more: boolean
  stream_id: string
  generated_at: number
  from_cache: boolean
  ranked: boolean
  model: string
  notice: 'rate_limited' | 'accumulating' | 'following_stale' | null
  following_freshness?: { status: 'checked' | 'stale'; checked_at: number | null; reason?: string }
}

export interface Category {
  id: string
  name: string
}

export interface UserProfile {
  mid: number
  name: string
  face: string
  level: number
  vip: boolean
}

export interface AuthStatus {
  logged_in: boolean
  expired?: boolean
  user?: UserProfile
}

export interface SystemStatus {
  model: 'ready' | 'none' | 'collecting' | 'training' | 'error'
  training: boolean
  stage: string
  progress: { epoch: number; total: number; loss?: number }
  error: string | null
  model_version: string | null
  trained_at: number | null
  history_samples: number
  history_updated_at: number | null
  summary: {
    samples: number
    positives: number
    num_tags: number
    num_authors: number
    best_loss: number
    final_metrics: Record<string, number>
  } | null
  feed_cache: boolean
  pool_size: number
  rate_limited: boolean
  logged_in: boolean
}

export type FeedbackAction = 'like' | 'not_interested' | 'block_up' | 'watched' | 'watch_later' | 'click' | 'undo'
export type FeedbackReason = 'uploader' | 'topic' | 'clickbait'
export type SourceLevel = 'off' | 'fallback' | 'small' | 'standard'
export interface SourceSettings { hot: SourceLevel; rcmd: SourceLevel; classic: boolean }
export interface SourceStatus {
  settings: SourceSettings
  report: { days: number; total: number; sources: { source: string; served: number; exposed: number; share: number; click_rate: number | null; not_interested_rate: number | null }[] }
}

export interface Explain {
  bvid: string
  matched_tags: string[]
  related_history: { bvid: string; title: string; shared_tags: string[]; same_author: boolean }[]
  same_author: boolean
  source: string
  rcmd_reason: string
  source_reason?: string
  seed_video?: { bvid: string; title: string } | null
  up_affinity?: { level: 'regular' | 'familiar' | 'stranger'; watched_count: number; following: boolean } | null
}

export interface KeywordRule {
  id: number
  keyword: string
  enabled: boolean
  created_at: number
}

export type FilterTarget = 'title' | 'uploader' | 'tag' | 'zone'
export type FilterMatchMode = 'contains' | 'exact' | 'regex'
export type FilterAction = 'hard_block' | 'downrank'

// v0.2.1 统一过滤规则：标题关键词 / UP 主名称关键词 / 未来的 tag 规则
export interface FilterRule {
  id: number
  target_type: FilterTarget
  keyword: string
  keyword_norm: string
  match_mode: FilterMatchMode
  action: FilterAction
  enabled: boolean
  source: string
  hit_count: number
  last_hit_at: string | null
  created_at: string | number
  updated_at: string | number
}

export interface FilterTargetSummary {
  target_type: FilterTarget
  total: number
  enabled: number
  hard_block: number
  imported: number
  hits: number | null
}

export interface FilterSummary {
  version: number
  by_target: FilterTargetSummary[]
}

export interface FilterImportResult {
  input: number
  inserted: number
  duplicate: number
  invalid: number
  total: number
}

export interface UpRule {
  id: number
  mid: number | null
  name: string
  created_at: number
}

export interface RecommendationRecord {
  id: number
  bvid: string
  feed_type: FeedType
  title: string
  author: string
  pic: string
  rating: number | null
  rank: number
  model_version: string | null
  served_at: number
  clicked: number
  feedback: string | null
}

export interface FeedbackRecord {
  id: number
  bvid: string
  action: FeedbackAction
  video: Partial<Video>
  created_at: number
}

export interface WeightedItem {
  name: string
  weight: number
  score: number
}

export interface Interests {
  samples: number
  favorites: number
  top_tags: WeightedItem[]
  recent_tags: WeightedItem[]
  top_ups: WeightedItem[]
  zones: WeightedItem[]
  durations: { name: string; count: number }[]
}

export interface DebugInfo {
  pool: { hot: number; rcmd: number; follow: number; up_archive: number; related: number; total: number }
  valid_candidates: number
  served: Record<string, number>
  recent_streams: { feed_type: string; category: string; pages: number; last: number }[]
  rate_limit_count: number
  rate_limited_until: number
  recent_errors: { time: number; path: string; code: string | number; message: string }[]
  model: SystemStatus & { history_hash?: string }
}
