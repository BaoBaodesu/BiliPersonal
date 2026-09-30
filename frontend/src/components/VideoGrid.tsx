import type { ReactNode } from 'react'
import type { Video } from '../types'
import { VideoCard } from './VideoCard'

export const GRID = 'grid gap-x-4 gap-y-8 grid-cols-[repeat(auto-fill,minmax(270px,1fr))] max-sm:grid-cols-1 sm:max-md:grid-cols-2'

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
