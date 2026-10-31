import { useEffect, useState } from 'react'
type Receipt = { id: string; task_id: string; user_id: string; status: string; started_at: string; attempt_count: number; online_complete: boolean; backup_cleanup_state: string; last_error_code: string | null; last_error_kind: string | null; alerts: { id: string; alert_code: string; resolved_at: string | null }[] }
const labels: Record<string, string> = { PENDING: '等待清理', RUNNING: '正在清理', MANUAL_REQUIRED: '需要管理员处理', COMPLETED: '在线清理完成' }
export default function DeletionPanel({ onExpired, refreshVersion = 0, admin = false, csrfToken = '' }: { onExpired: () => void; refreshVersion?: number; admin?: boolean; csrfToken?: string }) {
  const [rows, setRows] = useState<Receipt[]>([]), [refresh, setRefresh] = useState(0), [offset, setOffset] = useState(0)
  const [error, setError] = useState(''), [pending, setPending] = useState<string | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    void fetch(`/api/${admin ? 'admin/cleanup-jobs' : 'deletions'}?limit=20&offset=${offset}`, { credentials: 'same-origin', cache: 'no-store', signal: controller.signal }).then(async response => {
      if (response.status === 401) { onExpired(); return }
      if (!response.ok) throw new Error()
      const value = await response.json() as Receipt[]
      if (!Array.isArray(value)) throw new Error()
      if (!controller.signal.aborted) { setRows(value); setError('') }
    }).catch(() => { if (!controller.signal.aborted) setError('无法刷新清理记录，当前状态可能不是最新。') })
    return () => controller.abort()
  }, [admin, offset, refresh, refreshVersion, onExpired])
  useEffect(() => { const timer = window.setInterval(() => setRefresh(n => n + 1), 10000); return () => window.clearInterval(timer) }, [])
  async function retry(id: string) {
    if (pending) return
    setPending(id); setError('')
    try {
      const response = await fetch(`/api/admin/cleanup-jobs/${id}/retry`, { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': csrfToken } })
      if (response.status === 401) { onExpired(); return }
      if (!response.ok) throw new Error()
      setRefresh(n => n + 1)
    } catch { setError('尚未确认清理推进结果，请刷新记录。') }
    finally { setPending(null) }
  }
  return <section className="deletion-panel" aria-label={admin ? '管理员清理告警' : '删除记录'}><h3>{admin ? '清理告警与人工处理' : '删除记录'}</h3>
    <p className="field-help">删除请求会立即禁止访问。在线清理与备份清理分别确认；等待或失败不代表删除完成。{admin && '“一键强制删除”仅重新推进清理，仍需完成校验并记录审计。'}</p>
    {error && <p className="message error" role="alert">{error}</p>}
    <button className="secondary" type="button" onClick={() => setRefresh(n => n + 1)}>刷新清理记录</button>
    {!rows.length && !error && <p>当前页暂无清理记录。</p>}
    <ul className="task-list">{rows.map(row => <li className="task-card" key={row.id}><strong>{labels[row.status] || '状态待确认'}</strong><p className="identifier">任务 {row.task_id}{admin && ` · 用户 ${row.user_id}`}</p>
      <dl className="metadata"><dt>在线数据</dt><dd>{row.online_complete ? '实际清理完成' : '尚未确认完成'}</dd><dt>备份数据</dt><dd>{row.backup_cleanup_state === 'COMPLETED' ? '已确认清除' : row.backup_cleanup_state === 'NOT_APPLICABLE' ? '无适用的已登记备份' : '尚未确认清除'}</dd><dt>已尝试清理</dt><dd>{row.attempt_count} 次</dd><dt>开始</dt><dd>{new Date(row.started_at).toLocaleString()}</dd></dl>
      {row.last_error_code && <p>清理未完成：{row.last_error_kind || '类别待确认'} · {row.last_error_code}</p>}
      {!!row.alerts?.filter(alert => !alert.resolved_at).length && <p className="message error" role="status">已通知管理员：{row.alerts.filter(alert => !alert.resolved_at).map(alert => alert.alert_code).join('、')}</p>}
      {admin && !row.online_complete && <button className="secondary" type="button" disabled={pending !== null} onClick={() => void retry(row.id)}>{pending === row.id ? '正在提交…' : '一键强制删除'}</button>}
    </li>)}</ul><div className="task-pagination"><button className="secondary" type="button" disabled={offset === 0} onClick={() => setOffset(n => Math.max(0, n - 20))}>上一页</button><span>第 {offset / 20 + 1} 页</span><button className="secondary" type="button" disabled={rows.length < 20} onClick={() => setOffset(n => n + 20)}>下一页</button></div>
  </section>
}
