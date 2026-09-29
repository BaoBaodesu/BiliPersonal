import { useEffect, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import type { Category } from '../types'

interface Props {
  categories: Category[]
  value: string
  onChange: (id: string) => void
}

// 顶部分类标签：单行横向滚动，当前选择高亮
export function CategoryChips({ categories, value, onChange }: Props) {
  const scroller = useRef<HTMLDivElement>(null)
  const [edges, setEdges] = useState({ left: false, right: false })

  const update = () => {
    const el = scroller.current
    if (!el) return
    setEdges({ left: el.scrollLeft > 4, right: el.scrollLeft + el.clientWidth < el.scrollWidth - 4 })
  }

  useEffect(() => {
    update()
    window.addEventListener('resize', update)
    return () => window.removeEventListener('resize', update)
  }, [categories])

  const scroll = (dir: number) => scroller.current?.scrollBy({ left: dir * 300, behavior: 'smooth' })

  return (
    <div className="relative">
      {edges.left && (
        <div className="absolute inset-y-0 left-0 z-10 flex items-center bg-gradient-to-r from-bg from-60% to-transparent pr-6">
          <button aria-label="向左滚动" onClick={() => scroll(-1)} className="rounded-full p-1.5 hover:bg-surface-hover">
            <ChevronLeft size={22} />
          </button>
        </div>
      )}
      <div ref={scroller} onScroll={update} role="tablist" className="no-scrollbar flex gap-3 overflow-x-auto">
        {categories.map((c) => {
          const active = c.id === value
          return (
            <button
              key={c.id}
              role="tab"
              aria-selected={active}
              onClick={() => onChange(c.id)}
              className={`h-8 shrink-0 rounded-lg px-3 text-sm font-medium whitespace-nowrap transition-colors ${
                active ? 'bg-active text-on-active' : 'bg-surface text-fg hover:bg-surface-hover'
              }`}
            >
              {c.name}
            </button>
          )
        })}
      </div>
      {edges.right && (
        <div className="absolute inset-y-0 right-0 z-10 flex items-center bg-gradient-to-l from-bg from-60% to-transparent pl-6">
          <button aria-label="向右滚动" onClick={() => scroll(1)} className="rounded-full p-1.5 hover:bg-surface-hover">
            <ChevronRight size={22} />
          </button>
        </div>
      )}
    </div>
  )
}
