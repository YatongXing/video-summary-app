import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'
import { writeFileSync } from 'node:fs'
import { fixture, tid } from './wp05-fixtures.mjs'

const browser = await fixture(fileURLToPath(new URL('..', import.meta.url)))
const { state, calls, run, wait, input, click } = browser
const bvid = 'BV1iW4113786'
const url = 'https://www.bilibili.com/video/' + bvid + '/?p=2'
state.hook = async (call, { json }) => {
  if (call.path !== '/api/inputs/bilibili') return false
  assert.equal(call.body.url, url)
  assert.equal(call.body.rights_confirmed, true)
  assert.equal(call.body.combine_visual, true)
  state.tasks[0] = { ...state.tasks[0], input_kind: 'BILIBILI', filename: bvid + '-p2.mp4' }
  json({ id: tid, status: 'QUEUED', filename: bvid + '-p2.mp4', duration_seconds: 185, has_audio: true }, 201)
  return true
}

try {
  await browser.navigate()
  await browser.login()
  await click('Bilibili 链接')
  assert.equal(await run('document.querySelector("#video-url").labels[0].textContent'), 'Bilibili 播放页链接')
  assert(!await run('document.body.textContent.includes("HTTPS 直达")'))
  await input('#video-url', url)
  await run('document.querySelector(".import-section input[type=checkbox]:not(#combine-visual)").click()')
  await run('document.querySelector("#combine-visual").click()')
  await click('导入并校验')
  await wait('document.querySelector(".import-success")')
  await wait('document.querySelector(".task-card strong")?.textContent.includes("-p2.mp4")')
  await click('查看结果')
  await wait('document.querySelector(".timeline li")?.textContent.includes("00:00:00")')
  assert(!await run('document.querySelector(".result-panel video,.timeline button,.timeline a")'))
  await click('下载 Markdown')
  await wait('window.__downloads===1')
  assert(calls.some(call => call.path.endsWith('/export') && call.query === '?language=zh'))
  await input('.result-toolbar select', 'en')
  await wait('document.querySelector(".summary-text")?.textContent.includes("Synthetic English summary")')
  await click('下载 Markdown')
  await wait('window.__downloads===2')
  assert(calls.some(call => call.path.endsWith('/export') && call.query === '?language=en'))
  assert(!calls.some(call => call.path.endsWith('/video') || call.path === '/api/inputs/url'))
  assert(!browser.events.some(event => event.method === 'Runtime.exceptionThrown'
    || (event.method === 'Runtime.consoleAPICalled' && event.params.type === 'error')))
  if (process.env.UI_TEST_EVIDENCE) {
    await run('document.querySelector(".result-panel").scrollIntoView({block:"center"})')
    const { data } = await browser.command('Page.captureScreenshot', { format: 'png' })
    writeFileSync(resolve(process.env.UI_TEST_EVIDENCE, 'stage-c-markdown-result.png'), Buffer.from(data, 'base64'))
  }
  console.log('Stage C Chrome passed: explicit P2 URL, no video request or seek control, bilingual Markdown.')
} finally {
  await browser.close()
}
