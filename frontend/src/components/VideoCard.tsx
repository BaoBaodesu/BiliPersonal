import { useState } from 'react'
import { ImageOff, UserRound } from 'lucide-react'
import type { Video } from '../types'
import { formatCount, formatDuration, thumb, timeAgo, videoUrl } from '../hooks/format'
import { useUi } from '../stores/ui'
import { recordClick, useExposure } from '../hooks/useExposure'
import { VideoCardMenu } from './VideoCardMenu'

interface Props {
  video: Video
  inFeed?: boolean
  // 历史 / 收藏等页面显示的副信息
  meta?: string
  menu?: boolean
}

const SOURCE_LABEL: Record<string, string> = { hot: '热门', rcmd: '推荐流' }

export function VideoCard({ video, inFeed = true, meta, menu = true }: Props) {
  const exposure = useExposure(video, inFeed)
  const showRating = useUi((s) => s.showRating)
  const [imgFailed, setImgFailed] = useState(0)
  const [faceFailed, setFaceFailed] = useState(false)
  const href = videoUrl(video.bvid)
  // 防盗链兜底：直链失败时走后端图片代理
  const src = imgFailed ? `/api/v1/img?url=${encodeURIComponent(thumb(video.pic))}` : thumb(video.pic)
  const progress = video.progress && video.duration ? Math.min(1, Math.max(0, video.progress / video.duration)) : 0

  const onClick = () => {
    if (inFeed) recordClick(video)
  }

  return (
    <article ref={exposure} className="group flex flex-col">
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        onClick={onClick}
        onAuxClick={(e) => { if (e.button === 1) onClick() }}
        aria-label={`观看：${video.title}`}
        className="relative block aspect-video overflow-hidden rounded-xl bg-skeleton outline-offset-2"
      >
        {video.pic && imgFailed < 2 ? (
          <img
            src={src}
            alt=""
            loading="lazy"
            referrerPolicy="no-referrer"
            onError={() => setImgFailed((failed) => Math.min(2, failed + 1))}
            className="h-full w-full object-cover transition duration-200 group-hover:scale-[1.02]"
          />
        ) : (
          <span className="flex h-full items-center justify-center gap-2 text-sm text-muted">
            <ImageOff size={24} /> 封面暂不可用
          </span>
        )}
        {!!video.duration && (
          <span className="absolute right-1.5 bottom-1.5 rounded bg-black/80 px-1 py-px text-xs font-medium text-white">
            {formatDuration(video.duration)}
          </span>
        )}
        {progress > 0 && (
          <span className="absolute inset-x-0 bottom-0 h-1 bg-white/40">
            <span className="block h-full bg-accent" style={{ width: `${progress * 100}%` }} />
          </span>
        )}
      </a>
      <div className="mt-3 flex gap-3">
        <div aria-hidden="true" className="flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-full bg-surface text-muted">
        {video.face && !faceFailed ? (
          <img
            src={thumb(video.face, 72, 72)}
            alt=""
            loading="lazy"
            referrerPolicy="no-referrer"
            onError={() => setFaceFailed(true)}
            className="h-9 w-9 shrink-0 rounded-full bg-skeleton object-cover"
          />
        ) : <UserRound size={20} />}
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="line-clamp-2 min-h-[42px] text-[15px] leading-[1.4] font-medium text-fg">
            <a href={href} target="_blank" rel="noreferrer" onClick={onClick} onAuxClick={(e) => { if (e.button === 1) onClick() }} title={video.title}>
              {video.title}
            </a>
          </h2>
          <div className="mt-1 truncate text-sm text-muted">{video.author}</div>
          <div className="truncate text-sm text-muted">
            {meta ??
              [video.view ? `${formatCount(video.view)}次观看` : '', timeAgo(video.pubdate)].filter(Boolean).join(' · ')}
          </div>
          {showRating && inFeed && (
            <div className="mt-1 flex flex-wrap gap-x-2 font-mono text-[11px] text-subtle">
              <span>#{video.rank ?? '-'}</span>
              <span>RT {video.rating != null ? video.rating.toFixed(4) : '未评分'}</span>
              <span>{SOURCE_LABEL[video.source || ''] || video.source}</span>
              {!!video.matched_tags?.length && <span className="truncate">[{video.matched_tags.join(', ')}]</span>}
            </div>
          )}
        </div>
        {menu && <VideoCardMenu video={video} inFeed={inFeed} />}
      </div>
    </article>
  )
}
