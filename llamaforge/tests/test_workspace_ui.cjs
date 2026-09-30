const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const staticDir = path.join(__dirname, '../llamaforge/web/static');
function harness({agentDOM=false}={}) {
  let sequence=0;
  let elements = new Map();
  const el = key => {
    if (!elements.has(key)) {
      let html = '';
      const e = {isConnected:true,value:'',style:{setProperty(){}},classList:{add(){},remove(){},toggle(){}},dataset:{},setAttribute(){},addEventListener(type,fn){this['on'+type]=fn},focus(){},scrollHeight:100,clientHeight:100,
        appendChild(){},querySelector:s=>el(s),querySelectorAll:()=>[]};
      if(agentDOM){e.focus=()=>{document.activeElement=e};e.closest=s=>s==='.agent-page'&&e.agentControl&&e.isConnected?el('#view'):null;}
      Object.defineProperty(e, 'innerHTML', {get:()=>html,set:x=>{
        if(agentDOM&&key==='#view'){
          for(const match of html.matchAll(/\bid="([^"]+)"/g)){
            const old=elements.get('#'+match[1]);if(old){old.isConnected=false;if(document.activeElement===old)document.activeElement=document.body;}
            elements.delete('#'+match[1]);
          }
          for(const match of x.matchAll(/<([a-z]+)\b[^>]*\bid="([^"]+)"[^>]*>/g)){
            const node=el('#'+match[2]);node.tagName=match[1].toUpperCase();node.agentControl=x.includes('agent-page');
            node.disabled=/\sdisabled(?:\s|>)/.test(match[0]);node.checked=/\schecked(?:\s|>)/.test(match[0]);
          }
        }
        html=x;if(key==='#view'){elements.delete('#composerInput');const tag=x.match(/<textarea[^>]*id="composerInput"[^>]*>/);if(tag)el('#composerInput').disabled=/\sdisabled(?:\s|>)/.test(tag[0]);}
      }});
      elements.set(key,e);
    }
    return elements.get(key);
  };
  const document={createElement:()=>el('created'),querySelector:s=>agentDOM&&/^#(?:telegram|installTelegram)/.test(s)?elements.get(s)||null:el(s),querySelectorAll:()=>[],body:{classList:{toggle(){}},dataset:{}}};
  const sandbox={document,localStorage:{getItem:()=>null,setItem(){},removeItem(){}},location:{hash:'#chat'},crypto:{randomUUID:()=> 'test-'+(++sequence)},
    AbortController,DOMException,TextDecoder,performance,
    window:{addEventListener(){},open(){return {}}},setTimeout(){},clearTimeout(){},requestAnimationFrame(){},LFProtocol:require(path.join(staticDir,'stream_protocol.js')),LFWorkspaceUI:require(path.join(staticDir,'workspace_ui.js')), Date, console};
  let src=fs.readFileSync(path.join(staticDir,'app.js'),'utf8').split('  // shell events')[0];
  src+='\n renderMessages=()=>{};renderNav=()=>{};updateChrome=()=>{};this.fixture={App,renderAgent,refreshState,renderChat,selectThread,renderMarkdown,stopGeneration,generateAssistant,addChatAttachments,toggleVoiceRecording,openCalendarEventModal,updateChatStateOnly,searchCalendarEvents,showRequestTrace,traceOptions,chatPrefs,contextLabel,contextPercent,showChatOptions,apiProviderEditing,apiProviderCard,holdApiProviderInteraction,telegramPageShell,telegramSendFromPage,telegramLoadMedia,loadTelegramMessages,loadTelegramDashboard,programMonitorEvent,programMonitorFinish,setRender:fn=>render=fn,setFileReader:fn=>fileDataUrl=fn};})();';
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

test('API provider pointer interaction blocks pre-focus background rerender',()=>{
  const {App,apiProviderEditing,holdApiProviderInteraction,sandbox}=harness();
  App.route='models';App.apiProviderDraft={openai:'',gemini:''};App.apiProviderBusy={openai:false,gemini:false};
  assert.equal(apiProviderEditing(),false);
  holdApiProviderInteraction(2000);
  assert.equal(apiProviderEditing(),true);
  App.apiProviderInteractionUntil=0;
  sandbox.document.activeElement={matches:selector=>selector.includes('select[data-api-model]')};
  assert.equal(apiProviderEditing(),true);
  sandbox.document.activeElement={matches:selector=>selector.includes('input[data-api-output-open]')};
  assert.equal(apiProviderEditing(),true,'a focused API output marker editor is protected from refresh rerenders');
  sandbox.document.activeElement=sandbox.document.body;
  App.apiProviderInteractionUntil=0;App.apiProviderSelectOpen.openai=true;
  assert.equal(apiProviderEditing(),true,'the native popup stays protected after the pointer timer expires');
  App.apiProviderSelectOpen.openai=false;
  App.apiProviderDraft.openai='key-in-progress';
  assert.equal(apiProviderEditing(),false,'the input draft is retained in app state after blur');
});
test('API model choice and per-model test results survive provider-card rerenders',()=>{
  const {App,apiProviderCard}=harness();
  App.apiProviderSelection.openai='gpt-second';
  App.apiProviderTests.openai['gpt-second']={ok:true,response:'سلام!'};
  const html=apiProviderCard('openai',{name:'OpenAI',configured:true,models:[{id:'gpt-first'},{id:'gpt-second'}]}, {}, {backend:'openai',model:{id:'gpt-first'}});
  assert.match(html,/value="gpt-second" selected/);
  assert.match(html,/✓ gpt-second/);
  assert.match(html,/Test passed · gpt-second/);
});
test('API model output syntax is editable and stays scoped to its provider model',()=>{
  const {App,apiProviderCard}=harness();
  App.apiProviderSelection.gemini='gemma-output-test';
  const provider={name:'Gemini',configured:true,models:[{id:'gemma-output-test'}],output_syntax:{'gemma-output-test':{open_marker:'<thought>',close_marker:'</thought>'}}};
  let html=apiProviderCard('gemini',provider,{},{});
  assert.match(html,/Output syntax/);
  assert.match(html,/<details class="api-output-syntax" open>/);
  assert.match(html,/Custom markers/);
  assert.match(html,/value="&lt;thought&gt;"/);
  assert.match(html,/value="&lt;\/thought&gt;"/);
  App.apiOutputSyntaxDraft['gemini::gemma-output-test']={open_marker:'BEGIN',close_marker:'END'};
  html=apiProviderCard('gemini',provider,{},{});
  assert.match(html,/value="BEGIN"/);
  assert.match(html,/value="END"/);
});
test('forced state refresh does not replace a focused API key field',async()=>{
  const {App,sandbox,refreshState,setRender}=harness();
  App.route='models';App.renderKey='before';
  sandbox.document.activeElement={matches:selector=>selector.includes('input[id^="apiKey-"]')};
  sandbox.fetch=async()=>({ok:true,json:async()=>({server:{ready:false,running:false},inference:{backend:'openai',external:true,ready:true,model:{id:'gpt-test'}},api:{providers:{}},models:[],trainable_models:[]})});
  let renders=0;setRender(()=>renders++);
  await refreshState(true);
  assert.equal(renders,0);
  assert.equal(App.uiDeferredRender,true);
});
test('ready Gemini model can send chat while the local server is stopped',async()=>{
  const {App,sandbox,generateAssistant}=harness();
  App.route='chat';
  App.state={server:{ready:false,running:false},inference:{backend:'gemini',external:true,ready:true,model:{id:'gemini-2.5-flash'}},brain:{enabled:false},config:{agent_enabled_default:false}};
  let requested='';
  sandbox.fetch=async url=>{requested=url;return {ok:false,status:503,json:async()=>({error:'test stop'})}};
  await generateAssistant();
  assert.equal(requested,'/api/chat/stream');
});

test('chat and API context read the saved shared settings instead of an old browser cap or local plan',()=>{
  const {App,sandbox,chatPrefs,contextLabel,contextPercent,showChatOptions,el}=harness();
  sandbox.localStorage.getItem=key=>key==='lf.maxTokens'?'16':null;
  App.state={config:{default_context_size:12000,generation_max_tokens:7000},server:{ready:false,plan:{ctx_size:512}},
    inference:{backend:'gemini',external:true,ready:true,model:{id:'gemini-test'}},brain:{enabled:false}};
  App.contextTokens=100;
  assert.equal(chatPrefs().maxTokens,7000);
  assert.equal(contextLabel(),'~100 / 12,000 budget');
  assert.equal(contextPercent(),100/12000*100);
  showChatOptions();
  assert.match(el('#modalRoot').innerHTML,/Current output limit: 7,000 tokens/);
  assert.doesNotMatch(el('#modalRoot').innerHTML,/id="maxTokens"/);
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

test('Telegram page renders attachment metadata, explicit download, and composer',()=>{
  const {App,el,telegramPageShell}=harness();App.route='telegram';
  App.state={config:{agent_allow_telegram_write:true,agent_allow_workspace_write:true},agent:{telegram:{connected:true,installed:true}}};
  App.telegramPage.selected='chat_abc';App.telegramPage.data={dialogs:[{chat_ref:'chat_abc',name:'Friends',kind:'group'}]};
  App.telegramPage.messages=[{message_id:3,text:'<img src=x>',has_media:true,media:{name:'photo.jpg',kind:'photo',size:3,mime_type:'image/jpeg'},outgoing:false}];
  telegramPageShell(el('#view'));const html=el('#view').innerHTML;
  assert.match(html,/data-tg-media="3"/);assert.match(html,/photo.jpg/);
  assert.match(html,/id="telegramMessageDraft"/);assert.match(html,/id="telegramFileInput"/);
  assert.match(html,/id="telegramSendMessage"/);assert.ok(!html.includes('<img src=x>'));
});



test('Telegram page explicit reconnect overrides a persisted paused saved session',async()=>{
  const {App,sandbox,loadTelegramDashboard}=harness();App.route='telegram';
  App.state={config:{},agent:{telegram:{connected:false,saved_session:true,paused:true,installed:true,vault_installed:true}}};
  const calls=[];
  sandbox.fetch=async(url,options)=>{
    const body=options?.body?JSON.parse(options.body):null;calls.push({url,body});
    if(url==='/api/agent/status')return {ok:true,json:async()=>({telegram:{connected:false,saved_session:true,paused:true,installed:true,vault_installed:true}})};
    if(url==='/api/agent/telegram/login')return {ok:true,json:async()=>({connected:true,saved_session:true,paused:false,installed:true,vault_installed:true,account:{name:'Me'}})};
    if(url==='/api/agent/telegram/dashboard')return {ok:true,json:async()=>({dialogs:[]})};
    throw new Error(`Unexpected request: ${url}`);
  };
  await loadTelegramDashboard(true,true);
  const resume=calls.find(x=>x.url==='/api/agent/telegram/login');
  assert.deepEqual(resume?.body,{resume:true});
  assert.equal(App.state.agent.telegram.connected,true);
});

test('Telegram page sends once and clears its draft after confirmation',async()=>{
  const {App,el,sandbox,telegramPageShell,telegramSendFromPage}=harness();App.route='telegram';
  App.state={config:{agent_allow_telegram_write:true,agent_allow_workspace_write:true},agent:{telegram:{connected:true,installed:true}}};
  App.telegramPage.selected='chat_abc';App.telegramPage.data={dialogs:[{chat_ref:'chat_abc',name:'Friends',kind:'group'}]};
  const actions=[];sandbox.fetch=async(url,options)=>{
    if(url==='/api/agent/telegram/action'){actions.push(JSON.parse(options.body));return {ok:true,json:async()=>({message_id:77,verification:{verified:true}})};}
    if(url==='/api/agent/telegram/messages')return {ok:true,json:async()=>({messages:[{message_id:77,text:'سلام',outgoing:true}]})};
    throw new Error(`Unexpected request: ${url}`);
  };
  telegramPageShell(el('#view'));el('#telegramMessageDraft').value='سلام';
  await telegramSendFromPage();
  assert.equal(actions.length,1);assert.deepEqual(actions[0],{operation:'send',chat_ref:'chat_abc',text:'سلام'});
  assert.equal(App.telegramPage.drafts.chat_abc,'');assert.equal(App.telegramPage.messages[0].message_id,77);
});

test('Telegram media download is explicit, saved once, and previews a photo locally',async()=>{
  const {App,el,sandbox,telegramPageShell,telegramLoadMedia}=harness();App.route='telegram';
  App.state={config:{agent_allow_telegram_write:true,agent_allow_workspace_write:true},agent:{telegram:{connected:true,installed:true}}};
  App.telegramPage.selected='chat_abc';App.telegramPage.data={dialogs:[{chat_ref:'chat_abc',name:'Friends',kind:'group'}]};
  App.telegramPage.messages=[{message_id:9,has_media:true,media:{kind:'photo',mime_type:'image/jpeg'},outgoing:false}];
  let downloads=0,clicked=0;
  sandbox.URL={createObjectURL:()=> 'blob:photo',revokeObjectURL(){}};sandbox.Blob=Blob;
  sandbox.document.body.appendChild=()=>{};el('created').click=()=>{clicked++};el('created').remove=()=>{};
  sandbox.fetch=async url=>{
    if(url==='/api/agent/telegram/download'){downloads++;return {ok:true,json:async()=>({file:{id:'file_9',name:'photo.jpg'}})};}
    if(url==='/api/workspace/download?id=file_9')return {ok:true,blob:async()=>new Blob(['photo'],{type:'image/jpeg'})};
    throw new Error(`Unexpected request: ${url}`);
  };
  telegramPageShell(el('#view'));
  assert.equal(downloads,0);
  await telegramLoadMedia(9);
  assert.equal(downloads,1);assert.equal(clicked,1);
  assert.match(el('#view').innerHTML,/src="blob:photo"/);
  await telegramLoadMedia(9);assert.equal(downloads,1);
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

test('Agent settings expose disabled-by-default command/tool toggles and separate local voice paths',async()=>{
  const {App,el,sandbox,renderAgent,setRender}=harness();App.route='agent';setRender(()=>{});
  const state={server:{ready:true},brain:{enabled:false},config:{agent_allow_system_commands:false,agent_allow_tool_creation:false,audio_ffmpeg_path:'',audio_vosk_model_path:''},agent:{builtin_tools:['calendar'],telegram:{}}};
  App.state=state;
  sandbox.fetch=async(url,opts={})=>{
    if(url==='/api/agent/status')return {ok:true,json:async()=>state.agent};
    if(url==='/api/settings')return {ok:true,json:async()=>({ok:true})};
    if(url==='/api/state')return {ok:true,json:async()=>state};
    if(url==='/api/voice/status')return {ok:true,json:async()=>({ready:false,recordings_dir:'/local/voice'})};
    return {ok:true,json:async()=>({ok:true})};
  };
  await renderAgent(el('#view'));
  assert.equal(el('#agentSystemCommands').checked,false);assert.equal(el('#agentCreateTools').checked,false);
  assert.ok(el('#audioFfmpegPath'));assert.ok(el('#audioVoskModelPath'));assert.ok(el('#saveVoiceSettings'));
  el('#audioFfmpegPath').value='/tools/ffmpeg';el('#audioVoskModelPath').value='/models/vosk-model-fa';
  let saved;const original=sandbox.fetch;sandbox.fetch=async(url,opts={})=>{if(url==='/api/settings')saved=JSON.parse(opts.body);return original(url,opts)};
  await el('#saveVoiceSettings').onclick();
  assert.equal(saved.audio_ffmpeg_path,'/tools/ffmpeg');assert.equal(saved.audio_vosk_model_path,'/models/vosk-model-fa');
});
test('program builder toggle and job status save through Agent settings',async()=>{
  const {App,el,sandbox,renderAgent,setRender}=harness();App.route='agent';setRender(()=>{});
  App.state={server:{ready:true},brain:{enabled:false},config:{agent_allow_code_execution:false},agent:{code_jobs:{jobs:[{job_id:'job_1234567890abcdef',name:'Demo',status:'running'}]}}};
  let saved;
  sandbox.fetch=async(url,opts={})=>({ok:true,json:async()=>{
    if(url==='/api/settings'){saved=JSON.parse(opts.body);return {ok:true}}
    return url==='/api/agent/status'?App.state.agent:App.state;
  }});
  await renderAgent(el('#view'));
  assert.equal(el('#agentCodeExecution').checked,false);
  assert.match(el('#view').innerHTML,/Demo/);
  const toggle=el('#agentCodeExecution');toggle.checked=true;toggle.onchange();
  await el('#saveAgentSettings').onclick();
  assert.equal(saved.agent_allow_code_execution,true);
});
test('program window opens for generic code actions and streams process output',async()=>{
  const {App,el,sandbox,programMonitorEvent,programMonitorFinish}=harness();
  const id='job_1234567890abcdef';
  sandbox.fetch=async()=>({ok:true,json:async()=>({ok:true,result:{job_id:id,status:'running',output:'سلام از برنامه\n'}})});
  programMonitorEvent({tool:'code_job',event:'tool_start',arguments:{operation:'new',name:'Example'}});
  assert.equal(el('#programMonitor').hidden,false);
  programMonitorEvent({tool:'code_job',event:'tool_result',ok:true,code_job:{operation:'new',job_id:id,status:'created'}});
  programMonitorEvent({tool:'code_job',event:'tool_start',arguments:{operation:'write',job_id:id,path:'main.py',content:'print("سلام")'}});
  assert.equal(el('#programMonitorCode').textContent,'print("سلام")');
  programMonitorEvent({tool:'code_job',event:'tool_result',ok:true,code_job:{operation:'write',job_id:id}});
  programMonitorEvent({tool:'code_job',event:'tool_start',arguments:{operation:'run',job_id:id,path:'main.py'}});
  programMonitorEvent({tool:'code_job',event:'tool_result',ok:true,code_job:{operation:'run',job_id:id,status:'running'}});
  await new Promise(setImmediate);
  assert.equal(el('#programMonitorOutput').textContent,'سلام از برنامه\n');
  assert.equal(el('#programMonitorStop').disabled,false);
  programMonitorFinish();
  assert.equal(App.programMonitor.visible,true);
});
test('a failed code action does not falsely mark a live program as failed',async()=>{
  const {App,el,sandbox,programMonitorEvent}=harness();
  const id='job_1234567890abcdef';
  sandbox.fetch=async()=>({ok:true,json:async()=>({ok:true,result:{job_id:id,status:'running',output:'GUI is still alive\n'}})});
  programMonitorEvent({tool:'code_job',event:'tool_start',arguments:{operation:'run',job_id:id,path:'main.py'}});
  programMonitorEvent({tool:'code_job',event:'tool_result',ok:true,code_job:{operation:'run',job_id:id,status:'running'}});
  assert.equal(App.programMonitor.processStatus,'running');
  programMonitorEvent({tool:'code_job',event:'tool_start',arguments:{operation:'run',job_id:id,path:'main.py'}});
  programMonitorEvent({tool:'code_job',event:'tool_result',ok:false,error_preview:'Stop or finish this job before starting another process',code_job:{operation:'run',job_id:id,status:''}});
  await new Promise(setImmediate);
  assert.equal(App.programMonitor.processStatus,'running');
  assert.equal(App.programMonitor.status,'running');
  assert.equal(el('#programMonitorStop').disabled,false);
  assert.match(el('#programMonitorSteps').innerHTML,/failed/);
});

test('program window escapes untrusted labels and keeps failures visible',()=>{
  const {App,el,programMonitorEvent,programMonitorFinish}=harness();
  programMonitorEvent({tool:'code_job',event:'tool_start',arguments:{operation:'write',job_id:'job_1234567890abcdef',path:'<img src=x onerror=alert(1)>',content:'<script>not HTML</script>'}});
  assert.ok(!el('#programMonitorSteps').innerHTML.includes('<img'));
  assert.equal(el('#programMonitorCode').textContent,'<script>not HTML</script>');
  programMonitorEvent({tool:'code_job',event:'tool_result',ok:false,error_preview:'syntax error',code_job:{operation:'write'}});
  programMonitorFinish();
  assert.equal(App.programMonitor.visible,true);
  assert.match(el('#programMonitorSteps').innerHTML,/failed/);
});
test('successful program window closes after the Agent finishes; hiding does not stop the program',()=>{
  const {App,el,sandbox,programMonitorEvent,programMonitorFinish}=harness();
  let closeTimer,requests=0;sandbox.setTimeout=fn=>{closeTimer=fn;return 1};sandbox.fetch=async()=>{requests++;return {ok:true,json:async()=>({ok:true})}};
  programMonitorEvent({tool:'code_job',event:'tool_start',arguments:{operation:'run',job_id:'job_1234567890abcdef',path:'main.py'}});
  programMonitorEvent({tool:'code_job',event:'tool_result',ok:true,code_job:{operation:'run',job_id:'job_1234567890abcdef',status:'finished',output:'done'}});
  programMonitorFinish();assert.equal(typeof closeTimer,'function');closeTimer();
  assert.equal(el('#programMonitor').hidden,true);assert.equal(requests,0);
  programMonitorEvent({tool:'code_job',event:'tool_start',arguments:{operation:'run',job_id:'job_1234567890abcdef'}});
  el('#programMonitorClose').onclick();
  assert.equal(App.programMonitor.dismissed,true);
  assert.equal(requests,0,'hiding the monitor does not call stop');
});
test('chat composer includes a microphone control for local Vosk voice input',()=>{
  const {App,el,renderChat}=harness();App.route='chat';App.state={server:{ready:true},inference:{ready:true,backend:'local',external:false},active_model:{name:'Local'},voice:{ready:true},brain:{enabled:false},config:{}};
  renderChat(el('#view'));
  assert.match(el('#view').innerHTML,/id="recordVoice"/);assert.match(el('#view').innerHTML,/Record, transcribe locally and send/);
});

test('voice transcription stays in its original chat after navigation',async()=>{
  const {App,el,sandbox,renderChat,selectThread,toggleVoiceRecording}=harness();
  App.route='chat';App.state={server:{ready:true},inference:{ready:true,backend:'local',external:false},active_model:{name:'Local'},voice:{ready:true},brain:{enabled:false},config:{}};
  const recorders=[];let finishUpload,chatRequests=0;
  class Recorder {
    static isTypeSupported(){return true}
    constructor(){this.mimeType='audio/webm';this.state='inactive';recorders.push(this)}
    start(){this.state='recording'}
    stop(){this.state='inactive';this.ondataavailable({data:new Blob(['audio'])});this.finished=this.onstop()}
  }
  sandbox.window.isSecureContext=true;sandbox.window.MediaRecorder=Recorder;sandbox.MediaRecorder=Recorder;sandbox.Blob=Blob;
  sandbox.navigator={mediaDevices:{getUserMedia:async()=>({getTracks:()=>[{stop(){}}]})}};
  sandbox.setTimeout=(callback,delay)=>{if(delay===800)queueMicrotask(callback);return 1};
  sandbox.fetch=async url=>{
    if(url==='/api/voice/transcribe')return new Promise(resolve=>{finishUpload=resolve});
    if(url.startsWith('/api/voice/job'))return {ok:true,json:async()=>({state:'done',text:'سلام',filename:'voice.webm'})};
    if(url==='/api/chat/stream')chatRequests++;
    return {ok:true,json:async()=>({})};
  };
  renderChat(el('#view'));await toggleVoiceRecording();recorders[0].stop();
  selectThread('two');assert.match(el('#view').innerHTML,/aria-label="Cancel upload"/);
  finishUpload({ok:true,json:async()=>({job:'voice_test'})});
  await recorders[0].finished;
  assert.equal(chatRequests,0);assert.equal(App.threads.find(t=>t.id==='two').messages.length,0);
  assert.equal(App.chatDrafts.one,'سلام');assert.equal(el('#composerInput').value,'');
  selectThread('one');assert.equal(el('#composerInput').value,'سلام');
});

test('voice recording stops before collecting more than 25 MB',async()=>{
  const {App,el,sandbox,renderChat,toggleVoiceRecording}=harness();
  App.route='chat';App.state={server:{ready:true},inference:{ready:true,backend:'local',external:false},active_model:{name:'Local'},voice:{ready:true},brain:{enabled:false},config:{}};
  let recorder,uploads=0;
  class Recorder {
    static isTypeSupported(){return true}
    constructor(){this.mimeType='audio/webm';this.state='inactive';recorder=this}
    start(){this.state='recording'}
    stop(){this.state='inactive';this.finished=this.onstop()}
  }
  sandbox.window.isSecureContext=true;sandbox.window.MediaRecorder=Recorder;sandbox.MediaRecorder=Recorder;sandbox.Blob=Blob;
  sandbox.navigator={mediaDevices:{getUserMedia:async()=>({getTracks:()=>[{stop(){}}]})}};
  sandbox.fetch=async()=>{uploads++;return {ok:true,json:async()=>({})}};
  renderChat(el('#view'));await toggleVoiceRecording();
  renderChat(el('#view'));assert.match(el('#view').innerHTML,/aria-label="Stop recording and transcribe"/);
  recorder.ondataavailable({data:{size:25*1024*1024+1}});
  await recorder.finished;
  assert.equal(recorder.state,'inactive');assert.equal(uploads,0);
  assert.equal(App.voiceChunks.length,0);assert.equal(App.voiceRecording,false);
});

async function telegramFixture(){
  const h=harness({agentDOM:true});h.App.route='agent';h.App.state.agent={telegram:{installed:true,vault_installed:true}};
  h.requests=[];h.persisted=[];h.reply=async()=>({next:'code',login_pending:true,connected:false});
  h.sandbox.localStorage.setItem=(...args)=>h.persisted.push(args);
  h.sandbox.fetch=async(url,opts={})=>{
    if(url.startsWith('/api/agent/telegram/')){h.requests.push({url,body:JSON.parse(opts.body)});const reply=await h.reply(url);return {ok:true,json:async()=>reply};}
    return {ok:true,json:async()=>url==='/api/agent/status'?h.App.state.agent:url==='/api/state'?h.App.state:{ok:true}};
  };
  h.fill=(id,value)=>{const input=h.el('#'+id);input.value=value;input.oninput?.({currentTarget:input});return input};
  await h.renderAgent(h.el('#view'));
  h.fillLogin=()=>{h.fill('telegramApiId','123');h.fill('telegramApiHash','a'.repeat(32));h.fill('telegramPhone','+12025550123')};
  return h;
}
test('late Agent status cannot replace the focused Telegram input while typing',async()=>{
  const h=await telegramFixture();let resolve;
  h.sandbox.fetch=()=>new Promise(r=>{resolve=r});
  const pending=h.renderAgent(h.el('#view'));
  const phone=h.fill('telegramPhone','+1202');phone.focus();phone.selectionStart=5;
  resolve({ok:true,json:async()=>({telegram:{installed:true,vault_installed:true}})});await pending;
  assert.equal(h.el('#telegramPhone'),phone);assert.equal(phone.value,'+1202');assert.equal(h.sandbox.document.activeElement,phone);
});
test('Telegram login fields survive forced refresh and revisiting without browser storage',async()=>{
  const h=await telegramFixture();h.fillLogin();h.fill('telegramCode','12345');h.fill('telegramPassword','fixture password');
  await h.refreshState(true);await new Promise(setImmediate);
  h.App.route='chat';h.el('#view').innerHTML='<section>Another page</section>';h.App.route='agent';await h.renderAgent(h.el('#view'));
  for(const [id,value] of [['telegramApiId','123'],['telegramApiHash','a'.repeat(32)],['telegramPhone','+12025550123'],['telegramCode','12345'],['telegramPassword','fixture password']])assert.equal(h.el('#'+id).value,value,id);
  assert.deepEqual(h.persisted,[]);
});
test('Telegram login completion after leaving the page preserves the next step',async()=>{
  const h=await telegramFixture();h.fillLogin();let resolve;h.reply=()=>new Promise(r=>{resolve=r});
  const pending=h.el('#telegramConnect').onclick();await new Promise(setImmediate);
  h.App.route='chat';h.el('#view').innerHTML='<section>Another page</section>';
  resolve({next:'code',login_pending:true,connected:false});await pending;
  h.App.route='agent';await h.renderAgent(h.el('#view'));
  assert.match(h.el('#telegramLoginStatus').textContent,/verification code/i);
  assert.equal(h.el('#telegramApiHash').value,'');assert.equal(h.el('#telegramVerify').disabled,false);
});
test('Telegram busy controls prevent duplicate requests and restore after errors',async()=>{
  const h=await telegramFixture();h.fillLogin();let reject;h.reply=()=>new Promise((_,r)=>{reject=r});
  const first=h.el('#telegramConnect').onclick();await new Promise(setImmediate);
  assert.equal(h.el('#telegramConnect').disabled,true);assert.equal(h.el('#telegramResume').disabled,true);
  await h.el('#telegramConnect').onclick();assert.equal(h.requests.length,1);
  reject(new Error('fixture connection failed'));await first;
  assert.equal(h.el('#telegramConnect').disabled,false);assert.match(h.el('#telegramLoginStatus').textContent,/fixture connection failed/);
  await h.renderAgent(h.el('#view'));
  assert.match(h.el('#telegramLoginStatus').textContent,/fixture connection failed/);assert.equal(h.el('#telegramApiHash').value,'a'.repeat(32));
});
test('Telegram buttons submit normalized Persian digits through code and two-step login',async()=>{
  const h=await telegramFixture();h.fill('telegramApiId','۱۲۳');h.fill('telegramApiHash','a'.repeat(32));h.fill('telegramPhone','+۱ ۲۰۲-۵۵۵۰۱۲۳');
  await h.el('#telegramConnect').onclick();assert.deepEqual(h.requests[0].body,{api_id:123,api_hash:'a'.repeat(32),phone:'+12025550123'});
  h.reply=async()=>({next:'password',login_pending:true,connected:false});h.fill('telegramCode','۱۲۳۴۵');await h.el('#telegramVerify').onclick();
  assert.equal(h.requests[1].body.code,'12345');assert.match(h.el('#telegramLoginStatus').textContent,/two-step password/i);
  await h.renderAgent(h.el('#view'));assert.match(h.el('#telegramLoginStatus').textContent,/two-step password/i);
  h.reply=async()=>({connected:true,login_pending:false,account:{name:'Fixture'}});h.fill('telegramPassword','  exact password  ');await h.el('#telegramVerify').onclick();
  assert.equal(h.requests[2].body.password,'  exact password  ');assert.equal(h.el('#telegramPassword').value,'');
  assert.match(h.el('#telegramLoginStatus').textContent,/connected/i);assert.equal(h.el('#telegramConnect').disabled,true);assert.deepEqual(h.persisted,[]);
});
test('empty Telegram credentials explain the missing fields without calling login',async()=>{
  const h=await telegramFixture();await h.el('#telegramConnect').onclick();
  assert.equal(h.requests.length,0);assert.match(h.el('#telegramLoginStatus').textContent,/API ID|API Hash|phone/i);
});
test('saving Agent permissions preserves unfinished Telegram credentials',async()=>{
  const h=await telegramFixture();h.fillLogin();const tick=h.el('#agentWrite');tick.checked=false;tick.onchange();
  await h.el('#saveAgentSettings').onclick();await new Promise(setImmediate);
  assert.equal(h.el('#telegramPhone').value,'+12025550123');assert.equal(h.el('#telegramApiHash').value,'a'.repeat(32));assert.equal(h.el('#agentWrite').checked,false);
});
test('Telegram resume, disconnect and confirmed revoke update the visible account',async()=>{
  const h=await telegramFixture();h.reply=async url=>url.endsWith('/login')?{connected:true,account:{name:'Fixture',username:'fixture'}}:{connected:false,paused:true,account:{}};
  await h.el('#telegramResume').onclick();assert.deepEqual(h.requests[0].body,{resume:true});
  assert.match(h.el('#telegramAccountStatus').textContent,/Fixture.*@fixture/);
  await h.el('#telegramDisconnect').onclick();assert.deepEqual(h.requests[1].body,{revoke:false});
  assert.equal(h.requests[1].url,'/api/agent/telegram/disconnect');assert.match(h.el('#telegramAccountStatus').textContent,/Disconnected/);
  const revoke=h.el('#telegramRevoke').onclick();assert.equal(h.requests.length,2);h.el('[data-yes]').onclick();await revoke;
  assert.deepEqual(h.requests[2].body,{revoke:true});
  const cancelled=h.el('#telegramRevoke').onclick();h.el('[data-no]').onclick();await cancelled;assert.equal(h.requests.length,3);
});
test('Telegram installation preserves entered credentials and updates its controls',async()=>{
  const h=await telegramFixture();h.App.state.agent.telegram={installed:false,vault_installed:false};await h.renderAgent(h.el('#view'));h.fillLogin();
  h.reply=async()=>({state:'running',error:''});await h.el('#installTelegram').onclick();
  assert.equal(h.requests[0].url,'/api/agent/telegram/install');assert.equal(h.el('#installTelegram').disabled,true);
  assert.equal(h.el('#telegramPhone').value,'+12025550123');assert.equal(h.el('#telegramApiHash').value,'a'.repeat(32));
  const phone=h.el('#telegramPhone');phone.focus();h.App.state.agent.telegram={installed:true,vault_installed:true,installer:{state:'done'}};
  await h.refreshState();assert.equal(h.el('#telegramPhone'),phone);assert.equal(h.el('#telegramConnect').disabled,false);
  assert.match(h.el('#telegramLoginStatus').textContent,/support installed/);
});
test('autofilled Telegram credentials survive a render without input events',async()=>{
  const h=await telegramFixture();h.el('#telegramPhone').value='+12025550123';h.el('#telegramApiHash').value='b'.repeat(32);
  await h.renderAgent(h.el('#view'));assert.equal(h.el('#telegramPhone').value,'+12025550123');assert.equal(h.el('#telegramApiHash').value,'b'.repeat(32));
});
test('an older Agent response cannot overwrite a completed Telegram login',async()=>{
  const h=await telegramFixture();h.fillLogin();const fetch=h.sandbox.fetch;let resolve;
  h.sandbox.fetch=(url,opts)=>url==='/api/agent/status'?new Promise(r=>{resolve=r}):fetch(url,opts);
  const old=h.renderAgent(h.el('#view'));h.reply=async()=>({connected:true,account:{name:'Fixture'}});await h.el('#telegramConnect').onclick();
  resolve({ok:true,json:async()=>({telegram:{installed:true,vault_installed:true,connected:false}})});await old;
  assert.equal(h.App.state.agent.telegram.connected,true);assert.equal(h.el('#telegramConnect').disabled,true);
});
test('state refresh started before login cannot revert the new Telegram status',async()=>{
  const h=await telegramFixture();h.fillLogin();const oldState=JSON.parse(JSON.stringify(h.App.state)),fetch=h.sandbox.fetch;let resolve;
  h.sandbox.fetch=(url,opts)=>url==='/api/state'?new Promise(r=>{resolve=r}):fetch(url,opts);
  const old=h.refreshState();h.reply=async()=>({connected:true,account:{name:'Fixture'}});await h.el('#telegramConnect').onclick();
  resolve({ok:true,json:async()=>oldState});await old;await new Promise(setImmediate);
  assert.equal(h.App.state.agent.telegram.connected,true);assert.equal(h.el('#telegramConnect').disabled,true);
});

test('Telegram Ping button calls the credential-aware RPC diagnostic endpoint and shows route result',async()=>{
  const h=await telegramFixture();h.fillLogin();
  h.reply=async url=>url.endsWith('/ping')?{ok:true,message:'Telegram RPC reachable via socks5 127.0.0.1:1080 (42 ms).'}:{next:'code'};
  await h.el('#telegramPing').onclick();
  assert.equal(h.requests[0].url,'/api/agent/telegram/ping');
  assert.deepEqual(h.requests[0].body,{api_id:123,api_hash:'a'.repeat(32)});
  assert.match(h.el('#telegramLoginStatus').textContent,/RPC reachable via socks5/i);
});
