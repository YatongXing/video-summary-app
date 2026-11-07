import assert from 'node:assert/strict'
import {createServer} from 'node:http'
import {existsSync,readFileSync,mkdtempSync,rmSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join,resolve,sep,extname} from 'node:path'
import {spawn} from 'node:child_process'
export const uuid=n=>'00000000-0000-4000-8000-'+String(n).padStart(12,'0')
export const tid=uuid(1),uid=uuid(10),aid=uuid(11)
export const delay=ms=>new Promise(r=>setTimeout(r,ms))
export function result(language='zh'){return {language,offline_test:true,export:{schema_version:'1.0',video:{filename:'synthetic.mp4',duration_seconds:60},language,summary:language==='zh'?'合成中文摘要':'Synthetic English summary',key_events:[{timestamp_seconds:1,title:'Synthetic event',description:'Fixture only'}]},presentation:{overview:'Synthetic overview',points:['one','two','three'],chapters:[{timestamp_seconds:0,title:'Beginning'}],limitations:['Synthetic only']}}}
export async function fixture(root){
 const calls=[],state={logged:false,role:'USER',user:uid,hook:null,contentStatus:200,supportEmail:null}
 const task=(id,status)=>({id,filename:id===tid?'synthetic.mp4':'state-'+status+'.mp4',status,submitted_at:new Date().toISOString(),started_at:status==='QUEUED'?null:new Date().toISOString(),finished_at:['QUEUED','PROCESSING'].includes(status)?null:new Date().toISOString(),retry_count:0,retry_allowed:status==='FAILED',cooldown_seconds:0,failure_category:status==='FAILED'?'INTERNAL':null,refund_status:status==='QUEUED'?'NOT_CHARGED':'CHARGED',api_fee_warning:false,processing_seconds:1})
 state.tasks=[task(tid,'SUCCEEDED'),task(uuid(2),'QUEUED'),task(uuid(3),'PROCESSING'),task(uuid(4),'FAILED'),task(uuid(5),'CANCELLED')]
 const session=()=>({user:{id:state.user,email:state.email||(state.role==='ADMIN'?'admin@example.invalid':'synthetic@example.invalid'),role:state.role},csrf_token:'synthetic-csrf-only'})
 const server=createServer(async(req,res)=>{
  const url=new URL(req.url,'http://localhost'),path=url.pathname
  const json=(v,status=200)=>{if(res.destroyed)return;res.writeHead(status,{'content-type':'application/json','cache-control':'no-store'});res.end(status===204?'':JSON.stringify(v))}
  if(path.startsWith('/api/')){
   const chunks=[];for await(const c of req)chunks.push(c)
   let body={};try{body=JSON.parse(Buffer.concat(chunks).toString()||'{}')}catch{}
   const call={path,query:url.search,method:req.method,body,csrf:req.headers['x-csrf-token'],origin:req.headers.origin};calls.push(call)
   if(state.hook&&await state.hook(call,{req,res,json}))return
   if(path==='/api/config/public')return json({support_email:state.supportEmail})
   if(path==='/api/health/live')return json({status:'alive'})
   if(path==='/api/auth/login'){res.setHeader('Set-Cookie','wp05session=synthetic; HttpOnly; SameSite=Lax; Path=/');state.logged=true;state.email=body.email;state.role=body.email?.startsWith('admin')?'ADMIN':'USER';state.user=body.email?.startsWith('second')?uuid(12):state.role==='ADMIN'?aid:uid;return json(session())}
   if(path==='/api/auth/me')return json(state.logged?session():{},state.logged?200:401)
   if(path==='/api/auth/register')return json({},201)
   if(!state.logged)return json({},401)
   if(req.method!=='GET'&&call.csrf!=='synthetic-csrf-only')return json({},403)
   if(path==='/api/auth/logout'){state.logged=false;return json({},204)}
   if(path==='/api/quota')return json({credits:state.role==='ADMIN'?null:3,unlimited:state.role==='ADMIN',recovery_seconds:null})
   if(path==='/api/tasks')return json(state.tasks)
   if(path==='/api/admin/tasks'){const q=url.searchParams.get('task_id')?.toLowerCase();let rows=state.tasks.map((t,i)=>({id:t.id,status:t.status,user_id:i===0?aid:uid,submitted_at:t.submitted_at,retry_count:0,quota_unlimited:i===0,credits_available:i===0?null:2}));if(q)rows=rows.filter(t=>t.id.toLowerCase().includes(q));rows=rows.slice(Number(url.searchParams.get('offset')||0),Number(url.searchParams.get('offset')||0)+Number(url.searchParams.get('limit')||50));return json(state.role==='ADMIN'?rows: {},state.role==='ADMIN'?200:403)}
   if(path.endsWith('/result'))return json(result(url.searchParams.get('language')||'zh'),state.contentStatus)
   if(path.endsWith('/export'))return json(result(url.searchParams.get('language')||'zh').export,state.contentStatus)
   if(path.endsWith('/video'))return json({},404)
   if(path.match(/\/tasks\/[^/]+$/)&&req.method==='DELETE'){state.tasks=state.tasks.filter(t=>t.id!==path.split('/').at(-1));return json({id:uuid(90),task_id:path.split('/').at(-1),accepted:true},202)}
   if(path.endsWith('/retry')){assert.equal(typeof body.request_key,'string');return json({})}
   if(path.endsWith('/cancel'))return json({})
   return json({},404)
  }
  const dist=join(root,'dist'),file=resolve(dist,'.'+decodeURIComponent(path==='/'?'/index.html':path))
  if(!file.startsWith(dist+sep)||!existsSync(file)){res.writeHead(404);return res.end()}
  res.writeHead(200,{'content-type':({'.js':'text/javascript','.css':'text/css','.html':'text/html'})[extname(file)]||'application/octet-stream'});res.end(readFileSync(file))
 })
 await new Promise(r=>server.listen(0,'127.0.0.1',r))
 const origin='http://127.0.0.1:'+server.address().port
 const browser=[process.env.UI_TEST_BROWSER,join(process.env.PROGRAMFILES||'','Google/Chrome/Application/chrome.exe'),join(process.env['PROGRAMFILES(X86)']||'','Microsoft/Edge/Application/msedge.exe')].filter(Boolean).find(existsSync)
 assert(browser,'Installed Chrome/Edge required')
 const profile=mkdtempSync(join(tmpdir(),'video-summary-wp05-'))
 const child=spawn(browser,['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'],{windowsHide:true,stdio:'ignore'})
 let socket,counter=0;const pending=new Map(),events=[]
 const close=async()=>{socket?.close();child.kill();server.closeAllConnections();await new Promise(r=>server.close(r));await Promise.race([new Promise(r=>child.once('exit',r)),delay(3000)]);assert(resolve(profile).startsWith(resolve(tmpdir())+sep)&&profile.split(sep).at(-1).startsWith('video-summary-wp05-'));rmSync(profile,{recursive:true,force:true,maxRetries:10,retryDelay:200})}
 try{
  const active=join(profile,'DevToolsActivePort'),end=Date.now()+15000
  while(!existsSync(active)){assert(Date.now()<end,'Browser ready timeout');await delay(100)}
  const port=readFileSync(active,'utf8').split('\n')[0],targets=await(await fetch('http://127.0.0.1:'+port+'/json/list')).json()
  socket=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl)
  await new Promise((r,j)=>{socket.onopen=r;socket.onerror=j})
  socket.onmessage=e=>{const m=JSON.parse(e.data);if(m.id&&pending.has(m.id)){pending.get(m.id)(m);pending.delete(m.id)}else events.push(m)}
  const command=(method,params={})=>new Promise((r,j)=>{const id=++counter,t=setTimeout(()=>{pending.delete(id);j(Error('CDP timeout '+method))},15000);pending.set(id,m=>{clearTimeout(t);m.error?j(Error(JSON.stringify(m.error))):r(m.result)});socket.send(JSON.stringify({id,method,params}))})
  const run=async expression=>{const r=await command('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});assert(!r.exceptionDetails,JSON.stringify(r.exceptionDetails));return r.result?.value}
  const wait=async expression=>{const deadline=Date.now()+15000;while(!await run('Boolean('+expression+')')){assert(Date.now()<deadline,'UI wait failed: '+expression);await delay(75)}}
  const click=async(text,scope='document')=>{const q='Array.from('+scope+'.querySelectorAll("button")).find(b=>b.textContent.trim()==='+JSON.stringify(text)+')';await wait(q+'&&!('+q+').disabled');return run('('+q+').click()')}
  const input=(selector,value)=>run('(()=>{const e=document.querySelector('+JSON.stringify(selector)+');Object.getOwnPropertyDescriptor(e.tagName==="SELECT"?HTMLSelectElement.prototype:HTMLInputElement.prototype,"value").set.call(e,'+JSON.stringify(value)+');e.dispatchEvent(new Event(e.tagName==="SELECT"?"change":"input",{bubbles:true}))})()')
  await command('Page.enable');await command('Runtime.enable')
  await command('Page.addScriptToEvaluateOnNewDocument',{source:"window.__downloads=0;window.__alerts=0;window.alert=()=>window.__alerts++;window.confirm=()=>true;HTMLAnchorElement.prototype.click=function(){window.__downloads++};window.__clipboard=[];Object.defineProperty(navigator,'clipboard',{value:{writeText:async v=>window.__clipboard.push(v)},configurable:true})"})
  return {state,calls,run,wait,click,input,command,events,origin,browser,close,get resources(){return {serverClosed:!server.listening,browserExited:child.exitCode!==null||child.signalCode!==null,profileRemoved:!existsSync(profile)}},async navigate(){await command('Page.navigate',{url:origin});await wait("document.querySelector('#email')&&!document.querySelector('.checking')")},async login(email='synthetic@example.invalid'){await input('#email',email);await input('#password','synthetic-only-password');await click('登录',"document.querySelector('form')");await wait("document.querySelector('[aria-labelledby=tasks-title]')")}}
 }catch(e){await close();throw e}
}
