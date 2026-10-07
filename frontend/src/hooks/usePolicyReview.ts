import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { useAuth } from './queries'

export function useReviewVisibility() {
  const [visible, setVisible] = useState(document.visibilityState === 'visible')
  useEffect(() => {
    const update = () => setVisible(document.visibilityState === 'visible')
    document.addEventListener('visibilitychange', update)
    return () => document.removeEventListener('visibilitychange', update)
  }, [])
  return visible
}

export function usePolicyReviews() {
  const auth = useAuth()
  const visible = useReviewVisibility()
  return useQuery({ queryKey: ['policy-reviews', auth.data?.user?.mid], queryFn: api.reviews.list, enabled: visible && auth.data?.logged_in === true, refetchInterval: visible ? 30000 : false })
}
