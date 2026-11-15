// Exercise the shipped frontend through its normal browser entry point.
import assert from 'node:assert/strict'
import { execFile, spawn } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
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
const browserErrors = []
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
    } else if (message.method === 'Runtime.exceptionThrown' ||
               (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error')) {
      browserErrors.push(message)
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

  async function screenshot(name) {
    if (!process.env.UI_TEST_EVIDENCE) return
    const { data } = await command('Page.captureScreenshot', { format: 'png' })
    writeFileSync(resolve(process.env.UI_TEST_EVIDENCE, (process.env.S312_FIXTURE_MODE || 'inline') + '-' + name + '.png'), Buffer.from(data, 'base64'))
  }

  await command('Page.enable')
  await command('Runtime.enable')
  await command('Emulation.setDeviceMetricsOverride', { width: 1100, height: 900, deviceScaleFactor: 1, mobile: false })
  const downloadPath = join(profile, 'downloads')
  await command('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath })
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
  await wait('document.querySelector(".feedback-footer a")?.getAttribute("href")==="mailto:feedback+s312@example.invalid"')
  await screenshot('feedback')
  const account = { email: 'fixture@s312.invalid', password: 'S312-Synthetic-Only-Password!' }
  assert.equal(await run(`fetch('/api/auth/register',{
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(${JSON.stringify(account)})
  }).then(r=>r.status)`), 201)
  await input('#email', account.email)
  await input('#password', account.password)
  await click('登录', 'document.querySelector("form")')
  await wait('document.querySelector("#video-file")')
  await wait('document.querySelector(".quota-card")?.textContent.includes("1 / 1")')
  assert(!await run('document.body.textContent.includes("当前为离线流程验证模式")'))
  const deepseek = process.env.S312_FIXTURE_MODE === 'deepseek'
  if (deepseek) {
    await wait('document.querySelector("#processing-model")?.value==="qwen3.8-omni-flash"')
    await run("(() => { const field = document.querySelector('#processing-model'); field.value = 'deepseek-v4.1-flash'; field.dispatchEvent(new Event('change', {bubbles:true})); })()")
    await wait('document.querySelector("#processing-model")?.value==="deepseek-v4.1-flash"')
    await screenshot('model-picker')
  }
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
    assert.equal(await run('window.s312Receipt.requested_model'),
      deepseek ? 'deepseek-v4.1-flash' : 'qwen3.8-omni-flash')
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
  assert.deepEqual(await run('Array.from(document.querySelectorAll(".result-panel h3"), h=>h.textContent)'), ['摘要','章节'])
  await click('0:00', 'document.querySelector(".result-panel .timeline")')
  await wait(`!${player}.seeking&&${player}.currentTime<.1`)
  await run('document.querySelector(".result-panel").scrollIntoView({block:"center"})')
  await screenshot('summary-zh')
  async function download(language) {
    await click('下载 Markdown', 'document.querySelector(".result-panel")')
    const file = join(downloadPath, `video-summary-${language}.md`)
    await waitFor(() => existsSync(file), `download in ${language}`)
    return readFileSync(file, 'utf8')
  }
  const chineseExport = await download('zh')
  await run(`(()=>{
    const select=document.querySelector('.result-toolbar select');
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(select,'en');
    select.dispatchEvent(new Event('change',{bubbles:true}));
  })()`)
  await wait('document.querySelector(".summary-text")?.textContent.includes("Blue picture")')
  await screenshot('summary-en')
  const englishExport = await download('en')
  const range = await run(`fetch('/api/tasks/${succeeded}/video',{headers:{Range:'bytes=0-29'}})
    .then(async r=>[r.status,(await r.arrayBuffer()).byteLength])`)
  assert.deepEqual(range, [206, 30])
  for (const language of ['zh', 'en']) {
    const [header, body] = await run(`fetch('/api/tasks/${succeeded}/export?language=${language}')
      .then(async r=>[r.headers.get('content-disposition'),await r.text()])`)
    assert.equal(header, `attachment; filename="video-summary-${language}.md"`)
    assert.equal(body, language === 'zh' ? chineseExport : englishExport)
    const expected = language === 'zh' ? ['摘要', '蓝色画面', '章节'] : ['Summary', 'Blue picture', 'Chapters']
    assert(body.startsWith('## '+expected[0]+'\n\n') && body.includes(expected[1]))
    assert.deepEqual(body.match(/^## .+$/gm), ['## '+expected[0], '## '+expected[2]])
    assert.match(body, /\n- 00:00:00 .+\n/)
    assert(body.split('\n\n## '+expected[2]+'\n\n')[1].trimEnd().split('\n').every(
      line => /^- \d{2}:\d{2}:\d{2} .+$/.test(line)))
    if (process.env.UI_TEST_EVIDENCE) {
      writeFileSync(resolve(process.env.UI_TEST_EVIDENCE, 'video-summary-'+language+'.md'), body, 'utf8')
    }
  }

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

  await wait('document.querySelector(".quota-card")?.textContent.includes("0 / 1")')
  // Advance the recovery anchor in this disposable database so the same user can start another task.
  await shell('docker', ['--context', context, 'exec', project+'-db-1', 'psql',
    '-X', '-U', 'vsa_init', '-d', 's312_test', '-v', 'ON_ERROR_STOP=1', '-c',
    "UPDATE users SET recovery_started_at=now()-interval '6 hours' WHERE email_normalized='fixture@s312.invalid'"])
  await click('刷新', scope)
  await wait('document.querySelector(".quota-card")?.textContent.includes("1 / 1")')
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

  if (deepseek) {
    const { stdout } = await shell('docker', ['--context', context, 'exec', project+'-db-1', 'psql',
      '-X', '-U', 'vsa_init', '-d', 's312_test', '-At', '-v', 'ON_ERROR_STOP=1', '-c',
      "SELECT count(*) FROM media_assets WHERE asset_kind='ASR_AUDIO_TRANSFER'"])
    assert.equal(stdout.trim(), '0')
  }
  for (const id of [succeeded, cancelled]) {
    await click('删除任务', card(id))
    await wait(`!(${card(id)})`)
    assert.equal(await run(`fetch('/api/tasks/${id}/video').then(r=>r.status)`), 404)
    assert.equal(await run(`fetch('/api/tasks/${id}/result?language=en').then(r=>r.status)`), 404)
  }
  assert.deepEqual(browserErrors, [])
  console.log(JSON.stringify({ browser: (await command('Browser.getVersion')).product,
    uploads: 2, languages: ['zh','en'], deepseek_selected: deepseek,
    decoded_and_seeked: true, range_status: range[0],
    markdown_export: true, cross_user_denied: true, user_admin_route_denied: true,
    processing_cancelled: true, deleted_from_ui: 2 }))
} finally {
  socket?.close()
  child.kill()
  await Promise.race([new Promise(resolve => child.once('exit', resolve)), delay(3000)])
  const profilePath = resolve(profile)
  assert(profilePath.startsWith(resolve(tmpdir())+sep) && profilePath.split(sep).at(-1).startsWith('vsa-s312-e2e-browser-'))
  rmSync(profilePath, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 })
}
