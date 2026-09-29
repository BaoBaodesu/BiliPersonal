import { useEffect, useRef } from 'react'

// 距离底部 800px 时触发加载下一页
export function LoadMoreTrigger({ onLoad, disabled }: { onLoad: () => void; disabled?: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  const cb = useRef(onLoad)
  cb.current = onLoad
  useEffect(() => {
    if (disabled || !ref.current) return
    const io = new IntersectionObserver((entries) => entries[0].isIntersecting && cb.current(), {
      rootMargin: '800px 0px',
    })
    io.observe(ref.current)
    return () => io.disconnect()
  }, [disabled])
  return <div ref={ref} className="h-px" />
}
