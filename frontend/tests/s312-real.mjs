// Exercise the shipped frontend through its normal browser entry point.
import assert from 'node:assert/strict'
import { execFile, spawn } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve, sep } from 'node:path'
import { promisify } from 'node:util'

const [origin, videoPath, project, context] = process.argv.slice(2)
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/)
assert.match(project, /^vsa-s312-e2e-[a-f0-9]{10}$/)
const shell = promisify(execFile)
const browser = [
  process.env.UI_TEST_BROWSER,
  join(process.env.PROGRAMFILES || '', 'Google/Chrome/Application/chrome.exe'),
  join(process.env['PROGRAMFILES(X86)'] || '', 'Microsoft/Edge/Application/msedge.exe'),
].filter(Boolean).find(existsSync)
assert(browser, 'Installed Chrome or Edge required')
const profile = mkdtempSync(join(tmpdir(), 'vsa-s312-e2e-browser-'))
const child = spawn(browser, [
  '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  '--disable-background-networking', '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
  '--remote-debugging-port=0', '--user-data-dir='+profile, 'about:blank',
], { windowsHide: true, stdio: 'ignore' })
let socket, nextId = 0
const pending = new Map()
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))

async function waitFor(condition, label, ms = 30000) {
  const end = Date.now() + ms
  while (!(await condition())) {
    assert(Date.now() < end, 'Timed out: '+label)
    await delay(150)
  }
}

try {
  const debugPort = join(profile, 'DevToolsActivePort')
  await waitFor(() => existsSync(debugPort), 'browser debugging port', 15000)
  const port = readFileSync(debugPort, 'utf8').split('\n')[0]
  const targets = await (await fetch('http://127.0.0.1:'+port+'/json/list')).json()
  socket = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl)
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject })
  socket.onmessage = event => {
    const message = JSON.parse(event.data)
    if (pending.has(message.id)) {
      pending.get(message.id)(message)
      pending.delete(message.id)
    }
  }
  const command = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++nextId
    const timeout = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout '+method)) }, 15000)
    pending.set(id, response => {
      clearTimeout(timeout)
      response.error ? reject(new Error(JSON.stringify(response.error))) : resolve(response.result)
    })
    socket.send(JSON.stringify({ id, method, params }))
  })
  const run = async expression => {
    const result = await command('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
    assert(!result.exceptionDetails, JSON.stringify(result.exceptionDetails))
    return result.result?.value
  }
  const wait = expression => waitFor(() => run('Boolean('+expression+')'), expression)
  const input = (selector, value) => run(`(() => {
    const field = document.querySelector(${JSON.stringify(selector)})
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(field,${JSON.stringify(value)})
    field.dispatchEvent(new Event('input',{bubbles:true}))
  })()`)
  const click = async (text, scope = 'document') => {
    const button = `Array.from((${scope})?.querySelectorAll('button')||[]).find(b=>b.textContent.trim()===${JSON.stringify(text)})`
    await wait(`(${button})&&!((${button}).disabled)`)
    await run(`(${button}).click()`)
  }

  await command('Page.enable')
  await command('Page.addScriptToEvaluateOnNewDocument', { source: `
    window.confirm = () => true;
    const original = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(method,url,...args) {
      this.addEventListener('load',()=>{
        if (method==='POST'&&new URL(url,location.href).pathname==='/api/inputs/upload'&&this.status===201)
          window.s312Receipt=JSON.parse(this.responseText);
      });
      return original.call(this,method,url,...args);
    };` })
  await command('Page.navigate', { url: origin })
  await wait('document.querySelector("#email")&&!document.querySelector(".checking")')
  const account = { email: 'fixture@s312.invalid', password: 'S312-Synthetic-Only-Password!' }
  assert.equal(await run(`fetch('/api/auth/register',{
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(${JSON.stringify(account)})
  }).then(r=>r.status)`), 201)
  await input('#email', account.email)
  await input('#password', account.password)
  await click('登录', 'document.querySelector("form")')
  await wait('document.querySelector("#video-file")')
  const scope = 'document.querySelector("[aria-labelledby=tasks-title]")'
  const card = id => `Array.from(${scope}.querySelectorAll('.task-card')).find(e=>e.querySelector('code.identifier')?.textContent===${JSON.stringify(id)})`
  async function upload() {
    await run('window.s312Receipt=null')
    const { root } = await command('DOM.getDocument')
    const { nodeId } = await command('DOM.querySelector', { nodeId: root.nodeId, selector: '#video-file' })
    await command('DOM.setFileInputFiles', { nodeId, files: [videoPath] })
    await run('document.querySelector(".rights-confirmation input").click()')
    await click('导入并校验', 'document.querySelector(".import-section")')
    await wait('window.s312Receipt?.status==="QUEUED"&&document.querySelector(".import-success")?.textContent.includes("导入成功")')
    const id = await run('window.s312Receipt.id')
    assert.match(id, /^[a-f0-9-]{36}$/)
    return id
  }

  const succeeded = await upload()
  await wait(`(${card(succeeded)})?.textContent.includes('已完成')`)
  await click('查看结果', card(succeeded))
  await wait('document.querySelector(".summary-text")?.textContent.includes("蓝色画面")')
  const player = 'document.querySelector("[aria-labelledby=tasks-title] video")'
  await wait(`${player}?.readyState>=2`)
  await run(`(()=>{const video=${player};video.muted=true;return video.play().then(()=>true)})()`)
  await wait(`${player}.currentTime>.1`)
  assert(await run(`${player}.duration>5`))
  await run(`(()=>{const video=${player};video.pause();video.currentTime=5})()`)
  await wait(`!${player}.seeking&&${player}.currentTime>=4.9`)
  await run(`(()=>{
    const select=document.querySelector('.result-toolbar select');
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(select,'en');
    select.dispatchEvent(new Event('change',{bubbles:true}));
  })()`)
  await wait('document.querySelector(".summary-text")?.textContent.includes("Blue picture")')
  const range = await run(`fetch('/api/tasks/${succeeded}/video',{headers:{Range:'bytes=0-29'}})
    .then(async r=>[r.status,(await r.arrayBuffer()).byteLength])`)
  assert.deepEqual(range, [206, 30])
  assert.equal(await run(`fetch('/api/tasks/${succeeded}/export?language=en')
    .then(r=>r.headers.get('content-disposition'))`), 'attachment; filename="video-summary-en.json"')

  const other = { ...account, email: 'other@s312.invalid' }
  const auth = route => fetch(origin + '/api/auth/' + route, {
    method: 'POST', headers: { 'Content-Type': 'application/json', Origin: origin },
    body: JSON.stringify(other),
  })
  assert.equal((await auth('register')).status, 201)
  const login = await auth('login')
  assert.equal(login.status, 200)
  const cookie = login.headers.getSetCookie().map(value => value.split(';', 1)[0]).join('; ')
  assert(cookie)
  for (const route of ['result?language=en', 'export?language=en', 'video']) {
    assert.equal((await fetch(origin + '/api/tasks/' + succeeded + '/' + route,
      { headers: { Cookie: cookie } })).status, 404)
  }
  assert.equal((await fetch(origin + '/api/admin/tasks', { headers: { Cookie: cookie } })).status, 403)

  const cancelled = await upload()
  await waitFor(async () => {
    const result = await shell('docker', ['--context', context, 'logs', project+'-worker-1'])
    return result.stdout.includes('S312_MOCK_VALIDATED 2')
  }, 'second model request reached its mock transport', 15000)
  await click('刷新', scope)
  await wait(`(${card(cancelled)})?.textContent.includes('处理中')`)
  await click('取消任务', card(cancelled))
  await wait(`(${card(cancelled)})?.textContent.includes('已取消')`)
  assert([404, 409].includes(await run(`fetch('/api/tasks/${cancelled}/result?language=en').then(r=>r.status)`)))

  for (const id of [succeeded, cancelled]) {
    await click('删除任务', card(id))
    await wait(`!(${card(id)})`)
    assert.equal(await run(`fetch('/api/tasks/${id}/video').then(r=>r.status)`), 404)
    assert.equal(await run(`fetch('/api/tasks/${id}/result?language=en').then(r=>r.status)`), 404)
  }
  console.log(JSON.stringify({ browser: (await command('Browser.getVersion')).product,
    uploads: 2, languages: ['zh','en'], decoded_and_seeked: true, range_status: range[0],
    json_export: true, cross_user_denied: true, user_admin_route_denied: true,
    processing_cancelled: true, deleted_from_ui: 2 }))
} finally {
  socket?.close()
  child.kill()
  await Promise.race([new Promise(resolve => child.once('exit', resolve)), delay(3000)])
  const profilePath = resolve(profile)
  assert(profilePath.startsWith(resolve(tmpdir())+sep) && profilePath.split(sep).at(-1).startsWith('vsa-s312-e2e-browser-'))
  rmSync(profilePath, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 })
}
