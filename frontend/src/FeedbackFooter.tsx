import { useEffect, useState } from 'react'

export default function FeedbackFooter() {
  const [email, setEmail] = useState<string | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      try {
        const response = await fetch('/api/config/public', { cache: 'no-store', credentials: 'same-origin', signal: controller.signal })
        if (!response.ok) return
        const value: unknown = await response.json()
        if (controller.signal.aborted || !value || typeof value !== 'object' || !('support_email' in value)) return
        const configured = value.support_email
        if (typeof configured === 'string' && configured.length <= 254 && /^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?\.[a-zA-Z]{2,}$/.test(configured)) setEmail(configured)
      } catch { /* No placeholder contact when configuration is unavailable. */ }
    })()
    return () => controller.abort()
  }, [])
  if (!email) return null
  return <footer className="feedback-footer" aria-label="问题反馈">遇到问题？请将任务 ID 和问题描述发送至：<a href={`mailto:${email}`}>{email}</a>。</footer>
}
