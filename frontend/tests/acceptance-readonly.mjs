// Actual React + actual WP06 backend/Worker/PG/RustFS. No API mocks.
import assert from 'node:assert/strict'
import {readFileSync,writeFileSync,mkdtempSync,existsSync,rmSync} from 'node:fs'
import {join,resolve,sep} from 'node:path'
import {tmpdir} from 'node:os'
import {spawn} from 'node:child_process'
const state=JSON.parse(readFileSync(process.env.WP06_STATE,'utf8'))
assert(state.url.startsWith('http://127.0.0.1:'))
const browser=[process.env.UI_TEST_BROWSER,join(process.env.PROGRAMFILES||'','Google/Chrome/Application/chrome.exe'),join(process.env['PROGRAMFILES(X86)']||'','Microsoft/Edge/Application/msedge.exe')].filter(Boolean).find(existsSync)
assert(browser)
const profile=mkdtempSync(join(tmpdir(),'video-summary-wp06-real-'))
const child=spawn(browser,['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'],{windowsHide:true,stdio:'ignore'})
let socket,counter=0;const pending=new Map(),delay=ms=>new Promise(r=>setTimeout(r,ms))
try{
 const end=Date.now()+15000
 while(!existsSync(join(profile,'DevToolsActivePort'))){assert(Date.now()<end);await delay(100)}
 const port=readFileSync(join(profile,'DevToolsActivePort'),'utf8').split('\n')[0]
 const targets=await(await fetch('http://127.0.0.1:'+port+'/json/list')).json()
 socket=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl)
 await new Promise((r,j)=>{socket.onopen=r;socket.onerror=j})
 socket.onmessage=e=>{const m=JSON.parse(e.data);if(pending.has(m.id)){pending.get(m.id)(m);pending.delete(m.id)}}
 const cmd=(method,params={})=>new Promise((r,j)=>{const id=++counter,t=setTimeout(()=>j(Error('CDP timeout '+method)),20000);pending.set(id,m=>{clearTimeout(t);m.error?j(Error(JSON.stringify(m.error))):r(m.result)});socket.send(JSON.stringify({id,method,params}))})
 const run=async expression=>{const r=await cmd('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});assert(!r.exceptionDetails,JSON.stringify(r.exceptionDetails));return r.result?.value}
 const wait=async(expression,ms=60000)=>{const end=Date.now()+ms;while(!await run('Boolean('+expression+')')){assert(Date.now()<end,'UI wait '+expression);await delay(200)}}
 const input=async(selector,v)=>run('(()=>{let e=document.querySelector('+JSON.stringify(selector)+');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(e,'+JSON.stringify(v)+');e.dispatchEvent(new Event("input",{bubbles:true}))})()')
 const click=async(text,scope='document')=>{const q='Array.from('+scope+'.querySelectorAll("button")).find(b=>b.textContent.trim()==='+JSON.stringify(text)+')';await wait(q+'&&!('+q+').disabled');await run('('+q+').click()')}
 await cmd('Page.enable');await cmd('Page.addScriptToEvaluateOnNewDocument',{source:"window.__copied=[];Object.defineProperty(navigator,'clipboard',{value:{writeText:async v=>window.__copied.push(v)},configurable:true})"});await cmd('Page.navigate',{url:state.url})
 const seeds=[{email:'user1@wp06.invalid',taskId:'8b10e001-fe46-4e4c-a984-9d71556aee4f'},{email:'admin@wp06.invalid',taskId:'8ee10940-c841-4c94-8822-e648ef500442'}]
 const checks=[]
 for(const seed of seeds){
  const account=state.accounts.find(a=>a.email===seed.email);assert(account)
  await wait('document.querySelector("#email")&&!document.querySelector(".checking")');await input('#email',account.email);await input('#password',account.password);await click('登录','document.querySelector("form")');await wait('document.querySelector("[aria-labelledby=tasks-title]")')
  const card='Array.from(document.querySelectorAll("[aria-labelledby=tasks-title] .task-card")).find(e=>e.querySelector("code.identifier")?.textContent==='+JSON.stringify(seed.taskId)+')'
  await wait(card);await click('查看结果',card);await wait('document.querySelector(".result-panel .summary-text")')
  assert.equal(await run('('+card+').querySelectorAll(".task-identifier").length'),1);assert(!await run('Boolean(document.querySelector(".result-panel .task-identifier"))'))
  await click('复制任务 ID',card);assert.equal(await run('window.__copied.at(-1)'),seed.taskId);checks.push(account.role+' known synthetic seed has one task ID/copy group with result open')
  if(account.role==='USER'){await click('退出登录');continue}
  await click('任务元数据');await wait('document.querySelector("#admin-task")');assert(!await run('Boolean(document.querySelector("video,.result-panel"))'))
  for(const query of [seed.taskId.toUpperCase(),'1','中文','%','_',"'",'a'.repeat(120)]){
   await input('#admin-task',query);await click('搜索');await wait('!document.querySelector("[aria-labelledby=admin-title]").textContent.includes("正在读取管理记录")');await delay(100)
   assert(!await run('Boolean(document.querySelector("[aria-labelledby=admin-title] [role=alert]"))'))
   const ids=await run('Array.from(document.querySelectorAll(".admin-group code.identifier")).map(e=>e.textContent)');assert(ids.every(id=>id.toLowerCase().includes(query.toLowerCase())))
  }
  await input('#admin-task','');await wait('document.querySelectorAll(".admin-group .task-card").length>0');assert(await run('document.querySelector("[aria-labelledby=admin-title] .task-pagination").textContent.includes("第 1 页")'))
  checks.push('actual admin API exact/one-character/Unicode/%/_/quote/long search, auto-clear defaults')
 }
 console.log(JSON.stringify({status:'passed',browser:(await cmd('Browser.getVersion')).product,checks,limits:['Only original explicitly synthetic seed content was opened','No upload/seed/delete/cleanup/account changes','Only task metadata search and session login/logout','Existing manual environment retained']}))
}finally{
 socket?.close();child.kill();await Promise.race([new Promise(r=>child.once('exit',r)),delay(3000)])
 assert(resolve(profile).startsWith(resolve(tmpdir())+sep)&&profile.split(sep).at(-1).startsWith('video-summary-wp06-real-'))
 rmSync(profile,{recursive:true,force:true,maxRetries:10,retryDelay:200})
 console.log(JSON.stringify({resources:{browserExited:child.exitCode!==null||child.signalCode!==null,profileRemoved:!existsSync(profile)}}))
}
