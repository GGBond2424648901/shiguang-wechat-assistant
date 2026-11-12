const {chromium}=require('playwright');
const http=require('node:http'); const fs=require('node:fs'); const path=require('node:path'); const assert=require('node:assert/strict');
const dist=path.resolve(__dirname,'../dist');
const server=http.createServer((req,res)=>{if(req.url==='/favicon.ico'){res.statusCode=204;res.end();return;}let file=path.join(dist,decodeURIComponent(req.url.split('?')[0]));if(req.url==='/')file=path.join(dist,'index.html');try{res.setHeader('Content-Type',file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':file.endsWith('.png')?'image/png':'text/html');res.end(fs.readFileSync(file));}catch{res.statusCode=404;res.end();}});
(async()=>{await new Promise(r=>server.listen(0,'127.0.0.1',r));let browser;try{
browser=await chromium.launch({headless:true,channel:'chrome'});const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
await page.addInitScript(()=>{
 const sessions=[{id:'s1',name:'晨光摄影群',kind:'group',bound:true,mode:'auto',replyMode:'natural',stickerFrequency:'natural',reason:'',calibrated:true,windowState:'ready',pending:0,lastResult:null},{id:'s2',name:'圆桌讨论组',kind:'group',bound:true,mode:'paused',replyMode:'natural',stickerFrequency:'natural',reason:'',calibrated:true,windowState:'ready',pending:0,lastResult:null}];
 const status={connected:true,readOnly:false,mode:'auto',reason:'',primary:'s1',accountLabel:'测试账号',sessions,revision:'cfg1',tray:false,stats:{pending:0,verified:0,unknown:0,failed:0,skipped:0}};
 const prefs={enabled:false,followup:true,idle:true,gentle_followups:true,daily_max:3,quiet_start:1380,quiet_end:480,style:'',interests:'',dates:[],routines:[],idle_probability:.5,followup_probability:.5,idle_min_minutes:30,idle_max_minutes:120,followup_min_minutes:10,followup_max_minutes:30,checkin_min_minutes:30,checkin_max_minutes:60,signoff_min_minutes:10,signoff_max_minutes:20};
 const jobs={};let n=0;window.qaCalls=[];window.qaSend=[];let watcher;window.qaSwitch=()=>{status.revision+='b';status.accountScope='account-b';watcher?.({...status});};
 window.shiguang={draft:async()=>{},watch:fn=>{watcher=fn;return()=>{watcher=undefined;};},windowState:()=>()=>{},appInfo:async()=>({maximized:false,hasWallpaper:false}),zoom:async()=>{},windowAction:async()=>{},request:async(method,params={})=>{
 window.qaCalls.push({method,params});
 if(method==='view.status')return status;if(method==='view.queue')return{total:0,receiptTotal:0,note:'',tasks:[],receipts:[]};
 if(method==='appearance.read')return{transparency:21,reduceMotion:true,zoomPercent:100,revision:'a'};
 if(method==='persona.read')return{format:'modules',title:'试聊人格',revision:'p1',modules:[{id:'main',title:'身份与语气',text:'完整人格原文',enabled:true,origin:'主稿'},{id:'other',title:'补充设定',text:'停用内容',enabled:false,origin:'自定义'}]};
 if(method==='companion.read')return{session:params.session,bound:true,preferences:prefs,revision:'c1'};
 if(method==='companion.state')return{connected:true,reason:'',routines:[],history:[]};
 if(method==='studio.chat'){if(params.text.startsWith('延迟拒绝')){await new Promise(resolve=>{window.qaReject=resolve;});throw new Error('延迟服务拒绝');}if(params.text==='服务拒绝本轮')throw new Error('已有两个模型任务运行，请等待或取消后重试');const job='j'+(++n);if(params.text==='取消本轮')return jobs[job]={job,kind:'chat',conversation:params.conversation||'conv1',status:'running'};if(params.text==='迟到问题')await new Promise(r=>setTimeout(r,500));return jobs[job]={job,kind:'chat',conversation:params.conversation||'conv1',status:'completed',result:{text:params.text==='迟到问题'?'迟到回复':'已经记得前面的试聊',turns:1,contextLimit:24,enabledModules:['身份与语气']}};}
 if(method==='studio.summary'){const job='j'+(++n);return jobs[job]={job,kind:'summary',status:'completed',result:{highlights:['讨论了周五会议'],tasks:['小张确认场地'],dates:['周五（具体日期未约定）'],sendText:'讨论要点：周五会议\n行动事项：小张确认场地',messageCount:16,availableCount:140,coveredStart:1735804800,coveredEnd:1735804900,truncated:true,insufficient:false,storedOnly:true}};}
 if(method==='studio.state')return jobs[params.job];
 if(method==='studio.cancel'){if(jobs[params.job])jobs[params.job]={...jobs[params.job],status:'canceled',result:undefined};return jobs[params.job];}
 if(method==='studio.sendSummary'){window.qaSend.push(params);if(params.text==='响应丢失的原正文'&&!jobs[params.job].delivery){jobs[params.job].delivery={state:'unresolved',submittedText:params.text};throw new Error('synthetic lost response');}jobs[params.job].delivery={queued:true,id:'task',state:'queued',submittedText:params.text};return jobs[params.job].delivery;}
 throw new Error('unsupported test route '+method);
 }};
});

const url='http://127.0.0.1:'+server.address().port;await page.goto(url);
await page.getByRole('button',{name:'人格',exact:true}).click();await page.getByRole('button',{name:'人格试聊',exact:true}).click();
await page.getByLabel('试聊消息').fill('第一问');await page.getByRole('button',{name:'发送试聊',exact:true}).click();await page.getByText('已经记得前面的试聊',{exact:true}).waitFor();
await page.getByLabel('试聊消息').fill('服务拒绝本轮');await page.getByRole('button',{name:'发送试聊',exact:true}).click();await page.getByText('已有两个模型任务运行，请等待或取消后重试',{exact:true}).waitFor();
const rejected={bubbles:await page.locator('.studio-bubble').allInnerTexts(),input:await page.getByLabel('试聊消息').inputValue()};
assert.equal(rejected.input,'服务拒绝本轮');assert.equal(rejected.bubbles.length,2);assert.equal(await page.locator('.studio-job-note').innerText(),'');
if(process.env.SG_STUDIO_OUTPUT){fs.mkdirSync(process.env.SG_STUDIO_OUTPUT,{recursive:true});await page.screenshot({path:path.join(process.env.SG_STUDIO_OUTPUT,'trial-rejection-restored.png')});}
await page.getByLabel('试聊消息').fill('之后一轮');await page.getByRole('button',{name:'发送试聊',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('.studio-bubble.assistant').length===2);
assert.equal(await page.locator('.studio-bubble').count(),4);
const send = async text => { await page.getByLabel('试聊消息').fill(text); await page.getByRole('button',{name:'发送试聊',exact:true}).click(); };
// A rejected optimistic 25th entry must not evict the oldest committed entry.
for(let i=2;i<12;i++) { await send('已提交 '+i); await page.waitForFunction(count=>document.querySelectorAll('.studio-bubble').length===count,(i+1)*2); }
const committed=await page.locator('.studio-bubble').allInnerTexts();assert.equal(committed.length,24);
await send('服务拒绝本轮');await page.getByText('已有两个模型任务运行，请等待或取消后重试',{exact:true}).waitFor();
assert.deepEqual(await page.locator('.studio-bubble').allInnerTexts(),committed);assert.equal(await page.getByLabel('试聊消息').inputValue(),'服务拒绝本轮');
// Cancel before the bridge returns a job restores the pending text immediately.
await send('延迟拒绝取消');await page.getByRole('button',{name:'取消生成',exact:true}).click();
assert.equal(await page.getByLabel('试聊消息').inputValue(),'延迟拒绝取消');assert.deepEqual(await page.locator('.studio-bubble').allInnerTexts(),committed);
await page.evaluate(()=>window.qaReject());
await page.getByLabel('试聊消息').fill('取消后输入');
// A late rejection after clear cannot restore the abandoned turn or clobber new input.
await send('延迟拒绝重置');await page.getByRole('button',{name:'清空试聊',exact:true}).click();await page.getByLabel('试聊消息').fill('重置后输入');await page.evaluate(()=>window.qaReject());
assert.equal(await page.locator('.studio-bubble').count(),0);assert.equal(await page.getByLabel('试聊消息').inputValue(),'重置后输入');
// Editing the persona unmounts/fences the old request, retaining the persona draft itself.
await send('延迟拒绝草稿');await page.getByRole('button',{name:'人格编辑',exact:true}).click();
await page.getByLabel('人格提示词').fill('新的完整人格草稿');await page.getByRole('button',{name:'人格试聊',exact:true}).click();
await page.getByLabel('试聊消息').fill('新草稿输入');await page.evaluate(()=>window.qaReject());
assert.equal(await page.locator('.studio-bubble').count(),0);assert.equal(await page.getByLabel('试聊消息').inputValue(),'新草稿输入');
await send('新草稿成功');await page.locator('.studio-bubble.assistant').waitFor();
assert.equal(await page.evaluate(()=>window.qaCalls.filter(x=>x.method==='studio.chat').at(-1).params.modules[0].text),'新的完整人格草稿');
// Account change invalidates the pending request and committed conversation.
await send('延迟拒绝换号');await page.evaluate(()=>window.qaSwitch());await page.waitForFunction(()=>!document.querySelectorAll('.studio-bubble').length);
await page.getByLabel('试聊消息').fill('新账号输入');await page.evaluate(()=>window.qaReject());
assert.equal(await page.locator('.studio-bubble').count(),0);assert.equal(await page.getByLabel('试聊消息').inputValue(),'新账号输入');
await send('新账号成功');await page.locator('.studio-bubble.assistant').waitFor();
assert.equal(await page.evaluate(()=>window.qaCalls.filter(x=>x.method==='studio.chat').at(-1).params.conversation),'');
assert.equal(await page.getByText('延迟服务拒绝',{exact:true}).count(),0);
assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,url,title:await page.title(),rejected,checks:['pre-job rejection rollback','24-message committed history retained','pending-start cancellation','reset fence','draft fence','account fence'],errors}));
}finally{if(browser)await browser.close();server.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
