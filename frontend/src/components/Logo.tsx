import { Link } from 'react-router-dom'

export function Logo() {
  return (
    <Link to="/" viewTransition aria-label="BiliPersonal 首页" className="flex items-center gap-1.5 px-1 sm:px-2">
      <svg viewBox="0 0 32 24" className="h-6 w-8" aria-hidden>
        <rect width="32" height="24" rx="6" fill="var(--accent)" />
        <path d="M13 7.5v9l7.5-4.5z" fill="#fff" />
      </svg>
      <span className="text-lg font-semibold tracking-tight">
        Bili<span className="text-muted">Personal</span>
      </span>
    </Link>
  )
}
