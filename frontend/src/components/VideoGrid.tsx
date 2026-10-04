import type { ReactNode } from 'react'
import type { Video } from '../types'
import { VideoCard } from './VideoCard'

// 配合 2200px 内容容器最多显示六列，常用桌面宽度显示四至五列。
export const GRID = 'grid gap-x-4 gap-y-8 grid-cols-[repeat(auto-fill,minmax(320px,1fr))] max-sm:grid-cols-1 sm:max-md:grid-cols-2'

export function VideoGrid({ videos, inFeed = true, children }: { videos: Video[]; inFeed?: boolean; children?: ReactNode }) {
  return (
    <div className={GRID}>
      {videos.map((v) => (
        <VideoCard key={v.bvid} video={v} inFeed={inFeed} />
      ))}
      {children}
    </div>
  )
}
