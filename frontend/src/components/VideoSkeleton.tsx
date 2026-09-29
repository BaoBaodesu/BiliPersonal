import { GRID } from './VideoGrid'

export function VideoSkeleton() {
  return (
    <div aria-hidden className="flex flex-col">
      <div className="skeleton aspect-video rounded-xl" />
      <div className="mt-3 flex gap-3">
        <div className="skeleton h-9 w-9 shrink-0 rounded-full" />
        <div className="flex-1 space-y-2">
          <div className="skeleton h-4 w-[90%] rounded" />
          <div className="skeleton h-4 w-[60%] rounded" />
          <div className="skeleton h-3 w-[40%] rounded" />
        </div>
      </div>
    </div>
  )
}

export function SkeletonCards({ count = 12 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <VideoSkeleton key={i} />
      ))}
    </>
  )
}

export function SkeletonGrid({ count = 12 }: { count?: number }) {
  return (
    <div className={GRID} role="status" aria-label="加载中">
      <SkeletonCards count={count} />
    </div>
  )
}
