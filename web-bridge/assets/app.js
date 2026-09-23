(() => {
  'use strict';
  const boot = window.AIB_BOOT || {};
  const apiUrl = boot.apiUrl || 'api.php';
  const $ = (s) => document.querySelector(s);
  const els = {
    sidebar: $('#sidebar'), scrim: $('#sidebarScrim'), menu: $('#menuBtn'), closeSidebar: $('#closeSidebar'),
    newChat: $('#newChatBtn'), newMobile: $('#newMobileBtn'), list: $('#conversationList'),
    messages: $('#messages'), empty: $('#emptyState'), scroll: $('#chatScroll'), input: $('#messageInput'),
    send: $('#sendBtn'), website: $('#websiteField'), sideStatus: $('#sidebarStatus'), topStatus: $('#topStatus'), pill: $('#connectionPill'),
    modelSelect: $('#modelSelect'), modelState: $('#modelLoadState'), modelStop: $('#modelStopBtn'),
    stopReply: $('#stopReplyBtn'), clearHistory: $('#clearHistoryBtn'), scrollBottom: $('#scrollBottomBtn'), modalRoot: $('#modalRoot'),
    attach: $('#attachBtn'), fileInput: $('#fileInput'), attachmentTray: $('#attachmentTray'), attachmentHint: $('#attachmentHint'), agentVersion: $('#agentVersion'),
    navChat: $('#navChat'), navCalendar: $('#navCalendar'), navFiles: $('#navFiles'), viewTitle: $('#viewTitle'), composerWrap: $('#composerWrap'),
    calendarView: $('#calendarView'), filesView: $('#filesView'), calendarGrid: $('#calendarGrid'), calendarMonthTitle: $('#calendarMonthTitle'), calendarTodayText: $('#calendarTodayText'), calendarClock: $('#calendarClock'),
    calPrev: $('#calPrev'), calNext: $('#calNext'), calToday: $('#calToday'), upcomingEvents: $('#upcomingEvents'), selectedJalaliDate: $('#selectedJalaliDate'), quickEventTitle: $('#quickEventTitle'), quickEventTime: $('#quickEventTime'), quickEventDuration: $('#quickEventDuration'), quickEventReminder: $('#quickEventReminder'), quickEventNotes: $('#quickEventNotes'), saveQuickEvent: $('#saveQuickEvent'),
    workspaceUpload: $('#workspaceUploadBtn'), workspaceFileInput: $('#workspaceFileInput'), newFolder: $('#newFolderBtn'), workspaceSearch: $('#workspaceSearch'), fileBreadcrumbs: $('#fileBreadcrumbs'), workspaceFileList: $('#workspaceFileList'), workspaceFileEmpty: $('#workspaceFileEmpty')
  };

  const LS_CONVS = 'aib_conversations_v3';
  const LS_ACTIVE = 'aib_active_conversation_v3';
  const LS_MODEL = 'aib_selected_model_v1';
  const LS_BROWSER = 'aib_browser_identity_v1';
  const LS_DRAFTS = 'aib_chat_drafts_v1';
  const browserKey = getBrowserKey();
  let drafts = loadObject(LS_DRAFTS, {});
  let conversations = loadJson(LS_CONVS, loadJson('aib_conversations_v2', []));
  let activeId = localStorage.getItem(LS_ACTIVE) || localStorage.getItem('aib_active_conversation_v2') || '';
  if(conversations.length) localStorage.setItem(LS_CONVS,JSON.stringify(conversations));
  if(activeId) localStorage.setItem(LS_ACTIVE,activeId);
  let watchHash = '';
  let watchAbort = null;
  let watchFailures = 0;
  let lastRenderSignature = '';
  let sending = false;
  let currentRows = [];
  let currentAgent = {};
  let pendingAttachments = [];
  const activityExpanded = new Set();
  let currentView = 'chat';
  let calendarYear = 0, calendarMonth = 0, calendarDb = {events:[],custom_holidays:[]};
  let selectedJalali = '', selectedGregorian = '';
  let currentFolder = '';
  let fileSearchTimer = null;

  function loadJson(key, fallback) {
    try { const x = JSON.parse(localStorage.getItem(key)); return Array.isArray(x) ? x : fallback; } catch { return fallback; }
  }
  function loadObject(key, fallback) {
    try { const x = JSON.parse(localStorage.getItem(key)); return x && typeof x === 'object' && !Array.isArray(x) ? x : fallback; } catch { return fallback; }
  }
  function getBrowserKey(){
    let key=localStorage.getItem(LS_BROWSER)||'';
    if(key.length>=24)return key;
    const bytes=new Uint8Array(32);crypto.getRandomValues(bytes);key='br_'+[...bytes].map(b=>b.toString(16).padStart(2,'0')).join('');
    localStorage.setItem(LS_BROWSER,key);return key;
  }
  function browserHeaders(extra={}){return {'X-AIB-Browser':browserKey,...extra};}
  function apiFetch(url,opts={}){opts={...opts,headers:browserHeaders(opts.headers||{})};return fetch(url,opts);}
  function saveDraft(){drafts[activeId]=els.input.value||'';localStorage.setItem(LS_DRAFTS,JSON.stringify(drafts));}
  function loadDraft(){els.input.value=String(drafts[activeId]||'');autoGrow();}
  function uuid() {
    if (crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
      const r = Math.random() * 16 | 0, v = c === 'x' ? r : (r & 3 | 8); return v.toString(16);
    });
  }
  function saveConvs() { localStorage.setItem(LS_CONVS, JSON.stringify(conversations.slice(0, 40))); }
  function ensureActive() {
    if (!activeId || !conversations.some(c => c.id === activeId)) {
      const c = { id: uuid(), title: 'گفتگوی جدید', updated: Date.now() };
      conversations.unshift(c); activeId = c.id; saveConvs(); localStorage.setItem(LS_ACTIVE, activeId);
    }
  }
  function setActive(id) {
    if (!conversations.some(c => c.id === id)) return;
    if(activeId&&activeId!==id) saveDraft();
    activeId = id; localStorage.setItem(LS_ACTIVE, id); watchHash = ''; lastRenderSignature = '';
    renderConversationList(); stopWatch(); fetchHistory(true).then(startWatch); closeSidebar(); loadDraft();
  }
  function createNewChat() {
    saveDraft();
    const c = { id: uuid(), title: 'گفتگوی جدید', updated: Date.now() };
    conversations.unshift(c); saveConvs(); setActive(c.id); els.input.focus();
  }
  function updateTitleFromMessage(text) {
    const c = conversations.find(x => x.id === activeId); if (!c) return;
    if (c.title === 'گفتگوی جدید') {
      const clean = text.replace(/\s+/g, ' ').trim(); c.title = clean.length > 36 ? clean.slice(0, 36) + '…' : clean || 'گفتگوی جدید';
    }
    c.updated = Date.now();
    conversations.sort((a,b) => b.updated - a.updated); saveConvs(); renderConversationList();
  }
  function relativeConversationTime(ts) {
    const d=Date.now()-Number(ts||0);
    if(!Number(ts))return '';
    if(d<60*1000)return 'همین الان';
    if(d<60*60*1000)return `${Math.max(1,Math.floor(d/60000))} دقیقه پیش`;
    if(d<24*60*60*1000)return `${Math.floor(d/3600000)} ساعت پیش`;
    if(d<7*24*60*60*1000)return `${Math.floor(d/86400000)} روز پیش`;
    try{return new Date(Number(ts)).toLocaleDateString('fa-IR',{month:'short',day:'numeric'})}catch{return ''}
  }
  async function syncServerSessions(){
    try{
      const r=await apiFetch(`${apiUrl}?action=sessions&_=${Date.now()}`,{cache:'no-store'});const d=await r.json();if(!r.ok||!d.ok)return;
      const remote=Array.isArray(d.sessions)?d.sessions:[];const map=new Map(conversations.map(c=>[c.id,c]));
      remote.forEach(s=>{if(!s?.id)return;const cur=map.get(s.id);if(cur){cur.updated=Math.max(Number(cur.updated||0),Number(s.updated||0));if((!cur.title||cur.title==='گفتگوی جدید')&&s.title)cur.title=s.title;}else{map.set(s.id,{id:s.id,title:s.title||'گفتگوی جدید',updated:Number(s.updated||Date.now())});}});
      conversations=[...map.values()].sort((a,b)=>Number(b.updated||0)-Number(a.updated||0)).slice(0,100);saveConvs();renderConversationList();
    }catch{}
  }

  function conversationGroup(ts){
    const d=Date.now()-Number(ts||0),day=86400000;
    if(d<day)return 'امروز';
    if(d<2*day)return 'دیروز';
    if(d<7*day)return '۷ روز اخیر';
    return 'قدیمی‌تر';
  }
  function conversationHasActive(id){return id===activeId&&currentRows.some(m=>['pending','processing'].includes(String(m.status||'')));}
  function renderConversationList() {
    els.list.textContent = '';
    let lastGroup='';
    conversations.forEach(c => {
      const group=conversationGroup(c.updated);
      if(group!==lastGroup){const h=document.createElement('div');h.className='conv-group';h.textContent=group;els.list.appendChild(h);lastGroup=group;}
      const row=document.createElement('div');row.className='conv-row'+(c.id===activeId?' active':'');
      const b = document.createElement('button'); b.className = 'conv-item'; b.type = 'button'; b.addEventListener('click', () => setActive(c.id));
      b.innerHTML = '<svg viewBox="0 0 24 24"><path d="M6.5 18.5 4 20l.8-3.1A7.5 7.5 0 1 1 12 19.5c-2 0-3.8-.4-5.5-1Z"/></svg>';
      const copy=document.createElement('span');copy.className='conv-copy';
      const s = document.createElement('span'); s.className='conv-title'; s.textContent = c.title; copy.appendChild(s);
      const meta=document.createElement('small');meta.textContent=(conversationHasActive(c.id)?'در حال پاسخ · ':'')+relativeConversationTime(c.updated);copy.appendChild(meta);
      b.appendChild(copy);
      const del=document.createElement('button');del.type='button';del.className='conv-delete';del.title='حذف گفتگو';del.setAttribute('aria-label','حذف گفتگو');del.innerHTML='<svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V4h6v3M7 7l1 14h8l1-14"/></svg>';
      del.addEventListener('click',e=>{e.stopPropagation();deleteConversation(c.id)});
      row.appendChild(b);row.appendChild(del);els.list.appendChild(row);
    });
    if(!conversations.length){const e=document.createElement('div');e.className='history-empty';e.textContent='هنوز گفتگویی نیست';els.list.appendChild(e);}
  }

  async function confirmAction(title,text,confirmText='حذف'){
    return new Promise(resolve=>{
      const root=els.modalRoot;if(!root){resolve(window.confirm(text));return;}
      root.innerHTML=`<div class="modal-backdrop"><div class="confirm-card" role="dialog" aria-modal="true"><h3>${escapeHtml(title)}</h3><p>${escapeHtml(text)}</p><div><button class="modal-cancel" type="button">انصراف</button><button class="modal-danger" type="button">${escapeHtml(confirmText)}</button></div></div></div>`;
      const finish=v=>{root.innerHTML='';resolve(v)};
      root.querySelector('.modal-cancel').onclick=()=>finish(false);root.querySelector('.modal-danger').onclick=()=>finish(true);root.querySelector('.modal-backdrop').onclick=e=>{if(e.target===e.currentTarget)finish(false)};
    });
  }
  function escapeHtml(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));}
  async function deleteConversation(id){
    const c=conversations.find(x=>x.id===id);if(!c)return;
    if(!(await confirmAction('حذف گفتگو',`«${c.title}» و پیام‌هایش پاک شود؟`,'حذف')))return;
    if(id===activeId&&isResponseActive())await cancelCurrentResponse();
    try{await apiFetch(`${apiUrl}?action=delete_session`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:id})});}catch{}
    conversations=conversations.filter(x=>x.id!==id);delete drafts[id];saveConvs();localStorage.setItem(LS_DRAFTS,JSON.stringify(drafts));
    if(activeId===id){activeId='';ensureActive();localStorage.setItem(LS_ACTIVE,activeId);watchHash='';lastRenderSignature='';await fetchHistory(true);stopWatch();startWatch();loadDraft();}
    renderConversationList();
  }
  async function clearAllHistory(){
    if(!(await confirmAction('پاک کردن تاریخچه','تمام گفتگوهای این مرورگر پاک شوند؟ این کار قابل بازگشت نیست.','پاک کردن همه')))return;
    if(isResponseActive())await cancelCurrentResponse();
    try{await apiFetch(`${apiUrl}?action=delete_all_history`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_ids:conversations.map(c=>c.id)})});}catch{}
    conversations=[];drafts={};localStorage.removeItem(LS_CONVS);localStorage.removeItem(LS_ACTIVE);localStorage.setItem(LS_DRAFTS,'{}');activeId='';ensureActive();renderConversationList();watchHash='';lastRenderSignature='';await fetchHistory(true);stopWatch();startWatch();loadDraft();closeSidebar();
  }

  function renderModelPicker(agent) {
    currentAgent = agent && typeof agent === 'object' ? agent : {};
    if (!els.modelSelect) return;
    const models = Array.isArray(agent?.models) ? agent.models : [];
    const control = agent?.model_control && typeof agent.model_control === 'object' ? agent.model_control : {};
    const loadedId = String(agent?.model_id || '');
    const savedId = localStorage.getItem(LS_MODEL) || '';
    const desiredId = String(control.model_id || savedId || loadedId || '');
    const currentOptions = [...els.modelSelect.options].map(o => o.value).join('|');
    const nextOptions = models.map(m => String(m.id||'')).join('|');
    if (currentOptions !== nextOptions || els.modelSelect.options.length !== models.length) {
      els.modelSelect.textContent='';
      if (!models.length) {
        const o=document.createElement('option');o.value='';o.textContent=agent?.online?'هیچ مدل محلی پیدا نشد':'منتظر LlamaForge…';els.modelSelect.appendChild(o);
      } else {
        models.forEach(m=>{const o=document.createElement('option');o.value=String(m.id||'');const bits=[m.name||'Model'];if(m.quantization)bits.push(m.quantization);if(m.vision_capable)bits.push('Vision');if(Number(m.size_gb||0)>0)bits.push(`${Number(m.size_gb).toFixed(1)} GB`);o.textContent=bits.join(' · ');els.modelSelect.appendChild(o)});
      }
    }
    const validDesired=models.some(m=>String(m.id||'')===desiredId);
    const validLoaded=models.some(m=>String(m.id||'')===loadedId);
    const target=validDesired?desiredId:(validLoaded?loadedId:(models[0]?.id||''));
    if(target) els.modelSelect.value=String(target);
    els.modelSelect.disabled=!agent?.online || !models.length || ['pending','loading'].includes(String(control.status||''));
    if(target) localStorage.setItem(LS_MODEL,String(target));
    if (els.modelState) {
      let label='', cls='';
      if (!agent?.online) { label='آفلاین'; cls='error'; }
      else if (String(control.status||'')==='error') { label='خطای لود'; cls='error'; }
      else if (['pending','loading'].includes(String(control.status||'')) || agent?.model_loading) { label='در حال لود…'; cls='loading'; }
      else if (agent?.model_ready && loadedId) { label='آماده'; cls='ready'; }
      else { label='مدل لود نشده'; cls=''; }
      els.modelState.textContent=label; els.modelState.className=cls;
      if(control.error) els.modelState.title=String(control.error); else els.modelState.removeAttribute('title');
    }
    if(els.modelStop)els.modelStop.disabled=!agent?.online || (!agent?.model_ready && !agent?.model_loading) || ['pending','loading'].includes(String(control.status||''));
  }

  async function requestModelLoad(modelId) {
    modelId=String(modelId||'').trim(); if(!modelId)return;
    localStorage.setItem(LS_MODEL,modelId);
    if(els.modelSelect)els.modelSelect.disabled=true;
    if(els.modelState){els.modelState.textContent='در حال ارسال درخواست…';els.modelState.className='loading'}
    try{
      const r=await apiFetch(`${apiUrl}?action=model_select`,{method:'POST',headers:{'Content-Type':'application/json'},cache:'no-store',body:JSON.stringify({model_id:modelId})});
      const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||'model_select_failed');
      renderModelPicker(d.agent||{});
    }catch(e){if(els.modelState){els.modelState.textContent='خطای انتخاب مدل';els.modelState.className='error';els.modelState.title=String(e.message||e)};if(els.modelSelect)els.modelSelect.disabled=false}
  }

  async function requestModelStop(){
    if(!(await confirmAction('توقف مدل','مدل محلی Unload شود و حافظه آزاد شود؟ پاسخ فعال هم متوقف خواهد شد.','توقف مدل')))return;
    if(isResponseActive()) await cancelCurrentResponse();
    if(els.modelStop)els.modelStop.disabled=true;
    if(els.modelState){els.modelState.textContent='در حال توقف…';els.modelState.className='loading'}
    try{
      const r=await apiFetch(`${apiUrl}?action=model_stop`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||'model_stop_failed');renderModelPicker(d.agent||{});
    }catch(e){if(els.modelState){els.modelState.textContent='خطای توقف';els.modelState.className='error';els.modelState.title=String(e.message||e)}}
  }

  function setConnectivity(agent, connected = true) {
    const online = !!(agent && agent.online);
    const state = agent && agent.state ? String(agent.state) : '';
    let label = online ? 'LlamaForge آنلاین' : (connected ? 'منتظر اتصال LlamaForge' : 'اتصال قطع است');
    if (online && state === 'working') label = 'Agent در حال اجرای درخواست';
    else if (online && state === 'waiting_model') label = 'LlamaForge وصل است · مدل آماده نیست';
    else if (online && state === 'polling') label = 'LlamaForge آماده و منتظر پیام';
    els.sideStatus.classList.toggle('online', online);
    els.sideStatus.querySelector('span:last-child').textContent = label;
    els.topStatus.textContent = label;
    els.pill.classList.toggle('live', online);
    if(els.agentVersion)els.agentVersion.textContent=String(agent?.version||'—');
    renderModelPicker(agent || {});
  }
  function messageSignature(rows) {
    return JSON.stringify(rows.map(m => [m.id,m.status,m.answer,m.partial_answer,m.claimed_at,m.activity,m.attachments]));
  }
  function activityLabel(ev) {
    if (!ev || typeof ev !== 'object') return '';
    if (ev.label && String(ev.label).toLowerCase() !== 'working') return String(ev.label);
    const map = {
      received:'درخواست دریافت شد', route:'مسیر اجرا مشخص شد', route_decision:'مدل مسیر اجرا را انتخاب کرد', context:'Context بهینه شد',
      understand:'در حال فهم درخواست', capabilities:'انتخاب Skillهای مرتبط', planning:'تصمیم‌گیری مرحله بعد',
      execute:'اجرای Skill', observe:'تحلیل نتیجه Skill', policy:'اعمال قانون اجرایی',
      finalize:'ساخت پاسخ نهایی', complete:'تکمیل شد', error:'خطا در اجرا'
    };
    return map[ev.phase] || map[ev.type] || 'به‌روزرسانی Agent';
  }
  function activeRemoteMessage(){for(let i=currentRows.length-1;i>=0;i--){const m=currentRows[i];if(['pending','processing'].includes(String(m.status||'')))return m;}return null;}
  function isResponseActive(){return !!activeRemoteMessage();}
  function updateComposerState(){
    const active=isResponseActive();
    if(els.stopReply)els.stopReply.hidden=!active;
    if(els.send)els.send.hidden=active;
    if(els.send)els.send.disabled=sending||active||(!els.input.value.trim()&&!pendingAttachments.length);
    renderConversationList();
  }
  function selectedModel(){const id=(els.modelSelect&&els.modelSelect.value)||localStorage.getItem(LS_MODEL)||'';return (currentAgent.models||[]).find(m=>String(m.id||'')===String(id))||null;}
  function renderAttachments(){
    if(!els.attachmentTray)return;
    els.attachmentTray.hidden=!pendingAttachments.length;els.attachmentTray.textContent='';
    pendingAttachments.forEach(a=>{const chip=document.createElement('div');chip.className='attachment-chip '+a.kind;if(a.kind==='image'&&a.data_url){const img=document.createElement('img');img.src=a.data_url;img.alt='';chip.appendChild(img)}else{const mark=document.createElement('span');mark.className='attachment-file-mark';mark.textContent=(a.name?.split('.').pop()||'FILE').slice(0,4).toUpperCase();chip.appendChild(mark)}const copy=document.createElement('div');copy.className='attachment-copy';const strong=document.createElement('strong');strong.textContent=a.name||'فایل';const small=document.createElement('small');small.textContent=a.kind==='image'?'تصویر':'فایل';copy.append(strong,small);const rm=document.createElement('button');rm.type='button';rm.className='attachment-remove';rm.textContent='×';rm.setAttribute('aria-label','حذف پیوست');rm.onclick=()=>{pendingAttachments=pendingAttachments.filter(x=>x.id!==a.id);renderAttachments();updateComposerState()};chip.append(copy,rm);els.attachmentTray.appendChild(chip)});
    if(els.attachmentHint){const hasImage=pendingAttachments.some(a=>a.kind==='image');const model=selectedModel();els.attachmentHint.textContent=hasImage&&!model?.vision_capable?'تصویر برای ذخیره قابل ارسال است؛ برای تحلیل محتوای تصویر مدل Vision لازم است.':'پیوست‌ها ابتدا بدون خواندن محتوا به File Manager تحویل می‌شوند.';}
  }
  function readDataUrl(file){return new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result||''));r.onerror=()=>reject(r.error||new Error('read_failed'));r.readAsDataURL(file)});}
  async function addAttachments(files){
    const maxTotal=24*1024*1024;
    for(const file of [...(files||[])].slice(0,8)){
      const used=pendingAttachments.reduce((n,a)=>n+Number(a.size||0),0);if(used+Number(file.size||0)>maxTotal){alert('حجم مجموع پیوست‌ها باید کمتر از ۲۴ مگابایت باشد.');continue}
      if(file.size>20*1024*1024){alert(`حجم ${file.name} بیشتر از ۲۰ مگابایت است.`);continue}
      const image=(file.type||'').startsWith('image/');
      pendingAttachments.push({id:uuid(),kind:image?'image':'file',name:file.name,type:file.type||(image?'image/jpeg':'application/octet-stream'),size:file.size,data_url:await readDataUrl(file)});
    }
    pendingAttachments=pendingAttachments.slice(-8);renderAttachments();updateComposerState();
  }

  async function cancelCurrentResponse(){
    const m=activeRemoteMessage();if(!m)return;
    try{const r=await apiFetch(`${apiUrl}?action=cancel_message`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:activeId,message_id:m.id})});const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||'cancel_failed');await fetchHistory(false);}catch(e){console.warn(e)}
  }

  function appendActivity(body, m) {
    const rows = Array.isArray(m.activity) ? m.activity : [];
    if (!rows.length) return;
    const active = !m.answer && ['pending','processing'].includes(String(m.status||''));
    const id = String(m.id||'');
    const expanded = active || activityExpanded.has(id);
    const panel = document.createElement('div');
    panel.className='activity-panel ' + (active?'running ':'completed ') + (expanded?'expanded':'collapsed');
    const head = document.createElement('button'); head.type='button'; head.className='activity-head';
    const left = document.createElement('div'); left.className='activity-head-main';
    const title = document.createElement('span'); title.textContent=active?'فعالیت Agent':'جزئیات اجرای Agent';
    const last = rows[rows.length-1] || {};
    const summary = document.createElement('small'); summary.className='activity-summary'; summary.textContent=activityLabel(last);
    left.appendChild(title); left.appendChild(summary);
    const right=document.createElement('div');right.className='activity-head-side';
    const count = document.createElement('small'); count.textContent = `${rows.length} مرحله`;
    const chevron=document.createElement('span');chevron.className='activity-chevron';chevron.textContent=expanded?'⌃':'⌄';
    right.appendChild(count);right.appendChild(chevron);head.appendChild(left);head.appendChild(right);panel.appendChild(head);
    const list = document.createElement('div'); list.className='activity-list';
    rows.slice(-16).forEach((ev) => {
      const row = document.createElement('div');
      const failed = ev.ok === false || ev.status === 'error' || ev.type === 'error';
      const done = ev.ok === true || ev.status === 'done' || ev.type === 'complete' || ev.type === 'tool_result';
      row.className = 'activity-row ' + (failed ? 'failed' : done ? 'done' : 'active');
      const dot = document.createElement('span'); dot.className='activity-dot';
      const text = document.createElement('div'); text.className='activity-text';
      const label = document.createElement('b'); label.textContent = activityLabel(ev);
      text.appendChild(label);
      const details=[];
      if(ev.skill && !String(label.textContent).includes(String(ev.skill))) details.push(String(ev.skill));
      if(ev.detail && String(ev.detail).toLowerCase()!=='working') details.push(String(ev.detail));
      if(ev.error) details.push(String(ev.error));
      if(details.length){ const small=document.createElement('small'); small.textContent=details.join(' · '); text.appendChild(small); }
      row.appendChild(dot); row.appendChild(text); list.appendChild(row);
    });
    if(!expanded) list.hidden=true;
    panel.appendChild(list); body.appendChild(panel);
    head.addEventListener('click',()=>{
      if(active)return; // running activity intentionally stays open
      if(activityExpanded.has(id))activityExpanded.delete(id);else activityExpanded.add(id);
      renderMessages(currentRows,true);
    });
    if(active) requestAnimationFrame(()=>{list.scrollTop=list.scrollHeight});
  }
  function renderMessages(rows, force = false) {
    const sig = messageSignature(rows);
    if (!force && sig === lastRenderSignature) return;
    const wasNearBottom = els.scroll.scrollHeight - els.scroll.scrollTop - els.scroll.clientHeight < 150;
    lastRenderSignature = sig;
    currentRows = rows.map(m => ({...m}));
    els.messages.textContent = '';
    els.empty.classList.toggle('hidden', rows.length > 0);

    rows.forEach(m => {
      const user = document.createElement('div'); user.className = 'message-row user';
      const userWrap=document.createElement('div');userWrap.className='user-message-wrap';
      const atts=Array.isArray(m.attachments)?m.attachments:[];if(atts.length){const files=document.createElement('div');files.className='message-attachment-list';atts.forEach(a=>{const chip=document.createElement('span');chip.className='message-attachment';chip.textContent=(a.kind==='image'?'تصویر: ':'فایل: ')+(a.name||'پیوست');files.appendChild(chip)});userWrap.appendChild(files)}
      if(m.question){const bubble = document.createElement('div'); bubble.className = 'user-bubble'; bubble.textContent = m.question || ''; userWrap.appendChild(bubble)}user.appendChild(userWrap); els.messages.appendChild(user);

      const assistant = document.createElement('div'); assistant.className = 'message-row assistant';
      const wrap = document.createElement('div'); wrap.className = 'assistant-wrap';
      const avatar = document.createElement('div'); avatar.className = 'assistant-avatar';
      avatar.innerHTML = '<svg viewBox="0 0 24 24"><path d="M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17Z"/><path d="M8.4 12.2 11 14.6l4.9-5.2"/></svg>';
      const body = document.createElement('div'); body.className = 'assistant-body';
      if (m.answer) {
        const answerText=document.createElement('div');answerText.className='assistant-answer-text';answerText.textContent=m.answer;body.appendChild(answerText);
        const actions=document.createElement('div');actions.className='assistant-actions';
        const copy=document.createElement('button');copy.type='button';copy.className='assistant-action-btn';copy.setAttribute('aria-label','کپی پاسخ');copy.title='کپی پاسخ';copy.innerHTML='<svg viewBox="0 0 24 24"><rect x="8" y="8" width="10" height="10" rx="2"/><path d="M6 16H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg><span>کپی</span>';
        copy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(String(m.answer||''));copy.querySelector('span').textContent='کپی شد';setTimeout(()=>{const sp=copy.querySelector('span');if(sp)sp.textContent='کپی';},1300);}catch{}});
        actions.appendChild(copy);body.appendChild(actions);
        if (m.agent_name) { const meta = document.createElement('div'); meta.className='assistant-meta'; meta.textContent = m.agent_name; body.appendChild(meta); }
      } else if (String(m.status||'') === 'cancelled') {
        const stopped=document.createElement('div');stopped.className='stopped-message';stopped.textContent='پاسخ متوقف شد';body.appendChild(stopped);
      } else if (m.partial_answer) {
        const txt = document.createElement('span'); txt.textContent = m.partial_answer; body.appendChild(txt);
        const caret = document.createElement('span'); caret.className='partial-caret'; body.appendChild(caret);
      } else {
        const p = document.createElement('div'); p.className='pending-box';
        p.innerHTML='<span class="typing-dots"><i></i><i></i><i></i></span><span class="pending-label"></span>';
        p.querySelector('.pending-label').textContent = m.status === 'processing' ? 'در حال پاسخ…' : 'منتظر Agent…'; body.appendChild(p);
      }
      appendActivity(body, m);
      wrap.appendChild(avatar); wrap.appendChild(body); assistant.appendChild(wrap); els.messages.appendChild(assistant);
    });
    updateComposerState();
    if (force || wasNearBottom) requestAnimationFrame(() => els.scroll.scrollTo({top: els.scroll.scrollHeight, behavior: force ? 'auto':'smooth'}));
  }

  async function fetchHistory(force = false) {
    try {
      const r = await apiFetch(`${apiUrl}?action=history&session_id=${encodeURIComponent(activeId)}&_=${Date.now()}`, {cache:'no-store'});
      const d = await r.json(); if (!r.ok || !d.ok) throw new Error(d.error || 'history_error');
      const rows = d.messages || []; watchHash = d.hash || await hashRows(rows); renderMessages(rows, force); setConnectivity(d.agent, true); watchFailures = 0;
      return true;
    } catch (e) { setConnectivity(null, false); return false; }
  }
  async function hashRows(rows) {
    // Server hash is authoritative once watch returns; this initial marker only asks watch for immediate state if WebCrypto is unavailable.
    try {
      const bytes = new TextEncoder().encode(JSON.stringify(rows)); const digest = await crypto.subtle.digest('SHA-256', bytes);
      return [...new Uint8Array(digest)].map(b=>b.toString(16).padStart(2,'0')).join('');
    } catch { return ''; }
  }
  function stopWatch() { if (watchAbort) watchAbort.abort(); watchAbort = null; }
  function applyLiveState(d){
    if(!d||!d.ok)return;
    if(d.hash)watchHash=d.hash;
    if(d.changed&&Array.isArray(d.messages))renderMessages(d.messages);
    setConnectivity(d.agent,true);watchFailures=0;
  }
  async function streamOnce(mySession,controller){
    const u=`${apiUrl}?action=stream&session_id=${encodeURIComponent(mySession)}&since=${encodeURIComponent(watchHash)}&_=${Date.now()}`;
    const r=await apiFetch(u,{cache:'no-store',signal:controller.signal,headers:{'Accept':'text/event-stream'}});
    if(!r.ok||!r.body)throw new Error(`stream_http_${r.status}`);
    const reader=r.body.getReader(),decoder=new TextDecoder();let buffer='',firstChunk=true;
    while(!controller.signal.aborted){
      let readResult;
      if(firstChunk){
        readResult=await Promise.race([reader.read(),delay(4500).then(()=>({streamTimeout:true}))]);
        if(readResult&&readResult.streamTimeout){try{await reader.cancel()}catch{}throw new Error('sse_buffered_or_blocked')}
        firstChunk=false;
      }else{readResult=await reader.read();}
      const {value,done}=readResult;if(done)break;buffer+=decoder.decode(value,{stream:true}).replace(/\r\n/g,'\n');
      let cut;while((cut=buffer.indexOf('\n\n'))>=0){
        const block=buffer.slice(0,cut);buffer=buffer.slice(cut+2);
        if(!block||block.startsWith(':'))continue;
        let event='message',data='';
        block.split('\n').forEach(line=>{if(line.startsWith('event:'))event=line.slice(6).trim();else if(line.startsWith('data:'))data+=(data?'\n':'')+line.slice(5).trimStart();});
        if(!data)continue;
        try{const payload=JSON.parse(data);if(event==='state')applyLiveState(payload);if(event==='reconnect'&&payload.hash)watchHash=payload.hash;}catch{}
      }
    }
  }
  async function pollOnce(mySession,controller){
    const u=`${apiUrl}?action=watch&session_id=${encodeURIComponent(mySession)}&since=${encodeURIComponent(watchHash)}&wait=18&_=${Date.now()}`;
    const r=await apiFetch(u,{cache:'no-store',signal:controller.signal});const d=await r.json();
    if(!r.ok||!d.ok)throw new Error(d.error||'watch_error');applyLiveState(d);
  }
  async function startWatch() {
    stopWatch();
    const mySession=activeId,controller=new AbortController();watchAbort=controller;let streamFailures=0;
    while(!controller.signal.aborted&&activeId===mySession){
      try{
        if(streamFailures<2){await streamOnce(mySession,controller);}else{await pollOnce(mySession,controller);}
        if(!controller.signal.aborted)await delay(80);
      }catch(e){
        if(controller.signal.aborted)break;
        watchFailures++;streamFailures++;setConnectivity(null,false);
        await delay(Math.min(2800,350*watchFailures));
        if(watchFailures>=3)await fetchHistory(false);
      }
    }
  }
  const delay = ms => new Promise(r => setTimeout(r, ms));

  async function sendMessage() {
    const text = els.input.value.trim(); const attachments=pendingAttachments.map(a=>({...a})); if ((!text&&!attachments.length) || sending || isResponseActive()) return;
    sending = true; els.send.disabled = true;
    const original = text; els.input.value=''; drafts[activeId]='';localStorage.setItem(LS_DRAFTS,JSON.stringify(drafts)); pendingAttachments=[];renderAttachments();autoGrow(); updateTitleFromMessage(original||attachments[0]?.name||'پیوست');
    // Immediate optimistic user rendering by writing a temporary row over current UI.
    const tempRows = currentRows.map(m => ({...m}));
    tempRows.push({id:'temp-'+Date.now(),question:original,attachments:attachments.map(({kind,name,type,size,data_url})=>({kind,name,type,size,data_url})),answer:null,partial_answer:null,status:'pending'});
    renderMessages(tempRows, false);
    try {
      const modelId=(els.modelSelect&&els.modelSelect.value)||localStorage.getItem(LS_MODEL)||'';
      const r = await apiFetch(`${apiUrl}?action=send`, {method:'POST',headers:{'Content-Type':'application/json'},cache:'no-store',body:JSON.stringify({session_id:activeId,message:original,attachments,website:els.website.value,model_id:modelId})});
      const d = await r.json(); if (!r.ok || !d.ok) throw new Error(d.error || 'send_error');
      await fetchHistory(false);
      stopWatch(); startWatch();
    } catch (e) {
      els.input.value = original; pendingAttachments=attachments;renderAttachments();autoGrow();
      alert('پیام ارسال نشد. اتصال سرور را بررسی کن.');
      await fetchHistory(false);
    } finally { sending = false; updateComposerState(); els.input.focus(); }
  }
  function autoGrow(){ els.input.style.height='auto'; els.input.style.height=Math.min(190, els.input.scrollHeight)+'px'; if(activeId){drafts[activeId]=els.input.value||'';localStorage.setItem(LS_DRAFTS,JSON.stringify(drafts));} updateComposerState(); }
  function updateScrollButton(){if(!els.scrollBottom)return;const gap=els.scroll.scrollHeight-els.scroll.scrollTop-els.scroll.clientHeight;els.scrollBottom.hidden=gap<220;}
  function scrollToBottom(){els.scroll.scrollTo({top:els.scroll.scrollHeight,behavior:'smooth'});}

  function openSidebar(){ els.sidebar.classList.add('open'); els.scrim.classList.add('show'); }
  function closeSidebar(){ els.sidebar.classList.remove('open'); els.scrim.classList.remove('show'); }

  // ---------------------------------------------------------------------------
  // Smart domain workspaces: a few generic capabilities, composed by the model.
  // ---------------------------------------------------------------------------
  const FA_MONTHS=['فروردین','اردیبهشت','خرداد','تیر','مرداد','شهریور','مهر','آبان','آذر','دی','بهمن','اسفند'];
  const FIXED_HOLIDAYS={'1-1':'نوروز','1-2':'تعطیلات نوروز','1-3':'تعطیلات نوروز','1-4':'تعطیلات نوروز','1-12':'روز جمهوری اسلامی ایران','1-13':'روز طبیعت','3-14':'رحلت امام خمینی','3-15':'قیام ۱۵ خرداد','11-22':'پیروزی انقلاب اسلامی','12-29':'ملی شدن صنعت نفت'};
  function g2j(gy,gm,gd){const gdm=[0,31,59,90,120,151,181,212,243,273,304,334];const gy2=gy+(gm>2?1:0);let days=355666+365*gy+Math.floor((gy2+3)/4)-Math.floor((gy2+99)/100)+Math.floor((gy2+399)/400)+gd+gdm[gm-1];let jy=-1595+33*Math.floor(days/12053);days%=12053;jy+=4*Math.floor(days/1461);days%=1461;if(days>365){jy+=Math.floor((days-1)/365);days=(days-1)%365}let jm,jd;if(days<186){jm=1+Math.floor(days/31);jd=1+(days%31)}else{jm=7+Math.floor((days-186)/30);jd=1+((days-186)%30)}return [jy,jm,jd]}
  function j2g(jy,jm,jd){jy+=1595;let days=-355668+365*jy+Math.floor(jy/33)*8+Math.floor(((jy%33)+3)/4)+jd;days+=(jm<7?(jm-1)*31:(jm-7)*30+186);let gy=400*Math.floor(days/146097);days%=146097;if(days>36524){gy+=100*Math.floor((days-1)/36524);days=(days-1)%36524;if(days>=365)days++}gy+=4*Math.floor(days/1461);days%=1461;if(days>365){gy+=Math.floor((days-1)/365);days=(days-1)%365}let gd=days+1;const leap=gy%4===0&&(gy%100!==0||gy%400===0);const sal=[0,31,leap?29:28,31,30,31,30,31,31,30,31,30,31];let gm=1;while(gm<=12&&gd>sal[gm]){gd-=sal[gm];gm++}return [gy,gm,gd]}
  function jMonthLength(y,m){if(m<=6)return 31;if(m<=11)return 30;const a=j2g(y,12,1),b=j2g(y+1,1,1);return Math.round((new Date(b[0],b[1]-1,b[2])-new Date(a[0],a[1]-1,a[2]))/86400000)}
  function pad2(n){return String(n).padStart(2,'0')}
  function dateKey(d){return `${d.getFullYear()}-${pad2(d.getMonth()+1)}-${pad2(d.getDate())}`}
  function faDigits(v){return String(v).replace(/\d/g,d=>'۰۱۲۳۴۵۶۷۸۹'[Number(d)])}
  function switchView(view){
    currentView=['chat','calendar','files'].includes(view)?view:'chat';
    els.scroll.hidden=currentView!=='chat'; els.calendarView.hidden=currentView!=='calendar'; els.filesView.hidden=currentView!=='files'; els.composerWrap.hidden=currentView!=='chat';
    [[els.navChat,'chat'],[els.navCalendar,'calendar'],[els.navFiles,'files']].forEach(([b,v])=>b?.classList.toggle('active',v===currentView));
    if(els.viewTitle)els.viewTitle.textContent=currentView==='calendar'?'تقویم':currentView==='files'?'فایل‌ها':(boot.appName||'AI Bridge');
    if(els.scrollBottom)els.scrollBottom.hidden=currentView!=='chat'||els.scrollBottom.hidden;
    if(currentView==='calendar')loadCalendar(); if(currentView==='files')loadWorkspaceFiles(); closeSidebar();
  }
  function calendarToday(){const d=new Date(),j=g2j(d.getFullYear(),d.getMonth()+1,d.getDate());return {d,j,greg:dateKey(d)}}
  async function loadCalendar(){
    try{const r=await apiFetch(`${apiUrl}?action=calendar_get&_=${Date.now()}`,{cache:'no-store'});const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||'calendar_error');calendarDb=d.calendar||{events:[],custom_holidays:[]};if(!calendarYear){const t=calendarToday();calendarYear=t.j[0];calendarMonth=t.j[1];selectedJalali=`${t.j[0]}-${pad2(t.j[1])}-${pad2(t.j[2])}`;selectedGregorian=t.greg;}renderCalendar();}catch(e){if(els.calendarGrid)els.calendarGrid.innerHTML='<div class="upcoming-empty">خطا در دریافت تقویم</div>'}
  }
  function eventLocalDate(ev){try{return dateKey(new Date(ev.start))}catch{return ''}}
  function renderCalendar(){
    if(!els.calendarGrid)return;const today=calendarToday();els.calendarMonthTitle.textContent=`${FA_MONTHS[calendarMonth-1]} ${faDigits(calendarYear)}`;els.calendarTodayText.textContent=`امروز ${faDigits(today.j[2])} ${FA_MONTHS[today.j[1]-1]} ${faDigits(today.j[0])}`;
    const first=j2g(calendarYear,calendarMonth,1),fd=new Date(first[0],first[1]-1,first[2]),offset=(fd.getDay()+1)%7,len=jMonthLength(calendarYear,calendarMonth);els.calendarGrid.textContent='';
    const custom=new Map((calendarDb.custom_holidays||[]).map(x=>[`${Number(x.month)||0}-${Number(x.day)||0}`,String(x.title||'تعطیل')]));
    for(let i=0;i<offset;i++){const x=document.createElement('div');x.className='calendar-day empty';els.calendarGrid.appendChild(x)}
    for(let day=1;day<=len;day++){const g=j2g(calendarYear,calendarMonth,day),gd=new Date(g[0],g[1]-1,g[2]),gk=dateKey(gd),jk=`${calendarYear}-${pad2(calendarMonth)}-${pad2(day)}`,holiday=custom.get(`${calendarMonth}-${day}`)||FIXED_HOLIDAYS[`${calendarMonth}-${day}`]||'',friday=gd.getDay()===5,evs=(calendarDb.events||[]).filter(e=>String(e.status||'active')!=='cancelled'&&eventLocalDate(e)===gk);const cell=document.createElement('button');cell.type='button';cell.className='calendar-day'+(gk===today.greg?' today':'')+(holiday?' holiday':'')+(friday?' friday':'')+(jk===selectedJalali?' selected':'');cell.innerHTML=`<span class="day-number">${faDigits(day)}</span>${holiday?`<div class="day-holiday">${escapeHtml(holiday)}</div>`:''}<div class="day-events">${evs.slice(0,2).map(e=>`<div class="day-event">${escapeHtml(e.title||'رویداد')}</div>`).join('')}${evs.length>2?`<div class="day-more">+${faDigits(evs.length-2)}</div>`:''}</div>`;cell.onclick=()=>{selectedJalali=jk;selectedGregorian=gk;renderCalendar();renderUpcoming();};els.calendarGrid.appendChild(cell)}
    const cells=offset+len,pad=(7-(cells%7))%7;for(let i=0;i<pad;i++){const x=document.createElement('div');x.className='calendar-day empty';els.calendarGrid.appendChild(x)}
    if(els.selectedJalaliDate)els.selectedJalaliDate.textContent=faDigits(selectedJalali.replace(/-/g,'/'));renderUpcoming();
  }
  function renderUpcoming(){
    if(!els.upcomingEvents)return;const now=Date.now();const rows=(calendarDb.events||[]).filter(e=>String(e.status||'active')!=='cancelled'&&new Date(e.end||e.start).getTime()>=now).sort((a,b)=>new Date(a.start)-new Date(b.start)).slice(0,10);els.upcomingEvents.textContent='';if(!rows.length){els.upcomingEvents.innerHTML='<div class="upcoming-empty">برنامه‌ای در آینده ثبت نشده.</div>';return}rows.forEach(ev=>{const d=new Date(ev.start),j=g2j(d.getFullYear(),d.getMonth()+1,d.getDate()),row=document.createElement('div');row.className='upcoming-event';const strong=document.createElement('strong');strong.textContent=ev.title||'رویداد';const small=document.createElement('small');small.textContent=`${faDigits(j[2])} ${FA_MONTHS[j[1]-1]} · ${d.toLocaleTimeString('fa-IR',{hour:'2-digit',minute:'2-digit'})}`;const cancel=document.createElement('button');cancel.type='button';cancel.className='crumb-btn';cancel.textContent='لغو';cancel.onclick=async()=>{if(!confirm('این رویداد لغو شود؟'))return;await calendarWrite({operation:'cancel',id:ev.id});};row.append(strong,small,cancel);els.upcomingEvents.appendChild(row)})
  }
  async function calendarWrite(payload){const r=await apiFetch(`${apiUrl}?action=calendar_write`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||'calendar_write_failed');await loadCalendar();return d}
  async function saveQuickEvent(){const title=(els.quickEventTitle?.value||'').trim();if(!title){els.quickEventTitle?.focus();return}if(!selectedGregorian){const t=calendarToday();selectedGregorian=t.greg;selectedJalali=`${t.j[0]}-${pad2(t.j[1])}-${pad2(t.j[2])}`;}const time=els.quickEventTime?.value||'10:00',start=new Date(`${selectedGregorian}T${time}:00`),minutes=Math.max(5,Number(els.quickEventDuration?.value||60)),end=new Date(start.getTime()+minutes*60000),rem=Number(els.quickEventReminder?.value||0);try{els.saveQuickEvent.disabled=true;await calendarWrite({operation:'create',title,start:start.toISOString(),end:end.toISOString(),notes:els.quickEventNotes?.value||'',reminders:rem?[rem]:[]});els.quickEventTitle.value='';els.quickEventNotes.value='';}catch(e){alert('ثبت رویداد انجام نشد: '+(e.message||e))}finally{els.saveQuickEvent.disabled=false}}
  function calendarMove(delta){calendarMonth+=delta;if(calendarMonth<1){calendarMonth=12;calendarYear--}if(calendarMonth>12){calendarMonth=1;calendarYear++}renderCalendar()}
  function goCalendarToday(){const t=calendarToday();calendarYear=t.j[0];calendarMonth=t.j[1];selectedJalali=`${t.j[0]}-${pad2(t.j[1])}-${pad2(t.j[2])}`;selectedGregorian=t.greg;renderCalendar()}
  function updateCalendarClock(){if(!els.calendarClock)return;const d=new Date();els.calendarClock.textContent=d.toLocaleTimeString('fa-IR',{hour:'2-digit',minute:'2-digit'});}

  function humanBytes(n){n=Number(n||0);if(n<1024)return `${n} B`;if(n<1048576)return `${(n/1024).toFixed(1)} KB`;if(n<1073741824)return `${(n/1048576).toFixed(1)} MB`;return `${(n/1073741824).toFixed(1)} GB`}
  function renderBreadcrumbs(){if(!els.fileBreadcrumbs)return;els.fileBreadcrumbs.textContent='';const parts=currentFolder?currentFolder.split('/'):[];const root=document.createElement('button');root.className='crumb-btn';root.textContent='فایل‌های من';root.onclick=()=>{currentFolder='';loadWorkspaceFiles()};els.fileBreadcrumbs.appendChild(root);let path='';parts.forEach(part=>{const sep=document.createElement('span');sep.className='crumb-sep';sep.textContent='‹';els.fileBreadcrumbs.appendChild(sep);path=path?`${path}/${part}`:part;const btn=document.createElement('button');btn.className='crumb-btn';btn.textContent=part;const p=path;btn.onclick=()=>{currentFolder=p;loadWorkspaceFiles()};els.fileBreadcrumbs.appendChild(btn)})}
  async function loadWorkspaceFiles(){
    if(!els.workspaceFileList)return;const q=(els.workspaceSearch?.value||'').trim();try{const r=await apiFetch(`${apiUrl}?action=files_list&folder=${encodeURIComponent(q?'':currentFolder)}&q=${encodeURIComponent(q)}&_=${Date.now()}`,{cache:'no-store'});const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||'files_error');renderWorkspaceFiles(d.items||[],q);}catch(e){els.workspaceFileList.innerHTML='<div class="file-empty">خطا در دریافت فایل‌ها</div>'}
  }
  function renderWorkspaceFiles(items,q=''){renderBreadcrumbs();els.workspaceFileList.textContent='';els.workspaceFileEmpty.hidden=items.length>0;if(q&&els.fileBreadcrumbs)els.fileBreadcrumbs.innerHTML='<span class="crumb-btn">نتایج جستجو</span>';items.forEach(item=>{const row=document.createElement('div');row.className='file-row';const name=document.createElement('div');name.className='file-name-cell';const icon=document.createElement('div');icon.className='file-icon';icon.textContent=item.kind==='folder'?'DIR':((item.name||'').split('.').pop()||'FILE').slice(0,4).toUpperCase();const copy=document.createElement('div');copy.className='file-name-copy';const strong=document.createElement('strong');strong.textContent=item.name||'بدون نام';const small=document.createElement('small');small.textContent=item.path||'';copy.append(strong,small);name.append(icon,copy);if(item.kind==='folder')name.onclick=()=>{currentFolder=item.path||'';if(els.workspaceSearch)els.workspaceSearch.value='';loadWorkspaceFiles()};const kind=document.createElement('div');kind.className='file-kind';kind.textContent=item.kind==='folder'?'پوشه':(item.mime||'فایل');const size=document.createElement('div');size.className='file-size';size.textContent=item.kind==='folder'?'—':humanBytes(item.size);const actions=document.createElement('div');actions.className='file-row-actions';if(item.kind==='folder'){const open=document.createElement('button');open.textContent='باز کردن';open.onclick=()=>name.click();actions.append(open)}else{const dl=document.createElement('button');dl.textContent='دانلود';dl.onclick=()=>downloadWorkspaceFile(item);const mv=document.createElement('button');mv.textContent='انتقال';mv.onclick=()=>moveWorkspaceFile(item);const ren=document.createElement('button');ren.textContent='تغییر نام';ren.onclick=()=>renameWorkspaceFile(item);const tr=document.createElement('button');tr.textContent='حذف';tr.className='danger';tr.onclick=()=>trashWorkspaceFile(item);actions.append(dl,mv,ren,tr)}row.append(name,kind,size,actions);els.workspaceFileList.appendChild(row)})}
  async function uploadWorkspaceFiles(files){for(const file of [...files]){if(file.size>50*1024*1024){alert(`${file.name}: حداکثر حجم ۵۰ مگابایت است.`);continue}try{const dataUrl=await readDataUrl(file),r=await apiFetch(`${apiUrl}?action=files_upload`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:file.name,folder:currentFolder,data_url:dataUrl})}),d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||'upload_failed')}catch(e){alert(`آپلود ${file.name} انجام نشد: ${e.message||e}`)}}await loadWorkspaceFiles()}
  async function fileAction(payload){const r=await apiFetch(`${apiUrl}?action=files_action`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||'file_action_failed');await loadWorkspaceFiles();return d}
  async function newWorkspaceFolder(){const name=prompt('نام پوشه جدید:','');if(!name?.trim())return;try{await fileAction({operation:'mkdir',folder:currentFolder,name:name.trim()})}catch(e){alert(e.message||e)}}
  async function renameWorkspaceFile(item){const name=prompt('نام جدید فایل:',item.name||'');if(!name?.trim()||name===item.name)return;try{await fileAction({operation:'rename',id:item.id,name:name.trim(),folder:(item.path||'').split('/').slice(0,-1).join('/')})}catch(e){alert(e.message||e)}}
  async function moveWorkspaceFile(item){const folder=prompt('مسیر پوشه مقصد را بنویس (مثلاً مدارک شرکت/قراردادها):',(item.path||'').split('/').slice(0,-1).join('/'));if(folder===null)return;try{await fileAction({operation:'move',id:item.id,folder:folder.trim(),name:item.name})}catch(e){alert(e.message||e)}}
  async function trashWorkspaceFile(item){if(!confirm(`«${item.name}» به سطل زباله منتقل شود؟`))return;try{await fileAction({operation:'trash',id:item.id})}catch(e){alert(e.message||e)}}
  async function downloadWorkspaceFile(item){try{const r=await apiFetch(`${apiUrl}?action=file_download&id=${encodeURIComponent(item.id)}`);if(!r.ok)throw new Error('download_failed');const blob=await r.blob(),u=URL.createObjectURL(blob),a=document.createElement('a');a.href=u;a.download=item.name||'download';document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(u),2000)}catch(e){alert('دانلود فایل انجام نشد.')}
  }

  els.navChat?.addEventListener('click',()=>switchView('chat'));
  els.navCalendar?.addEventListener('click',()=>switchView('calendar'));
  els.navFiles?.addEventListener('click',()=>switchView('files'));
  els.calPrev?.addEventListener('click',()=>calendarMove(-1)); els.calNext?.addEventListener('click',()=>calendarMove(1)); els.calToday?.addEventListener('click',goCalendarToday);
  els.saveQuickEvent?.addEventListener('click',saveQuickEvent);
  els.workspaceUpload?.addEventListener('click',()=>els.workspaceFileInput?.click());
  els.workspaceFileInput?.addEventListener('change',async e=>{await uploadWorkspaceFiles(e.target.files||[]);e.target.value=''});
  els.newFolder?.addEventListener('click',newWorkspaceFolder);
  els.workspaceSearch?.addEventListener('input',()=>{clearTimeout(fileSearchTimer);fileSearchTimer=setTimeout(loadWorkspaceFiles,260)});
  updateCalendarClock();setInterval(updateCalendarClock,30000);
  els.input.addEventListener('input', autoGrow);
  els.input.addEventListener('keydown', e => { if(e.key==='Enter' && !e.shiftKey && !e.isComposing){ e.preventDefault(); sendMessage(); } else if(e.key==='Escape'&&isResponseActive()){e.preventDefault();cancelCurrentResponse();} });
  els.send.addEventListener('click', sendMessage);
  if(els.stopReply)els.stopReply.addEventListener('click',cancelCurrentResponse);
  if(els.attach)els.attach.addEventListener('click',()=>els.fileInput?.click());
  if(els.fileInput)els.fileInput.addEventListener('change',async e=>{await addAttachments(e.target.files);e.target.value='';});
  if(els.modelSelect)els.modelSelect.addEventListener('change',e=>{renderAttachments();requestModelLoad(e.target.value)});
  if(els.modelStop)els.modelStop.addEventListener('click',requestModelStop);
  if(els.clearHistory)els.clearHistory.addEventListener('click',clearAllHistory);
  if(els.scrollBottom)els.scrollBottom.addEventListener('click',scrollToBottom);
  els.scroll.addEventListener('scroll',updateScrollButton,{passive:true});
  els.newChat.addEventListener('click', createNewChat); els.newMobile.addEventListener('click', createNewChat);
  els.menu.addEventListener('click', openSidebar); els.closeSidebar.addEventListener('click', closeSidebar); els.scrim.addEventListener('click', closeSidebar);
  window.addEventListener('beforeunload',()=>{saveDraft();stopWatch();});
  window.addEventListener('keydown',e=>{if(e.key==='Escape'&&isResponseActive()){e.preventDefault();cancelCurrentResponse();}});
  document.addEventListener('visibilitychange', () => { if (!document.hidden && !watchAbort) startWatch(); });

  ensureActive(); renderConversationList(); loadDraft();renderAttachments(); switchView('chat'); fetchHistory(true).then(()=>{syncServerSessions();startWatch();}); autoGrow(); updateScrollButton();
})();
