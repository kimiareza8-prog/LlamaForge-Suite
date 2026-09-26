const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const staticDir = path.join(__dirname, '../llamaforge/web/static');
function harness() {
  let sequence=0;
  let elements = new Map();
  const el = key => {
    if (!elements.has(key)) {
      let html = '';
      const e = {isConnected:true,value:'',style:{setProperty(){}},classList:{add(){},remove(){},toggle(){}},dataset:{},setAttribute(){},focus(){},scrollHeight:100,clientHeight:100,
        appendChild(){},querySelector:s=>el(s),querySelectorAll:()=>[]};
      Object.defineProperty(e, 'innerHTML', {get:()=>html,set:x=>{html=x;if(key==='#view'){elements.delete('#composerInput');const tag=x.match(/<textarea[^>]*id="composerInput"[^>]*>/);if(tag)el('#composerInput').disabled=/\sdisabled(?:\s|>)/.test(tag[0]);}}});
      elements.set(key,e);
    }
    return elements.get(key);
  };
  const document={createElement:()=>el('created'),querySelector:el,querySelectorAll:()=>[],body:{classList:{toggle(){}},dataset:{}}};
  const sandbox={document,localStorage:{getItem:()=>null,setItem(){},removeItem(){}},location:{hash:'#chat'},crypto:{randomUUID:()=> 'test-'+(++sequence)},
    AbortController,DOMException,TextDecoder,performance,
    window:{addEventListener(){}},setTimeout(){},requestAnimationFrame(){},LFProtocol:require(path.join(staticDir,'stream_protocol.js')),LFWorkspaceUI:require(path.join(staticDir,'workspace_ui.js')), Date, console};
  let src=fs.readFileSync(path.join(staticDir,'app.js'),'utf8').split('  // shell events')[0];
  src+='\n renderMessages=()=>{};renderNav=()=>{};updateChrome=()=>{};this.fixture={App,renderAgent,refreshState,renderChat,selectThread,renderMarkdown,stopGeneration,generateAssistant,addChatAttachments,openCalendarEventModal,updateChatStateOnly,searchCalendarEvents,showRequestTrace,traceOptions,setFileReader:fn=>fileDataUrl=fn};})();';
  vm.runInNewContext(src,sandbox);
  const f=sandbox.fixture;
  f.App.state={server:{ready:true},brain:{enabled:false},config:{}};
  f.App.threads=[{id:'one',title:'One',messages:[]},{id:'two',title:'Two',messages:[]}];
  f.App.activeThreadId='one';
  return {...f,el,sandbox};
}
test('composer text survives attachment/options rerender and returning to its thread',()=>{
  const {App,el,renderChat,selectThread}=harness();
  renderChat(el('#view'));
  const input=el('#composerInput');input.value='این فایل را خلاصه کن';input.oninput({currentTarget:input});
  App.pendingAttachments=[{id:'a',name:'notes.pdf',kind:'file'}];
  renderChat(el('#view'));
  assert.equal(el('#composerInput').value,'این فایل را خلاصه کن');
  selectThread('two');assert.equal(el('#composerInput').value,'');
  selectThread('one');assert.equal(el('#composerInput').value,'این فایل را خلاصه کن');
  assert.equal(App.pendingAttachments[0].id,'a');
});
test('changing thread cancels the original stream before switching',()=>{
  const {App,selectThread}=harness();let activeAtAbort='';App.streaming=true;
  App.chatAbort={abort(){activeAtAbort=App.activeThreadId;}};
  selectThread('two');assert.equal(activeAtAbort,'one');
});
test('calendar payload keeps server wall time when browser uses another timezone',()=>{
  const {calendarPayload}=require(path.join(staticDir,'workspace_ui.js'));
  const p=calendarPayload({title:'جلسه',date:'2026-09-25',time:'10:00',duration:60,reminder:'30'});
  assert.equal(p.start,'2026-09-25T10:00:00');assert.equal(p.end,'2026-09-25T11:00:00');
  const all=calendarPayload({title:'سفر',date:'2026-12-31',allDay:true});
  assert.equal(all.end,'2027-01-01T00:00:00');assert.equal(all.all_day,true);
  for(const bad of [{date:''},{date:'2026-02-30'},{time:'25:10'},{duration:0},{title:'  '}]){
    assert.throws(()=>calendarPayload({title:'جلسه',date:'2026-09-25',time:'10:00',duration:60,...bad}));
  }
});

test('late cancelled stream cannot delete or unlock the next response',async()=>{
  const {App,sandbox,generateAssistant,stopGeneration}=harness();
  const reads=[];
  sandbox.fetch=async url=>url==='/api/chat/cancel'?{}:{ok:true,body:{getReader:()=>({read:()=>new Promise(resolve=>reads.push(resolve))})}};
  const first=generateAssistant();await new Promise(setImmediate);
  stopGeneration();
  const second=generateAssistant();await new Promise(setImmediate);
  const next=App.threads[0].messages.at(-1);
  reads[0]({done:true});await first;
  assert.ok(App.threads[0].messages.includes(next));assert.equal(App.streaming,true);
  stopGeneration();reads[1]({done:true});await second;
  assert.equal(App.streaming,false);
});
test('agenda overlap excludes midnight end and includes the following occupied day',()=>{
  const {eventsForDay}=require(path.join(staticDir,'workspace_ui.js'));
  const events=[{id:'trip',start:'2026-09-24T10:00+03:30',end:'2026-09-27T00:00+03:30'}];
  assert.equal(eventsForDay(events,'2026-09-26').length,1);
  assert.equal(eventsForDay(events,'2026-09-27').length,0);
});

test('attachment finishing after navigation stays in its original conversation',async()=>{
  const {App,selectThread,addChatAttachments,setFileReader}=harness();
  let finish,calls=0;setFileReader(()=>++calls===1?new Promise(resolve=>finish=resolve):Promise.resolve('data:text/plain;base64,aGVsbG8='));
  const upload=addChatAttachments([{name:'notes.txt',type:'text/plain',size:5},{name:'second.txt',type:'text/plain',size:5}]);
  selectThread('two');finish('data:text/plain;base64,aGVsbG8=');await upload;
  assert.equal(App.pendingAttachments.length,0);
  selectThread('one');assert.equal(App.pendingAttachments.length,2);assert.equal(App.pendingAttachments[0].name,'notes.txt');
});

for(const stage of ['auto-setup','download-base','detect-base','trainer','doctor']){
  test('background Brain '+stage+' keeps chat usable on render and refresh',()=>{
    const {App,el,renderChat,updateChatStateOnly}=harness();
    App.state.brain={enabled:true,job:{state:'running',stage}};
    renderChat(el('#view'));updateChatStateOnly();
    assert.equal(el('#composerInput').disabled,false);
    assert.equal(el('#sendButton').disabled,false);
  });
}
test('typing a date invalidates an older conversion response before blur',async()=>{
  const {el,sandbox,openCalendarEventModal}=harness();let finish;
  sandbox.fetch=()=>new Promise(resolve=>finish=resolve);
  openCalendarEventModal({gregorian:'2026-09-25',jalali:'1405-07-03'},{timezone:'UTC',utc_offset:'+0000'});
  const input=el('#calJalali');input.value='1405-07-03';
  const first=input.onchange({target:input});
  input.value='1405-07-04';input.oninput?.({target:input});
  finish({ok:true,json:async()=>({result:{jalali:'1405-07-03',gregorian:'2026-09-25'}})});
  await first;
  assert.equal(input.value,'1405-07-04');
});

test('calendar search ignores stale responses and escapes titles',async()=>{
  const {App,el,sandbox,searchCalendarEvents}=harness();const replies=[];
  App.route='calendar';sandbox.fetch=url=>new Promise(resolve=>replies.push({url,resolve}));
  const root=el('#calSearchResults');
  App.calendarQuery='جلسه';const first=searchCalendarEvents('جلسه',root,{},()=>{});
  App.calendarQuery='سفر';const second=searchCalendarEvents('سفر',root,{},()=>{});
  replies[1].resolve({ok:true,json:async()=>({events:[{id:'2',title:'<img onerror=alert(1)>',start:'2026-09-25T10:00+00:00'}]})});await second;
  const latest=root.innerHTML;assert.match(latest,/&lt;img/);assert.ok(!latest.includes('<img'));
  assert.ok(replies[0].url.includes(encodeURIComponent('جلسه')));
  replies[0].resolve({ok:true,json:async()=>({events:[{id:'1',title:'old'}]})});await first;
  assert.equal(root.innerHTML,latest);
});

test('composer rerender preserves a pending strict lesson lock',()=>{
  const {App,el,renderChat}=harness();
  App.brainTurnPending=true;App.state.brain={enabled:true,strict_learning:true,job:{state:'idle'}};
  renderChat(el('#view'));
  assert.equal(el('#composerInput').disabled,true);
});


test('request trace timeline ignores stale detail and escapes event text',async()=>{
  const {App,el,sandbox,showRequestTrace}=harness();const replies=[];
  App.route='logs';sandbox.fetch=url=>new Promise(resolve=>replies.push({url,resolve}));
  const one='trace_'+'1'.repeat(32),two='trace_'+'2'.repeat(32);
  const first=showRequestTrace(one),second=showRequestTrace(two);
  replies[1].resolve({ok:true,json:async()=>({events:[{seq:1,event:'<img onerror=alert(1)>',elapsed_ms:1}]})});await second;
  const latest=el('#requestTimeline').innerHTML;
  assert.match(latest,/&lt;img/);assert.ok(!latest.includes('<img'));
  replies[0].resolve({ok:true,json:async()=>({events:[{seq:1,event:'old',elapsed_ms:1}]})});await first;
  assert.equal(el('#requestTimeline').innerHTML,latest);
});
test('trace picker escapes prompt previews and excludes invalid identifiers',()=>{
  const {traceOptions}=harness();
  const html=traceOptions([{id:'trace_'+'a'.repeat(32),preview:'<script>alert(1)</script>',status:'error'},
    {id:'../private',preview:'not an id',status:'error'}]);
  assert.match(html,/&lt;script/);assert.ok(!html.includes('<script>'));assert.ok(!html.includes('../private'));
});


test('agent permission draft survives background refresh and revisiting page',async()=>{
  const {App,el,sandbox,renderAgent,refreshState}=harness();App.route='agent';
  const state={server:{ready:true},brain:{enabled:false},config:{agent_allow_write:true},agent:{}};
  App.state=state;sandbox.fetch=async url=>({ok:true,json:async()=>url==='/api/state'?{...state,agent:{revision:2}}:{}});
  await renderAgent(el('#view'));
  const tick=el('#agentWrite');tick.checked=false;tick.onchange?.();
  await refreshState();await renderAgent(el('#view'));
  assert.equal(el('#agentWrite').checked,false);
  assert.equal(App.agentDraft.agent_allow_write,false);
});
test('agent save retains a newer edit while request is in flight',async()=>{
  const {App,el,sandbox,renderAgent}=harness();App.route='agent';App.state.config={agent_allow_write:false};let finish,payload;
  sandbox.fetch=async (url,opts)=>url==='/api/settings'?new Promise(resolve=>{finish=resolve;payload=JSON.parse(opts.body)}):({ok:true,json:async()=>url==='/api/state'?App.state:{}});
  await renderAgent(el('#view'));
  const tick=el('#agentWrite');tick.checked=true;tick.onchange?.();
  const save=el('#saveAgentSettings').onclick();await new Promise(setImmediate);
  tick.checked=false;tick.onchange?.();finish({ok:true,json:async()=>({ok:true})});await save;
  assert.equal(payload.agent_allow_write,true);assert.equal(App.agentDraft.agent_allow_write,false);
});
