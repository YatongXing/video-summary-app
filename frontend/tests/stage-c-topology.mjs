import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { fixture, delay } from './wp05-fixtures.mjs'
import { writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const origin = process.env.STAGE_C_TEST_ORIGIN
const selected = new URL(origin)
assert.equal(selected.protocol, 'http:')
assert.equal(selected.hostname, '127.0.0.1')
assert.notEqual(selected.port, '')
const browser = await fixture(fileURLToPath(new URL('..', import.meta.url)))
const { command, run, wait, click, input } = browser
async function shot(name) {
  if (!process.env.UI_TEST_EVIDENCE) return
  const { data } = await command('Page.captureScreenshot', { format: 'png' })
  writeFileSync(resolve(process.env.UI_TEST_EVIDENCE, name + '.png'), Buffer.from(data, 'base64'))
}
try {
  await command('Network.enable')
  await command('Page.navigate', { url: origin })
  await wait('document.querySelector("#email")')
  await browser.login('stagec2@example.invalid')
  await wait('document.body.textContent.includes("local-012.mp4")')
  await click('查看结果')
  await wait('document.querySelector("video")?.readyState>=1 && document.querySelector(".timeline button")')
  await run('document.querySelector("video").currentTime=6')
  await delay(300)
  const stamp = await run('document.querySelector(".timeline button").textContent.trim()')
  const [minutes, seconds] = stamp.split(':').map(Number)
  await run('document.querySelector(".timeline button").click()')
  await delay(300)
  assert(Math.abs(await run('document.querySelector("video").currentTime') - (60 * minutes + seconds)) < 1)
  await click('下载 Markdown')
  await wait('window.__downloads===1')
  await shot('stage-c2-local-player')
  await click('退出登录')
  await wait('document.querySelector("#email")')
  await browser.login('stagec2-other@example.invalid')
  await click('Bilibili 链接')
  await input('#video-url', 'https://www.bilibili.com/video/BV1synthetA1/?p=2')
  await run('document.querySelector("#combine-visual").click()')
  await run('document.querySelector(".import-section input[type=checkbox]:not(#combine-visual)").click()')
  await click('导入并校验')
  await wait('document.body.textContent.includes("正在导入")')
  await delay(1800)
  await click('停止本次请求')
  await wait('document.body.textContent.includes("已停止本次请求")')
  assert(!await run('document.querySelector(".import-success")'))
  await shot('stage-c2-browser-cancel')
  console.log('Chrome: 012 local playback, chapter seek and Markdown after upgrade. Bilibili browser abort through gateway.')
} finally {
  await browser.close()
}
