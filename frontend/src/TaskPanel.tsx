import { useCallback, useEffect, useRef, useState } from 'react'
import ResultPanel from './ResultPanel'
import TaskIdentifier from './TaskIdentifier'

type Quota = { credits: number | null; unlimited: boolean; recovery_seconds: number | null }
type Task = {
  id: string; status: string; filename: string | null; submitted_at: string
  started_at: string | null; finished_at: string | null; retry_count: number
  cooldown_seconds: number; retry_allowed: boolean; retry_reason: string | null
  failure_category: string | null; failure_code?: string | null; refund_status: string; api_fee_warning: boolean
  duration_seconds?: number; queue_seconds?: number | null; processing_seconds?: number | null; target_seconds?: number | null
}
const labels: Record<string, string> = { QUEUED: '排队中', PROCESSING: '处理中', SUCCEEDED: '已完成', FAILED: '失败', CANCELLED: '已取消' }
const failures: Record<string, string> = {
  USER_INPUT: '视频输入不符合要求', INTERNAL: '系统内部错误', EXTERNAL_TIMEOUT: '模型服务超时',
  EXTERNAL_RATE_LIMIT: '模型服务限流', EXTERNAL_UNAVAILABLE: '模型服务暂不可用', UNKNOWN: '原因暂未确定', USER_CANCELLED: '用户主动取消',
}
const refunds: Record<string, string> = { NOT_CHARGED: '尚未扣除机会', CHARGED: '已扣除一次机会', REFUNDED: '已按规则返还机会', UNLIMITED: '管理员不限处理次数' }
function duration(value: number) {
  const n = Math.max(0, Math.ceil(value))
  return `${Math.floor(n / 3600)} 小时 ${Math.floor(n % 3600 / 60)} 分 ${n % 60} 秒`
}
function validTask(value: unknown): value is Task {
  if (!value || typeof value !== 'object') return false
  const t = value as Partial<Task>
  return typeof t.id === 'string' && typeof t.status === 'string' && t.status in labels
    && typeof t.retry_count === 'number' && typeof t.retry_allowed === 'boolean'
    && typeof t.cooldown_seconds === 'number' && Number.isFinite(t.cooldown_seconds)
}
function validQuota(value: unknown): value is Quota {
  if (!value || typeof value !== 'object') return false
  const q = value as Partial<Quota>
  return typeof q.unlimited === 'boolean' && (q.credits === null || typeof q.credits === 'number')
    && (q.recovery_seconds === null || typeof q.recovery_seconds === 'number' && Number.isFinite(q.recovery_seconds))
}
export default function TaskPanel({ csrfToken, refreshVersion, onExpired }: {
  csrfToken: string; refreshVersion: number; onExpired: () => void
}) {
  const [selectedTask, setSelectedTask] = useState<string | null>(null)
  const [quota, setQuota] = useState<Quota | null>(null)
  const [tasks, setTasks] = useState<Task[]>([])
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [actionError, setActionError] = useState('')
  const [loading, setLoading] = useState(true)
  const [pending, setPending] = useState<string | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const received = useRef(performance.now())
  const mounted = useRef(true)
  const refreshing = useRef<AbortController | null>(null)
  const version = useRef(0)
  const retryKeys = useRef(new Map<string, string>())
  const mutating = useRef(false)

  const refresh = useCallback(async () => {
    refreshing.current?.abort()
    const controller = new AbortController()
    refreshing.current = controller
    const requestVersion = ++version.current
    try {
      const options = { credentials: 'same-origin' as const, cache: 'no-store' as const, signal: controller.signal }
      const [q, t] = await Promise.all([fetch('/api/quota', options), fetch(`/api/tasks?limit=20&offset=${offset}`, options)])
      if (!mounted.current || requestVersion !== version.current) return
      if (q.status === 401 || t.status === 401) { onExpired(); return }
      if (!q.ok || !t.ok) throw new Error('Unavailable')
      const quotaValue: unknown = await q.json()
      const taskValue: unknown = await t.json()
      if (!validQuota(quotaValue) || !Array.isArray(taskValue) || !taskValue.every(validTask)) throw new Error('Invalid response')
      if (!mounted.current || requestVersion !== version.current) return
      setQuota(quotaValue); setTasks(taskValue); setError('')
      setSelectedTask(current => current && taskValue.some(task => task.id === current && task.status === 'SUCCEEDED') ? current : null)
      received.current = performance.now(); setElapsed(0)
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') return
      if (mounted.current && requestVersion === version.current) setError('暂时无法刷新账户与任务信息，当前显示可能不是最新状态。')
    } finally {
      if (mounted.current && requestVersion === version.current) setLoading(false)
    }
  }, [offset, onExpired])

  useEffect(() => {
    mounted.current = true
    void refresh()
    const interval = window.setInterval(() => void refresh(), 15000)
    const clock = window.setInterval(() => setElapsed((performance.now() - received.current) / 1000), 1000)
    const focus = () => void refresh()
    window.addEventListener('focus', focus)
    return () => { mounted.current = false; refreshing.current?.abort(); window.clearInterval(interval); window.clearInterval(clock); window.removeEventListener('focus', focus) }
  }, [refresh, refreshVersion])

  useEffect(() => setOffset(0), [refreshVersion])

  async function act(task: Task, action: 'cancel' | 'retry') {
    if (mutating.current) return
    mutating.current = true; setPending(task.id); setActionError(''); setNotice('')
    if (action === 'retry' && !retryKeys.current.has(task.id)) retryKeys.current.set(task.id, crypto.randomUUID())
    try {
      const response = await fetch(`/api/tasks/${task.id}/${action}`, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'X-CSRF-Token': csrfToken, ...(action === 'retry' ? { 'Content-Type': 'application/json' } : {}) },
        ...(action === 'retry' ? { body: JSON.stringify({ request_key: retryKeys.current.get(task.id) }) } : {}),
      })
      if (!mounted.current) return
      if (response.status === 401) { onExpired(); return }
      if (response.status < 500 && action === 'retry') retryKeys.current.delete(task.id)
      if (response.ok) {
        setNotice(action === 'retry' ? '重试请求已接受，请查看任务状态和等待时间。' : '取消请求已处理，请以刷新后的任务状态为准。')
      } else if (response.status === 429) {
        setActionError('任务正在等待或已达到重试上限，请查看刷新后的状态。')
      } else if (response.status === 409 || response.status === 404) {
        setActionError('任务状态已经变化或不可访问，请查看刷新后的状态。')
      } else if (response.status === 403) {
        setActionError('无法授权此操作，请刷新登录状态后重试。')
      } else {
        setActionError('尚未确认操作结果。请刷新状态后再试；重试请求将复用原请求标识。')
      }
      // Keep the operation explanation while refreshing authoritative state.
      await refresh()
    } catch {
      if (mounted.current) setActionError('连接中断，尚未确认操作结果。请刷新后再试；重试请求将复用原请求标识。')
    } finally { mutating.current = false; if (mounted.current) setPending(null) }
  }

  async function removeTask(task: Task) {
    if (mutating.current || !window.confirm('删除此任务及其关联资料？访问将立即禁止，实际清理在后台进行。')) return
    mutating.current = true; setPending(task.id); setActionError(''); setNotice('')
    try {
      const response = await fetch(`/api/tasks/${task.id}`, { method: 'DELETE', credentials: 'same-origin', headers: { 'X-CSRF-Token': csrfToken } })
      if (!mounted.current) return
      if (response.status === 401) { onExpired(); return }
      if (response.status !== 202) throw new Error()
      const accepted = await response.json()
      if (!mounted.current) return
      if (accepted.accepted !== true || accepted.task_id !== task.id) throw new Error()
      refreshing.current?.abort(); ++version.current
      setTasks(current => current.filter(row => row.id !== task.id)); setSelectedTask(null)
      setNotice('删除请求已接受，任务已移出列表。实际内容清理在后台进行，尚不能据此确认完成。')
    } catch { if (mounted.current) setActionError('删除请求结果未确认，请刷新任务状态后再试。') }
    finally { mutating.current = false; if (mounted.current) setPending(null) }
  }

  const recovery = quota?.recovery_seconds === null ? null : Math.max(0, (quota?.recovery_seconds ?? 0) - elapsed)
  return (
    <section className="task-section" aria-labelledby="tasks-title">
      <div className="section-heading"><span className="eyebrow">处理机会与任务</span><h2 id="tasks-title">我的视频任务</h2></div>
      {quota && <div className="quota-card"><strong>{quota.unlimited ? '管理员 · 处理次数不限' : `可用处理机会：${quota.credits} / 3`}</strong>{!quota.unlimited && recovery !== null && <span>下次恢复约需 {duration(recovery)}。以服务器结果为准。</span>}<span>导入和排队不扣机会；同一任务重试不额外扣机会。</span></div>}
      {error && <p className="message error" role="alert">{error}</p>}
      {actionError && <p className="message error" role="alert">{actionError}</p>}
      {notice && <p className="message notice" role="status">{notice}</p>}
      <div className="task-toolbar"><span>每页最多 20 个任务，状态定期刷新。</span><button type="button" className="secondary" onClick={() => void refresh()} disabled={pending !== null}>刷新</button></div>
      {loading ? <p role="status">正在读取任务…</p> : tasks.length === 0 ? <p className="empty-tasks">当前页暂无视频任务。</p> : <ul className="task-list">{tasks.map(task => {
        const cooldown = Math.max(0, task.cooldown_seconds - elapsed)
        return <li className="task-card" key={task.id}>
          <div className="task-heading"><strong>{task.filename || '未命名视频'}</strong><span className={`task-state state-${task.status.toLowerCase()}`}>{labels[task.status]}</span></div>
          <TaskIdentifier taskId={task.id} />
          {task.submitted_at && <p className="task-time">提交于 {new Date(task.submitted_at).toLocaleString()}</p>}
          <p>{refunds[task.refund_status] || '额度状态待确认'} · 已接受重试 {task.retry_count} / 20 次</p>
          {(task.status === 'QUEUED' || task.status === 'PROCESSING' || task.status === 'SUCCEEDED') && <div className="task-progress">
            <progress aria-label={labels[task.status]} {...(task.status === 'SUCCEEDED' ? { value: 100, max: 100 } : task.status === 'QUEUED' ? { value: 0, max: 100 } : {})} />
            {task.status === 'QUEUED' && <span>等待可用处理名额{typeof task.queue_seconds === 'number' ? ` · 已排队 ${duration(task.queue_seconds + elapsed)}` : ''}</span>}
            {task.status === 'PROCESSING' && <span>正在分析音视频{typeof task.processing_seconds === 'number' ? ` · 已处理 ${duration(task.processing_seconds + elapsed)}` : ''}。完成时间尚不能准确估计。</span>}
            {task.target_seconds != null && <span>60 分钟视频处理目标：20 分钟内完成（不含排队，目标尚待实际验证）。</span>}
            {task.status === 'SUCCEEDED' && <span>处理已完成{typeof task.processing_seconds === 'number' ? ` · 实际处理 ${duration(task.processing_seconds)}` : ''}</span>}
          </div>}
          {task.failure_category && <p>{failures[task.failure_category] || '任务失败，原因待核实'}</p>}
          {task.failure_code && <p>错误代码：{task.failure_code}</p>}
          {cooldown > 0 && <p className="task-wait">再次尝试约需等待 {duration(cooldown)}。等待结束后仍须由服务器确认。</p>}
          {task.status === 'FAILED' && !task.retry_allowed && cooldown <= 0 && <p className="field-help">{task.retry_count >= 20 ? '已达到累计重试上限。' : '当前状态暂不可重试，请刷新确认。'}</p>}
          {task.api_fee_warning && <p className="fee-warning">模型服务反复超时。继续重试可能产生模型 API 费用，但不会额外扣除此任务的处理机会。</p>}
          <div className="task-actions"><button className="secondary" type="button" disabled={pending !== null} onClick={() => void removeTask(task)}>删除任务</button>{task.status === 'SUCCEEDED' && <button className="secondary" type="button" onClick={() => setSelectedTask(current => current === task.id ? null : task.id)} aria-expanded={selectedTask === task.id}>{selectedTask === task.id ? '收起结果' : '查看结果'}</button>}{(task.status === 'QUEUED' || task.status === 'PROCESSING') && <button type="button" className="secondary" disabled={pending !== null} onClick={() => void act(task, 'cancel')}>{pending === task.id ? '正在处理…' : '取消任务'}</button>}{task.status === 'FAILED' && <button type="button" className="secondary" disabled={pending !== null || !task.retry_allowed || cooldown > 0} onClick={() => void act(task, 'retry')}>{pending === task.id ? '正在处理…' : '重试任务'}</button>}</div>
          {selectedTask === task.id && task.status === 'SUCCEEDED' && <ResultPanel key={`result-${task.id}`} taskId={task.id} onExpired={onExpired} />}
        </li>
      })}</ul>}
      <div className="task-pagination"><button type="button" className="secondary" onClick={() => setOffset(Math.max(0, offset - 20))} disabled={offset === 0 || pending !== null}>上一页</button><span>第 {offset / 20 + 1} 页</span><button type="button" className="secondary" onClick={() => setOffset(offset + 20)} disabled={tasks.length < 20 || pending !== null}>下一页</button></div>
    </section>
  )
}
