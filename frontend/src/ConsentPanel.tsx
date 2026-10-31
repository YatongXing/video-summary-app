import { useEffect, useState } from 'react'
type Consent = { id: string; purpose: string; access_scope: string; granted_at: string; revoked_at: string | null }
export default function ConsentPanel({ taskId, csrfToken, onExpired }: { taskId: string; csrfToken: string; onExpired: () => void }) {
  const [rows, setRows] = useState<Consent[]>([]), [purpose, setPurpose] = useState(''), [scope, setScope] = useState('summary')
  const [refresh, setRefresh] = useState(0), [busy, setBusy] = useState(false), [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    void fetch(`/api/tasks/${taskId}/consents`, { credentials: 'same-origin', cache: 'no-store', signal: controller.signal }).then(async response => {
      if (response.status === 401) { onExpired(); return }
      if (!response.ok) throw new Error()
      const value = await response.json() as Consent[]
      if (!Array.isArray(value)) throw new Error()
      if (!controller.signal.aborted) setRows(value)
    }).catch(() => { if (!controller.signal.aborted) setError('无法读取授权记录。') })
    return () => controller.abort()
  }, [taskId, refresh, onExpired])
  async function change(id?: string) {
    if (busy) return
    setBusy(true); setError('')
    try {
      const response = await fetch(`/api/tasks/${taskId}/consents${id ? `/${id}` : ''}`, { method: id ? 'DELETE' : 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': csrfToken, 'Content-Type': 'application/json' }, ...(!id ? { body: JSON.stringify({ purpose: purpose.trim(), access_scope: scope }) } : {}) })
      if (response.status === 401) { onExpired(); return }
      if (!response.ok) throw new Error()
      setRefresh(n => n + 1)
    } catch { setError('未能确认授权操作，请刷新后重试。') }
    finally { setBusy(false) }
  }
  return <section className="consent-panel" aria-label="任务级排障授权"><h4>管理员排障授权</h4>
    <p className="field-help">只授权本任务指定用途和内容。撤销后拒绝后续访问；已下载的外部副本无法收回。</p>
    {error && <p role="alert" className="message error">{error}</p>}
    <form onSubmit={e => { e.preventDefault(); void change() }}><label>访问目的<input required maxLength={200} value={purpose} onChange={e => setPurpose(e.target.value)} /></label><label>授权范围<select value={scope} onChange={e => setScope(e.target.value)}><option value="summary">摘要</option><option value="video">原始视频</option></select></label><button className="secondary" disabled={busy || !purpose.trim()} type="submit">明确授权本任务</button></form>
    <ul>{rows.map(row => <li key={row.id}>{row.purpose} · {row.access_scope === 'video' ? '原始视频' : '摘要'} · {row.revoked_at ? '已撤销' : '有效'} {!row.revoked_at && <button type="button" className="secondary" disabled={busy} onClick={() => void change(row.id)}>撤销授权</button>}</li>)}</ul>
  </section>
}
