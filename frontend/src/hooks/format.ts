// 展示用格式化函数

export function formatCount(n?: number | null): string {
  if (n == null) return ''
  if (n >= 1e8) return `${(n / 1e8).toFixed(1).replace(/\.0$/, '')}亿`
  if (n >= 1e4) return `${(n / 1e4).toFixed(1).replace(/\.0$/, '')}万`
  return String(n)
}

export function formatDuration(sec?: number | null): string {
  if (!sec || sec <= 0) return ''
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = Math.floor(sec % 60)
  const pad = (x: number) => String(x).padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`
}

// 后端时间戳有两种形态：Unix 秒（REAL 列）与 SQLite CURRENT_TIMESTAMP 字符串。
// 统一归一化成秒，避免字符串时间戳被当成秒数参与运算产生 NaN。
type Stamp = number | string | null | undefined

function toSeconds(ts: Stamp): number | null {
  if (ts == null || ts === '') return null
  if (typeof ts === 'number') return Number.isFinite(ts) ? ts : null
  // "YYYY-MM-DD HH:MM:SS" 按 UTC 解析（SQLite CURRENT_TIMESTAMP 为 UTC）
  const parsed = Date.parse(ts.includes('T') ? ts : `${ts.replace(' ', 'T')}Z`)
  return Number.isNaN(parsed) ? null : parsed / 1000
}

export function timeAgo(ts: Stamp): string {
  const sec = toSeconds(ts)
  if (sec == null) return ''
  const diff = Date.now() / 1000 - sec
  if (diff < 0) return '刚刚'
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)} 天前`
  if (diff < 86400 * 365) return `${Math.floor(diff / 86400 / 30)} 个月前`
  return `${Math.floor(diff / 86400 / 365)} 年前`
}

export function formatDateTime(ts: Stamp): string {
  const sec = toSeconds(ts)
  if (sec == null) return ''
  const d = new Date(sec * 1000)
  const pad = (x: number) => String(x).padStart(2, '0')
  return `${d.getMonth() + 1}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

// B 站图片支持尺寸参数，请求合适尺寸的 webp，减少流量
export function thumb(url: string, w = 480, h = 270): string {
  if (!url) return ''
  const u = url.replace(/^http:\/\//, 'https://').replace(/^\/\//, 'https://')
  if (!/hdslb\.com|biliimg\.com/.test(u) || u.includes('@')) return u
  return `${u}@${w}w_${h}h_1c.webp`
}

export function videoUrl(bvid: string): string {
  return `https://www.bilibili.com/video/${bvid}`
}
