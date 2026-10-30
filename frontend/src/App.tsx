import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import ImportForm from './ImportForm'
import TaskPanel from './TaskPanel'

type Health = 'checking' | 'healthy' | 'unavailable'
type Mode = 'login' | 'register'
type Session = { user: { id: string; email: string; role: string }; csrf_token: string }

function isSession(value: unknown): value is Session {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<Session>
  return Boolean(candidate.user && typeof candidate.user.id === 'string'
    && typeof candidate.user.email === 'string' && typeof candidate.user.role === 'string'
    && typeof candidate.csrf_token === 'string' && candidate.csrf_token)
}

function requestError(status: number, mode: Mode): string {
  if (status === 429) return '尝试过于频繁，请稍后再试。'
  if (status === 401) return '邮箱或密码不正确，或账户当前不可用。'
  if (status === 400 || status === 422) return '请检查邮箱和密码格式后重试。'
  if (status === 409 && mode === 'register') return '无法使用此邮箱注册，请尝试登录或使用其他邮箱。'
  return '暂时无法完成操作，请稍后重试。'
}

export default function App() {
  const [health, setHealth] = useState<Health>('checking')
  const [mode, setMode] = useState<Mode>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [session, setSession] = useState<Session | null>(null)
  const [checkingSession, setCheckingSession] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [taskRefresh, setTaskRefresh] = useState(0)
  const expireSession = useCallback(() => { setSession(null); setError('登录已失效，请重新登录。') }, [])
  const importedVideo = useCallback(() => setTaskRefresh(value => value + 1), [])

  const checkHealth = useCallback(async () => {
    setHealth('checking')
    try {
      const response = await fetch('/api/health/live', { cache: 'no-store', credentials: 'same-origin' })
      setHealth(response.ok ? 'healthy' : 'unavailable')
    } catch {
      setHealth('unavailable')
    }
  }, [])

  const checkSession = useCallback(async () => {
    try {
      const response = await fetch('/api/auth/me', { cache: 'no-store', credentials: 'same-origin' })
      if (response.status === 401) {
        setSession(null)
        return
      }
      if (!response.ok) throw new Error('Session unavailable')
      const result: unknown = await response.json()
      if (!isSession(result)) throw new Error('Invalid session response')
      setSession(result)
    } catch {
      setSession(null)
      setError('暂时无法确认登录状态，请检查服务连接后重试。')
    } finally {
      setCheckingSession(false)
    }
  }, [])

  useEffect(() => {
    void checkHealth()
    void checkSession()
    const onFocus = () => void checkSession()
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [checkHealth, checkSession])

  function changeMode(next: Mode) {
    setMode(next)
    setPassword('')
    setError('')
    setNotice('')
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const response = await fetch(`/api/auth/${mode}`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim(), password }),
      })
      if (!response.ok) {
        setError(requestError(response.status, mode))
        if (response.status === 401) setSession(null)
        return
      }
      if (mode === 'register') {
        setMode('login')
        setNotice('注册成功。请使用刚才的邮箱和密码登录。')
      } else {
        const result: unknown = await response.json()
        if (!isSession(result)) throw new Error('Invalid session response')
        setSession(result)
      }
    } catch {
      setError('无法连接服务，请确认本地服务已启动后重试。')
    } finally {
      setPassword('')
      setBusy(false)
    }
  }

  async function logout() {
    if (!session || busy) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const response = await fetch('/api/auth/logout', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRF-Token': session.csrf_token },
      })
      if (!response.ok && response.status !== 401) {
        setError('暂时无法退出，请重试。')
        return
      }
      setSession(null)
      setPassword('')
      setNotice('已退出登录。')
    } catch {
      setError('无法连接服务，尚未确认退出，请重试。')
    } finally {
      setBusy(false)
    }
  }

  const healthLabel = {
    checking: '正在检查连接',
    healthy: '本地服务已连接',
    unavailable: '本地服务暂不可用',
  }[health]

  return (
    <main className="page">
      <div className="workspace">
        <header className="brand"><span className="brand-mark" aria-hidden="true">V</span><span>Video Summary App</span></header>
        <section className="panel" aria-labelledby="app-title">
          <span className="eyebrow">你的视频，清晰整理</span>
          <h1 id="app-title">{session ? '欢迎回来' : '开启你的私有空间'}</h1>
          <p className="intro">{session ? '账户已登录。导入视频并完成校验，准备后续内容总结。' : '使用邮箱和密码登录，管理属于你的视频与处理结果。'}</p>
          {error && <p className="message error" role="alert">{error}</p>}
          {notice && <p className="message notice" role="status">{notice}</p>}
          {checkingSession ? <p role="status" className="checking">正在确认登录状态…</p> : session ? (
            <>
            <div className="account">
              <div className="account-avatar" aria-hidden="true">{session.user.email.slice(0, 1).toUpperCase()}</div>
              <div className="account-details"><strong>{session.user.email}</strong><span>{session.user.role === 'ADMIN' ? '管理员账户' : '个人账户'}</span></div>
              <button type="button" className="secondary" onClick={() => void logout()} disabled={busy}>{busy ? '正在退出…' : '退出登录'}</button>
            </div>
            <ImportForm key={session.user.id} csrfToken={session.csrf_token} onExpired={expireSession} onImported={importedVideo} />
            <TaskPanel key={`tasks-${session.user.id}`} csrfToken={session.csrf_token} refreshVersion={taskRefresh} onExpired={expireSession} />
            </>
          ) : (
            <>
              <div className="mode-switch" aria-label="账户操作">
                <button type="button" aria-pressed={mode === 'login'} onClick={() => changeMode('login')} disabled={busy}>登录</button>
                <button type="button" aria-pressed={mode === 'register'} onClick={() => changeMode('register')} disabled={busy}>注册账户</button>
              </div>
              <form onSubmit={(event) => void submit(event)} aria-busy={busy}>
                <div className="field"><label htmlFor="email">邮箱</label><input id="email" name="email" type="email" autoComplete="username" placeholder="you@example.com" required maxLength={254} value={email} onChange={(event) => setEmail(event.target.value)} disabled={busy} /></div>
                <div className="field"><label htmlFor="password">密码</label><input id="password" name="password" type="password" autoComplete={mode === 'register' ? 'new-password' : 'current-password'} required maxLength={128} value={password} onChange={(event) => setPassword(event.target.value)} disabled={busy} /></div>
                <button className="primary" type="submit" disabled={busy}>{busy ? '正在处理…' : mode === 'register' ? '创建个人账户' : '登录'}</button>
              </form>
              <p className="account-note">{mode === 'register' ? '注册后即可使用邮箱和密码登录。' : '每次重新登录都需要输入密码。'}</p>
            </>
          )}
          <footer className="status-row"><span className={`status-dot ${health}`} aria-hidden="true" /><span role="status">{healthLabel}</span><button className="text-button" type="button" onClick={() => { void checkHealth(); void checkSession() }} disabled={health === 'checking' || busy}>重新检查</button></footer>
        </section>
        <p className="footer-note">本地工作空间 · 账户访问受保护</p>
      </div>
    </main>
  )
}
