// Actual built React + local mock API. No model calls or real credentials.
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { existsSync, readFileSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, extname, join, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawn } from 'node:child_process'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..'), dist = join(root, 'dist')
const candidates = [process.env.UI_TEST_BROWSER,
  join(process.env.PROGRAMFILES || '', 'Google/Chrome/Application/chrome.exe'),
  join(process.env['PROGRAMFILES(X86)'] || '', 'Microsoft/Edge/Application/msedge.exe'),
  join(process.env.LOCALAPPDATA || '', 'Google/Chrome/Application/chrome.exe')].filter(Boolean)
const browser = candidates.find(existsSync)
assert(browser, 'Chrome/Edge is required; set UI_TEST_BROWSER to an installed executable')
assert(existsSync(join(dist, 'index.html')), 'Run npm run build first')
assert(typeof WebSocket === 'function', 'Node needs --experimental-websocket on Node 20')
const id = '00000000-0000-0000-0000-000000000001', bad = '00000000-0000-0000-0000-000000000002'
const uid = '00000000-0000-0000-0000-000000000010', now = new Date().toISOString()
const session = { user: { id: uid, email: 'synthetic@example.invalid', role: 'ADMIN' }, csrf_token: 'synthetic-csrf-only' }
const task = (identifier, filename) => ({ id: identifier, filename, status: 'SUCCEEDED', submitted_at: now, started_at: now, finished_at: now, retry_count: 0, retry_allowed: false, cooldown_seconds: 0, refund_status: 'CHARGED', api_fee_warning: false, processing_seconds: 1 })
const stateTask = (number, status, filename) => ({ ...task(`00000000-0000-0000-0000-00000000000${number}`, filename), status,
  started_at: status === 'QUEUED' ? null : now, finished_at: status === 'QUEUED' || status === 'PROCESSING' ? null : now,
  failure_category: status === 'FAILED' ? 'INTERNAL' : status === 'CANCELLED' ? 'USER_CANCELLED' : null,
  refund_status: status === 'QUEUED' ? 'NOT_CHARGED' : status === 'FAILED' ? 'REFUNDED' : 'CHARGED' })
let logged = false, tasks = [task(id, 'synthetic.mp4'), task(bad, 'invalid-output.mp4'),
  stateTask(3, 'QUEUED', 'queued.mp4'), stateTask(4, 'PROCESSING', 'processing.mp4'),
  stateTask(5, 'FAILED', 'failed.mp4'), stateTask(6, 'CANCELLED', 'cancelled.mp4')], consents = [], deleted = false
const calls = []
const receipt = { id: 'job-1', task_id: id, user_id: uid, status: 'MANUAL_REQUIRED', started_at: now, attempt_count: 5, online_complete: false, backup_cleanup_state: 'PENDING', alerts: [{ id: 'alert-1', alert_code: 'CLEANUP_RETRY_LIMIT', resolved_at: null }] }
const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost'); const path = url.pathname
  const json = (value, status = 200) => { res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' }); res.end(status === 204 ? '' : JSON.stringify(value)) }
  if (path.startsWith('/api/')) {
    const chunks = []; for await (const chunk of req) chunks.push(chunk)
    const body = Buffer.concat(chunks).toString(); calls.push({ path, query: url.search, method: req.method, body })
    if (path === '/api/health/live') return json({ status: 'alive' })
    if (path === '/api/auth/me') return json(logged ? session : {}, logged ? 200 : 401)
    if (path === '/api/auth/login') { logged = true; return json(session) }
    if (path === '/api/quota') return json({ credits: null, unlimited: true, recovery_seconds: null })
    if (path === '/api/tasks') return json(tasks)
    if (path === '/api/admin/tasks') return json([{ id, user_id: uid, status: 'SUCCEEDED', submitted_at: now, started_at: now, finished_at: now, retry_count: 0, quota_unlimited: true, credits_available: null }])
    if (path === '/api/deletions') return json(deleted ? [receipt] : [])
    if (path === '/api/admin/cleanup-jobs') return json([receipt])
    if (path === '/api/admin/cleanup-jobs/job-1/retry') return json(receipt, 202)
    if (path === `/api/tasks/${id}` && req.method === 'DELETE') { tasks = tasks.filter(t => t.id !== id); deleted = true; return json(receipt, 202) }
    if (path.endsWith('/consents')) {
      if (req.method === 'POST') { const value = JSON.parse(body); consents.push({ ...value, id: 'consent-1', granted_at: now, revoked_at: null }); return json(consents[0], 201) }
      return json(consents)
    }
    if (path.endsWith('/consents/consent-1')) { consents[0].revoked_at = now; return json(null, 204) }
    if (path.endsWith('/result')) {
      if (path.includes(bad)) return json({}, 409)
      const language = url.searchParams.get('language') || 'zh'
      return json({ language, offline_test: true, export: { summary: language === 'zh' ? '合成中文摘要' : 'Synthetic English summary', key_events: [{ timestamp_seconds: 1, title: 'Synthetic event', description: 'Fixture only' }] }, presentation: { overview: 'Synthetic overview', points: ['one', 'two', 'three'], chapters: [{ timestamp_seconds: 0, title: 'Beginning' }], limitations: ['Offline fixture; no semantic quality evidence'] } })
    }
    if (path.endsWith('/export')) return json({ schema_version: '1.0', language: url.searchParams.get('language') })
    return json({}, 404) // Video decoding is outside this mock test.
  }
  const file = resolve(dist, '.' + decodeURIComponent(path === '/' ? '/index.html' : path))
  if (!file.startsWith(dist + sep) || !existsSync(file)) { res.writeHead(404); return res.end() }
  res.writeHead(200, { 'content-type': ({ '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html' })[extname(file)] || 'application/octet-stream' }); res.end(readFileSync(file))
})
await new Promise(r => server.listen(0, '127.0.0.1', r))
const origin = `http://127.0.0.1:${server.address().port}`, profile = mkdtempSync(join(tmpdir(), 'video-summary-ui-'))
const child = spawn(browser, ['--headless=new', '--disable-gpu', '--disable-extensions', '--no-first-run', '--no-default-browser-check', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { windowsHide: true, stdio: 'ignore' })
let socket, counter = 0; const pending = new Map()
const delay = ms => new Promise(r => setTimeout(r, ms))
try {
  const active = join(profile, 'DevToolsActivePort'); let deadline = Date.now() + 15000
  while (!existsSync(active)) { assert(Date.now() < deadline, 'Browser did not expose CDP'); await delay(100) }
  const port = readFileSync(active, 'utf8').split('\n')[0]
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
  const target = targets.find(t => t.type === 'page'); assert(target, 'No browser page target')
  socket = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject })
  socket.onmessage = event => { const message = JSON.parse(event.data); if (message.id && pending.has(message.id)) { pending.get(message.id)(message); pending.delete(message.id) } }
  const command = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++counter, timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)) }, 15000)
    pending.set(id, value => { clearTimeout(timer); value.error ? reject(new Error(JSON.stringify(value.error))) : resolve(value.result) }); socket.send(JSON.stringify({ id, method, params }))
  })
  const run = async expression => { const value = await command('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }).catch(error=>{throw new Error(error.message+' expression='+expression)}); assert(!value.exceptionDetails, JSON.stringify(value.exceptionDetails)); return value.result?.value }
  const wait = async expression => { const end = Date.now() + 15000; while (!await run(`Boolean(${expression})`)) { assert(Date.now() < end, `UI wait failed: ${expression}`); await delay(100) } }
  const click = async (text, scope = 'document') => { const button = `Array.from(${scope}.querySelectorAll('button')).find(b=>b.textContent.trim()===${JSON.stringify(text)})`; await wait(`Boolean(${button} && !(${button}).disabled)`); return run(`(${button}).click()`) }
  const input = (selector, value) => run(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,${JSON.stringify(value)});e.dispatchEvent(new Event('input',{bubbles:true}))})()`)
  await command('Page.enable')
  await command('Page.addScriptToEvaluateOnNewDocument', { source: "window.__downloads=0;window.__alerts=0;window.alert=()=>window.__alerts++;window.confirm=()=>true;HTMLAnchorElement.prototype.click=function(){window.__downloads++}" })
  await command('Page.navigate', { url: origin })
  await wait("document.querySelector('#email') && !document.querySelector('.checking')")
  await run("window.__downloads=0;window.__alerts=0;window.alert=()=>window.__alerts++;window.confirm=()=>true;HTMLAnchorElement.prototype.click=function(){window.__downloads++};true")
  await input('#email', 'synthetic@example.invalid'); await input('#password', 'synthetic-only-password'); await click('登录', "document.querySelector('form')")
  await wait("document.body.textContent.includes('synthetic.mp4')")
  assert.equal(calls.filter(c => c.path.includes('/admin/tasks/') && c.path.endsWith('/result')).length, 0, 'Admin content must not auto-load')
  const owned = "document.querySelector('[aria-labelledby=tasks-title]')"
  const stateLabels = await run(`Array.from(${owned}.querySelectorAll('.task-state')).map(e=>e.textContent)`)
  assert.deepEqual([...new Set(stateLabels)].sort(), ['排队中', '处理中', '已完成', '失败', '已取消'].sort())
  assert.equal(await run(`${owned}.querySelector('.state-processing').closest('.task-card').querySelector('progress').hasAttribute('value')`), false)
  assert.equal(await run(`${owned}.querySelector('.state-queued').closest('.task-card').querySelector('progress').getAttribute('value')`), '0')
  assert.equal(await run(`${owned}.querySelector('.state-succeeded').closest('.task-card').querySelector('progress').getAttribute('value')`), '100')
  tasks = tasks.map(row => row.status === 'PROCESSING' ? { ...row, status: 'SUCCEEDED', finished_at: now } : row)
  await click('刷新', owned)
  await wait(`${owned}.querySelector('.state-processing')===null`)
  assert.equal(await run('window.__alerts'), 0, 'Successful refresh must not open an alert popup')
  await click('查看结果', owned); await wait("document.body.textContent.includes('合成中文摘要')")
  await run("(()=>{const e=document.querySelector('.result-toolbar select');e.value='en';e.dispatchEvent(new Event('change',{bubbles:true}))})()")
  await wait("document.body.textContent.includes('Synthetic English summary')"); await click('导出 JSON')
  await wait('window.__downloads===1'); assert.equal(await run('window.__alerts'), 0)
  await click('排障授权', owned); await wait("document.querySelector('.consent-panel input')")
  await input('.consent-panel input', 'diagnosis'); await click('明确授权本任务'); await wait("document.querySelector('.consent-panel').textContent.includes('有效')")
  await click('撤销授权'); await wait("document.querySelector('.consent-panel').textContent.includes('已撤销')")
  await run("(()=>{const e=Array.from(document.querySelectorAll('label')).find(x=>x.textContent.includes('本次排障访问目的')).querySelector('input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,'diagnosis');e.dispatchEvent(new Event('input',{bubbles:true}))})()")
  await click('请求查看授权摘要'); await wait("document.querySelector('.admin-group .result-panel')")
  assert(calls.some(c => c.path === `/api/admin/tasks/${id}/result` && c.query.includes('purpose=diagnosis')))
  await click('一键强制删除'); await delay(150)
  assert(calls.some(c => c.path === '/api/admin/cleanup-jobs/job-1/retry' && c.method === 'POST'))
  await click('删除任务', owned); await wait(`!${owned}.querySelector('.task-heading').textContent.includes('synthetic.mp4')`)
  assert((await run(`${owned}.textContent`)).includes('实际清理状态'))
  // Remaining task has an invalid result: no enabled export control may appear.
  await click('查看结果', owned); await wait("document.querySelector('[aria-labelledby=tasks-title] .result-panel [role=alert]')")
  assert.equal(await run("document.querySelector('[aria-labelledby=tasks-title] .result-toolbar button').disabled"), true)
  assert(calls.some(c => c.method === 'DELETE' && c.path.endsWith('/consents/consent-1')))
  console.log(JSON.stringify({ status: 'passed', scenarios: ['login/history', 'five states/progress/inline completion', 'bilingual results/export', 'invalid output blocked', 'consent/revocation', 'admin purpose and manual retry', 'delete hides task without claiming cleanup complete'], limits: ['Mocked API is not backend authorization proof', 'Video decoding/seek and model quality not exercised'] }))
} finally {
  socket?.close(); child.kill(); server.closeAllConnections(); await new Promise(r => server.close(r))
  await Promise.race([new Promise(r => child.once('exit', r)), delay(5000)])
  assert(resolve(profile).startsWith(resolve(tmpdir()) + sep) && profile.split(sep).at(-1).startsWith('video-summary-ui-'), 'Temporary profile boundary check failed')
  rmSync(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 })
}
