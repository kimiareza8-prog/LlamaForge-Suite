(() => {
  "use strict";

  const $ = (sel, root=document) => root.querySelector(sel);
  const $$ = (sel, root=document) => [...root.querySelectorAll(sel)];
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  const icons = {
    menu:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M4 7h16M4 12h16M4 17h16"/></svg>',
    panel:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/></svg>',
    plus:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M12 5v14M5 12h14"/></svg>',
    home:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="m3 11 9-7 9 7"/><path d="M5 10v10h14V10M9 20v-6h6v6"/></svg>',
    chat:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z"/></svg>',
    models:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9z"/><path d="m4.5 7.8 7.5 4.3 7.5-4.3M12 12v9"/></svg>',
    tune:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M4 7h10M18 7h2M4 17h2M10 17h10M14 4v6M6 14v6"/></svg>',
    cpu:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><rect x="7" y="7" width="10" height="10" rx="2"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3"/></svg>',
    runtime:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M8 3h8M9 3v4l-4.5 8a4 4 0 0 0 3.5 6h8a4 4 0 0 0 3.5-6L15 7V3"/><path d="M7 14h10"/></svg>',
    terminal:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="m4 6 5 5-5 5M11 17h9"/></svg>',
    logs:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M5 5h14M5 9h14M5 13h9M5 17h11"/></svg>',
    settings:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.56V21h-4v-.08A1.7 1.7 0 0 0 9 19.36a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.63 15 1.7 1.7 0 0 0 3.08 14H3v-4h.08A1.7 1.7 0 0 0 4.64 9a1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.63 1.7 1.7 0 0 0 10 3.08V3h4v.08A1.7 1.7 0 0 0 15 4.64a1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.37 9 1.7 1.7 0 0 0 20.92 10H21v4h-.08A1.7 1.7 0 0 0 19.4 15Z"/></svg>',
    chevron:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="m9 18 6-6-6-6"/></svg>',
    down:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="m7 10 5 5 5-5"/></svg>',
    send:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M12 19V5M6.5 10.5 12 5l5.5 5.5"/></svg>',
    stop:'<svg viewBox="0 0 24 24" fill="currentColor"><rect x="7" y="7" width="10" height="10" rx="2"/></svg>',
    copy:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M15 9V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h4"/></svg>',
    regen:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M20 6v5h-5M4 18v-5h5"/><path d="M6.1 9a7 7 0 0 1 11.5-2.6L20 11M4 13l2.4 4.6A7 7 0 0 0 18 15"/></svg>',
    edit:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L8 18l-4 1 1-4z"/></svg>',
    trash:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M4 7h16M9 7V4h6v3M7 7l1 14h8l1-14"/></svg>',
    search:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></svg>',
    check:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="m5 12 4 4L19 6"/></svg>',
    spark:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="m12 3 1.3 4.2L17.5 9l-4.2 1.8L12 15l-1.3-4.2L6.5 9l4.2-1.8zM19 15l.7 2.3L22 18l-2.3.7L19 21l-.7-2.3L16 18l2.3-.7z"/></svg>',
    folder:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M3 6h7l2 2h9v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
    refresh:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M20 6v5h-5M4 18v-5h5"/><path d="M6.2 9A7 7 0 0 1 18 6l2 5M4 13l2 5a7 7 0 0 0 11.8-3"/></svg>',
    download:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M12 3v12M7 10l5 5 5-5M5 21h14"/></svg>',
    play:'<svg viewBox="0 0 24 24" fill="currentColor"><path d="m8 5 11 7-11 7z"/></svg>',
    harddrive:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 15h.01M11 15h.01"/></svg>',
    alert:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M12 3 2.8 20h18.4z"/><path d="M12 9v4M12 17h.01"/></svg>',
    info:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/></svg>',
    brain:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M9.5 4.5A3.5 3.5 0 0 0 6 8v.5A3.5 3.5 0 0 0 4.5 15 3.5 3.5 0 0 0 8 18.5h1.5M14.5 4.5A3.5 3.5 0 0 1 18 8v.5a3.5 3.5 0 0 1 1.5 6.5 3.5 3.5 0 0 1-3.5 3.5h-1.5M9.5 3v18M14.5 3v18M9.5 8h-2M14.5 8h2M9.5 15h-2M14.5 15h2"/></svg>',
    globe:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a15 15 0 0 1 0 18M12 3a15 15 0 0 0 0 18"/></svg>',
    link:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M10 13a5 5 0 0 0 7.1.1l2-2a5 5 0 0 0-7.1-7.1l-1.1 1.1"/><path d="M14 11a5 5 0 0 0-7.1-.1l-2 2A5 5 0 0 0 12 20l1.1-1.1"/></svg>',
    x:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="m6 6 12 12M18 6 6 18"/></svg>',
    calendar:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 10h18"/></svg>',
    files:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v5h5M9 13h6M9 17h6"/></svg>',
    cluster:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="8.5" y="14" width="7" height="7" rx="2"/><path d="M6.5 10v2h11v-2M12 12v2"/></svg>'
  };
  function icon(name){ return icons[name] || ''; }
  $$('[data-icon]').forEach(el => el.innerHTML = icon(el.dataset.icon));
  $('#sidebarToggle').innerHTML = icon('x');
  $('#collapseSidebar').innerHTML = icon('panel');
  $('#mobileNavButton').innerHTML = icon('menu');

  const routes = [
    ['home','Home','home'], ['chat','Chat','chat'], ['calendar','Calendar','calendar'], ['files','Files','files'], ['cluster','Cluster','cluster'], ['agent','Agent','globe'], ['brain','Brain','brain'], ['models','Models','models'], ['optimize','Optimize','tune'],
    ['system','System','cpu'], ['runtime','Runtime','runtime'], ['advanced','Advanced','terminal'], ['logs','Logs','logs'], ['settings','Settings','settings']
  ];

  const App = {
    state:null,
    route:(location.hash || '#models').slice(1),
    chatAbort:null,
    streaming:false,
    contextTokens:null,
    threads:loadThreads(),
    activeThreadId:localStorage.getItem('lf.activeThread') || null,
    sidebarCollapsed:localStorage.getItem('lf.sidebarCollapsed') === '1',
    eventSource:null,
    reconnectTimer:null,
    renderKey:'',
    lastStateError:'',
    chatFollowTail:true,
    brainLearning:false,
    brainSetup:false,
    brainTurnPending:false,
    brainTogglePending:false,
    toolsOpen:localStorage.getItem('lf.toolsOpen')==='1',
    pendingMemoryMode:null,
    settingsMemoryDirty:false,
    settingsFormDirty:false,
    settingsDraft:{},
    pendingAttachments:[],
    calendarCursor:null,
    filesFolder:'',
    filesSearch:'',
  };

  function escapeHtml(s='') { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c])); }
  function formatBytesGB(gb){ return Number(gb||0).toFixed(gb>=10?1:2) + ' GB'; }
  function formatBytes(n){n=Number(n||0);if(n<=0)return '0 B';const u=['B','KB','MB','GB','TB'];let i=0;while(n>=1024&&i<u.length-1){n/=1024;i++}return `${n.toFixed(i>=3?2:i>=2?1:0)} ${u[i]}`;}
  function formatEta(sec){sec=Number(sec);if(!Number.isFinite(sec)||sec<0)return '';if(sec<60)return `${Math.ceil(sec)}s`;const m=Math.floor(sec/60),s=Math.ceil(sec%60);return `${m}m ${s}s`;}
  function shortName(name='', n=44){ return name.length>n ? name.slice(0,n-1)+'…' : name; }
  function hasRTL(text=''){ return /[\u0590-\u08FF\uFB1D-\uFEFC]/.test(text); }
  function routeTitle(){ return routes.find(x=>x[0]===App.route)?.[1] || 'LlamaForge'; }
  function brainStructuralKey(brain={}){
    const j=brain.job||{};
    return JSON.stringify({
      enabled:!!brain.enabled,zero_context:!!brain.zero_context,strict_learning:!!brain.strict_learning,auto_synthesize:!!brain.auto_synthesize,
      setup_ready:!!brain.setup_ready,training_base_ready:!!brain.training_base_ready,trainer_ready:!!brain.trainer_ready,toolchain_ready:!!brain.toolchain_ready,
      adapter_ready:!!brain.adapter_ready,generation:Number(brain.generation||0),learned_packets:Number(brain.learned_packets||0),training_base:String(brain.training_base||''),
      job_state:String(j.state||''),job_stage:String(j.stage||''),job_error:String(j.error||'')
    });
  }
  function stateRenderKey(next){
    const base={route:App.route,model:next.active_model?.path,ready:next.server?.ready,running:next.server?.running,error:next.server?.error,runtime:next.runtime?.server,job:next.job?.state,models:next.models?.length,trainables:next.trainable_models?.length};
    if(App.route==='brain')base.brain=brainStructuralKey(next.brain||{});
    if(App.route==='agent')base.agent=JSON.stringify(next.agent||{});
    if(App.route==='settings')base.settings=JSON.stringify(next.config||{});
    if(App.route==='runtime')base.runtimeJob=JSON.stringify(next.job||{});
    if(App.route==='cluster')base.cluster=JSON.stringify(next.cluster||{});
    return JSON.stringify(base);
  }
  async function api(path, options={}) {
    const opts = {...options};
    opts.headers = {...(opts.headers||{})};
    if (opts.body && typeof opts.body !== 'string') { opts.headers['Content-Type']='application/json'; opts.body=JSON.stringify(opts.body); }
    const r = await fetch(path, opts);
    let data = null; try { data = await r.json(); } catch { data = {}; }
    if (!r.ok) throw new Error(data?.error || `Request failed (${r.status})`);
    return data;
  }
  function toast(title, message='', type='ok', timeout=3500){
    const stack=$('#toastStack'); const el=document.createElement('div'); el.className='toast';
    el.innerHTML=`<div class="toast-icon">${type==='error'?icon('alert'):type==='info'?icon('info'):icon('check')}</div><div><strong>${escapeHtml(title)}</strong>${message?`<p>${escapeHtml(message)}</p>`:''}</div>`;
    stack.appendChild(el); setTimeout(()=>{el.style.opacity='0';el.style.transform='translateY(5px)';setTimeout(()=>el.remove(),180)},timeout);
  }
  function confirmModal(title, message, confirmText='Continue', danger=false){
    return new Promise(resolve=>{
      const root=$('#modalRoot'); root.innerHTML=`<div class="modal-backdrop"><div class="modal"><h3>${escapeHtml(title)}</h3><p>${escapeHtml(message)}</p><div class="inline-actions"><button class="secondary-button" data-no>Cancel</button><button class="${danger?'danger-button':'primary-button'}" data-yes>${escapeHtml(confirmText)}</button></div></div></div>`;
      $('[data-no]',root).onclick=()=>{root.innerHTML='';resolve(false)}; $('[data-yes]',root).onclick=()=>{root.innerHTML='';resolve(true)};
      $('.modal-backdrop',root).onclick=e=>{if(e.target===e.currentTarget){root.innerHTML='';resolve(false)}};
    });
  }
  function setRoute(route){
    App.route = routes.some(x=>x[0]===route) ? route : 'models';
    location.hash = App.route;
    document.querySelector('.app-shell').classList.remove('mobile-menu');
    renderNav(); render();
  }

  function loadThreads(){
    try { const x=JSON.parse(localStorage.getItem('lf.threads')||'[]'); return Array.isArray(x)?x:[]; } catch { return []; }
  }
  function saveThreads(){
    const persisted=App.threads.slice(0,40).map(t=>({...t,messages:(t.messages||[]).map(m=>({...m,attachments:Array.isArray(m.attachments)?m.attachments.map(a=>{const x={...a};if(x.data_url){delete x.data_url;x.unavailable=true}if(x.text){delete x.text;x.unavailable=true}return x;}):undefined}))}));
    try{localStorage.setItem('lf.threads',JSON.stringify(persisted));}catch{}
    renderRecent();
  }
  function currentThread(create=true){
    let t=App.threads.find(x=>x.id===App.activeThreadId);
    if(!t && create){ t={id:crypto.randomUUID?crypto.randomUUID():String(Date.now()),title:'New chat',messages:[],created:Date.now(),updated:Date.now()}; App.threads.unshift(t); App.activeThreadId=t.id; localStorage.setItem('lf.activeThread',t.id); saveThreads(); }
    return t;
  }
  function newThread(){
    if(App.streaming && App.chatAbort) App.chatAbort.abort();
    const t={id:crypto.randomUUID?crypto.randomUUID():String(Date.now()),title:'New chat',messages:[],created:Date.now(),updated:Date.now()};
    App.threads.unshift(t); App.activeThreadId=t.id; App.contextTokens=null; localStorage.setItem('lf.activeThread',t.id); saveThreads(); setRoute('chat');
  }
  function selectThread(id){ App.activeThreadId=id; localStorage.setItem('lf.activeThread',id); App.contextTokens=null; setRoute('chat'); }
  function deleteThread(id){ App.threads=App.threads.filter(t=>t.id!==id); if(App.activeThreadId===id) App.activeThreadId=App.threads[0]?.id||null; saveThreads(); render(); }
  async function clearAllThreads(){
    if(App.streaming)stopGeneration();
    const ok=await confirmModal('Clear chat history','Delete every local conversation stored in this browser? This cannot be undone.','Clear all',true);if(!ok)return;
    App.threads=[];App.activeThreadId=null;App.contextTokens=null;localStorage.removeItem('lf.threads');localStorage.removeItem('lf.activeThread');saveThreads();newThread();
  }
  function titleFromPrompt(text){ const clean=text.replace(/\s+/g,' ').trim(); return clean.length>38?clean.slice(0,37)+'…':clean||'New chat'; }

  function renderNav(){
    // Keep the daily workflow intentionally small.  Advanced engine pages still
    // exist and can be reached from Settings, but the main navigation is just:
    // choose a model -> chat -> see learning status.
    const primaryIds=new Set(['models','chat','calendar','files','cluster','agent','brain','settings','logs']);
    const primary=routes.filter(x=>primaryIds.has(x[0]));
    const button=([id,label,ic])=>`<button class="nav-item ${App.route===id?'active':''}" data-route="${id}" title="${label}">${icon(ic)}<span class="nav-label">${label}</span></button>`;
    $('#nav').innerHTML=primary.map(button).join('');
    $$('[data-route]').forEach(b=>b.onclick=()=>setRoute(b.dataset.route));
    document.querySelector('.app-shell').classList.toggle('sidebar-collapsed',App.sidebarCollapsed);
    renderRecent(); updateChrome();
  }
  function renderRecent(){
    const root=$('#recentChats'); if(!root) return;
    root.innerHTML=App.threads.slice(0,12).map(t=>`<div class="recent-chat-row ${App.activeThreadId===t.id?'active':''}"><button class="recent-chat" data-thread="${escapeHtml(t.id)}" title="${escapeHtml(t.title)}">${escapeHtml(t.title)}</button><button class="recent-delete" data-delete-thread="${escapeHtml(t.id)}" title="Delete chat">${icon('trash')}</button></div>`).join('') || '<div style="padding:6px 11px;color:var(--faint);font-size:10px">No conversations yet</div>';
    $$('[data-thread]',root).forEach(b=>b.onclick=()=>selectThread(b.dataset.thread));
    $$('[data-delete-thread]',root).forEach(b=>b.onclick=async e=>{e.stopPropagation();const id=b.dataset.deleteThread,t=App.threads.find(x=>x.id===id);const ok=await confirmModal('Delete chat',`Delete “${t?.title||'this chat'}”?`,'Delete',true);if(ok)deleteThread(id)});
  }
  function updateChrome(){
    const s=App.state;
    const currentRoute=routes.find(x=>x[0]===App.route);
    const heading=`<span class="page-heading-icon">${icon(currentRoute?.[2]||'home')}</span><h1>${routeTitle()}</h1>`; if($('#pageHeading')?.innerHTML!==heading) $('#pageHeading').innerHTML=heading;
    const model=s?.active_model;
    const modelHtml=model?`${icon('models')}<span class="pill-name">${escapeHtml(shortName(model.name,32))}</span>${icon('down')}`:`${icon('models')}<span class="pill-name">Choose model</span>`; if($('#modelPill')?.innerHTML!==modelHtml) $('#modelPill').innerHTML=modelHtml;
    const ss=s?.server;
    let cls='',txt='Offline';
    if(ss?.ready){cls='ready';txt='Ready'} else if(ss?.running){cls='loading';txt='Loading'} else if(ss?.error){cls='error';txt='Needs attention'};
    $('#connectionPill').className='connection-pill '+cls;
    const connectionHtml=`<span class="status-dot ${cls==='ready'?'ready':cls==='loading'?'loading':cls==='error'?'error':''}"></span>${txt}`; if($('#connectionPill')?.innerHTML!==connectionHtml) $('#connectionPill').innerHTML=connectionHtml;
    const runtimeHtml=`<span class="status-dot ${ss?.ready?'ready':ss?.running?'loading':ss?.error?'error':''}"></span><span class="nav-label">${ss?.ready?'Model ready':ss?.running?'Loading model':s?.runtime?.installed?'Runtime ready':'Runtime not installed'}</span>`; if($('#runtimeMini')?.innerHTML!==runtimeHtml) $('#runtimeMini').innerHTML=runtimeHtml;
  }

  function render(){
    updateChrome();
    document.body.classList.toggle('chat-active', App.route==='chat');
    document.body.dataset.route=App.route;
    const view=$('#view');
    // renderChat adds the non-scrolling ``chat-route`` class.  The old shell
    // never removed it when navigating away, so Models/Brain/Settings could
    // become permanently unscrollable after visiting Chat once.
    view.className='view';
    switch(App.route){
      case 'chat': return renderChat(view);
      case 'calendar': return renderCalendar(view);
      case 'files': return renderFiles(view);
      case 'cluster': return renderCluster(view);
      case 'agent': return renderAgent(view);
      case 'brain': return renderBrain(view);
      case 'models': return renderModels(view);
      case 'optimize': return renderOptimize(view);
      case 'system': return renderSystem(view);
      case 'runtime': return renderRuntime(view);
      case 'advanced': return renderAdvanced(view);
      case 'logs': return renderLogs(view);
      case 'settings': return renderSettings(view);
      default: return renderHome(view);
    }
  }

  function pageTitle(eyebrow,title,desc){ return `<div class="page-title"><div class="eyebrow">${escapeHtml(eyebrow)}</div><h2>${escapeHtml(title)}</h2><p>${escapeHtml(desc)}</p></div>`; }
  function runButtonLabel(){
    const ss=App.state?.server; if(ss?.ready) return `${icon('chat')} Open chat`; if(ss?.running) return `${icon('runtime')} Loading…`; return `${icon('play')} Run optimized`;
  }

  async function renderCluster(view){
    let c=App.state?.cluster||{};
    try{ c=await api('/api/cluster'); if(App.state)App.state.cluster=c; }catch(e){ view.innerHTML=`<div class="error-card"><strong>Cluster unavailable</strong><p>${escapeHtml(e.message)}</p></div>`; return; }
    const role=c.role||'standalone', backend=c.backend||{}, workers=c.nodes||[], nodes=(role==='master'&&c.master_compute)?[c.master_compute,...workers]:workers, plan=c.active_plan;
    const online=Number(c.workers_online||0), registered=Number(c.workers_registered||0);
    const roleBtn=(id,label,sub)=>`<button class="cluster-role ${role===id?'active':''}" data-role="${id}"><strong>${label}</strong><small>${sub}</small></button>`;
    const modeLabel={auto:'Auto',selected_pool:'Selected Pool',force_selected:'Force Selected Nodes'};
    const optLabel={smart:'Smart / Balanced',maximum_compute:'Maximum Compute',maximum_model_size:'Maximum Model Size',lowest_latency:'Lowest Latency',manual:'Manual'};
    const nodeCards=nodes.length?nodes.map(n=>{
      const h=n.hardware||{},l=n.limits||{},b=n.benchmark||{},gpu=(h.gpus||[])[0],safe=Number(l.ram_limit_gb||0),free=Number(h.ram_available_gb||0),total=Number(h.ram_total_gb||0);
      const paired=!!n.paired, online=!!n.online, enabled=l.enabled!==false, isMaster=n.node_id==='__master__';
      return `<article class="cluster-node ${online?'online':'offline'}" data-node="${escapeHtml(n.node_id)}">
        <div class="cluster-node-head"><div><span class="status-dot ${online?'ready':''}"></span><div><strong>${escapeHtml(n.hostname||n.node_id)}</strong><small>${isMaster?'Local compute node':escapeHtml(n.ip||'Discovered worker')} · ${escapeHtml(n.version||'unknown version')}</small></div></div><label class="cluster-use"><input type="checkbox" data-node-enable ${enabled?'checked':''} ${!paired?'disabled':''}> Use this Node</label></div>
        <div class="cluster-node-metrics"><span><b>${Number(h.logical_cores||0)}</b> threads</span><span><b>${free.toFixed(1)}</b> / ${total.toFixed(1)} GB free</span><span><b>${Number(n.ping_ms||0).toFixed(1)} ms</b> ping</span><span><b>${Number(n.network_mbps||h.link_speed_mbps||0).toFixed(0)} Mbps</b> network</span>${gpu?`<span><b>${escapeHtml(gpu.name||'GPU')}</b> ${Number(gpu.memory_gb||gpu.vram_gb||0).toFixed(1)} GB</span>`:''}</div>
        ${!paired?`<div class="cluster-pair"><input class="input" data-pair-code placeholder="6-digit pairing code" maxlength="6"><button class="secondary-button" data-pair>Pair securely</button></div>`:`
        <div class="cluster-controls"><label><span>RAM policy</span><select data-ram-mode><option value="auto" ${l.ram_mode==='auto'?'selected':''}>Auto</option><option value="manual" ${l.ram_mode==='manual'?'selected':''}>Manual</option></select></label><label><span>Cluster RAM limit</span><input data-ram-limit type="number" min="0.25" step="0.25" max="${Math.max(.25,free-.5).toFixed(2)}" value="${safe.toFixed(2)}" ${l.ram_mode==='auto'?'disabled':''}></label><label><span>CPU policy</span><select data-cpu-mode><option value="auto" ${l.cpu_mode==='auto'?'selected':''}>Auto</option><option value="manual" ${l.cpu_mode==='manual'?'selected':''}>Manual</option></select></label><label><span>CPU threads</span><input data-cpu-threads type="number" min="1" max="${Math.max(1,Number(h.logical_cores||1))}" value="${Number(l.cpu_threads||h.logical_cores||1)}" ${l.cpu_mode==='auto'?'disabled':''}></label></div>
        <div class="cluster-node-foot"><span>${b.measured_at?`Bench: CPU ${Number(b.cpu_score||0).toFixed(0)} · RAM ${Number(b.memory_bandwidth_gbps||0).toFixed(1)} GB/s · Net ${Number(b.network_mbps||0).toFixed(0)} Mbps`:'Not benchmarked yet'}</span><div><button class="text-button" data-save-node>Save limits</button><button class="text-button" data-benchmark>${b.measured_at?'Re-Benchmark':'Benchmark'}</button>${isMaster?'':'<button class="text-button danger" data-forget>Forget</button>'}</div></div>`}
      </article>`}).join(''):`<div class="cluster-empty">${icon('cluster')}<strong>No Workers discovered yet</strong><span>Master local compute is available here; turn Worker mode ON on other LlamaForge computers to add more capacity.</span></div>`;
    const planRows=plan?.nodes?.map(n=>`<div class="plan-node"><strong>${escapeHtml(n.hostname||n.node_id)}</strong><span>${Number(n.ram_allocation_gb||n.allocated_ram_gb||0).toFixed(2)} GB RAM</span><span>${Number(n.compute_share||0).toFixed(1)}% compute</span></div>`).join('')||'';
    const worker=c.worker||{};
    view.innerHTML=`<div class="page cluster-page">
      ${pageTitle('ELASTIC COMPUTE','Smart Cluster','One LlamaForge build can run as Standalone, Master or Worker. The Master discovers paired Workers automatically and keeps memory allocation separate from compute share.')}
      <section class="cluster-role-grid">${roleBtn('standalone','Standalone','Use only this computer')}${roleBtn('master','Master','Schedule and control Workers')}${roleBtn('worker','Worker','Expose compute to an authorized Master')}</section>
      ${role==='worker'?`<section class="cluster-worker-hero"><div>${icon('cluster')}<div><span class="eyebrow">LLAMAFORGE WORKER</span><h3>${escapeHtml(worker.hostname||c.hostname||'This computer')}</h3><p>Status: <b>${escapeHtml(worker.state||'ready')}</b> · RPC ${worker.rpc?.running?'computing':'ready'} · ${Number(worker.hardware?.ram_available_gb||0).toFixed(1)} GB RAM free</p><label class="cluster-autostart"><input id="workerAutostart" type="checkbox" ${c.worker_autostart?'checked':''}> Start Worker automatically with this computer</label></div></div><div class="pair-code"><small>PAIRING CODE</small><strong>${escapeHtml(c.pairing_code||'------')}</strong><span>Enter this once on the Master.</span></div></section>`:''}
      ${role==='master'?`<>
      <div class="cluster-summary"><div><span>Cluster</span><strong>${c.enabled?'READY':'OFF'}</strong><small>${online} online / ${registered} registered</small></div><div><span>Allowed RAM</span><strong>${Number(c.allowed_cluster_ram_gb||0).toFixed(1)} GB</strong><small>hard per-node budgets</small></div><div><span>CPU threads</span><strong>${Number(c.active_cpu_threads||0)}</strong><small>currently allowed</small></div><div><span>Backend</span><strong>${backend.distributed_compute?'RPC ready':'Needs runtime'}</strong><small>${backend.tensor_split?'tensor split available':'capability probe active'}</small></div></div>
      <section class="cluster-config panel"><div class="cluster-config-row"><label class="cluster-switch"><input id="clusterEnabled" type="checkbox" ${c.enabled?'checked':''}><span>Enable distributed inference</span></label><label><span>Node selection</span><select id="clusterSelection">${Object.entries(modeLabel).map(([v,t])=>`<option value="${v}" ${c.selection_mode===v?'selected':''}>${t}</option>`).join('')}</select></label><label><span>Optimization</span><select id="clusterOptimization">${Object.entries(optLabel).map(([v,t])=>`<option value="${v}" ${c.optimization_mode===v?'selected':''}>${t}</option>`).join('')}</select></label><button id="clusterSaveModes" class="primary-button">Apply</button></div>
      ${!backend.distributed_compute?`<div class="cluster-runtime-warning">${icon('alert')}<div><strong>Cluster-capable llama.cpp runtime is not active.</strong><span>The Worker backend requires matching llama-server/RPC binaries. Build the RPC-enabled CPU runtime from source on each cluster computer.</span></div><button id="clusterBuildRuntime" class="secondary-button">Build cluster runtime</button></div>`:''}</section>
      <section class="section"><div class="section-head"><div><h3>Workers</h3><p>Discovery is automatic. Pair once, then control RAM/CPU from the Master.</p></div><button id="clusterRefresh" class="secondary-button">${icon('refresh')} Refresh</button></div><div class="cluster-nodes">${nodeCards}</div></section>
      ${plan?`<section class="section"><div class="section-head"><div><h3>Active plan</h3><p>${escapeHtml(plan.strategy||'distributed')} · ${escapeHtml(plan.optimization_mode||'smart')} · required ${Number(plan.required_ram_gb||0).toFixed(2)} GB</p></div><span class="tag">${escapeHtml(plan.plan_id||'')}</span></div><div class="cluster-plan panel">${planRows}</div></section>`:''}
      <section class="section"><div class="section-head"><div><h3>Cluster profiles</h3><p>Save RAM/CPU/node-selection presets such as Office Cluster or Night Mode.</p></div><div class="profile-actions"><input id="clusterProfileName" class="input" placeholder="Profile name"><button id="clusterProfileSave" class="secondary-button">Save profile</button></div></div><div class="profile-list">${(c.profiles||[]).map(p=>`<button class="profile-chip" data-profile="${escapeHtml(p.name)}">${escapeHtml(p.name)}</button>`).join('')||'<span class="muted">No profiles yet.</span>'}</div></section>
      </>`:''}
      ${role==='standalone'?`<section class="cluster-empty standalone">${icon('cpu')}<strong>Standalone mode</strong><span>All existing LlamaForge features work exactly as before. Switch this machine to Master or Worker only when you want a LAN cluster.</span></section>`:''}
    </div>`.replaceAll('<>','').replaceAll('</>','');

    $$('.cluster-role').forEach(b=>b.onclick=async()=>{try{await api('/api/cluster/role',{method:'POST',body:{role:b.dataset.role}});await refreshState(true);renderCluster(view)}catch(e){toast('Role change failed',e.message,'error')}});
    if($('#clusterSaveModes'))$('#clusterSaveModes').onclick=async()=>{try{await api('/api/cluster/modes',{method:'POST',body:{enabled:$('#clusterEnabled').checked,selection_mode:$('#clusterSelection').value,optimization_mode:$('#clusterOptimization').value}});toast('Cluster policy saved','','info');await refreshState(true);renderCluster(view)}catch(e){toast('Could not save policy',e.message,'error')}};
    if($('#workerAutostart'))$('#workerAutostart').onchange=async()=>{try{const d=await api('/api/cluster/worker-autostart',{method:'POST',body:{enabled:$('#workerAutostart').checked}});$('#workerAutostart').checked=!!d.enabled;toast('Worker startup updated',d.enabled?'Auto-start enabled':'Auto-start disabled','info')}catch(e){toast('Could not change auto-start',e.message,'error')}};
    if($('#clusterBuildRuntime'))$('#clusterBuildRuntime').onclick=async()=>{try{await api('/api/runtime/build-cluster',{method:'POST',body:{}});toast('Cluster runtime build started','Progress is shown in Runtime and Logs.','info')}catch(e){toast('Build failed',e.message,'error')}};
    if($('#clusterRefresh'))$('#clusterRefresh').onclick=()=>renderCluster(view);
    $$('.cluster-node').forEach(card=>{
      const id=card.dataset.node;
      const pair=card.querySelector('[data-pair]'); if(pair)pair.onclick=async()=>{try{await api('/api/cluster/pair',{method:'POST',body:{node_id:id,pairing_code:card.querySelector('[data-pair-code]').value}});toast('Worker paired','','info');renderCluster(view)}catch(e){toast('Pairing failed',e.message,'error')}};
      const ramMode=card.querySelector('[data-ram-mode]'),ramLimit=card.querySelector('[data-ram-limit]'),cpuMode=card.querySelector('[data-cpu-mode]'),cpuThreads=card.querySelector('[data-cpu-threads]');
      if(ramMode)ramMode.onchange=()=>ramLimit.disabled=ramMode.value==='auto'; if(cpuMode)cpuMode.onchange=()=>cpuThreads.disabled=cpuMode.value==='auto';
      const save=card.querySelector('[data-save-node]'); if(save)save.onclick=async()=>{try{await api('/api/cluster/node',{method:'POST',body:{node_id:id,enabled:card.querySelector('[data-node-enable]').checked,ram_mode:ramMode.value,ram_limit_gb:Number(ramLimit.value),cpu_mode:cpuMode.value,cpu_threads:Number(cpuThreads.value)}});toast('Worker limits saved','','info');renderCluster(view)}catch(e){toast('Could not save Worker',e.message,'error')}};
      const bench=card.querySelector('[data-benchmark]'); if(bench)bench.onclick=async()=>{bench.disabled=true;try{toast('Benchmark started',id,'info');await api('/api/cluster/benchmark',{method:'POST',body:{node_id:id}});renderCluster(view)}catch(e){toast('Benchmark failed',e.message,'error')}finally{bench.disabled=false}};
      const forget=card.querySelector('[data-forget]'); if(forget)forget.onclick=async()=>{try{await api('/api/cluster/node/forget',{method:'POST',body:{node_id:id}});renderCluster(view)}catch(e){toast('Could not forget Worker',e.message,'error')}};
    });
    if($('#clusterProfileSave'))$('#clusterProfileSave').onclick=async()=>{const name=$('#clusterProfileName').value.trim();if(!name)return;try{await api('/api/cluster/profile/save',{method:'POST',body:{name}});renderCluster(view)}catch(e){toast('Profile save failed',e.message,'error')}};
    $$('[data-profile]').forEach(b=>b.onclick=async()=>{try{await api('/api/cluster/profile/load',{method:'POST',body:{name:b.dataset.profile}});toast('Cluster profile loaded',b.dataset.profile,'info');renderCluster(view)}catch(e){toast('Profile load failed',e.message,'error')}});
  }

  async function renderCalendar(view){
    view.className='view calendar-route';
    view.innerHTML=`<div class="page calendar-page"><div class="calendar-loading">${icon('calendar')}<strong>در حال آماده‌سازی تقویم…</strong></div></div>`;
    try{
      const nowData=await api('/api/calendar/now');
      const now=nowData.now||{};
      if(!App.calendarCursor){const [y,m]=(now.jalali||'1405-01-01').split('-').map(Number);App.calendarCursor={year:y,month:m};}
      const {year,month}=App.calendarCursor;
      const data=await api(`/api/calendar/month?year=${year}&month=${month}`);
      if(App.route!=='calendar')return;
      const cal=data.month||{},days=cal.days||[];
      const today=String(data.now?.jalali||'');
      const offset=((Number(cal.first_weekday||0)-5)+7)%7;
      const headers=['شنبه','یکشنبه','دوشنبه','سه‌شنبه','چهارشنبه','پنجشنبه','جمعه'];
      const blanks=Array.from({length:offset},()=>'<div class="calendar-day blank"></div>').join('');
      const dayHtml=days.map(d=>{
        const isToday=d.jalali===today,holiday=!!d.holiday||!!d.weekend;
        const events=(d.events||[]).slice(0,3);
        return `<button class="calendar-day ${isToday?'today':''} ${holiday?'holiday':''}" data-cal-day="${escapeHtml(d.jalali)}" data-greg-day="${escapeHtml(d.gregorian)}">
          <div class="cal-day-top"><strong>${d.day}</strong><span>${escapeHtml(d.weekday_fa||'')}</span></div>
          ${d.holiday?`<small class="holiday-name">${escapeHtml(d.holiday)}</small>`:''}
          <div class="day-events">${events.map(e=>`<span title="${escapeHtml(e.title||'')}">${escapeHtml(shortName(e.title||'رویداد',22))}</span>`).join('')}${(d.events||[]).length>3?`<em>+${d.events.length-3}</em>`:''}</div>
        </button>`;
      }).join('');
      const upcoming=days.flatMap(d=>(d.events||[]).map(e=>({...e,jalali:d.jalali,weekday:d.weekday_fa}))).filter(e=>String(e.start||'')>=String(new Date().toISOString().slice(0,16))).slice(0,10);
      const eventCount=days.reduce((n,d)=>n+(d.events||[]).length,0);
      view.innerHTML=`<div class="page calendar-page calendar-v2" dir="rtl">
        <header class="calendar-topbar"><div class="calendar-title-block"><span class="mini-kicker">تقویم هوشمند</span><div class="calendar-title-line"><h2>${escapeHtml(cal.month_name||'')} <b>${year}</b></h2><span>${eventCount} رویداد این ماه</span></div><p>برنامه‌ات را اینجا ببین یا مستقیم به Agent بگو چه چیزی ثبت، جابه‌جا یا پیدا کند.</p></div><div class="calendar-top-actions"><button id="calToday" class="secondary-button">امروز</button><button id="calAdd" class="primary-button">${icon('plus')} رویداد جدید</button></div></header>
        <div class="calendar-glance"><div class="calendar-today-card"><span class="today-orb">${escapeHtml(String((now.jalali||'').split('-')[2]||''))}</span><div><small>امروز · ${escapeHtml(now.weekday_fa||'')}</small><strong>${escapeHtml(now.jalali_text||now.jalali||'')}</strong></div><b>${escapeHtml((now.time||'').slice(0,5))}</b></div><div class="calendar-agent-prompt">${icon('spark')}<div><strong>با زبان طبیعی برنامه‌ریزی کن</strong><span>مثلاً: «فردا ساعت ۱۰ جلسه بذار» یا «اولین زمان خالی دو ساعته‌ام را پیدا کن»</span></div></div></div>
        <div class="calendar-layout">
          <section class="calendar-card calendar-main-card">
            <div class="calendar-toolbar"><div class="calendar-nav"><button id="calPrev" class="icon-button ghost" aria-label="ماه قبل">${icon('chevron')}</button><button id="calNext" class="icon-button ghost" aria-label="ماه بعد">${icon('chevron')}</button></div><div class="calendar-month-title">${escapeHtml(cal.month_name||'')} ${year}</div><div class="calendar-toolbar-note">برای افزودن رویداد روی یک روز کلیک کن</div></div>
            <div class="calendar-weekdays">${headers.map((h,i)=>`<span class="${i===6?'holiday':''}">${h}</span>`).join('')}</div>
            <div class="calendar-grid">${blanks}${dayHtml}</div>
          </section>
          <aside class="calendar-side"><div class="calendar-side-card calendar-agenda"><div class="calendar-side-title"><div><span class="mini-kicker">AGENDA</span><h3>برنامه‌های پیشِ رو</h3></div><span class="agenda-count">${upcoming.length}</span></div><div class="upcoming-list">${upcoming.length?upcoming.map(e=>`<div class="upcoming-item"><span class="upcoming-time">${escapeHtml(String(e.start||'').slice(11,16)||'—')}</span><div><strong>${escapeHtml(e.title||'رویداد')}</strong><small>${escapeHtml(e.weekday||'')} · ${escapeHtml(e.jalali||'')}</small></div></div>`).join(''):`<div class="calendar-empty-agenda">${icon('calendar')}<strong>برنامه‌ای نزدیک نیست</strong><span>روی هر روز کلیک کن تا یک رویداد بسازی.</span></div>`}</div></div>
          <div class="calendar-side-card calendar-agent-card"><div class="calendar-agent-icon">${icon('spark')}</div><div><strong>Agent به تقویم دسترسی دارد</strong><p>خواندن زمان، دیدن رویدادها، ثبت و تغییر برنامه‌ها از Skillهای عمومی تقویم انجام می‌شود؛ لازم نیست نام Skill را بدانی.</p></div></div></aside>
        </div>
      </div>`;
      const shift=delta=>{let y=App.calendarCursor.year,m=App.calendarCursor.month+delta;if(m<1){m=12;y--}if(m>12){m=1;y++}App.calendarCursor={year:y,month:m};renderCalendar(view)};
      $('#calPrev').onclick=()=>shift(-1);$('#calNext').onclick=()=>shift(1);
      $('#calToday').onclick=()=>{const [y,m]=(now.jalali||'1405-01').split('-').map(Number);App.calendarCursor={year:y,month:m};renderCalendar(view)};
      $('#calAdd').onclick=()=>openCalendarEventModal(today||`${year}-${String(month).padStart(2,'0')}-01`);
      $$('[data-cal-day]').forEach(b=>b.onclick=()=>openCalendarEventModal(b.dataset.calDay,b.dataset.gregDay));
    }catch(e){view.innerHTML=`<div class="page"><div class="error-card"><strong>تقویم باز نشد</strong><p>${escapeHtml(e.message)}</p></div></div>`;}
  }

  function openCalendarEventModal(jalali='',gregorian=''){
    const root=$('#modalRoot');
    const today=new Date().toISOString().slice(0,10);
    const g=gregorian||today;
    root.innerHTML=`<div class="modal-backdrop calendar-modal"><div class="modal-card" dir="rtl"><div class="modal-head"><div><div class="eyebrow">CALENDAR</div><h3>رویداد جدید</h3><p>${escapeHtml(jalali||'')}</p></div><button id="calModalClose" class="icon-button ghost">${icon('x')}</button></div><div class="modal-body form-grid"><label class="field span-2"><span>عنوان</span><input id="calTitle" placeholder="مثلاً جلسه با شرکت" autofocus></label><label class="field"><span>تاریخ میلادی</span><input id="calDate" type="date" value="${escapeHtml(g)}"></label><label class="field"><span>ساعت</span><input id="calTime" type="time" value="10:00"></label><label class="field"><span>مدت</span><select id="calDuration"><option value="30">۳۰ دقیقه</option><option value="60" selected>۱ ساعت</option><option value="90">۹۰ دقیقه</option><option value="120">۲ ساعت</option></select></label><label class="field"><span>یادآوری</span><select id="calReminder"><option value="">بدون یادآوری</option><option value="10">۱۰ دقیقه قبل</option><option value="30" selected>۳۰ دقیقه قبل</option><option value="60">۱ ساعت قبل</option><option value="1440">۱ روز قبل</option></select></label><label class="field span-2"><span>توضیح</span><textarea id="calNotes" rows="3" placeholder="اختیاری"></textarea></label></div><div class="modal-actions"><button id="calCancel" class="secondary-button">انصراف</button><button id="calSave" class="primary-button">ثبت رویداد</button></div></div></div>`;
    const close=()=>root.innerHTML='';$('#calModalClose').onclick=close;$('#calCancel').onclick=close;
    $('#calSave').onclick=async()=>{const title=$('#calTitle').value.trim();if(!title){toast('عنوان لازم است','','error');return}const d=$('#calDate').value,t=$('#calTime').value||'10:00',dur=Number($('#calDuration').value||60),start=new Date(`${d}T${t}:00`),end=new Date(start.getTime()+dur*60000),rem=$('#calReminder').value;try{await api('/api/calendar',{method:'POST',body:{operation:'create',title,start:start.toISOString(),end:end.toISOString(),notes:$('#calNotes').value,reminders:rem?[Number(rem)]:[]}});close();toast('رویداد ثبت شد',title);renderCalendar($('#view'));}catch(e){toast('ثبت نشد',e.message,'error')}};
  }

  async function renderFiles(view){
    view.className='view files-route';
    view.innerHTML=`<div class="page files-page"><div class="calendar-loading">${icon('files')}<strong>در حال خواندن فایل‌ها…</strong></div></div>`;
    try{
      const q=App.filesSearch.trim();const data=await api(q?`/api/workspace/files?q=${encodeURIComponent(q)}`:`/api/workspace/files?folder=${encodeURIComponent(App.filesFolder||'')}`);
      if(App.route!=='files')return;
      const items=q?(data.matches||[]):(data.items||[]);
      const parts=(App.filesFolder||'').split('/').filter(Boolean);
      const crumbs=[`<button data-folder="">فایل‌های من</button>`];let acc='';for(const part of parts){acc=acc?`${acc}/${part}`:part;crumbs.push(`<span>/</span><button data-folder="${escapeHtml(acc)}">${escapeHtml(part)}</button>`)}
      view.innerHTML=`<div class="page files-page" dir="rtl">
        <div class="files-hero"><div><div class="eyebrow">SMART FILE MANAGER</div><h2>مدیریت اسناد و فایل‌ها</h2><p>Agent ابتدا Metadata را می‌بیند. محتوا فقط وقتی خوانده می‌شود که سؤال واقعاً به فهمیدن فایل نیاز داشته باشد.</p></div><div class="files-hero-badge">${icon('folder')}<span><strong>${items.length}</strong><small>${q?'نتیجه':'مورد در این پوشه'}</small></span></div></div>
        <section class="files-card"><div class="files-toolbar"><div class="file-breadcrumbs">${crumbs.join('')}</div><div class="files-actions"><div class="search compact">${icon('search')}<input id="fileSearch" value="${escapeHtml(App.filesSearch)}" placeholder="جستجو در نام، مسیر و برچسب…"></div><button id="newFolderBtn" class="secondary-button">${icon('folder')} پوشه جدید</button><button id="uploadWorkspaceBtn" class="primary-button">${icon('plus')} افزودن فایل</button><input id="workspaceFileInput" type="file" multiple hidden></div></div>
        <div class="files-table"><div class="files-head"><span>نام</span><span>نوع</span><span>حجم</span><span>آخرین تغییر</span><span></span></div><div class="files-body">${items.length?items.map(item=>fileManagerRow(item,q)).join(''):'<div class="files-empty"><div>'+icon('folder')+'</div><strong>اینجا خالی است</strong><span>یک پوشه بساز یا فایل اضافه کن.</span></div>'}</div></div></section>
        <div class="files-agent-note">${icon('spark')}<div><strong>مدل مجبور نیست فایل را بخواند.</strong><p>برای «این مدرک را ببر داخل مدارک شرکت» فقط عملیات فایل انجام می‌شود. برای «این فایل درباره چیست؟» همان لحظه خواندن محتوا فعال می‌شود.</p></div></div>
      </div>`;
      $$('[data-folder]').forEach(b=>b.onclick=()=>{App.filesFolder=b.dataset.folder||'';App.filesSearch='';renderFiles(view)});
      $$('[data-open-folder]').forEach(b=>b.onclick=()=>{App.filesFolder=b.dataset.openFolder||'';App.filesSearch='';renderFiles(view)});
      $$('[data-trash-file]').forEach(b=>b.onclick=async()=>{const ok=await confirmModal('انتقال به سطل زباله','این فایل به سطل زباله منتقل شود؟','انتقال',true);if(!ok)return;try{await api('/api/workspace/files',{method:'POST',body:{operation:'trash',id:b.dataset.trashFile}});toast('فایل منتقل شد');renderFiles(view)}catch(e){toast('عملیات ناموفق',e.message,'error')}});
      $$('[data-download-file]').forEach(b=>b.onclick=()=>{location.href=`/api/workspace/download?id=${encodeURIComponent(b.dataset.downloadFile)}`});
      $('#newFolderBtn').onclick=async()=>{const name=prompt('نام پوشه جدید:');if(!name)return;try{await api('/api/workspace/files',{method:'POST',body:{operation:'mkdir',folder:App.filesFolder,name}});renderFiles(view)}catch(e){toast('ساخت پوشه ناموفق',e.message,'error')}};
      const uploadBtn=$('#uploadWorkspaceBtn'),input=$('#workspaceFileInput');uploadBtn.onclick=()=>input.click();input.onchange=()=>uploadWorkspaceFiles(input.files,view);
      let st=null;$('#fileSearch').oninput=e=>{clearTimeout(st);App.filesSearch=e.target.value;st=setTimeout(()=>renderFiles(view),260)};
    }catch(e){view.innerHTML=`<div class="page"><div class="error-card"><strong>فایل منیجر باز نشد</strong><p>${escapeHtml(e.message)}</p></div></div>`;}
  }

  function fileManagerRow(item,searchMode=false){
    if(item.kind==='folder')return `<button class="file-row folder-row" data-open-folder="${escapeHtml(item.path||'')}"><span class="file-name-cell"><i>${icon('folder')}</i><b>${escapeHtml(item.name||'پوشه')}</b></span><span>پوشه</span><span>—</span><span>—</span><span>${icon('chevron')}</span></button>`;
    const name=item.name||'file',ext=(name.split('.').pop()||'FILE').toUpperCase();
    return `<div class="file-row"><span class="file-name-cell"><i class="file-ext">${escapeHtml(ext.slice(0,4))}</i><span><b>${escapeHtml(name)}</b><small>${escapeHtml(item.path||'')}</small></span></span><span>${escapeHtml(item.mime||'فایل')}</span><span>${formatBytes(item.size||0)}</span><span>${escapeHtml(String(item.updated_at||'').replace('T',' ').slice(0,16))}</span><span class="file-row-actions"><button class="icon-button ghost" data-download-file="${escapeHtml(item.id||'')}" title="دانلود">${icon('download')}</button><button class="icon-button ghost danger" data-trash-file="${escapeHtml(item.id||'')}" title="سطل زباله">${icon('trash')}</button></span></div>`;
  }

  function readAsDataURL(file){return new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result||''));r.onerror=()=>reject(r.error||new Error('read failed'));r.readAsDataURL(file)})}
  async function uploadWorkspaceFiles(files,view){
    for(const file of [...(files||[])]){if(file.size>45*1024*1024){toast('فایل خیلی بزرگ است',`${file.name} بیشتر از ۴۵ مگابایت است.`,'error');continue}try{toast('در حال افزودن فایل',file.name,'info',1800);const data_url=await readAsDataURL(file);await api('/api/workspace/upload',{method:'POST',body:{name:file.name,folder:App.filesFolder,data_url}})}catch(e){toast('آپلود ناموفق',`${file.name}: ${e.message}`,'error',5000)}}renderFiles(view);
  }

  function renderHome(view){
    const s=App.state||{}, m=s.active_model, a=s.assessment, live=s.live||{}, rt=s.runtime||{}, ss=s.server||{}, cpu=cpuThreadPrefs(), perf=s.performance||ss.performance||{};
    const heroTitle=m ? `Ready for ${shortName(m.name,36)}` : 'Local AI that tunes itself to this machine.';
    const heroDesc=m ? `${m.quantization||'GGUF'} · ${m.architecture||'unknown architecture'} · ${formatBytesGB(m.size_gb)}. Smart Core handles memory, context and CPU policy while keeping the technical controls available when you want them.` : 'Choose a GGUF. LlamaForge inspects the model, validates the runtime and builds a CPU-first launch plan without uploading anything.';
    const procCpu=Number(live.process_cpu_percent||0), sysCpu=Number(live.cpu_percent||0);
    const perfState=perf.state||'idle';
    const perfTitle={"cpu-saturated":"CPU saturated","paging-bound":"SSD/page-cache bound","memory-bound":"Memory-bandwidth bound","not-cpu-bound":"Not CPU-bound","balanced":"Balanced workload","loading":"Loading model","idle":"Waiting for model"}[perfState]||'Performance status';
    view.innerHTML=`<div class="page home-page">
      <section class="hero premium-hero"><div class="hero-grid"><div><div class="eyebrow">LOCAL CONTROL PLANE</div><h2>${escapeHtml(heroTitle)}</h2><p>${escapeHtml(heroDesc)}</p><div class="hero-actions">
      <button id="homeRun" class="primary-button" ${(!m||ss.running&&!ss.ready)?'disabled':''}>${runButtonLabel()}</button>
      <button id="homeChoose" class="secondary-button">${icon('folder')} ${m?'Change model':'Choose GGUF'}</button>
      ${ss.running?`<button id="homeUnload" class="secondary-button">${icon('stop')} Unload model</button>`:''}</div></div>
      <div class="model-orb"><div class="meta-kicker">ACTIVE MODEL</div><strong>${escapeHtml(m?.name||'No model selected')}</strong><small>${m?`${escapeHtml(m.quantization)} · ${escapeHtml(m.size_label||m.architecture||'GGUF')}`:'Pick a local file — no upload occurs'}</small></div></div></section>
      <div class="metric-grid">
        <div class="metric-card"><div class="metric-label">llama.cpp CPU</div><div><div class="metric-value" data-live="process-cpu">${procCpu.toFixed(0)}%</div><div class="metric-sub" data-live="system-cpu">System ${sysCpu.toFixed(0)}%</div></div></div>
        <div class="metric-card"><div class="metric-label">Memory</div><div><div class="metric-value" data-live="ram-used">${(live.ram_used_gb||0).toFixed(1)} / ${(live.ram_total_gb||0).toFixed(1)} GB</div><div class="metric-sub" data-live="ram-free">${(live.ram_available_gb||0).toFixed(1)} GB available</div></div></div>
        ${metric('Runtime',rt.installed?'Ready':'Missing',rt.build_number?`llama.cpp b${rt.build_number}`:'CPU runtime')}
        ${metric('Server',ss.ready?'Ready':ss.running?'Loading':'Stopped',perf.generation_tps?`${perf.generation_tps} tok/s last decode`:(ss.ready&&ss.uptime?`${formatDuration(ss.uptime)} uptime`:'127.0.0.1 only'))}
      </div>
      <section class="section"><div class="performance-strip ${escapeHtml(perfState)}"><div class="performance-icon">${icon(perfState==='paging-bound'?'harddrive':perfState==='cpu-saturated'?'cpu':'tune')}</div><div><span class="mini-kicker">LIVE BOTTLENECK</span><strong id="perfTitle">${escapeHtml(perfTitle)}</strong><p id="perfDetail">${escapeHtml(perf.detail||'LlamaForge will explain what limits utilization after the model starts.')}</p></div><div class="performance-numbers"><span><b id="perfProcessCpu">${procCpu.toFixed(0)}%</b> llama.cpp</span><span><b id="perfTarget">${perf.target_percent||cpu.target_percent}%</b> budget</span></div></div></section>
      <section class="section cpu-power-section"><div class="section-head"><div><h3>CPU policy</h3><p>Set a normal CPU budget, or deliberately bias toward Task Manager saturation.</p></div><span class="cpu-live-pill" data-live="cpu-pill">llama.cpp ${procCpu.toFixed(0)}%</span></div>
        <div class="cpu-power-card"><div class="cpu-power-main"><div><span class="mini-kicker">CPU BUDGET</span><div class="cpu-power-value"><strong id="homeCpuTargetValue">${cpu.target_percent}%</strong><span id="homeCpuThreadSummary">${cpuPlanSummary(cpu)}</span></div></div><div class="cpu-mode-actions"><button id="fullThrottle" class="secondary-button">100% budget</button><button id="forceSaturation" class="${cpu.mode==='saturate'?'primary-button':'secondary-button'}">${icon('cpu')} Saturate CPU</button></div></div>
        <input id="homeCpuTarget" class="cpu-target-range" type="range" min="25" max="100" step="5" value="${cpu.target_percent}" aria-label="CPU target percent">
        <div class="cpu-target-marks"><span>25%</span><span>50%</span><span>75%</span><span>100%</span></div>
        <div class="cpu-power-note">${icon('info')}<span><b>100% budget</b> uses every logical CPU once. <b>Saturate CPU</b> additionally oversubscribes workers, enables aggressive polling, high process priority and all-core affinity. It can increase Task Manager usage, but memory-bandwidth-bound models may still stay below 100% and may even run slower.</span></div></div>
      </section>
      <section class="section"><div class="section-head"><div><h3>Smart launch</h3><p>Memory/context safeguards remain active even when CPU saturation is selected.</p></div><button id="openOptimize" class="text-button">See launch plans ${icon('chevron')}</button></div>
      ${a?`<div class="panel"><div class="smart-row"><div class="score-ring" style="--score:${a.score}"><span>${a.score}</span></div><div class="smart-copy"><strong>${escapeHtml(a.fit)} fit · ${escapeHtml(a.recommended_profile)}</strong><p>${Number(a.plan.ctx_size).toLocaleString()} context · ${escapeHtml(cpuPlanSummary(cpu))} · ${escapeHtml(a.memory_status)}</p><div class="tag-row"><span class="tag">${cpu.threads}/${cpu.threads_batch} workers</span><span class="tag">${Number(a.plan.ctx_size).toLocaleString()} ctx</span><span class="tag">${a.memory_status}</span></div></div><button id="smartRun" class="primary-button" ${ss.running?'disabled':''}>${icon('play')} Run</button></div></div>`:emptyMini('Choose a model to get a launch recommendation.')}</section>
      ${ss.error?`<section class="section"><div class="panel panel-pad error-panel"><div class="error-row"><span>${icon('alert')}</span><div><strong>Last launch needs attention</strong><p>${escapeHtml(ss.error)}</p></div></div></div></section>`:''}
    </div>`;
    $('#homeChoose').onclick=chooseModel; if($('#homeUnload')) $('#homeUnload').onclick=unloadModel;
    $('#openOptimize').onclick=()=>setRoute('optimize');
    const cpuRange=$('#homeCpuTarget'),cpuValue=$('#homeCpuTargetValue'),cpuSummary=$('#homeCpuThreadSummary');
    const applyCpuTarget=(value)=>{const target=Math.max(25,Math.min(100,Number(value)||100));cpuRange.value=target;cpuValue.textContent=`${target}%`;const t=App.state?.cpu_threading||{},hard=Math.max(1,Number(t.hard_max||1)),n=Math.max(1,Math.min(hard,Math.round(hard*target/100)));saveCpuThreadPrefs('target',n,n,target);cpuSummary.textContent=cpuPlanSummary(cpuThreadPrefs());$('#forceSaturation')?.classList.remove('primary-button');$('#forceSaturation')?.classList.add('secondary-button');};
    if(cpuRange)cpuRange.oninput=()=>applyCpuTarget(cpuRange.value);
    if($('#fullThrottle'))$('#fullThrottle').onclick=()=>{applyCpuTarget(100);toast('100% CPU budget selected',cpuTargetSummary(100),'info',2200)};
    if($('#forceSaturation'))$('#forceSaturation').onclick=()=>{const t=App.state?.cpu_threading||{},hard=Math.max(1,Number(t.hard_max||1)),aggr=Math.max(hard,Number(t.aggressive_max||hard*2)),n=Math.max(hard,Math.min(aggr,Math.round(hard*1.5)));saveCpuThreadPrefs('saturate',n,n,100);cpuRange.value=100;cpuValue.textContent='100%';cpuSummary.textContent=cpuPlanSummary(cpuThreadPrefs());$('#forceSaturation').className='primary-button';toast('CPU saturation armed',`${n} llama.cpp workers on ${hard} logical CPUs. Applies on next load.`,'info',3400)};
    const run=()=> ss.ready ? setRoute('chat') : startOptimized();
    if($('#homeRun')) $('#homeRun').onclick=run; if($('#smartRun')) $('#smartRun').onclick=run;
  }
  function metric(label,value,sub){ return `<div class="metric-card"><div class="metric-label">${escapeHtml(label)}</div><div><div class="metric-value">${escapeHtml(value)}</div><div class="metric-sub">${escapeHtml(sub)}</div></div></div>`; }
  function emptyMini(text){ return `<div class="empty-state"><strong>Nothing to optimize yet</strong>${escapeHtml(text)}</div>`; }
  function formatDuration(sec){ if(sec<60)return `${sec}s`; if(sec<3600)return `${Math.floor(sec/60)}m`; return `${Math.floor(sec/3600)}h ${Math.floor(sec%3600/60)}m`; }

  async function chooseModel(){
    try{
      const p=await api('/api/dialog/model'); if(!p.path) return;
      const result=await api('/api/model/select',{method:'POST',body:{path:p.path}});
      await refreshState(true);
      toast('Loading model',result.model?.name||'Local model','info');
      await startOptimized();
    }catch(e){toast('Could not open model',e.message,'error',5500)}
  }
  function cpuThreadPrefs(){
    const t=App.state?.cpu_threading||{};
    const hard=Math.max(1,Number(t.hard_max||1));
    const aggressive=Math.max(hard,Number(t.aggressive_max||hard*2));
    let mode=localStorage.getItem('lf.cpuThreadMode')||'target';
    if(!['auto','performance','target','manual','saturate'].includes(mode)) mode='target';
    const target=Math.max(Number(t.target_min||25),Math.min(Number(t.target_max||100),Number(localStorage.getItem('lf.cpuTargetPercent')||100)));
    const targetThreads=Math.max(1,Math.min(hard,Math.round(hard*target/100)));
    const defaultGen=Number(localStorage.getItem('lf.cpuThreads')||t.recommended_generation||1);
    const defaultPrompt=Number(localStorage.getItem('lf.cpuPromptThreads')||t.recommended_prompt||defaultGen);
    const satThreads=Math.max(hard,Math.min(aggressive,Math.round(hard*1.5)));
    const gen=mode==='target'?targetThreads:mode==='saturate'?satThreads:Math.max(1,Math.min(aggressive,defaultGen));
    const prompt=mode==='target'?targetThreads:mode==='saturate'?satThreads:Math.max(1,Math.min(aggressive,defaultPrompt));
    return {mode,threads:gen,threads_batch:prompt,target_percent:mode==='saturate'?100:target,saturation:mode==='saturate'};
  }
  function saveCpuThreadPrefs(mode,threads,prompt,target){
    localStorage.setItem('lf.cpuThreadMode',mode);
    localStorage.setItem('lf.cpuThreads',String(threads));
    localStorage.setItem('lf.cpuPromptThreads',String(prompt));
    if(target!==undefined&&target!==null)localStorage.setItem('lf.cpuTargetPercent',String(target));
  }
  function cpuTargetSummary(target){
    const t=App.state?.cpu_threading||{},hard=Math.max(1,Number(t.hard_max||1));
    const threads=Math.max(1,Math.min(hard,Math.round(hard*Number(target||100)/100)));
    return `${threads}/${hard} logical CPUs`;
  }
  function cpuPlanSummary(prefs=cpuThreadPrefs()){
    const hard=Math.max(1,Number(App.state?.cpu_threading?.hard_max||1));
    if(prefs.mode==='saturate') return `${prefs.threads} workers on ${hard} logical CPUs · saturation bias`;
    if(prefs.mode==='target') return `${prefs.target_percent}% budget · ${prefs.threads}/${hard} logical CPUs`;
    return `${prefs.threads}/${prefs.threads_batch} generation/prompt workers`;
  }
  function acceleratorPrefs(){
    const s=App.state||{}, c=s.config||{}, hasGpu=Array.isArray(s.hardware?.gpus)&&s.hardware.gpus.length>0;
    let mode=localStorage.getItem('lf.acceleratorMode')||c.accelerator_mode||'adaptive';
    if(!['adaptive','cpu','gpu','hybrid','max_both'].includes(mode))mode='adaptive';
    if(!hasGpu)mode='cpu';
    const rawPercent=Math.max(5,Math.min(95,Number(localStorage.getItem('lf.gpuLayerPercent')||c.gpu_layer_percent||35)));
    const percent=mode==='max_both'?Math.max(5,Math.min(30,rawPercent===35?10:rawPercent)):rawPercent;
    return {mode,enabled:mode!=='cpu'&&hasGpu,percent,hasGpu,requiresGpu:mode!=='cpu'&&hasGpu};
  }
  function saveAcceleratorPrefs(mode,percent){
    const normalized=['adaptive','cpu','gpu','hybrid','max_both'].includes(mode)?mode:'adaptive';
    const raw=Math.max(5,Math.min(95,Number(percent)||35));
    const pct=normalized==='max_both'?Math.max(5,Math.min(30,raw===35?10:raw)):raw;
    localStorage.setItem('lf.acceleratorMode',normalized);
    localStorage.setItem('lf.gpuLayerPercent',String(pct));
    if(App.state?.config){App.state.config.accelerator_mode=normalized;App.state.config.gpu_layer_percent=pct;}
  }
  function acceleratorLabel(mode){return mode==='adaptive'?'Adaptive AutoTune':mode==='cpu'?'CPU only':mode==='gpu'?'GPU max offload':mode==='max_both'?'CPU + GPU Max Both':'CPU + GPU hybrid'}
  function acceleratorDetail(acc=acceleratorPrefs()){
    if(acc.mode==='adaptive'){
      const t=App.state?.autotune||null;
      if(t)return `Measured for this GGUF · ${Number(t.generation_tps||0).toFixed(2)} tok/s decode · ${Number(t.prompt_tps||0).toFixed(1)} tok/s prompt · ${Number(t.gpu_layer_percent||0)}% GPU`;
      return 'Adaptive heuristic · run AutoTune once to measure this exact GGUF on this machine';
    }
    if(acc.mode==='cpu')return 'CPU only · no Vulkan acceleration required';
    if(acc.mode==='gpu')return 'GPU max offload · all possible transformer layers on Vulkan';
    if(acc.mode==='max_both')return `Max Both · CPU-first with a small Vulkan slice (${acc.percent}% GPU layers)`;
    return `CPU + GPU hybrid · ${acc.percent}% transformer layers on GPU`;
  }
  function runtimeCompatibleFor(acc,rt={}){
    if(!rt.installed)return false;
    if(acc.mode==='cpu')return true;
    const backend=String(rt.backend||'').toLowerCase();
    return ['vulkan','cuda','rocm','sycl','openvino'].includes(backend)||String(rt.source||'').startsWith('custom');
  }
  function runtimeProgressModal(job={},acc=acceleratorPrefs()){
    const root=$('#modalRoot'); if(!root)return;
    let shell=$('#runtimeInstallModal',root);
    if(!shell){
      root.innerHTML=`<div class="modal-backdrop runtime-install-backdrop"><div class="modal premium-modal runtime-install-modal" id="runtimeInstallModal"><div class="modal-kicker">LOCAL ENGINE</div><h3 id="runtimeInstallTitle">Preparing llama.cpp</h3><p id="runtimeInstallMode"></p><div class="runtime-progress-big"><div id="runtimeInstallBar"></div></div><div class="runtime-progress-line"><strong id="runtimeInstallPct">0%</strong><span id="runtimeInstallBytes">Resolving package…</span></div><div class="runtime-progress-meta"><span id="runtimeInstallSpeed">—</span><span id="runtimeInstallEta">—</span></div><div class="runtime-install-message" id="runtimeInstallMessage">Finding a compatible runtime…</div><div class="inline-actions"><button id="runtimeInstallCancel" class="secondary-button">Cancel download</button></div></div></div>`;
      shell=$('#runtimeInstallModal',root);
      const cancel=$('#runtimeInstallCancel',root);
      if(cancel)cancel.onclick=async()=>{cancel.disabled=true;cancel.textContent='Cancelling…';try{await api('/api/runtime/cancel',{method:'POST',body:{}})}catch(e){toast('Could not cancel runtime download',e.message,'error')}};
    }
    const done=Number(job.done||0),total=Number(job.total||0),frac=total>0?Math.max(0,Math.min(1,done/total)):Math.max(0,Math.min(1,Number(job.progress||0))),pct=Math.round(frac*100);
    const set=(id,text)=>{const el=$(id,root);if(el)el.textContent=text};
    const bar=$('#runtimeInstallBar',root);if(bar)bar.style.width=`${Math.max(job.stage==='resolve'?4:0,pct)}%`;
    set('#runtimeInstallTitle',job.state==='cancelling'?'Stopping download…':job.stage==='extract'?'Installing runtime…':`Downloading ${job.requested_backend||((acc.mode==='cpu')?'CPU':'Vulkan')} runtime`);
    set('#runtimeInstallMode',`${acceleratorLabel(acc.mode)} · the model will load automatically when this finishes.`);
    set('#runtimeInstallPct',total>0?`${pct}%`:job.stage==='extract'?'100%':'…');
    set('#runtimeInstallBytes',total>0?`${formatBytes(done)} / ${formatBytes(total)}`:'Package metadata');
    set('#runtimeInstallSpeed',Number(job.bytes_per_sec||0)>0?`${formatBytes(Number(job.bytes_per_sec))}/s`:'Speed: —');
    set('#runtimeInstallEta',job.eta_seconds!=null?`ETA ${formatEta(job.eta_seconds)}`:'ETA —');
    set('#runtimeInstallMessage',job.message||'Preparing runtime…');
    const cancel=$('#runtimeInstallCancel',root);if(cancel)cancel.disabled=job.state==='cancelling';
  }
  function closeRuntimeProgressModal(){const root=$('#modalRoot');if(root&&$('#runtimeInstallModal',root))root.innerHTML=''}

  async function startOptimized(){
    const s=App.state; if(!s?.active_model){return chooseModel()}
    try{await ensureRuntimeInstalled()}catch(e){toast('Runtime installation failed',e.message,'error',8000);return}
    try{
      const a=s.assessment, tp=cpuThreadPrefs(), c=App.state?.config||{}, acc=acceleratorPrefs();
      await api('/api/settings',{method:'POST',body:{accelerator_mode:acc.mode,gpu_layer_percent:acc.percent}});
      await api('/api/server/start',{method:'POST',body:{model_path:s.active_model.path,profile:a?.recommended_profile||'Balanced',ctx:Number(c.default_context_size||a?.recommended_ctx||4096),accelerator_mode:acc.mode,cpu_only:acc.mode==='cpu',gpu_layer_percent:acc.percent,thread_mode:tp.mode,threads:tp.threads,threads_batch:tp.threads_batch,cpu_target_percent:tp.target_percent,cpu_saturation:tp.saturation,memory_mode:c.model_memory_mode||'hybrid'}});
      const cpuMsg=acc.mode==='max_both'?`Max Both · ${acc.percent}% GPU layers · all logical CPU workers`:acc.mode==='hybrid'?`CPU + GPU hybrid · ${acc.percent}% GPU layers · CPU budget ${tp.target_percent}%`:acc.mode==='gpu'?`GPU max offload · CPU remains available for orchestration`:(tp.mode==='saturate'?`CPU-only saturation · ${tp.threads} workers`:tp.mode==='target'?`CPU only · target ${tp.target_percent}%`:'CPU-only launch');
      toast('Loading local model',cpuMsg,'info'); setRoute('chat');
    }catch(e){toast('Model could not start',e.message,'error',6500)}
  }

  async function ensureRuntimeInstalled(forceInstall=false){
    let acc=acceleratorPrefs(), rt=App.state?.runtime||{};
    if(runtimeCompatibleFor(acc,rt)&&!forceInstall)return true;
    let job=App.state?.job||{};
    if(!(job.state==='running'&&job.kind==='runtime-install')&&!(job.state==='cancelling'&&job.kind==='runtime-install')){
      await api('/api/runtime/install',{method:'POST',body:{prefer_vulkan:acc.mode!=='cpu'}});
      toast('Preparing local engine',acc.mode==='cpu'?'Installing the CPU llama.cpp runtime.':'Installing the Vulkan llama.cpp runtime for GPU acceleration.','info',4200);
      const first=await api('/api/state');App.state=first;job=first.job||{};
    }
    runtimeProgressModal(job,acc);
    const started=Date.now();
    try{
      while(Date.now()-started<30*60*1000){
        await sleep(450);
        const next=await api('/api/state');App.state=next; job=next.job||{}; acc=acceleratorPrefs(); rt=next.runtime||{};
        runtimeProgressModal(job,acc);
        if(runtimeCompatibleFor(acc,rt)&&job.state!=='running'&&job.state!=='cancelling'){await refreshState(true);closeRuntimeProgressModal();return true}
        if(job.state==='done'){
          if(runtimeCompatibleFor(acc,rt)){await refreshState(true);closeRuntimeProgressModal();return true}
          if(acc.mode!=='cpu')throw new Error('The installed runtime does not expose a GPU backend. Update the Intel/AMD graphics driver or switch Compute Engine to CPU only.');
          await refreshState(true);closeRuntimeProgressModal();return true;
        }
        if(job.state==='error')throw new Error(job.error||job.message||'Runtime install failed');
        if(job.state==='cancelled')throw new Error('Runtime installation was cancelled');
      }
      throw new Error('Runtime installation timed out');
    }catch(e){closeRuntimeProgressModal();throw e}
  }

  function renderModels(view){
    const s=App.state||{}, models=s.model_library||s.models||[], managed=s.trainable_models||[], active=s.active_model?.path, brain=s.brain||{}, quick=(s.quick_models||[])[0]||{};
    const internal=managed.filter(m=>m.internal_dependency), sources=managed.filter(m=>!m.internal_dependency);
    const dl=['model-download','model-bundle-download'].includes(s.job?.kind)&&s.job?.state==='running'?s.job:null;
    const quickBusy=s.job?.kind==='model-bundle-download'&&s.job?.state==='running';
    const quickStatus=quick.installed?'Chat + learning ready':quick.chat_ready?'Chat ready · learning setup continues in background':'Downloads chat first, then prepares learning';
    view.innerHTML=`<div class="page simple-models-page">${pageTitle('MODELS','One model. Chat with it. Teach the same model.','For the easiest path, install the lightweight Qwen2.5 model below. LlamaForge downloads a sub-1GB GGUF for chat and the exact matching trainable checkpoint for learning, then binds them as one model.')}
      <section class="quick-model-hero"><div class="quick-model-icon">${icon('brain')}</div><div class="quick-model-copy"><div class="eyebrow">RECOMMENDED · LIGHT · ONE CLICK</div><h3>${escapeHtml(quick.name||'Qwen2.5 1.5B Instruct')}</h3><p>One logical model: <strong>${escapeHtml(quick.chat_size||'~986 MB')}</strong> Q4_K_M GGUF for chat + the exact <code>${escapeHtml(quick.training_repo||'Qwen/Qwen2.5-1.5B-Instruct')}</code> checkpoint for Personal Brain learning. Chat is never blocked by the larger learning download.</p><div class="tag-row"><span class="tag">${escapeHtml(quick.size_label||'1.5B')}</span><span class="tag">Qwen2.5</span><span class="tag">Persian-friendly</span><span class="tag">Under 1 GB chat</span><span class="tag brain-ready-tag">Trainable</span><span class="tag">${escapeHtml(quickStatus)}</span></div></div><button id="quickModel" class="primary-button quick-model-button" ${quickBusy&&!quick.chat_ready?'disabled':''}>${icon(quick.chat_ready?'chat':'download')} ${quick.chat_ready?'Load & chat':quickBusy?'Downloading chat…':'Download light model'}</button></section>
      <div class="simple-model-actions"><button id="downloadModel" class="secondary-button">${icon('download')} Other models</button><button id="browseModel" class="secondary-button">${icon('folder')} Open local GGUF</button><button id="addFolder" class="secondary-button">${icon('plus')} Add folder</button><button id="rescanModels" class="secondary-button">${icon('refresh')} Rescan</button></div>
      <div class="learning-mode-banner ${brain.enabled?'on':'off'}"><div>${icon('brain')}<span><strong>Personal learning ${brain.enabled?'ON':'OFF'}</strong><small>${brain.enabled?'User facts and corrections are trained into this model; chat history is not used as hidden memory.':'Chat works normally, but messages will not change model weights.'}</small></span></div>${brain.enabled?'':`<button id="enableLearning" class="primary-button">Enable learning</button>`}</div>
      ${dl?`<div class="model-download-strip"><div><strong>${escapeHtml(dl.message||'Downloading model…')}</strong><small>${escapeHtml(dl.repo||'')} ${dl.total?`· ${formatBytes(dl.done||0)} / ${formatBytes(dl.total)}`:''}</small></div><div class="progress"><div style="width:${Math.max(3,Math.min(100,dl.progress!=null?Number(dl.progress||0)*100:(dl.total?Number(dl.done||0)/Number(dl.total)*100:8)))}%"></div></div></div>`:''}
      <div class="toolbar simple-search"><div class="search">${icon('search')}<input id="modelSearch" placeholder="Filter downloaded models…" autocomplete="off"></div></div>
      <section class="section"><div class="section-head"><div><h3>Ready to chat</h3><p>There is no second training-model choice. Learning files are implementation details for the selected model.</p></div><span class="tag">${models.length}</span></div><div id="modelGrid" class="model-grid">${models.length?models.map(m=>modelCard(m,active,!!s.server?.ready)).join(''):emptyMini('No model yet. Use “Download model” or “Open local GGUF”.')}</div></section>
      ${managed.length?`<details class="brain-advanced-disclosure model-files-disclosure"><summary><span>${icon('harddrive')}</span><div><strong>Managed learning files</strong><small>${managed.length} local Transformers/PEFT checkpoint${managed.length===1?'':'s'} · handled automatically</small></div>${icon('down')}</summary><div class="brain-advanced-body"><p class="muted-copy">These are support files used to train the model you selected above. They are not separate chat models and do not need to be switched manually.</p><div class="model-grid">${sources.map(managedLearningCard).join('')}${internal.map(managedLearningCard).join('')}</div></div></details>`:''}
    </div>`;
    $('#quickModel').onclick=async()=>{
      // The GGUF is sufficient for chat. Do not force the user to wait for the
      // larger Transformers checkpoint before opening the model. If learning
      // files are still missing, activateModelAndChat() starts chat first and
      // then asks the Brain setup worker to continue in the background.
      if(quick.chat_ready&&quick.chat_path){
        const card=[...$$('.model-card.unified-model')].find(c=>c.dataset.path===quick.chat_path);
        if(card)return activateModelAndChat(card);
        try{
          await api('/api/model/select',{method:'POST',body:{path:quick.chat_path}});await refreshState(true);
          await startOptimized();
          const brain=App.state?.brain||{};
          if(brain.enabled&&!brain.setup_ready&&!['running','cancelling'].includes(brain.job?.state)){
            api('/api/brain/autosetup',{method:'POST',body:{}}).catch(()=>{});
          }
          return;
        }catch(e){return toast('Could not load model',e.message,'error',8000)}
      }
      const ok=await confirmModal('Download Qwen2.5 1.5B · Chat + Learning',`LlamaForge will install one lightweight logical model:

• ~986 MB Q4_K_M GGUF for chat
• ~3.1 GB exact Qwen2.5 1.5B checkpoint for LoRA learning

You choose the model once. Chat can start as soon as the GGUF is ready; learning setup finishes in the background. Personal learning uses zero hidden chat-history context.`,'Download & use');
      if(!ok)return;
      try{
        if(!App.state?.runtime?.installed)await ensureRuntimeInstalled();
        await api('/api/models/quick/download',{method:'POST',body:{id:'qwen2.5-1.5b-instruct'}});toast('Light model download started','Chat becomes available after the sub-1GB Q4 file finishes; learning setup continues in the background.','info',6500);await waitForQuickModelChat()
      }catch(e){toast('Could not install model',e.message,'error',9000)}
    };
    $('#downloadModel').onclick=showModelDownloadLibrary;
    $('#browseModel').onclick=chooseModel;
    if($('#enableLearning'))$('#enableLearning').onclick=async()=>{try{const st=await api('/api/brain/settings',{method:'POST',body:{enabled:true,zero_context:true,strict_learning:true,auto_synthesize:true}});if(App.state)App.state.brain=st;toast('Personal learning enabled','Facts and corrections you teach will be written into this model’s personal LoRA weights.','ok',5200);renderModels($('#view'))}catch(e){toast('Could not enable learning',e.message,'error',7000)}};
    $('#rescanModels').onclick=async()=>{await api('/api/models/scan',{method:'POST'});toast('Scanning model folders','Runnable models and their learning files are being matched.','info');setTimeout(()=>refreshState(true),700)};
    $('#addFolder').onclick=async()=>{try{const p=await api('/api/dialog/folder');if(!p.path)return;await api('/api/models/add-folder',{method:'POST',body:{path:p.path}});toast('Folder added',p.path);setTimeout(()=>refreshState(true),700)}catch(e){toast('Could not add folder',e.message,'error')}};
    $('#modelSearch').oninput=e=>filterModelCards(e.target.value);
    $$('.model-card.unified-model').forEach(card=>{card.onclick=()=>activateModelAndChat(card);const b=card.querySelector('.model-run-button');if(b)b.onclick=e=>{e.stopPropagation();activateModelAndChat(card)}});
  }
  async function activateModelAndChat(card){
    if(!card||card.dataset.loading==='1')return;
    card.dataset.loading='1';card.classList.add('loading');
    const runButton=card.querySelector('.model-run-button'),oldRunHtml=runButton?.innerHTML||'';if(runButton){runButton.disabled=true;runButton.innerHTML=`${icon('runtime')} Preparing…`;}
    try{
      const target=card.dataset.path,current=App.state?.active_model?.path;
      if(target!==current){await api('/api/model/select',{method:'POST',body:{path:target}});await refreshState(true)}

      // Chat-first contract: a trainable checkpoint is an implementation detail.
      // Loading/downloading it must never sit in front of the sub-1GB GGUF chat
      // path. Start (or open) chat first, then prepare learning asynchronously.
      if(App.state?.server?.ready&&App.state?.active_model?.path===target){
        setRoute('chat');
      }else{
        await startOptimized();
      }

      const brain=App.state?.brain||{},job=brain.job||{};
      if(brain.enabled&&!brain.setup_ready&&!['running','cancelling'].includes(job.state)){
        try{
          const st=await api('/api/brain/autosetup',{method:'POST',body:{}});
          if(App.state)App.state.brain=st;
          toast('Learning is being prepared','Chat is already available. The matching training checkpoint continues downloading in the background.','info',5200);
        }catch{}
      }
    }catch(e){toast('Could not load model',e.message,'error',9000)}finally{card.dataset.loading='0';card.classList.remove('loading');if(runButton&&document.contains(runButton)){runButton.disabled=false;runButton.innerHTML=oldRunHtml}}
  }
  function modelCard(m,active,serverReady){
    const sel=m.path===active,learn=m.learning||{},running=sel&&serverReady,gen=Number(learn.generation||0);
    const learnTag=learn.ready?`<span class="tag brain-ready-tag">${icon('brain')} Learning linked</span>`:`<span class="tag">${icon('brain')} Learning setup automatic</span>`;
    return `<article class="model-card unified-model ${sel?'selected':''}" data-path="${escapeHtml(m.path)}" data-name="${escapeHtml(m.name)}"><div class="model-card-head"><div class="model-icon">${icon('models')}</div><div class="model-card-copy"><strong>${escapeHtml(m.name)}</strong><span>${escapeHtml(m.architecture||'Unknown architecture')} · ${escapeHtml(m.size_label||'GGUF')}</span></div>${sel?`<span class="card-check">${icon('check')}</span>`:''}</div><div class="model-card-footer"><span class="tag">${escapeHtml(m.quantization||'Unknown')}</span><span class="tag">${formatBytesGB(m.size_gb)}</span>${learnTag}${gen?`<span class="tag">Brain gen ${gen}</span>`:''}<button class="primary-button model-run-button" type="button">${icon(running?'chat':'play')} ${running?'Open chat':'Load & chat'}</button></div></article>`;
  }
  function managedLearningCard(m){
    const name=m.repo_id||m.local_dir||'Training checkpoint',role=m.internal_dependency?'Internal base dependency':'Matched learning source';
    return `<article class="model-card trainable-local-model" data-name="${escapeHtml(name)}"><div class="model-card-head"><div class="model-icon">${icon('brain')}</div><div class="model-card-copy"><strong>${escapeHtml(name)}</strong><span>${escapeHtml(role)} · ${escapeHtml(m.checkpoint_kind==='peft_adapter'?'PEFT adapter':'Safetensors checkpoint')}</span></div></div><div class="model-card-footer"><span class="tag">Managed automatically</span>${m.has_safetensors?'<span class="tag">safetensors</span>':''}<span class="tag">${Number(m.weight_gb||0).toFixed(2)} GB</span></div></article>`;
  }
  function filterModelCards(q){ q=q.trim().toLowerCase(); $$('.model-card').forEach(c=>c.classList.toggle('hidden',q&&!String(c.dataset.name||'').toLowerCase().includes(q))); }

  function modelCatalogCard(row){
    return `<button class="download-repo-card" data-repo="${escapeHtml(row.id||'')}"><div><strong>${escapeHtml(row.id||'Unknown')}</strong><small>${Number(row.downloads||0).toLocaleString()} downloads · ${Number(row.likes||0).toLocaleString()} likes</small></div>${icon('chevron')}</button>`;
  }

  function quantLabel(name=''){
    const base=PathLikeName(name).replace(/\.gguf$/i,'');
    const m=base.match(/(?:^|[-_.])(IQ\d[^-_.]*|Q\d[^-_.]*(?:[-_.][KSMXL0-9]+)*)$/i);
    return m?m[1].replace(/[-.]/g,'_'):shortName(base,32);
  }
  function PathLikeName(value=''){return String(value).replace(/\\/g,'/').split('/').pop()||String(value)}

  function quantFileCard(row,recommended){
    const size=Number(row.size||0),rec=row.name===recommended;
    return `<button class="quant-file-card ${rec?'recommended':''}" data-file="${escapeHtml(row.name||'')}"><div><strong>${escapeHtml(quantLabel(row.name||''))}${rec?' · Recommended':''}</strong><small>${escapeHtml(PathLikeName(row.name||''))}</small></div><span>${size?formatBytes(size):'size unknown'}</span></button>`;
  }

  async function showModelDownloadLibrary(){
    const root=$('#modalRoot');
    root.innerHTML=`<div class="modal-backdrop"><div class="modal model-download-modal"><div class="library-head"><div><div class="eyebrow">DOWNLOAD MODEL</div><h3>Find another GGUF model</h3><p>Search Hugging Face for inference models. Automatic learning depends on finding an exact matching trainable source; for guaranteed one-click chat + learning, use the lightweight Qwen2.5 card on the Models page.</p></div><button class="round-icon" data-close>${icon('x')}</button></div><div class="library-search"><span>${icon('search')}</span><input id="ggufSearch" value="qwen instruct" placeholder="Search models, e.g. qwen instruct gguf"><button id="ggufGo" class="primary-button">Search</button></div><div id="ggufResults" class="download-model-results"><div class="library-loading">${icon('search')} Search for a model.</div></div></div></div>`;
    $('[data-close]',root).onclick=()=>root.innerHTML='';
    $('.modal-backdrop',root).onclick=e=>{if(e.target===e.currentTarget)root.innerHTML=''};
    const input=$('#ggufSearch',root),results=$('#ggufResults',root);
    const search=async()=>{
      const q=input.value.trim();results.innerHTML=`<div class="library-loading">${icon('refresh')} Searching Hugging Face…</div>`;
      try{
        const x=await api('/api/models/catalog/search?q='+encodeURIComponent(q)+'&limit=16');
        const rows=x.results||[];results.innerHTML=rows.length?rows.map(modelCatalogCard).join(''):`<div class="empty-state">No GGUF repositories found.</div>`;
        $$('.download-repo-card',results).forEach(b=>b.onclick=()=>showRepoQuants(b.dataset.repo,root));
      }catch(e){results.innerHTML=`<div class="empty-state">${icon('alert')}<strong>Search failed</strong><p>${escapeHtml(e.message)}</p></div>`}
    };
    $('#ggufGo',root).onclick=search;input.onkeydown=e=>{if(e.key==='Enter')search()};
    search();
  }

  async function showRepoQuants(repo,root=$('#modalRoot')){
    const results=$('#ggufResults',root);if(!results)return;
    results.innerHTML=`<div class="library-loading">${icon('refresh')} Reading available GGUF files…</div>`;
    try{
      const x=await api('/api/models/catalog/files?repo='+encodeURIComponent(repo)),files=x.files||[];
      results.innerHTML=`<div class="quant-picker-head"><button id="quantBack" class="text-button">← Back</button><div><strong>${escapeHtml(repo)}</strong><small>Choose one file. Q4_K_M is usually the best CPU default when available.</small></div></div>${files.length?`<div class="quant-file-list">${files.map(f=>quantFileCard(f,x.recommended)).join('')}</div>`:`<div class="empty-state">No GGUF files found in this repository.</div>`}`;
      $('#quantBack',results).onclick=showModelDownloadLibrary;
      $$('.quant-file-card',results).forEach(b=>b.onclick=async()=>{
        const file=b.dataset.file;const ok=await confirmModal('Download model',`${repo}\n${PathLikeName(file)}\n\nAfter download LlamaForge will select it, load it for chat, and prepare learning for the same model.`,`Download`);if(!ok)return;
        try{
          await api('/api/models/catalog/download',{method:'POST',body:{repo,filename:file}});root.innerHTML='';setRoute('models');toast('Model download started',repo,'info',5000);await waitForModelDownloadAndOpen();
        }catch(e){toast('Could not start model download',e.message,'error',8000)}
      });
    }catch(e){results.innerHTML=`<div class="empty-state">${icon('alert')}<strong>Could not read model files</strong><p>${escapeHtml(e.message)}</p></div>`}
  }

  async function waitForQuickModelChat(){
    const started=Date.now();let opened=false;
    while(Date.now()-started<6*60*60*1000){
      await sleep(1000);const next=await api('/api/state');App.state=next;
      const j=next.job||{};
      if(j.kind==='model-bundle-download'&&j.state==='error')throw new Error(j.error||j.message||'Model download failed');
      if(j.kind==='model-bundle-download'&&j.state==='cancelled')return;
      if(j.kind==='model-bundle-download'&&j.chat_ready&&j.result_path&&!opened){
        opened=true;
        await api('/api/model/select',{method:'POST',body:{path:j.result_path}});await refreshState(true);await startOptimized();
        toast('Qwen chat is ready','The exact matching learning weights continue downloading in the background. Your first completed turn will wait and train when setup is ready.','ok',7000);
        return;
      }
      if(App.route==='models')renderModels($('#view'));
    }
    throw new Error('Model download timed out');
  }

  async function waitForModelDownloadAndOpen(){
    const started=Date.now();
    while(Date.now()-started<4*60*60*1000){
      await sleep(1000);const next=await api('/api/state');App.state=next;
      const j=next.job||{};
      if(j.kind==='model-download'&&j.state==='error')throw new Error(j.error||j.message||'Model download failed');
      if(j.kind==='model-download'&&j.state==='cancelled')return;
      if(j.kind==='model-download'&&j.state==='done'&&j.result_path){
        await refreshState(true);
        const card=[...$$('.model-card.unified-model')].find(c=>c.dataset.path===j.result_path);
        if(card){await activateModelAndChat(card);return}
        await api('/api/model/select',{method:'POST',body:{path:j.result_path}});await refreshState(true);await startOptimized();return;
      }
      if(App.route==='models')renderModels($('#view'));
    }
    throw new Error('Model download timed out');
  }

  function renderOptimize(view){
    const s=App.state||{}, m=s.active_model, a=s.assessment;
    view.innerHTML=`<div class="page">${pageTitle('Smart Core','Optimize','Compare CPU launch plans without memorizing llama.cpp flags. Scores are conservative heuristics; a measured auto-tuner is the next layer.')}
      ${!m?emptyMini('Choose a model from the Library first.'):`
      <div class="panel"><div class="smart-row"><div class="score-ring" style="--score:${a?.score||0}"><span>${a?.score||'—'}</span></div><div class="smart-copy"><strong>${escapeHtml(m.name)}</strong><p>${escapeHtml(a?.summary||'Analyzing model…')}</p><div class="tag-row"><span class="tag">${escapeHtml(m.quantization)}</span><span class="tag">${formatBytesGB(m.size_gb)}</span><span class="tag">${escapeHtml(a?.memory_status||'')}</span></div></div><button id="runBest" class="primary-button">${icon('play')} Run best plan</button></div></div>
      <section class="section"><div class="section-head"><div><h3>Candidate plans</h3><p>Sorted by fit, headroom and context.</p></div></div><div class="plan-list">${(a?App._plansCache||[]:[]).length?'':''}${(App._analysisPlans||[]).map(planRow).join('')}</div></section>`}
    </div>`;
    if(m){ loadAnalysisPlans(); if($('#runBest')) $('#runBest').onclick=startOptimized; }
  }
  async function loadAnalysisPlans(){
    try{ const x=await api('/api/model/analysis'); App._analysisPlans=x.plans||[]; if(App.route==='optimize') { const list=$('.plan-list'); if(list) list.innerHTML=App._analysisPlans.map(planRow).join(''); } }catch{}
  }
  function planRow(p,i){ return `<div class="plan-row ${i===0?'best':''}"><div class="plan-score">${p.score}</div><div class="plan-cell"><strong>${escapeHtml(p.profile)}</strong><small>${escapeHtml(p.note||'')}</small></div><div class="plan-cell"><strong>${Number(p.ctx_size).toLocaleString()}</strong><small>context</small></div><div class="plan-cell plan-hide-mobile"><strong>${p.threads} / ${p.threads_batch}</strong><small>CPU threads</small></div><div class="plan-cell plan-hide-mobile"><strong>${p.batch_size} / ${p.ubatch_size}</strong><small>batch / ubatch</small></div><div class="plan-cell"><strong>${p.oversized?'Paging':'In RAM'}</strong><small>${p.estimated_total_gb} GB est.</small></div></div>`; }

  function renderSystem(view){
    const s=App.state||{}, h=s.hardware||{}, l=s.live||{}, disks=h.disks||[], gpus=h.gpus||[], a=s.assessment, m=s.active_model;
    const paging=!!a?.plan?.oversized, loaded=!!s.server?.running;
    view.innerHTML=`<div class="page">${pageTitle('Hardware','System','What LlamaForge sees on this machine. CPU-first planning uses physical cores and currently available memory, not just total RAM.')}
      <div class="metric-grid">${metric('CPU load',`${l.cpu_percent||0}%`,`${h.physical_cores||'?'} physical cores`)}${metric('RAM free',`${(l.ram_available_gb||0).toFixed(1)} GB`,`${(l.ram_total_gb||0).toFixed(1)} GB total`)}${metric('Model memory',!m?'No model':!loaded?'Unloaded':paging?'SSD paging':'In RAM',!m?'Choose a GGUF':!loaded?'llama-server is stopped; selected GGUF is not resident':paging?'Some model pages may be read from disk':'Disk is not in the token hot path')}${metric('Models',String(s.models?.length||0),'local GGUF files')}</div>
      <div class="detail-grid section"><div class="detail-card"><h3>Processor</h3>${detail('CPU',h.cpu||'Unknown')}${detail('Cores',`${h.physical_cores||'?'} physical / ${h.logical_cores||'?'} logical`)}${detail('Platform',`${h.os_name||''} ${h.os_version||''} · ${h.machine||''}`)}</div>
      <div class="detail-card"><h3>Memory & accelerators</h3>${detail('RAM',`${(h.ram_total_gb||0).toFixed(1)} GB total`)}${detail('Available',`${(l.ram_available_gb||0).toFixed(1)} GB`)}${detail('GPU',gpus.map(g=>g.name).join(', ')||'None detected / CPU-only ready')}</div></div>
      <section class="section"><div class="section-head"><div><h3>RAM vs storage</h3><p>${!loaded&&m?'The model is selected but currently unloaded. No llama-server model process should remain resident.':paging?'This model is larger than LlamaForge’s safe RAM budget, so mmap lets Windows fetch model pages from disk when needed.':'This model fits the current safe memory budget. After loading, generation mainly reads model weights from RAM rather than the SSD.'}</p></div></div><div class="storage-explainer"><div class="storage-step ${paging?'active':'good'}"><strong>${paging?'1 · Model file on disk':'1 · GGUF loads from disk'}</strong><span>${paging?'The full file stays memory-mapped instead of being copied into RAM all at once.':'The SSD is mainly used to open/load the model.'}</span></div><div class="storage-arrow">→</div><div class="storage-step ${paging?'active':'good'}"><strong>${paging?'2 · RAM page cache':'2 · Model resident in RAM'}</strong><span>${paging?'Hot pages remain in RAM; cold pages can be evicted when memory is tight.':'The working weights are served from system memory.'}</span></div><div class="storage-arrow">→</div><div class="storage-step good"><strong>3 · CPU inference</strong><span>${paging?'A cache miss can stall while Windows reads another page from storage.':'The CPU reads weights from RAM while generating tokens.'}</span></div></div><div class="detail-grid" style="margin-top:10px">${disks.map(d=>`<div class="detail-card"><h3>${escapeHtml(d.mount)}</h3>${detail('Free',`${d.free_gb} GB`)}${detail('Total',`${d.total_gb} GB`)}</div>`).join('')||emptyMini('No disk information available.')}</div></section>
    </div>`;
  }
  function detail(k,v){ return `<div class="detail-row"><span>${escapeHtml(k)}</span><span>${escapeHtml(String(v))}</span></div>`; }

  function renderRuntime(view){
    const s=App.state||{}, rt=s.runtime||{}, job=s.job||{}, acc=acceleratorPrefs();
    const pct=job.total?Math.min(100,Number(job.done||0)/Number(job.total||1)*100):job.state==='done'?100:Number(job.progress||0)*100;
    const runtimeBusy=job.kind==='runtime-install'&&['running','cancelling'].includes(job.state);
    const requested=job.requested_backend||((acc.mode==='cpu')?'CPU':'Vulkan');
    view.innerHTML=`<div class="page runtime-page">${pageTitle('Engine','Runtime','Install and inspect the local llama.cpp engine. GPU and Hybrid modes use Vulkan; CPU mode can run without it.')}
      <div class="runtime-card runtime-card-pro"><div class="runtime-title"><div><div class="runtime-status-title"><span class="status-dot ${rt.installed?'ready':''}"></span><strong>${rt.installed?`${escapeHtml(rt.backend||'Runtime')} runtime ready`:'Runtime not installed yet'}</strong></div><div class="runtime-path">${escapeHtml(rt.server||'LlamaForge will install it under ~/.llamaforge/runtime')}</div></div><div class="inline-actions"><button id="chooseRuntime" class="secondary-button">${icon('folder')} Use existing</button><button id="installRuntime" class="primary-button" ${runtimeBusy?'disabled':''}>${icon('download')} ${rt.installed?'Update runtime':acc.mode==='cpu'?'Install CPU runtime':'Install Vulkan runtime'}</button>${runtimeBusy?`<button id="cancelRuntime" class="secondary-button">${icon('x')} Cancel</button>`:''}</div></div>
      <div class="runtime-mode-summary"><span>Selected compute mode</span><strong>${escapeHtml(acceleratorLabel(acc.mode))}</strong><small>${escapeHtml(acceleratorDetail(acc))}</small></div>
      ${rt.installed?`<div class="tag-row runtime-tags"><span class="tag">${escapeHtml(rt.source||'managed')}</span><span class="tag">${escapeHtml(rt.backend||'unknown backend')}</span>${rt.asset?`<span class="tag">${escapeHtml(shortName(rt.asset,48))}</span>`:''}${rt.build_number?`<span class="tag">build ${rt.build_number}</span>`:''}${rt.version?`<span class="tag">${escapeHtml(shortName(rt.version,50))}</span>`:''}</div>`:''}
      ${job.kind==='runtime-install'&&job.state!=='idle'?`<div class="runtime-job-card"><div class="runtime-job-head"><div><strong>${escapeHtml(job.message||job.state)}</strong><small>${escapeHtml(requested)} package ${job.total?`· ${formatBytes(job.done||0)} / ${formatBytes(job.total||0)}`:''}</small></div><b>${job.total?`${Math.round(pct)}%`:job.stage==='extract'?'100%':'…'}</b></div><div class="progress runtime-job-progress"><div style="width:${Math.max(job.stage==='resolve'?4:0,pct)}%"></div></div><div class="runtime-job-meta"><span>${Number(job.bytes_per_sec||0)>0?`${formatBytes(job.bytes_per_sec)}/s`:'Speed —'}</span><span>${job.eta_seconds!=null?`ETA ${formatEta(job.eta_seconds)}`:'ETA —'}</span><span>${escapeHtml(job.stage||'')}</span></div>${job.error?`<div class="runtime-error">${escapeHtml(job.error)}</div>`:''}</div>`:''}</div>
      <section class="section"><div class="panel panel-pad"><div class="runtime-explainer"><span>${icon('info')}</span><div><strong>CPU, GPU, or both — your choice</strong><p>CPU only uses no GPU layers. GPU max offload asks llama.cpp to move every possible model layer to Vulkan. Hybrid keeps part of the model on CPU and part on GPU, so both engines do useful work at the same time.</p></div></div></div></section>
    </div>`;
    $('#chooseRuntime').onclick=async()=>{try{const p=await api('/api/dialog/runtime');if(!p.path)return;await api('/api/runtime/select',{method:'POST',body:{path:p.path}});await refreshState(true);toast('Runtime selected',p.path)}catch(e){toast('Runtime selection failed',e.message,'error')}};
    $('#installRuntime').onclick=async()=>{const ok=await confirmModal(acc.mode==='cpu'?'Install CPU runtime':'Install Vulkan runtime',acc.mode==='cpu'?'LlamaForge will download a compatible official llama.cpp CPU package.':'LlamaForge will download the official Vulkan package required for GPU or Hybrid acceleration. Progress, speed and ETA will be shown while it downloads.','Install');if(!ok)return;try{await ensureRuntimeInstalled(true);toast('Runtime ready',`${acceleratorLabel(acc.mode)} can now be used.`,'ok');}catch(e){toast('Runtime installation failed',e.message,'error',9000)}finally{await refreshState(true)}};
    if($('#cancelRuntime'))$('#cancelRuntime').onclick=async()=>{try{await api('/api/runtime/cancel',{method:'POST',body:{}});toast('Stopping runtime download','','info');await refreshState(true)}catch(e){toast('Could not cancel download',e.message,'error')}};
  }

  function renderAdvanced(view){
    const s=App.state||{}, m=s.active_model, ss=s.server||{}, a=s.assessment, t=s.cpu_threading||{}, perf=s.performance||ss.performance||{};
    const prefs=cpuThreadPrefs(), acc=acceleratorPrefs(), hard=Math.max(1,Number(t.hard_max||s.hardware?.logical_cores||1)), aggressive=Math.max(hard,Number(t.aggressive_max||hard*2));
    const targetMin=Number(t.target_min||25),targetMax=Number(t.target_max||100),targetStep=Number(t.target_step||5);
    const recGen=Number(t.recommended_generation||s.hardware?.physical_cores||1), recPrompt=Number(t.recommended_prompt||recGen);
    const perfGen=Number(t.performance_generation||Math.max(1,hard-1)), perfPrompt=Number(t.performance_prompt||hard);
    const perfState=perf.state||'idle', tune=s.autotune||null, tuneJob=(s.job?.kind==='autotune'?s.job:null);
    view.innerHTML=`<div class="page">${pageTitle('Engine','Adaptive compute & launch','Adaptive AutoTune measures this exact GGUF on the installed runtime. Manual CPU/GPU modes stay available for experimentation.')}
      ${tuneJob?`<section class="section"><div class="panel panel-pad autotune-status"><div><span class="mini-kicker">AUTOTUNE</span><strong>${escapeHtml(tuneJob.message||tuneJob.state||'Working…')}</strong><small>${tuneJob.state==='done'&&tune?`Winner: ${Number(tune.generation_tps||0).toFixed(2)} tok/s decode · ${Number(tune.gpu_layer_percent||0)}% GPU · ${Number(tune.threads||0)} threads`:`llama-bench is measuring throughput without a second copy of the model.`}</small></div><div class="autotune-progress-line"><div class="progress"><div style="width:${Math.round(Math.max(0,Math.min(1,Number(tuneJob.progress||0)))*100)}%"></div></div><b>${Math.round(Math.max(0,Math.min(1,Number(tuneJob.progress||0)))*100)}%</b>${['running','cancelling'].includes(tuneJob.state)?`<button id="cancelAutotuneAdvanced" class="secondary-button" ${tuneJob.state==='cancelling'?'disabled':''}>${tuneJob.state==='cancelling'?'Cancelling…':'Cancel'}</button>`:''}</div></div></section>`:''}
      ${!m?emptyMini('Choose a model before changing launch settings.'):`<div class="engine-grid"><section class="panel panel-pad engine-main"><div class="form-grid"><div class="field"><label>Memory profile</label><select id="profileSelect">${['Safe','Balanced','Max Speed','Low RAM','Giant Model (Experimental)'].map(p=>`<option ${p===(a?.recommended_profile||'Balanced')?'selected':''}>${p}</option>`).join('')}</select></div><div class="field"><label>Context length</label><input id="ctxInput" type="number" min="512" max="${m.context_length||262144}" step="1" value="${Number((s.config||{}).default_context_size||a?.recommended_ctx||4096)}"></div></div>
      <div class="compute-mode-block"><div class="compute-mode-head"><div><span class="mini-kicker">COMPUTE ENGINE</span><h3>Choose CPU, GPU, or both</h3><p>${acc.hasGpu?`Detected ${escapeHtml((s.hardware.gpus||[]).map(g=>g.name).join(', '))}. Your choice is saved and used by every Load action.`:'No compatible GPU was detected, so CPU mode is the only available option.'}</p></div></div><div class="compute-mode-grid" id="accelModePicker"><button type="button" data-accel="adaptive" class="compute-mode-card ${acc.mode==='adaptive'?'selected':''}"><span>${icon('spark')}</span><strong>Adaptive</strong><small>Benchmark-picked per model</small></button><button type="button" data-accel="cpu" class="compute-mode-card ${acc.mode==='cpu'?'selected':''}"><span>${icon('cpu')}</span><strong>CPU only</strong><small>No GPU offload</small></button><button type="button" data-accel="gpu" class="compute-mode-card ${acc.mode==='gpu'?'selected':''}" ${acc.hasGpu?'':'disabled'}><span>${icon('harddrive')}</span><strong>GPU</strong><small>Maximum Vulkan offload</small></button><button type="button" data-accel="hybrid" class="compute-mode-card ${acc.mode==='hybrid'?'selected':''}" ${acc.hasGpu?'':'disabled'}><span>${icon('spark')}</span><strong>CPU + GPU</strong><small>Normal layer split</small></button><button type="button" data-accel="max_both" class="compute-mode-card ${acc.mode==='max_both'?'selected':''}" ${acc.hasGpu?'':'disabled'}><span>${icon('cpu')}</span><strong>Max Both</strong><small>CPU-first + iGPU assist</small></button></div></div>
      <div class="thread-control gpu-share-control ${['hybrid','max_both'].includes(acc.mode)?'active':'inactive'}" style="margin-top:12px"><div class="thread-label"><span>GPU share in Hybrid mode</span><strong id="gpuLayerValue">${acc.mode==='gpu'?100:['hybrid','max_both'].includes(acc.mode)?acc.percent:0}%</strong></div><input id="gpuLayerPercent" type="range" min="5" max="95" step="5" value="${acc.percent}" ${acc.hasGpu&&['hybrid','max_both'].includes(acc.mode)?'':'disabled'}><small>Hybrid: manual split. Max Both: use a small GPU slice so the CPU keeps real transformer work; Intel iGPU starts near 10%.</small></div>
      <div class="thread-tuner"><div class="thread-tuner-head"><div><span class="mini-kicker">CPU POLICY</span><h3>Utilization controller</h3><p>Use a percentage budget for normal operation. Saturation mode intentionally oversubscribes llama.cpp workers and raises process priority to chase higher Task Manager utilization.</p></div><div class="cpu-cap"><strong>${s.hardware?.physical_cores||'?'}P / ${s.hardware?.logical_cores||'?'}L</strong><span>${aggressive} max workers</span></div></div>
        <div class="segmented cpu-mode-tabs" id="threadMode"><button data-mode="auto" class="${prefs.mode==='auto'?'active':''}">Auto</button><button data-mode="performance" class="${prefs.mode==='performance'?'active':''}">Throughput</button><button data-mode="target" class="${prefs.mode==='target'?'active':''}">CPU budget</button><button data-mode="saturate" class="${prefs.mode==='saturate'?'active':''}">Saturate</button><button data-mode="manual" class="${prefs.mode==='manual'?'active':''}">Manual</button></div>
        <div class="cpu-target-advanced"><div class="thread-label"><span>CPU budget</span><strong id="cpuTargetValue">${prefs.target_percent}%</strong></div><input id="cpuTargetRange" type="range" min="${targetMin}" max="${targetMax}" step="${targetStep}" value="${prefs.target_percent}"><div class="cpu-target-row"><span id="cpuTargetThreads">${cpuPlanSummary(prefs)}</span><button id="cpuFullThrottle" class="text-button">100% normal budget</button><button id="cpuSaturate" class="text-button">Force saturation</button></div><small>Normal 100% means one worker budget across all logical CPUs. Saturation may use ~1.5× as many workers as logical CPUs, poll aggressively and request High process priority. It can increase utilization without guaranteeing more tokens/sec.</small></div>
        <div class="thread-presets"><button id="threadRecommended" class="text-button">Efficient ${recGen}/${recPrompt}</button><button id="threadPerformance" class="text-button">Throughput ${perfGen}/${perfPrompt}</button><span>Logical CPUs: ${hard} · worker guardrail: ${aggressive}</span></div>
        <div class="thread-grid"><div class="thread-control"><div class="thread-label"><span>Generation workers</span><strong id="genThreadValue">${prefs.threads}</strong></div><input id="genThreads" type="range" min="1" max="${aggressive}" value="${prefs.threads}"><small>Decode workers. Going above logical CPU count is oversubscription and is intended only for saturation experiments.</small></div><div class="thread-control"><div class="thread-label"><span>Prompt / batch workers</span><strong id="promptThreadValue">${prefs.threads_batch}</strong></div><input id="promptThreads" type="range" min="1" max="${aggressive}" value="${prefs.threads_batch}"><small>Prompt ingestion can use SMT more effectively than token-by-token decode.</small></div></div>
        <div class="thread-warning">${icon('info')}<span>If saturation mode still shows low CPU usage, the model is probably waiting on RAM bandwidth, mmap/SSD pages, or synchronization. LlamaForge now reports that bottleneck instead of pretending more threads always help.</span></div>
      </div>
      <div class="inline-actions" style="margin-top:18px"><button id="manualStart" class="primary-button" ${ss.running?'disabled':''}>${icon('play')} Start server</button>${ss.running?`<button id="manualStop" class="danger-button">${icon('stop')} Stop</button>`:''}<button id="advancedLogs" class="secondary-button">${icon('logs')} View logs</button></div></section>
      <aside class="engine-live-card"><span class="mini-kicker">LIVE ENGINE</span><div class="engine-live-number" data-live="process-cpu">${Number(s.live?.process_cpu_percent||0).toFixed(0)}%</div><div class="engine-live-label">llama.cpp CPU</div><div class="engine-live-divider"></div><div class="engine-live-row"><span>GPU backend</span><strong>${escapeHtml(s.runtime?.backend||'—')}</strong></div><div class="engine-live-row"><span>GPU share</span><strong>${ss.plan?.accelerator_mode==='gpu'?'GPU max offload':ss.plan?.accelerator_mode==='hybrid'?`${ss.plan.gpu_layer_percent||acc.percent}% · ${ss.plan.gpu_layers||'?'} layers`:ss.plan?.accelerator_mode==='max_both'?`Max Both · ${ss.plan.gpu_layer_percent||acc.percent}% · ${ss.plan.gpu_layers||'?'} layers`:ss.plan?.accelerator_mode==='adaptive'?`Adaptive · ${ss.plan.gpu_layer_percent||0}% · ${ss.plan.gpu_layers??'?'} layers`:acc.mode==='adaptive'?(s.autotune?`Adaptive tuned · ${Number(s.autotune.gpu_layer_percent||0)}% GPU`:'Adaptive heuristic armed'):acc.mode==='gpu'?'GPU max armed':acc.mode==='hybrid'?`${acc.percent}% hybrid armed`:acc.mode==='max_both'?`Max Both · ${acc.percent}% armed`:'CPU only'}</strong></div><div class="engine-live-row"><span>System CPU</span><strong data-live="system-cpu">System ${Number(s.live?.cpu_percent||0).toFixed(0)}%</strong></div><div class="engine-live-row"><span>Target</span><strong id="perfTarget">${perf.target_percent||prefs.target_percent}%</strong></div><div class="engine-live-row"><span>Workers</span><strong>${perf.threads||prefs.threads} / ${perf.threads_batch||prefs.threads_batch}</strong></div><div class="engine-live-row"><span>Decode</span><strong>${perf.generation_tps?`${perf.generation_tps} tok/s`:'—'}</strong></div><div class="engine-bottleneck ${escapeHtml(perfState)}"><strong id="perfTitle">${escapeHtml(perfState.replaceAll('-',' '))}</strong><p id="perfDetail">${escapeHtml(perf.detail||'Start a model to diagnose utilization.')}</p></div></aside></div>`}
      ${a?.plan?.warning?`<section class="section"><div class="panel panel-pad"><div style="display:flex;gap:11px"><span style="color:var(--warn)">${icon('alert')}</span><p style="margin:0;color:var(--muted);font-size:11px;line-height:1.55">${escapeHtml(a.plan.warning)}</p></div></div></section>`:''}</div>`;
    if(m){
      let mode=prefs.mode; const gen=$('#genThreads'), prompt=$('#promptThreads'),target=$('#cpuTargetRange');
      const setValues=(g,p,md,targetPct)=>{gen.value=Math.max(1,Math.min(aggressive,g));prompt.value=Math.max(1,Math.min(aggressive,p));$('#genThreadValue').textContent=gen.value;$('#promptThreadValue').textContent=prompt.value;if(targetPct!==undefined){target.value=Math.max(targetMin,Math.min(targetMax,targetPct));$('#cpuTargetValue').textContent=`${target.value}%`;}if(md)mode=md;$$('#threadMode button').forEach(b=>b.classList.toggle('active',b.dataset.mode===mode));saveCpuThreadPrefs(mode,Number(gen.value),Number(prompt.value),Number(target.value));$('#cpuTargetThreads').textContent=cpuPlanSummary(cpuThreadPrefs());};
      const applyTarget=(pct)=>{const val=Math.max(targetMin,Math.min(targetMax,Number(pct)||100)),n=Math.max(1,Math.min(hard,Math.round(hard*val/100)));setValues(n,n,'target',val);};
      const applySaturate=()=>{const n=Math.max(hard,Math.min(aggressive,Math.round(hard*1.5)));setValues(n,n,'saturate',100);target.value=100;$('#cpuTargetValue').textContent='100%';$('#cpuTargetThreads').textContent=cpuPlanSummary(cpuThreadPrefs());};
      gen.oninput=()=>setValues(Number(gen.value),Number(prompt.value),'manual',Number(target.value)); prompt.oninput=()=>setValues(Number(gen.value),Number(prompt.value),'manual',Number(target.value)); target.oninput=()=>applyTarget(target.value);
      $$('#threadMode button').forEach(b=>b.onclick=()=>{mode=b.dataset.mode;if(mode==='auto')setValues(recGen,recPrompt,'auto',Number(target.value));else if(mode==='performance')setValues(perfGen,perfPrompt,'performance',Number(target.value));else if(mode==='target')applyTarget(target.value);else if(mode==='saturate')applySaturate();else setValues(Number(gen.value),Number(prompt.value),'manual',Number(target.value))});
      $('#threadRecommended').onclick=()=>setValues(recGen,recPrompt,'auto',Number(target.value)); $('#threadPerformance').onclick=()=>setValues(perfGen,perfPrompt,'performance',Number(target.value)); $('#cpuFullThrottle').onclick=()=>applyTarget(100); $('#cpuSaturate').onclick=applySaturate;
      const gpuRange=$('#gpuLayerPercent'),gpuValue=$('#gpuLayerValue'),modeButtons=$$('#accelModePicker [data-accel]');
      let accelMode=acc.mode;
      const paintAccel=mode=>{accelMode=mode;modeButtons.forEach(b=>b.classList.toggle('selected',b.dataset.accel===mode));if(gpuRange)gpuRange.disabled=!acc.hasGpu||!['hybrid','max_both'].includes(mode);if(mode==='max_both'&&Number(gpuRange.value)>30)gpuRange.value='10';if(gpuValue)gpuValue.textContent=mode==='gpu'?'100%':['hybrid','max_both'].includes(mode)?`${gpuRange.value}%`:'0%';const ctl=$('.gpu-share-control');if(ctl){ctl.classList.toggle('active',['hybrid','max_both'].includes(mode));ctl.classList.toggle('inactive',!['hybrid','max_both'].includes(mode))}saveAcceleratorPrefs(mode,Number(gpuRange?.value||acc.percent));};
      modeButtons.forEach(b=>b.onclick=()=>{if(b.disabled)return;paintAccel(b.dataset.accel)});
      if(gpuRange)gpuRange.oninput=()=>{if(gpuValue)gpuValue.textContent=['hybrid','max_both'].includes(accelMode)?`${gpuRange.value}%`:accelMode==='gpu'?'100%':'0%';saveAcceleratorPrefs(accelMode,Number(gpuRange.value))};
      $('#manualStart').onclick=async()=>{try{const cp=cpuThreadPrefs(),pct=Number(gpuRange?.value||35);saveAcceleratorPrefs(accelMode,pct);await api('/api/settings',{method:'POST',body:{accelerator_mode:accelMode,gpu_layer_percent:pct,default_context_size:Number($('#ctxInput').value||4096)}});await ensureRuntimeInstalled();await api('/api/server/start',{method:'POST',body:{model_path:m.path,profile:$('#profileSelect').value,ctx:Number($('#ctxInput').value),accelerator_mode:accelMode,cpu_only:accelMode==='cpu',gpu_layer_percent:pct,thread_mode:accelMode==='adaptive'?'auto':accelMode==='max_both'?'saturate':cp.mode,threads:accelMode==='max_both'?(s.hardware?.logical_cores||cp.threads):cp.threads,threads_batch:accelMode==='max_both'?(s.hardware?.logical_cores||cp.threads_batch):cp.threads_batch,cpu_target_percent:accelMode==='max_both'?100:cp.target_percent,cpu_saturation:accelMode==='max_both'?true:cp.saturation,speculative_mode:String(s.config?.speculative_mode||'auto'),adaptive_context:Boolean(s.config?.adaptive_context!==false)}});toast('Server starting',accelMode==='adaptive'?(s.autotune?`Adaptive measured plan · ${Number(s.autotune.generation_tps||0).toFixed(2)} tok/s benchmark`:'Adaptive heuristic · run AutoTune for a measured plan'):accelMode==='max_both'?`Max Both · ${pct}% GPU layers · all logical CPU workers`:accelMode==='hybrid'?`CPU + GPU hybrid · ${pct}% GPU layers · ${cpuPlanSummary(cp)}`:accelMode==='gpu'?`GPU max offload · ${cpuPlanSummary(cp)}`:cpuPlanSummary(cp),'info')}catch(e){toast('Could not start model',e.message,'error')}};
      if($('#cancelAutotuneAdvanced'))$('#cancelAutotuneAdvanced').onclick=async()=>{try{await api('/api/autotune/cancel',{method:'POST',body:{}});toast('Cancelling AutoTune','','info');await refreshState(true)}catch(e){toast('Could not cancel AutoTune',e.message,'error')}};
      if($('#manualStop')) $('#manualStop').onclick=stopServer; $('#advancedLogs').onclick=()=>setRoute('logs');
    }
  }
  async function unloadModel(){
    try{
      const r=await api('/api/model/unload',{method:'POST'});
      const extra=Number(r.ram_delta_gb||0)>0.05?`Available RAM increased by ~${Number(r.ram_delta_gb).toFixed(2)} GB.`:'llama-server has been terminated. Windows may keep file pages in reclaimable standby cache.';
      toast('Model unloaded',extra,'ok',4800);
      await refreshState(true);
    }catch(e){toast('Could not unload model',e.message,'error')}
  }
  async function stopServer(){ return unloadModel(); }
  async function exitLlamaForge(){
    const ok=await confirmModal('Exit LlamaForge and unload model','This stops llama-server, releases the active model process, and shuts down the local LlamaForge control plane.','Exit & unload',true);
    if(!ok)return;
    try{
      await api('/api/app/exit',{method:'POST'});
      document.body.innerHTML='<div style="min-height:100vh;display:grid;place-items:center;background:#0b0c0f;color:#e9e9ee;font-family:system-ui"><div style="text-align:center"><h2 style="margin:0 0 8px">LlamaForge stopped</h2><p style="color:#8f919c">The model was unloaded. You can close this window.</p></div></div>';
      setTimeout(()=>{try{window.close()}catch{}},180);
    }catch(e){toast('Could not exit cleanly',e.message,'error')}
  }

  function renderLogs(view){
    view.innerHTML=`<div class="page">${pageTitle('Diagnostics','Runtime logs','Live llama.cpp output, Brain Trainer phases, native crash codes, memory snapshots and persistent session logs.')}
      <div class="log-shell"><div class="log-toolbar"><span id="logCount">Loading…</span><div class="inline-actions"><button id="copyLogs" class="text-button">${icon('copy')} Copy log</button><button id="copyTrainerLog" class="text-button">${icon('brain')} Copy trainer session</button><button id="copyDiagnostics" class="text-button">${icon('info')} Copy full diagnostic</button><button id="clearLogs" class="text-button">Clear</button><button id="refreshLogs" class="text-button">${icon('refresh')} Refresh</button></div></div><div id="logMeta" style="padding:8px 12px;border-bottom:1px solid var(--border);font-size:11px;color:var(--muted);line-height:1.55"></div><pre id="logOutput" class="log-output"></pre></div></div>`;
    refreshLogs(); $('#copyLogs').onclick=async()=>{const t=$('#logOutput').textContent||'';await navigator.clipboard.writeText(t);toast('Log copied',`${t.length.toLocaleString()} characters copied`)}; $('#copyTrainerLog').onclick=async()=>{try{const x=await api('/api/logs/trainer');const t=x.text||JSON.stringify(x.info||{},null,2);await navigator.clipboard.writeText(t);toast('Trainer session copied',`${t.length.toLocaleString()} characters copied`)}catch(e){toast('Could not copy trainer session',e.message,'error')}}; $('#copyDiagnostics').onclick=async()=>{try{const d=await api('/api/diagnostics');const t=JSON.stringify(d,null,2);await navigator.clipboard.writeText(t);toast('Full diagnostic copied',`${t.length.toLocaleString()} characters · secrets omitted`)}catch(e){toast('Could not build diagnostic',e.message,'error')}}; $('#clearLogs').onclick=async()=>{await api('/api/logs/clear',{method:'POST'});refreshLogs();toast('Logs cleared')}; $('#refreshLogs').onclick=refreshLogs;
  }
  async function refreshLogs(){ if(App.route!=='logs')return; try{const x=await api('/api/logs');const out=$('#logOutput'); if(!out)return; const near=out.scrollTop+out.clientHeight>=out.scrollHeight-80; out.textContent=(x.lines||[]).join('\n'); $('#logCount').textContent=`${(x.lines||[]).length} lines`; const meta=$('#logMeta'),t=x.last_trainer||{}; if(meta){const ex=t.exit_name?`${t.exit_name} ${t.exit_hex||''}`:'no trainer session yet';meta.textContent=`Persistent: ${x.persistent_log||'—'}  ·  Trainer: ${ex}  ·  Phase: ${t.last_phase||'—'}  ·  Peak RSS: ${Number(t.peak_worker_rss_mb||0).toFixed(0)} MB  ·  Session: ${t.log_path||'—'}`;} if(near)out.scrollTop=out.scrollHeight;}catch{} }

  function renderSettings(view){
    const c=App.state?.config||{}, ss=App.state?.server||{}, active=App.state?.active_model||null, loaded=!!ss.ready;
    const draft=App.settingsDraft||{}, own=(k)=>Object.prototype.hasOwnProperty.call(draft,k), val=(k,f)=>own(k)?draft[k]:f;
    const idle=Number(val('idle_unload_minutes',c.idle_unload_minutes||0));
    const savedMode=['ram_only','ssd_test','hybrid'].includes(c.model_memory_mode)?c.model_memory_mode:'hybrid';
    const selectedMode=App.settingsMemoryDirty&&['ram_only','ssd_test','hybrid'].includes(App.pendingMemoryMode)?App.pendingMemoryMode:savedMode;
    const memoryLabel=mode=>mode==='ram_only'?'Full RAM':mode==='ssd_test'?'SSD / mmap':'Smart RAM';
    const memoryHelp=mode=>mode==='ram_only'?'Full RAM disables mmap and loads GGUF weights through ordinary process memory. Windows may still page under pressure, but this avoids the fragile mlock path.':mode==='ssd_test'?'Disk-backed mode: mmap + lazy loading where supported; runtime buffers and OS cache still use RAM.':'Smart RAM uses Full RAM automatically when the model fits the safe budget; only oversized models fall back to mmap/SSD.';
    const card=(mode,title,desc)=>`<button type="button" class="memory-mode-card ${selectedMode===mode?'selected':''}" data-memory-mode="${mode}" aria-pressed="${selectedMode===mode?'true':'false'}"><span class="memory-mode-dot"></span><span><strong>${title}</strong><small>${desc}</small></span></button>`;
    const accelBase=acceleratorPrefs(), hasGpu=accelBase.hasGpu;
    let selectedAccel=String(val('accelerator_mode',accelBase.mode)||'adaptive');if(!['adaptive','cpu','gpu','hybrid','max_both'].includes(selectedAccel))selectedAccel='adaptive';if(!hasGpu&&selectedAccel!=='adaptive')selectedAccel='cpu';
    const gpuShare=Math.max(5,Math.min(95,Number(val('gpu_layer_percent',accelBase.percent)||35)));
    const ctxMax=Math.max(512,Number(active?.context_length||262144));
    const ctxValue=Math.max(512,Math.min(ctxMax,Number(val('default_context_size',c.default_context_size||4096))));
    const genManual=Boolean(val('generation_overrides_enabled',c.generation_overrides_enabled));
    const isDirty=App.settingsFormDirty||Object.keys(draft).length>0;

    view.innerHTML=`<div class="page settings-page">${pageTitle('Preferences','Settings','A cleaner control center for model loading, compute selection, context and local runtime behavior.')}
      <section class="settings-overview">
        <div class="settings-overview-main"><span class="mini-kicker">ACTIVE MODEL</span><strong>${active?escapeHtml(active.name||'Local model'):'No model selected'}</strong><small>${active?`${escapeHtml(active.quantization||'GGUF')} · ${formatBytesGB(active.size_gb||0)}`:'Choose a GGUF model to start.'}</small></div>
        <div class="settings-stat"><span>Context</span><strong id="settingsCtxSummary">${ctxValue.toLocaleString()}</strong><small>tokens</small></div>
        <div class="settings-stat"><span>Compute</span><strong id="settingsComputeSummary">${escapeHtml(acceleratorLabel(selectedAccel))}</strong><small>${selectedAccel==='adaptive'?(App.state?.autotune?`${Number(App.state.autotune.generation_tps||0).toFixed(2)} tok/s measured`:'not tuned yet'):['hybrid','max_both'].includes(selectedAccel)?`${gpuShare}% GPU share`:selectedAccel==='gpu'?'max offload':'local CPU'}</small></div>
        <div class="settings-stat"><span>Status</span><strong>${loaded?'Loaded':ss.running?'Loading':'Stopped'}</strong><small>${escapeHtml(App.state?.runtime?.backend||'runtime')}</small></div>
      </section>

      <section class="section settings-section"><div class="settings-section-head"><div><span class="mini-kicker">COMPUTE ENGINE</span><h3>Adaptive or manual compute</h3><p>Adaptive measures this exact GGUF and saves the fastest CPU/GPU/thread/batch plan. Manual modes remain available.</p></div><span class="settings-state-pill">${hasGpu?escapeHtml((App.state?.hardware?.gpus||[]).map(g=>g.name).join(', ')):'No GPU detected'}</span></div>
        <div class="compute-mode-grid settings-compute-grid" id="settingsAccelPicker">
          <button type="button" data-settings-accel="adaptive" class="compute-mode-card ${selectedAccel==='adaptive'?'selected':''}"><span>${icon('spark')}</span><strong>Adaptive</strong><small>AutoTune per GGUF</small></button>
          <button type="button" data-settings-accel="cpu" class="compute-mode-card ${selectedAccel==='cpu'?'selected':''}"><span>${icon('cpu')}</span><strong>CPU only</strong><small>No Vulkan download required</small></button>
          <button type="button" data-settings-accel="gpu" class="compute-mode-card ${selectedAccel==='gpu'?'selected':''}" ${hasGpu?'':'disabled'}><span>${icon('harddrive')}</span><strong>GPU</strong><small>Maximum layer offload</small></button>
          <button type="button" data-settings-accel="hybrid" class="compute-mode-card ${selectedAccel==='hybrid'?'selected':''}" ${hasGpu?'':'disabled'}><span>${icon('spark')}</span><strong>CPU + GPU</strong><small>Manual layer split</small></button>
          <button type="button" data-settings-accel="max_both" class="compute-mode-card ${selectedAccel==='max_both'?'selected':''}" ${hasGpu?'':'disabled'}><span>${icon('cpu')}</span><strong>Max Both</strong><small>CPU-first + iGPU assist</small></button>
        </div>
        <div class="settings-slider ${['hybrid','max_both'].includes(selectedAccel)?'active':'inactive'}" id="settingsGpuShareWrap"><div class="thread-label"><span>GPU share in Hybrid mode</span><strong id="settingsGpuShareValue">${selectedAccel==='gpu'?100:['hybrid','max_both'].includes(selectedAccel)?gpuShare:0}%</strong></div><input id="settingsGpuShare" type="range" min="5" max="95" step="5" value="${gpuShare}" ${['hybrid','max_both'].includes(selectedAccel)&&hasGpu?'':'disabled'}><small>Hybrid is manual. Max Both keeps GPU share low; for Intel integrated graphics start near 10% so CPU and GPU both receive useful work.</small></div>
        <div class="autotune-card"><div><span class="mini-kicker">PER-MODEL AUTOTUNE</span><strong>${App.state?.autotune?'Measured plan saved':'No benchmark for this GGUF yet'}</strong><small>${App.state?.autotune?`Decode ${Number(App.state.autotune.generation_tps||0).toFixed(2)} tok/s · prompt ${Number(App.state.autotune.prompt_tps||0).toFixed(1)} tok/s · ~${Math.round(Number(App.state.autotune.estimated_ttft_ms||0))} ms compute TTFT · ${Number(App.state.autotune.threads||0)} threads · ${Number(App.state.autotune.gpu_layer_percent||0)}% GPU`:'AutoTune temporarily unloads the model, measures CPU threads, GPU layers and batch sizes with llama-bench, then saves the winner for this exact file.'}</small></div><div class="inline-actions"><button type="button" id="runAutotune" class="primary-button" ${active?'':'disabled'}>${icon('spark')} ${App.state?.autotune?'Retune model':'Run AutoTune'}</button>${App.state?.autotune?'<button type="button" id="clearAutotune" class="secondary-button">Clear result</button>':''}</div></div>
      </section>

      <section class="section settings-section"><div class="settings-section-head"><div><span class="mini-kicker">MODEL WINDOW</span><h3>Context & output</h3><p>Your context value is now kept as a local draft while you edit it, so background state refreshes cannot reset the field.</p></div>${isDirty?'<span class="settings-unsaved">Unsaved changes</span>':''}</div>
        <div class="context-control-card"><div class="context-control-main"><label for="defaultContext">Context limit</label><div class="context-input-wrap"><input id="defaultContext" type="number" min="512" max="${ctxMax}" step="1" value="${ctxValue}"><span>tokens</span></div><small>Any integer from 512 to ${ctxMax.toLocaleString()} is accepted. The model is reloaded before a new context takes effect.</small></div><div class="context-presets" id="contextPresets">${[4096,8192,16384,32768].filter(x=>x<=ctxMax).map(x=>`<button type="button" data-context="${x}" class="${ctxValue===x?'active':''}">${x===4096?'4K':x===8192?'8K':x===16384?'16K':'32K'}</button>`).join('')}<button type="button" data-context="8000" class="${ctxValue===8000?'active':''}">8000</button></div></div>
        <div class="form-grid settings-form-grid" style="margin-top:14px"><div class="field"><label>Maximum answer tokens</label><input id="generationMaxTokens" type="number" min="16" max="32768" step="1" value="${Number(val('generation_max_tokens',c.generation_max_tokens||2048))}"><small>Maximum number of new tokens in one response.</small></div><div class="field"><label>Speculative decoding</label><select id="speculativeMode"><option value="auto" ${String(val('speculative_mode',c.speculative_mode||'auto'))==='auto'?'selected':''}>Auto (recommended)</option><option value="ngram" ${String(val('speculative_mode',c.speculative_mode||'auto'))==='ngram'?'selected':''}>N-gram</option><option value="off" ${String(val('speculative_mode',c.speculative_mode||'auto'))==='off'?'selected':''}>Off</option></select><small>Auto uses llama.cpp n-gram speculation when supported. AutoTune measures the target model only; speculative decoding is not part of llama-bench.</small></div><label class="check-row compact-check"><input id="adaptiveContext" type="checkbox" ${Boolean(val('adaptive_context',c.adaptive_context!==false))?'checked':''}><span><strong>Adaptive context guard</strong><small>For large models, clamp only the active launch context to preserve RAM; your saved preference stays unchanged.</small></span></label><label class="check-row compact-check"><input id="manualGeneration" type="checkbox" ${genManual?'checked':''}><span><strong>Manual generation settings</strong><small>Off = Smart Chat chooses sampling automatically.</small></span></label></div>
        <details class="settings-advanced-generation" ${genManual?'open':''}><summary>Sampling controls</summary><div class="form-grid" style="margin-top:14px"><div class="field"><label>Temperature</label><input id="generationTemperature" type="number" min="0" max="2" step="0.01" value="${Number(val('generation_temperature',c.generation_temperature??0.7))}"></div><div class="field"><label>Top P</label><input id="generationTopP" type="number" min="0" max="1" step="0.01" value="${Number(val('generation_top_p',c.generation_top_p??0.95))}"></div><div class="field"><label>Top K</label><input id="generationTopK" type="number" min="0" max="500" step="1" value="${Number(val('generation_top_k',c.generation_top_k??40))}"></div><div class="field"><label>Min P</label><input id="generationMinP" type="number" min="0" max="1" step="0.01" value="${Number(val('generation_min_p',c.generation_min_p??0))}"></div><div class="field"><label>Repeat penalty</label><input id="generationRepeat" type="number" min="0.8" max="1.3" step="0.01" value="${Number(val('generation_repeat_penalty',c.generation_repeat_penalty??1.03))}"></div></div></details>
        <div class="settings-primary-actions"><button id="saveModelControls" class="secondary-button">${icon('check')} Save controls</button>${active?`<button id="settingsLoadModel" class="primary-button">${icon('play')} ${loaded?'Reload model':'Load model'}</button>`:''}${ss.running?`<button id="settingsUnloadTop" class="secondary-button">${icon('stop')} Unload</button>`:''}<button id="settingsChooseModel" class="secondary-button">${icon('models')} Choose model</button></div>
      </section>

      <section class="section settings-section"><div class="settings-section-head"><div><span class="mini-kicker">MEMORY</span><h3>Model residency</h3><p>Control how aggressively model weights stay resident in RAM.</p></div></div><div class="memory-mode-grid" id="memoryModePicker">${card('ram_only','Full RAM','No mmap: load GGUF weights through normal process memory. Block loading when it cannot fit safely.')}${card('ssd_test','SSD / mmap','Disk-backed mmap/lazy mode for very large models or troubleshooting.')}${card('hybrid','Smart RAM','Automatically use Full RAM when it fits; otherwise switch to mmap.')}</div><div class="memory-mode-status" id="memoryModeStatus">${App.settingsMemoryDirty?`Not applied yet: ${memoryLabel(selectedMode)}`:`Saved mode: ${memoryLabel(savedMode)}`}</div><div class="inline-actions" style="margin-top:12px"><button type="button" id="applyMemoryMode" class="primary-button" ${App.settingsMemoryDirty?'':'disabled'}>Apply memory mode</button><button type="button" id="cancelMemoryMode" class="secondary-button" ${App.settingsMemoryDirty?'':'disabled'}>Cancel change</button></div><small id="memoryModeHelp" class="settings-help">${memoryHelp(selectedMode)}</small></section>

      <div class="settings-two-col"><section class="settings-section"><div class="settings-section-head"><div><span class="mini-kicker">SYSTEM</span><h3>Local runtime behavior</h3></div></div><div class="form-grid"><div class="field"><label>Maximum RAM target (%)</label><input id="ramGuard" type="number" min="50" max="95" step="1" value="${Number(val('max_ram_percent',c.max_ram_percent||88))}"></div><div class="field"><label>llama-server port</label><input id="portSetting" type="number" min="1024" max="65535" step="1" value="${Number(val('port',c.port||8080))}"></div><div class="field"><label>Unload after idle</label><select id="idleUnload"><option value="0" ${idle===0?'selected':''}>Never</option><option value="10" ${idle===10?'selected':''}>10 minutes</option><option value="30" ${idle===30?'selected':''}>30 minutes</option><option value="60" ${idle===60?'selected':''}>60 minutes</option></select></div><div class="field"><label>UI disconnect grace</label><select id="disconnectGrace">${[8,12,20,45].map(x=>`<option value="${x}" ${Number(val('ui_disconnect_shutdown_seconds',c.ui_disconnect_shutdown_seconds||12))===x?'selected':''}>${x} seconds</option>`).join('')}</select></div></div><label class="check-row" style="margin-top:12px"><input id="exitUnload" type="checkbox" ${Boolean(val('exit_unloads_model',c.exit_unloads_model!==false))?'checked':''}><span><strong>Unload model when LlamaForge closes</strong><small>Recommended so llama-server does not remain mapped in memory.</small></span></label></section>
        <section class="settings-section"><div class="settings-section-head"><div><span class="mini-kicker">FILES & ACCESS</span><h3>Local model sources</h3></div></div><div class="field"><label>Hugging Face access token</label><input id="hfToken" type="password" placeholder="${c.hf_token_configured?'Token already configured · enter only to replace':'hf_…'}"><small>Only needed for gated/private models.</small></div><div class="settings-folder-list">${(c.model_dirs||[]).map(p=>`<div class="detail-row"><span>${icon('folder')} Folder</span><span>${escapeHtml(p)}</span></div>`).join('')||'<div class="empty-mini">No extra model folder configured.</div>'}</div><div class="inline-actions" style="margin-top:14px"><button id="settingsAddFolder" class="secondary-button">${icon('plus')} Add folder</button><button id="saveSettings" class="primary-button">Save all settings</button></div></section></div>
      <section class="section"><div class="inline-actions">${ss.running?`<button id="settingsUnload" class="secondary-button">${icon('stop')} Unload model now</button>`:''}<button id="exitApp" class="danger-button">Exit & unload</button></div></section>
    </div>`;

    const setDraft=(key,value)=>{App.settingsDraft={...(App.settingsDraft||{}),[key]:value};App.settingsFormDirty=true};
    const clearDraft=(keys)=>{const d={...(App.settingsDraft||{})};keys.forEach(k=>delete d[k]);App.settingsDraft=d;App.settingsFormDirty=Object.keys(d).length>0};
    const getAccelFromDom=()=>String($$('#settingsAccelPicker [data-settings-accel].selected')[0]?.dataset.settingsAccel||selectedAccel);
    const modelControlPayload=()=>({
      default_context_size:Number($('#defaultContext').value||4096),
      accelerator_mode:getAccelFromDom(),
      gpu_layer_percent:Number($('#settingsGpuShare').value||35),
      speculative_mode:String($('#speculativeMode')?.value||'auto'),
      adaptive_context:Boolean($('#adaptiveContext')?.checked),
      generation_overrides_enabled:$('#manualGeneration').checked,
      generation_temperature:Number($('#generationTemperature').value||0),
      generation_top_p:Number($('#generationTopP').value||0),
      generation_top_k:Number($('#generationTopK').value||0),
      generation_min_p:Number($('#generationMinP').value||0),
      generation_repeat_penalty:Number($('#generationRepeat').value||1.03),
      generation_max_tokens:Number($('#generationMaxTokens').value||2048),
    });
    const modelKeys=['default_context_size','accelerator_mode','gpu_layer_percent','speculative_mode','adaptive_context','generation_overrides_enabled','generation_temperature','generation_top_p','generation_top_k','generation_min_p','generation_repeat_penalty','generation_max_tokens'];
    const bindDraft=(id,key,parser=(el)=>el.value)=>{const el=$('#'+id);if(!el)return;const mark=()=>{setDraft(key,parser(el));if(id==='defaultContext'){const summary=$('#settingsCtxSummary');if(summary)summary.textContent=Number(el.value||0).toLocaleString();$$('#contextPresets [data-context]').forEach(b=>b.classList.toggle('active',Number(b.dataset.context)===Number(el.value)))}};el.addEventListener('input',mark);el.addEventListener('change',mark)};
    bindDraft('defaultContext','default_context_size',el=>Number(el.value||4096));bindDraft('speculativeMode','speculative_mode',el=>String(el.value||'auto'));bindDraft('adaptiveContext','adaptive_context',el=>el.checked);bindDraft('generationMaxTokens','generation_max_tokens',el=>Number(el.value||2048));bindDraft('manualGeneration','generation_overrides_enabled',el=>el.checked);bindDraft('generationTemperature','generation_temperature',el=>Number(el.value||0));bindDraft('generationTopP','generation_top_p',el=>Number(el.value||0));bindDraft('generationTopK','generation_top_k',el=>Number(el.value||0));bindDraft('generationMinP','generation_min_p',el=>Number(el.value||0));bindDraft('generationRepeat','generation_repeat_penalty',el=>Number(el.value||1.03));bindDraft('ramGuard','max_ram_percent',el=>Number(el.value||88));bindDraft('portSetting','port',el=>Number(el.value||8080));bindDraft('exitUnload','exit_unloads_model',el=>el.checked);bindDraft('idleUnload','idle_unload_minutes',el=>Number(el.value||0));bindDraft('disconnectGrace','ui_disconnect_shutdown_seconds',el=>Number(el.value||12));
    const hf=$('#hfToken');if(hf){const mark=()=>{App.settingsFormDirty=true};hf.addEventListener('input',mark)};

    $$('#contextPresets [data-context]').forEach(b=>b.onclick=()=>{const x=Math.max(512,Math.min(ctxMax,Number(b.dataset.context)));$('#defaultContext').value=String(x);$('#defaultContext').dispatchEvent(new Event('input',{bubbles:true}))});
    const computeButtons=$$('#settingsAccelPicker [data-settings-accel]'),gpuRange=$('#settingsGpuShare'),gpuWrap=$('#settingsGpuShareWrap'),gpuValue=$('#settingsGpuShareValue');
    const paintCompute=mode=>{selectedAccel=mode;computeButtons.forEach(b=>b.classList.toggle('selected',b.dataset.settingsAccel===mode));if(mode==='max_both'&&Number(gpuRange.value)>30)gpuRange.value='10';gpuRange.disabled=!hasGpu||!['hybrid','max_both'].includes(mode);gpuWrap.classList.toggle('active',['hybrid','max_both'].includes(mode));gpuWrap.classList.toggle('inactive',!['hybrid','max_both'].includes(mode));gpuValue.textContent=mode==='gpu'?'100%':['hybrid','max_both'].includes(mode)?`${gpuRange.value}%`:'0%';const summary=$('#settingsComputeSummary');if(summary)summary.textContent=acceleratorLabel(mode);setDraft('accelerator_mode',mode);saveAcceleratorPrefs(mode,Number(gpuRange.value||gpuShare));api('/api/settings',{method:'POST',body:{accelerator_mode:mode,gpu_layer_percent:Number(gpuRange.value||gpuShare)}}).catch(()=>{})};
    computeButtons.forEach(b=>b.onclick=()=>{if(!b.disabled)paintCompute(b.dataset.settingsAccel)});
    gpuRange.oninput=()=>{gpuValue.textContent=['hybrid','max_both'].includes(selectedAccel)?`${gpuRange.value}%`:selectedAccel==='gpu'?'100%':'0%';setDraft('gpu_layer_percent',Number(gpuRange.value));saveAcceleratorPrefs(selectedAccel,Number(gpuRange.value))};

    if($('#runAutotune'))$('#runAutotune').onclick=async()=>{const btn=$('#runAutotune');try{btn.disabled=true;const payload=modelControlPayload();await api('/api/settings',{method:'POST',body:{accelerator_mode:'adaptive',speculative_mode:payload.speculative_mode,adaptive_context:payload.adaptive_context}});saveAcceleratorPrefs('adaptive',payload.gpu_layer_percent);await api('/api/autotune/start',{method:'POST',body:{model_path:active?.path||'',apply_and_start:true}});toast('AutoTune started','The current model will be unloaded while llama-bench measures this exact GGUF.','info',6000);setRoute('advanced');await refreshState(true)}catch(e){toast('AutoTune could not start',e.message,'error',9000)}finally{const b=$('#runAutotune');if(b)b.disabled=false}};
    if($('#clearAutotune'))$('#clearAutotune').onclick=async()=>{try{await api('/api/autotune/clear',{method:'POST',body:{model_path:active?.path||''}});toast('AutoTune result cleared','Adaptive will use its hardware heuristic until you benchmark again.','ok');await refreshState(true)}catch(e){toast('Could not clear AutoTune',e.message,'error')}};
    $('#saveModelControls').onclick=async()=>{try{const payload=modelControlPayload(),saved=await api('/api/settings',{method:'POST',body:payload});if(App.state?.config)Object.assign(App.state.config,payload,saved);saveAcceleratorPrefs(payload.accelerator_mode,payload.gpu_layer_percent);clearDraft(modelKeys);App.renderKey='';await refreshState(true);toast('Model controls saved',`Context ${Number(payload.default_context_size).toLocaleString()} · ${acceleratorLabel(payload.accelerator_mode)}`,'ok',4800)}catch(e){toast('Could not save model controls',e.message,'error')}};
    if($('#settingsLoadModel'))$('#settingsLoadModel').onclick=async()=>{const btn=$('#settingsLoadModel');const old=btn.innerHTML;try{btn.disabled=true;btn.innerHTML=`${icon('runtime')} Preparing engine…`;const payload=modelControlPayload();await api('/api/settings',{method:'POST',body:payload});if(App.state?.config)Object.assign(App.state.config,payload);saveAcceleratorPrefs(payload.accelerator_mode,payload.gpu_layer_percent);clearDraft(modelKeys);await ensureRuntimeInstalled();btn.innerHTML=`${icon('play')} Starting model…`;const cp=cpuThreadPrefs();await api('/api/server/start',{method:'POST',body:{model_path:active.path,profile:(App.state?.assessment?.recommended_profile||'Balanced'),ctx:payload.default_context_size,accelerator_mode:payload.accelerator_mode,cpu_only:payload.accelerator_mode==='cpu',gpu_layer_percent:payload.gpu_layer_percent,thread_mode:payload.accelerator_mode==='adaptive'?'auto':payload.accelerator_mode==='max_both'?'saturate':cp.mode,threads:payload.accelerator_mode==='max_both'?(App.state?.hardware?.logical_cores||cp.threads):cp.threads,threads_batch:payload.accelerator_mode==='max_both'?(App.state?.hardware?.logical_cores||cp.threads_batch):cp.threads_batch,cpu_target_percent:payload.accelerator_mode==='max_both'?100:cp.target_percent,cpu_saturation:payload.accelerator_mode==='max_both'?true:cp.saturation,memory_mode:savedMode,speculative_mode:payload.speculative_mode,adaptive_context:payload.adaptive_context}});toast(loaded?'Reloading model':'Loading model',`${payload.default_context_size.toLocaleString()} context · ${acceleratorLabel(payload.accelerator_mode)}`,'info',5000);await refreshState(true)}catch(e){toast('Could not load model',e.message,'error',9000)}finally{const current=$('#settingsLoadModel');if(current){current.disabled=false;current.innerHTML=old}}};
    if($('#settingsUnloadTop'))$('#settingsUnloadTop').onclick=unloadModel;$('#settingsChooseModel').onclick=()=>setRoute('models');

    const paintMemoryChoice=(mode,dirty=true)=>{App.pendingMemoryMode=mode;App.settingsMemoryDirty=dirty;$$('#memoryModePicker [data-memory-mode]').forEach(btn=>{const active=btn.dataset.memoryMode===mode;btn.classList.toggle('selected',active);btn.setAttribute('aria-pressed',active?'true':'false')});const help=$('#memoryModeHelp'),status=$('#memoryModeStatus'),apply=$('#applyMemoryMode'),cancel=$('#cancelMemoryMode');if(help)help.textContent=memoryHelp(mode);if(status)status.textContent=dirty?`Not applied yet: ${memoryLabel(mode)}`:`Saved mode: ${memoryLabel(mode)}`;if(apply)apply.disabled=!dirty;if(cancel)cancel.disabled=!dirty};
    $$('#memoryModePicker [data-memory-mode]').forEach(btn=>btn.onclick=()=>paintMemoryChoice(btn.dataset.memoryMode,true));$('#cancelMemoryMode').onclick=()=>{App.pendingMemoryMode=null;App.settingsMemoryDirty=false;paintMemoryChoice(savedMode,false)};
    $('#applyMemoryMode').onclick=async()=>{const requested=App.pendingMemoryMode||savedMode,button=$('#applyMemoryMode'),status=$('#memoryModeStatus');button.disabled=true;if(status)status.textContent=`Applying: ${memoryLabel(requested)}…`;try{const saved=await api('/api/settings',{method:'POST',body:{model_memory_mode:requested}});const actual=saved.model_memory_mode;if(actual!==requested)throw new Error(`Backend saved ${actual||'nothing'} instead of ${requested}`);if(App.state?.config)App.state.config.model_memory_mode=actual;App.pendingMemoryMode=null;App.settingsMemoryDirty=false;paintMemoryChoice(actual,false);App.renderKey=stateRenderKey(App.state||{});toast('Memory mode applied',`${memoryLabel(actual)} will be used on the next model load.`)}catch(e){App.pendingMemoryMode=requested;App.settingsMemoryDirty=true;paintMemoryChoice(requested,true);toast('Could not apply memory mode',e.message,'error')}};
    $('#saveSettings').onclick=async()=>{try{const mm=App.settingsMemoryDirty?(App.pendingMemoryMode||savedMode):savedMode,payload={...modelControlPayload(),model_memory_mode:mm,max_ram_percent:Number($('#ramGuard').value),port:Number($('#portSetting').value),exit_unloads_model:$('#exitUnload').checked,idle_unload_minutes:Number($('#idleUnload').value),ui_disconnect_shutdown_seconds:Number($('#disconnectGrace').value),...(($('#hfToken').value||'').trim()?{hf_token:$('#hfToken').value.trim()}:{})};await api('/api/settings',{method:'POST',body:payload});saveAcceleratorPrefs(payload.accelerator_mode,payload.gpu_layer_percent);App.settingsDraft={};App.pendingMemoryMode=null;App.settingsMemoryDirty=false;App.settingsFormDirty=false;await refreshState(true);toast('Settings saved',`${acceleratorLabel(payload.accelerator_mode)} · context ${Number(payload.default_context_size).toLocaleString()}`)}catch(e){toast('Could not save settings',e.message,'error')}};
    $('#settingsAddFolder').onclick=async()=>{try{const p=await api('/api/dialog/folder');if(!p.path)return;await api('/api/models/add-folder',{method:'POST',body:{path:p.path}});await refreshState(true);render()}catch(e){toast('Could not add folder',e.message,'error')}};if($('#settingsUnload'))$('#settingsUnload').onclick=unloadModel;$('#exitApp').onclick=exitLlamaForge;
  }

  function renderBrain(view){
    const s=App.state||{}, b=s.brain||{}, m=s.active_model, j=b.job||{};
    const busy=j.state==='running'||j.state==='cancelling';
    const setupReady=!!b.setup_ready;
    const modelReady=!!m;
    const baseReady=!!b.training_base_ready;
    const trainerReady=!!b.trainer_ready && !!b.toolchain_ready;
    const learned=!!b.adapter_ready;
    const pct=Math.max(0,Math.min(100,Number(j.progress||0)*100));
    const statusTitle=!b.enabled?'Personal Brain is off':busy?'Setting up your Brain…':setupReady?(learned?'Learning is active':'Ready to learn'):'One-time setup needed';
    const statusCopy=!b.enabled
      ? 'Turn it on and LlamaForge will guide the rest. No chat history, RAG, or memory text is injected when Zero-context is active.'
      : j.state==='cancelling' ? (j.message||'Stopping the current Brain task safely…')
      : busy ? (j.message||'Preparing the learning engine…')
      : setupReady ? `Just chat normally. After each completed turn, LlamaForge trains private personal weights for <strong>${escapeHtml(m?.name||'this model')}</strong> before the next turn.`
      : 'LlamaForge can detect the matching trainable model and prepare the learning engine automatically.';

    view.innerHTML=`<div class="page brain-page brain-simple">
      ${pageTitle('PERSONAL BRAIN','Teach by chatting','Keep the normal chat simple. LlamaForge handles the training plumbing in the background.')}

      <section class="brain-simple-hero ${setupReady?'ready':b.enabled?'armed':'off'}">
        <div class="brain-simple-copy">
          <div class="brain-orb">${icon('brain')}</div>
          <div><div class="eyebrow">WEIGHT LEARNING · ZERO-CONTEXT</div><h2>${escapeHtml(statusTitle)}</h2><p>${statusCopy}</p></div>
        </div>
        <button id="brainPower" class="brain-power-switch ${b.enabled?'on':'off'}" type="button" role="switch" aria-checked="${b.enabled?'true':'false'}" aria-label="${b.enabled?'Turn Personal Brain off':'Turn Personal Brain on'}" ${App.brainTogglePending?'disabled':''}><span class="brain-power-track"><i></i></span><strong>${App.brainTogglePending?'…':b.enabled?'ON':'OFF'}</strong></button>
      </section>

      <section class="brain-readiness">
        <div class="brain-ready-step ${modelReady?'done':''}"><span class="step-dot">${modelReady?icon('check'):'1'}</span><div><strong>Chat model</strong><small>${modelReady?escapeHtml(shortName(m.name,34)):'Choose a GGUF model'}</small></div></div>
        <div class="step-line ${modelReady&&baseReady?'done':''}"></div>
        <div class="brain-ready-step ${baseReady?'done':''}"><span class="step-dot">${baseReady?icon('check'):'2'}</span><div><strong>Same model · learning files</strong><small>${baseReady?escapeHtml(shortName(b.training_source_repo||b.training_base,38)):'Matched automatically from the selected model'}</small></div></div>
        <div class="step-line ${baseReady&&trainerReady?'done':''}"></div>
        <div class="brain-ready-step ${trainerReady?'done':''}"><span class="step-dot">${trainerReady?icon('check'):'3'}</span><div><strong>Learning engine</strong><small>${trainerReady?'Ready':'Prepared automatically'}</small></div></div>
      </section>

      ${busy||j.state==='cancelling'||j.state==='cancelled'||j.state==='error'||j.state==='done'?`<section class="brain-job ${j.state}" data-brain-job-state="${escapeHtml(j.state||'')}"><div class="brain-job-top"><div><span class="mini-kicker" data-brain-job-stage>${escapeHtml((j.stage||'brain').toUpperCase())}</span><strong data-brain-job-message>${escapeHtml(j.message||j.state)}</strong></div><div class="brain-job-right"><span data-brain-job-percent>${Math.round(pct)}%</span>${(busy||j.state==='cancelling')?`<button id="brainCancelJob" class="compact-button" ${j.state==='cancelling'?'disabled':''}>${icon('stop')} ${j.state==='cancelling'?'Stopping…':'Stop'}</button>`:''}</div></div><div class="brain-progress"><i data-brain-job-progress style="width:${pct}%"></i></div><div class="brain-transfer-meta" data-brain-transfer ${Number(j.total||0)>0?'':'hidden'}>${Number(j.total||0)>0?`<span>${formatBytes(j.done||0)} / ${formatBytes(j.total||0)}</span>${Number(j.bytes_per_sec||0)>0?`<span>${formatBytes(j.bytes_per_sec)}/s</span>`:''}${j.eta_seconds!=null?`<span>ETA ${formatEta(j.eta_seconds)}</span>`:''}`:''}</div>${j.error?`<p data-brain-job-error>${escapeHtml(j.error)}</p>`:''}</section>`:''}

      <section class="brain-action-card">
        <div class="brain-action-main">
          <span class="brain-action-icon">${setupReady?icon('check'):icon('spark')}</span>
          <div>
            <strong>${setupReady?'Everything is ready':'Let LlamaForge set it up'}</strong>
            <p>${setupReady?'Your next chats can update model weights automatically. Previous turns stay out of inference context.':'One button detects the exact Hugging Face base, prepares PyTorch/PEFT, and arms safe zero-context learning. First setup may download several GB.'}</p>
          </div>
        </div>
        <div class="brain-main-actions">
          ${!modelReady?`<button id="brainChooseModel" class="primary-button">${icon('models')} Choose model</button>`:''}
          ${modelReady&&!setupReady?`<button id="brainAutoSetup" class="primary-button" ${busy?'disabled':''}>${icon('spark')} Prepare learning for this model</button>`:''}
          ${setupReady?`<button id="brainOpenChat" class="primary-button">${icon('chat')} Open chat</button>`:''}
          <button id="brainDoctor" class="secondary-button">${icon('info')} Diagnose</button>
        </div>
        <div class="brain-storage-note">${icon('folder')} <span>Training models are stored beside LlamaForge:</span><strong>${escapeHtml(b.training_models_root||'')}</strong></div>
      </section>

      <section class="brain-simple-grid">
        <div class="brain-simple-card"><span class="card-kicker">HOW IT WORKS</span><h3>You talk. It learns. Context resets.</h3>
          <div class="brain-flow"><div><span>1</span><p><strong>Chat normally</strong><small>You send a message and get the usual local response.</small></p></div><i></i><div><span>2</span><p><strong>Weights update</strong><small>LlamaForge creates training examples and micro-trains your private LoRA.</small></p></div><i></i><div><span>3</span><p><strong>Next turn starts clean</strong><small>Only the newest user message is sent to inference.</small></p></div></div>
        </div>
        <div class="brain-simple-card"><span class="card-kicker">CURRENT BRAIN</span><h3>${learned?`Generation ${Number(b.generation||0)}`:'No learned weights yet'}</h3>
          <div class="brain-mini-stats"><div><span>Previous turns sent</span><strong>${b.enabled&&b.zero_context?'0':'Normal chat'}</strong></div><div><span>Confirmed learned turns</span><strong>${Number(b.learned_packets||0)}</strong></div><div><span>Mode</span><strong>${b.enabled?(setupReady?'Automatic':'Setup needed'):'Off'}</strong></div></div>
        </div>
      </section>

      <details class="brain-advanced-disclosure">
        <summary><span>${icon('tune')}</span><div><strong>Advanced learning settings</strong><small>Normally you do not need these.</small></div>${icon('down')}</summary>
        <div class="brain-advanced-body">
          <div class="brain-two-col">
            <section class="brain-panel"><div class="brain-panel-head"><div><span class="mini-kicker">BEHAVIOR</span><h3>Learning contract</h3></div></div>
              <label class="brain-check"><input id="brainZero" type="checkbox" ${b.zero_context?'checked':''}><span><strong>Zero-context inference</strong><small>Send only the latest user turn to llama-server.</small></span></label>
              <label class="brain-check"><input id="brainStrict" type="checkbox" ${b.strict_learning?'checked':''}><span><strong>Learn before next turn</strong><small>Wait for weight training before accepting the next message.</small></span></label>
              <label class="brain-check"><input id="brainSynthesize" type="checkbox" ${b.auto_synthesize?'checked':''}><span><strong>Generate durable examples</strong><small>Expand facts, people, plans and preferences into training examples.</small></span></label>
            </section>
            <section class="brain-panel"><div class="brain-panel-head"><div><span class="mini-kicker">MODEL SOURCE</span><h3>Managed automatically</h3></div></div>
              <div class="detail-row"><span>Selected model</span><span>${escapeHtml(b.selected_model_name||m?.name||'—')}</span></div>
              <div class="detail-row"><span>Learning source</span><span style="max-width:68%;overflow-wrap:anywhere">${escapeHtml(b.training_source_repo||b.training_base||'Will be matched automatically')}</span></div>
              <p style="color:var(--muted);font-size:10.5px;line-height:1.55">You do not choose a second model. LlamaForge binds the matching Transformers/PEFT files to the model selected in Library and keeps underlying base checkpoints as internal dependencies.</p>
              <div class="inline-actions"><button id="brainDetectBase" class="secondary-button">${icon('refresh')} Repair model link</button><button id="brainPrepare" class="secondary-button" ${busy?'disabled':''}>${icon('refresh')} Verify engine</button></div>
              <div class="field" style="margin-top:12px"><label>Training device</label><select id="brainDevice"><option value="auto">Auto</option><option value="cuda">NVIDIA CUDA</option><option value="cpu">CPU</option><option value="xpu">Intel XPU</option><option value="mps">Apple MPS</option></select></div>
              <label class="brain-check" style="margin-top:12px"><input id="brainRemoteCode" type="checkbox" ${b.allow_remote_code?'checked':''}><span><strong>Allow repository Python code</strong><small>Off by default. Enable only for a trusted model that explicitly requires custom Transformers code.</small></span></label>
            </section>
          </div>
          <section class="brain-panel advanced-brain"><div class="brain-panel-head"><div><span class="mini-kicker">MICRO-TRAINING</span><h3>Fine tuning</h3></div><span class="tag">PEFT LoRA</span></div>
            <div class="brain-param-grid">
              <div class="field"><label>LoRA rank</label><input id="brainRank" type="number" min="2" max="128" value="${b.rank||8}"></div>
              <div class="field"><label>Micro steps / turn</label><input id="brainSteps" type="number" min="1" max="128" value="${b.micro_steps||8}"></div>
              <div class="field"><label>Replay examples</label><input id="brainReplay" type="number" min="0" max="128" value="${b.replay_samples||8}"></div>
              <div class="field"><label>Max training tokens</label><input id="brainMaxLen" type="number" min="64" max="2048" value="${b.max_length||384}"></div>
              <div class="field"><label>Learning rate</label><input id="brainLR" type="number" step="0.00001" min="0.000001" max="0.01" value="${b.learning_rate||0.00015}"></div>
              <div class="field"><label>LoRA alpha</label><input id="brainAlpha" type="number" min="2" max="256" value="${b.alpha||16}"></div>
            </div>
            <div class="brain-bake-row"><div><strong>Optional: bake a standalone GGUF</strong><p>Normal daily learning uses Base GGUF + Personal LoRA. Bake only when you want a separate merged file.</p></div><div class="inline-actions"><button id="brainSaveAdvanced" class="secondary-button">Save advanced</button><button id="brainBake" class="secondary-button" ${!b.adapter_ready||busy?'disabled':''}>${icon('harddrive')} Bake GGUF</button></div></div>
          </section>
        </div>
      </details>
    </div>`;

    const device=$('#brainDevice'); if(device)device.value=b.device||'auto';
    const collect=()=>({
      enabled:b.enabled,
      zero_context:$('#brainZero')?.checked??b.zero_context,
      strict_learning:$('#brainStrict')?.checked??b.strict_learning,
      auto_synthesize:$('#brainSynthesize')?.checked??b.auto_synthesize,
      allow_remote_code:$('#brainRemoteCode')?.checked??b.allow_remote_code,
      training_base:$('#brainBase')?.value?.trim()??b.training_base,
      device:$('#brainDevice')?.value||b.device||'auto',
      rank:Number($('#brainRank')?.value||b.rank||8),alpha:Number($('#brainAlpha')?.value||b.alpha||16),
      learning_rate:Number($('#brainLR')?.value||b.learning_rate||0.00015),micro_steps:Number($('#brainSteps')?.value||b.micro_steps||8),
      replay_samples:Number($('#brainReplay')?.value||b.replay_samples||8),max_length:Number($('#brainMaxLen')?.value||b.max_length||384)
    });
    const applyBrainState=(x,{renderNow=false}={})=>{if(!x)return x;if(App.state)App.state.brain=x;patchBrainChatState(x);patchBrainPageState(x);if(renderNow&&App.route==='brain')renderBrain($('#view'));return x};
    const save=async(body=collect(),quiet=false,renderNow=false)=>{try{const x=await api('/api/brain/settings',{method:'POST',body});applyBrainState(x,{renderNow});if(!quiet)toast('Brain settings saved');return x}catch(e){toast('Could not save Brain settings',e.message,'error',6500);throw e}};
    const runAutoSetup=async({confirmDownload=false}={})=>{
      if(!modelReady){setRoute('models');return}
      if(confirmDownload){const ok=await confirmModal('Set up Personal Brain automatically','LlamaForge will detect the exact trainable base for this GGUF and prepare an isolated PyTorch/PEFT learning environment. The first setup can download several GB. Nothing is installed during normal startup.','Set up automatically');if(!ok)return}
      try{const st=await api('/api/brain/autosetup',{method:'POST',body:{}});applyBrainState(st,{renderNow:true});toast('Automatic setup started','LlamaForge is preparing everything in the background. You can leave this page.','info',5000)}catch(e){toast('Automatic setup could not start',e.message,'error',7500)}
    };
    if($('#brainChooseModel'))$('#brainChooseModel').onclick=()=>setRoute('models');
    if($('#brainOpenChat'))$('#brainOpenChat').onclick=()=>setRoute('chat');
    if($('#brainAutoSetup'))$('#brainAutoSetup').onclick=()=>runAutoSetup({confirmDownload:true});
    if($('#brainLibrary'))$('#brainLibrary').onclick=()=>showTrainableLibrary();
    if($('#brainDoctor'))$('#brainDoctor').onclick=()=>showBrainDoctor();
    if($('#brainCancelJob'))$('#brainCancelJob').onclick=async()=>{try{const st=await api('/api/brain/cancel',{method:'POST',body:{}});applyBrainState(st,{renderNow:true});toast('Stopping Brain task','The current operation will stop at the next safe point.','info',3200)}catch(e){toast('Could not stop Brain task',e.message,'error',6000)}};
    const power=$('#brainPower');
    if(power)power.onclick=async()=>{
      if(App.brainTogglePending)return;
      const requested=!Boolean(App.state?.brain?.enabled);
      App.brainTogglePending=true;
      power.disabled=true;power.classList.add('pending');
      const powerText=$('strong',power);if(powerText)powerText.textContent='…';
      try{
        const st=await api('/api/brain/toggle',{method:'POST',body:{enabled:requested}});
        if(App.state)App.state.brain=st;patchBrainChatState(st);patchBrainPageState(st);
        if(requested&&!st.setup_ready){
          toast('Personal Brain on','Automatic setup is starting.','info',2600);
          App.brainTogglePending=false;
          if(App.route==='brain')renderBrain(view);
          await runAutoSetup({confirmDownload:false});
          return;
        }
        toast(requested?'Personal Brain on':'Personal Brain off',requested?'Learning will run after each completed turn.':(st.job?.state==='cancelling'?'The active Brain task is stopping safely.':'New learning is disabled.'));
      }catch(err){
        toast('Could not change Brain power',err.message,'error',7000);
      }finally{
        App.brainTogglePending=false;
        if(App.route==='brain')renderBrain(view);
      }
    };
    if($('#brainBrowseBase'))$('#brainBrowseBase').onclick=async()=>{try{const p=await api('/api/dialog/folder');if(p.path)$('#brainBase').value=p.path}catch(e){toast('Folder picker failed',e.message,'error')}};
    if($('#brainDetectBase'))$('#brainDetectBase').onclick=()=>runAutoSetup({confirmDownload:false});
    if($('#brainPrepare'))$('#brainPrepare').onclick=async()=>{try{await save(collect(),true);await api('/api/brain/prepare',{method:'POST',body:{force:false}});toast('Engine verification started','','info')}catch(e){toast('Could not verify learning engine',e.message,'error',7000)}};
    if($('#brainSaveAdvanced'))$('#brainSaveAdvanced').onclick=()=>save();
    if($('#brainBake'))$('#brainBake').onclick=async()=>{const ok=await confirmModal('Bake a standalone GGUF','This creates a new merged GGUF and does not overwrite your original model.','Bake GGUF');if(!ok)return;try{await api('/api/brain/bake',{method:'POST',body:{}});toast('GGUF bake started','','info')}catch(e){toast('Could not start bake',e.message,'error',7000)}};
  }


  async function showBrainDoctor(){
    const root=$('#modalRoot');
    root.innerHTML=`<div class="modal-backdrop"><div class="modal wide-modal"><div class="library-head"><div><div class="eyebrow">BRAIN DIAGNOSTICS</div><h3>Checking the complete learning path…</h3></div><button class="round-icon" data-close>${icon('x')}</button></div><div class="doctor-body"><div class="library-loading">${icon('refresh')} Running non-destructive checks…</div></div></div></div>`;
    $('[data-close]',root).onclick=()=>root.innerHTML='';
    try{
      const r=await api('/api/brain/doctor');
      const body=$('.doctor-body',root); if(!body)return;
      const rows=(r.checks||[]).map(c=>`<div class="doctor-check ${c.ok?'ok':c.level==='warn'?'warn':'bad'}"><span>${c.ok?icon('check'):c.level==='warn'?icon('info'):icon('alert')}</span><div><strong>${escapeHtml(c.name.replaceAll('_',' '))}</strong><small>${escapeHtml(c.detail||'')}</small></div></div>`).join('');
      body.innerHTML=`<div class="doctor-summary ${r.ok?'ok':'bad'}"><strong>${r.ok?'Brain path is ready':'Brain path needs attention'}</strong><span>${escapeHtml(r.base||'No trainable base linked')}</span></div><div class="doctor-list">${rows}</div><div class="inline-actions" style="margin-top:16px"><button id="copyDoctor" class="secondary-button">${icon('copy')} Copy report</button><button id="openDoctorLogs" class="secondary-button">${icon('logs')} Open full logs</button></div>`;
      $('#copyDoctor',root).onclick=async()=>{await navigator.clipboard.writeText(JSON.stringify(r,null,2));toast('Brain report copied')};
      $('#openDoctorLogs',root).onclick=()=>{root.innerHTML='';setRoute('logs')};
    }catch(e){const body=$('.doctor-body',root);if(body)body.innerHTML=`<div class="empty-state">${icon('alert')}<strong>Diagnostics failed</strong><p>${escapeHtml(e.message)}</p></div>`}
  }

  function trainableCard(r){
    const size=Number(r.weight_gb||0); const tags=[];
    if(r.architecture)tags.push(r.architecture); if(r.has_safetensors)tags.push('safetensors'); if(r.gated)tags.push('gated'); if(r.requires_remote_code)tags.push('remote code');
    return `<article class="trainable-card ${r.trainable?'':'not-trainable'}" data-repo="${escapeHtml(r.repo_id||'')}"><div class="trainable-card-top"><div><strong>${escapeHtml(r.repo_id||'Unknown')}</strong><small>${escapeHtml(r.reason||'')}</small></div><span class="trainable-state ${r.trainable?'ok':'bad'}">${r.trainable?'Trainable':'Not trainable'}</span></div><div class="tag-row">${tags.map(x=>`<span class="tag">${escapeHtml(x)}</span>`).join('')}${size?`<span class="tag">${size.toFixed(1)} GB weights</span>`:''}<span class="tag">${Number(r.downloads||0).toLocaleString()} downloads</span></div><div class="trainable-actions">${r.downloaded?`<button class="primary-button use-trainable" data-path="${escapeHtml(r.local_dir||'')}">${icon('check')} Use downloaded</button>`:r.trainable?`<button class="primary-button download-trainable" data-repo="${escapeHtml(r.repo_id||'')}">${icon('download')} Download & use</button>`:''}<button class="text-button inspect-trainable" data-repo="${escapeHtml(r.repo_id||'')}">Details</button></div></article>`;
  }

  async function showTrainableLibrary(){
    const root=$('#modalRoot'); const m=App.state?.active_model;
    const suggested=((m?.name||'').replace(/[-_]/g,' ').split(/\s+/).slice(0,3).join(' ')||m?.architecture||'gemma').trim();
    root.innerHTML=`<div class="modal-backdrop"><div class="modal library-modal"><div class="library-head"><div><div class="eyebrow">TRAINABLE MODEL LIBRARY</div><h3>Choose what the Personal Brain actually trains</h3><p>GGUF stays the fast inference copy. Downloads are stored one folder above LlamaForge in <strong>${escapeHtml(App.state?.brain?.training_models_root||'LlamaForgeModels')}</strong>.</p></div><button class="round-icon" data-close>${icon('x')}</button></div><div class="library-search"><span>${icon('search')}</span><input id="trainableSearch" value="${escapeHtml(suggested)}" placeholder="Search Hugging Face trainable models"><button id="trainableGo" class="primary-button">Search</button></div><div id="trainableResults" class="trainable-results"><div class="library-loading">${icon('search')} Search to inspect trainable checkpoints.</div></div></div></div>`;
    $('[data-close]',root).onclick=()=>root.innerHTML='';
    $('.modal-backdrop',root).onclick=e=>{if(e.target===e.currentTarget)root.innerHTML=''};
    const results=$('#trainableResults',root), input=$('#trainableSearch',root);
    const bind=()=>{
      $$('.download-trainable',results).forEach(b=>b.onclick=async()=>{const repo=b.dataset.repo;const ok=await confirmModal('Download trainable checkpoint',`LlamaForge will download the required training bundle for ${repo} (adapter and/or its base model). This can be several GB. The GGUF inference model is not replaced.`,'Download & use');if(!ok)return;try{await api('/api/brain/catalog/download',{method:'POST',body:{repo}});root.innerHTML='';setRoute('brain');toast('Training model download started',repo,'info',5000)}catch(e){toast('Could not start download',e.message,'error',7000)}});
      $$('.use-trainable',results).forEach(b=>b.onclick=async()=>{try{await api('/api/brain/catalog/use',{method:'POST',body:{value:b.dataset.path}});root.innerHTML='';setRoute('brain');toast('Training model linked',b.dataset.path)}catch(e){toast('Could not link model',e.message,'error')}});
      $$('.inspect-trainable',results).forEach(b=>b.onclick=async()=>{try{const d=await api('/api/brain/catalog/inspect?repo='+encodeURIComponent(b.dataset.repo));toast(d.repo_id,`${d.reason} · ${Number(d.weight_gb||0).toFixed(1)} GB weights${d.gated?' · gated':''}`,d.trainable?'info':'error',7000)}catch(e){toast('Could not inspect model',e.message,'error')}});
    };
    const search=async()=>{const q=input.value.trim();results.innerHTML=`<div class="library-loading">${icon('refresh')} Inspecting Hugging Face model cards…</div>`;try{const [local,x]=await Promise.all([api('/api/brain/catalog/local').catch(()=>({results:[]})),api('/api/brain/catalog/search?q='+encodeURIComponent(q)+'&limit=12')]);const seen=new Set(),rows=[...(local.results||[]),...(x.results||[])].filter(r=>{const k=r.repo_id||r.local_dir;if(!k||seen.has(k))return false;seen.add(k);return true});results.innerHTML=rows.length?rows.map(trainableCard).join(''):`<div class="empty-state">No matching checkpoints found.</div>`;bind()}catch(e){results.innerHTML=`<div class="empty-state">${icon('alert')}<strong>Search failed</strong><p>${escapeHtml(e.message)}</p></div>`}};
    $('#trainableGo',root).onclick=search; input.onkeydown=e=>{if(e.key==='Enter')search()};
    search();
  }


  function agentEnabled(){
    const saved=localStorage.getItem('lf.agentMode');
    return saved===null?!!App.state?.config?.agent_enabled_default:saved==='1';
  }
  function setAgentEnabled(on){
    localStorage.setItem('lf.agentMode',on?'1':'0');
    if(App.route==='chat')renderChat($('#view'));
  }

  async function showBridgeRollback(appId){
    try{
      const st=await api('/api/agent/app/bridge-status',{method:'POST',body:{id:appId}});
      const backups=st?.remote?.backups||[];
      if(!backups.length){toast('No Bridge backup available','Update the Bridge at least once before rollback.','info',5200);return}
      const root=$('#modalRoot');
      root.innerHTML=`<div class="modal-backdrop"><div class="modal premium-modal"><div class="modal-kicker">WEB BRIDGE ROLLBACK</div><h3>Choose a backup</h3><p>The website data/config and chat history stay untouched. Only Bridge code is restored.</p><div class="agent-connector-list">${backups.map(b=>`<div class="agent-connector"><div><strong>${escapeHtml(b.from_version||'previous version')}</strong><small>${escapeHtml(b.created_at||'')} · backup ${escapeHtml(b.id||'')}</small></div><button class="secondary-button do-bridge-rollback" data-backup="${escapeHtml(b.id||'')}">Restore</button></div>`).join('')}</div><div class="inline-actions"><button class="secondary-button" data-no>Cancel</button></div></div></div>`;
      $('[data-no]',root).onclick=()=>root.innerHTML='';
      $$('.do-bridge-rollback',root).forEach(b=>b.onclick=async()=>{const backup_id=b.dataset.backup;const ok=await confirmModal('Rollback Web Bridge',`Restore Bridge code from ${backup_id}? Website messages and config are preserved.`,'Rollback',true);if(!ok)return;try{await api('/api/agent/app/rollback-bridge',{method:'POST',body:{id:appId,backup_id}});root.innerHTML='';await refreshState(true);toast('Web Bridge rolled back','The connected website is now using the selected backup.','ok',6000)}catch(e){toast('Rollback failed',e.message,'error',8000)}});
    }catch(e){toast('Could not read Bridge backups',e.message,'error',7000)}
  }

  async function renderAgent(view){
    let a=App.state?.agent||{},c=App.state?.config||{};
    try{a=await api('/api/agent/status');if(App.state)App.state.agent=a}catch{}
    const connectors=a.connectors||[],skills=a.skills||[],installer=a.installer||{},catalog=a.skill_catalog||[];
    const remoteInfo=a.remote_apps||{},remoteApps=remoteInfo.apps||[];
    const opCount=connectors.reduce((n,x)=>n+(x.operations||[]).length,0);
    const familyDefs={
      web:{title:'Live Web',icon:'globe',desc:'Search, read, check and download current public information.',cats:['web.read','web.search','web.download']},
      calendar:{title:'Time & Calendar',icon:'calendar',desc:'Real local date/time, Jalali conversion, schedules and reminders.',cats:['calendar']},
      files:{title:'Files & Attachments',icon:'files',desc:'Read uploaded files, inspect ZIP projects, save, organize and edit text/code.',cats:['files']},
      browser:{title:'Browser',icon:'panel',desc:'Escalation for JavaScript pages, login, forms, clicks and typing.',cats:['browser.read','browser.interact','browser.session']},
      api:{title:'API',icon:'link',desc:'HTTP endpoints, structured requests and configured service operations.',cats:['api']},
      extensions:{title:'Extensions',icon:'spark',desc:'OpenAPI connectors and custom declarative skills.',cats:['connector','custom']}
    };
    const familyOrder=['web','calendar','files','browser','api','extensions'];
    const skillCatalogHTML=familyOrder.map(key=>{
      const f=familyDefs[key],rows=catalog.filter(x=>f.cats.includes(x.category||'custom'));
      const available=rows.filter(x=>x.available).length;
      const state=rows.length?(available===rows.length?'Ready':available?'Partial':'Unavailable'):'Not configured';
      const detailRows=rows.length?rows.map(x=>`<div class="skill-leaf ${x.available?'available':'unavailable'}"><div><strong>${escapeHtml(x.title||x.name)}</strong><code>${escapeHtml(x.name)}</code></div><div class="skill-leaf-meta"><span class="skill-risk ${escapeHtml(x.risk||'read')}">${escapeHtml(x.risk||'read')}</span><span class="skill-dot ${x.available?'on':'off'}"></span></div>${x.available?'':`<small>${escapeHtml(x.unavailable_reason||'Unavailable')}</small>`}</div>`).join(''):'<div class="skill-empty">No extra setup required yet.</div>';
      return `<details class="skill-family-card family-${key}" ${key==='calendar'||key==='files'?'open':''}><summary><span class="skill-family-icon">${icon(f.icon)}</span><span class="skill-family-copy"><strong>${escapeHtml(f.title)}</strong><small>${escapeHtml(f.desc)}</small></span><span class="skill-family-state ${state.toLowerCase().replace(' ','-')}">${escapeHtml(state)}</span><span class="skill-family-count">${available}/${rows.length}</span>${icon('down')}</summary><div class="skill-leaves">${detailRows}</div></details>`;
    }).join('');
    view.innerHTML=`<div class="page agent-page">${pageTitle('LOCAL AGENT','Smart Skills','The model chooses broad capabilities automatically: live web, calendar/time, files, APIs and browser actions. Local files and calendar stay on this LlamaForge workspace.')}
      <section class="agent-hero"><div><span class="agent-orb">${icon('globe')}</span><div><h3>Agent runtime ${a.ready?'ready':'needs attention'}</h3><p>${(a.builtin_tools||[]).length} built-in tools · ${skills.length} custom skills · ${remoteApps.length} connected apps · ${connectors.length} API connectors / ${opCount} operations</p></div></div><button id="agentOpenChat" class="primary-button">${icon('chat')} Open chat</button></section>
      <div class="detail-grid agent-grid">
        <div class="detail-card"><h3>Agent behavior</h3>
          <label class="check-row"><input id="agentDefault" type="checkbox" ${c.agent_enabled_default?'checked':''}><span><strong>Agent on by default</strong><small>New/local chat sessions start with automatic capability routing.</small></span></label>
          <label class="check-row"><input id="agentWorkspaceWrite" type="checkbox" ${c.agent_allow_workspace_write!==false?'checked':''}><span><strong>Allow local calendar & file changes</strong><small>Recommended. Lets the Agent create reminders, save attachments and edit workspace text/code. This does not grant websites permission.</small></span></label>
          <label class="check-row"><input id="agentWrite" type="checkbox" ${c.agent_allow_write?'checked':''}><span><strong>Allow external site actions</strong><small>Separate higher-risk permission for POST/PUT/DELETE, browser clicks, typing and form submission on remote sites.</small></span></label>
          <label class="check-row"><input id="agentPrivate" type="checkbox" ${c.agent_allow_private_network?'checked':''}><span><strong>Allow localhost/private network</strong><small>Needed only for LAN services or local APIs. Public internet works without it.</small></span></label>
          <label class="check-row"><input id="agentHeadless" type="checkbox" ${c.agent_browser_headless?'checked':''}><span><strong>Hide Agent browser window</strong><small>Off is easier to inspect: you can watch Chrome interact with the site.</small></span></label>
          <div class="field" style="margin-top:12px"><label>Maximum tool steps per message</label><input id="agentSteps" type="number" min="1" max="16" value="${Number(c.agent_max_steps||8)}"></div>
          <div class="inline-actions" style="margin-top:14px"><button id="saveAgentSettings" class="primary-button">Save Agent settings</button></div>
        </div>
        <div class="detail-card"><h3>Browser skill</h3><p style="color:var(--muted);font-size:10.5px;line-height:1.55">For normal pages the Agent uses lightweight HTTP tools. For JavaScript apps, forms and buttons it can drive Chrome with Selenium.</p>
          <div class="detail-row"><span>Python browser package</span><strong>${a.browser_available?'Installed':'Not installed'}</strong></div><div class="detail-row"><span>Browser session</span><strong>${a.browser_running?'Running':'Closed'}</strong></div>
          <div class="inline-actions" style="margin-top:14px">${a.browser_available?`<button id="closeAgentBrowser" class="secondary-button" ${a.browser_running?'':'disabled'}>${icon('stop')} Close browser</button>`:`<button id="installAgentBrowser" class="primary-button" ${installer.state==='running'?'disabled':''}>${icon('download')} ${installer.state==='running'?'Installing…':'Install browser skill'}</button>`}</div>
          ${installer.error?`<p class="agent-error">${escapeHtml(installer.error)}</p>`:''}<small style="display:block;margin-top:10px">Chrome uses a separate persistent LlamaForge profile, so logins/cookies can survive between Agent sessions.</small>
        </div>
      </div>
      <section class="section"><div class="detail-card"><h3>Connected websites / apps</h3><p style="color:var(--muted);font-size:10.5px;line-height:1.65">Paste the <b>LlamaForge Connection URL</b> generated by your website/app. Once connected, LlamaForge watches it in the background while a local model is ready, runs the normal Agent + Skill system for incoming tasks, and sends live execution activity plus the final answer back to the website.</p>
        <div class="form-grid agent-connector-form"><div class="field"><label>Connection URL</label><input id="remoteAppUrl" placeholder="https://example.com/ai/connect.php?token=..."></div><div class="field"><label>Token (optional)</label><input id="remoteAppToken" type="password" placeholder="Leave blank when token is inside URL"></div></div>
        <div class="inline-actions" style="margin-top:12px"><button id="addRemoteApp" class="primary-button">${icon('plus')} Connect website/app</button><button id="refreshRemoteApps" class="secondary-button">Refresh status</button></div>
        <div class="agent-connector-list">${remoteApps.length?remoteApps.map(x=>`<div class="agent-connector remote-app-card"><div><strong>${escapeHtml(x.name||'Connected app')}</strong><small>${escapeHtml(x.base_url||x.connect_url||'')} · <span class="remote-state ${escapeHtml(x.state||'')}">${escapeHtml(x.state||'unknown')}</span>${x.active_message_id?` · working`:''}</small>${x.last_error?`<small class="agent-error">${escapeHtml(x.last_error)}</small>`:''}<div class="tag-row"><span class="tag">${x.enabled?'enabled':'paused'}</span><span class="tag">Bridge ${escapeHtml(x.remote_version||'unknown')}</span><span class="tag">${Number(x.tasks_completed||0)} completed</span><span class="tag">token ${x.token_configured?'configured':'missing'}</span><span class="tag">${x.bridge_update_supported?'remote updater ready':'manual bootstrap needed'}</span></div></div><div class="inline-actions"><button class="secondary-button test-remote-app" data-id="${escapeHtml(x.id)}">Test</button>${x.bridge_update_supported?`<button class="secondary-button update-remote-bridge" data-id="${escapeHtml(x.id)}">Update Bridge</button><button class="secondary-button rollback-remote-bridge" data-id="${escapeHtml(x.id)}">Rollback</button>`:''}<button class="secondary-button toggle-remote-app" data-id="${escapeHtml(x.id)}" data-enabled="${x.enabled?'1':'0'}">${x.enabled?'Pause':'Resume'}</button><button class="secondary-button remove-remote-app" data-id="${escapeHtml(x.id)}">Remove</button></div></div>`).join(''):'<div class="empty-state">No website/app connected yet. Copy its LlamaForge Connection URL and paste it above.</div>'}</div>
      </div></section>
      <section class="section"><div class="detail-card"><h3>OpenAPI / Bridge connectors</h3><p style="color:var(--muted);font-size:10.5px;line-height:1.55">Paste any OpenAPI JSON URL. The PHP AI Bridge you sent is supported as one example: use its <code>openapi.php</code> URL and Agent bearer token. LlamaForge learns the operation IDs instead of being hard-coded to that plugin.</p>
        <div class="form-grid agent-connector-form"><div class="field"><label>Name</label><input id="connectorName" placeholder="My website bridge"></div><div class="field"><label>OpenAPI schema URL</label><input id="connectorUrl" placeholder="https://example.com/ai/openapi.php"></div><div class="field"><label>Bearer token (optional)</label><input id="connectorToken" type="password" placeholder="Agent token"></div></div>
        <div class="inline-actions" style="margin-top:12px"><button id="addConnector" class="primary-button">${icon('plus')} Add connector</button></div>
        <div class="agent-connector-list">${connectors.length?connectors.map(x=>`<div class="agent-connector"><div><strong>${escapeHtml(x.name)}</strong><small>${escapeHtml(x.schema_url)} · ${(x.operations||[]).length} operations · token ${x.token_configured?'configured':'not set'}</small><div class="tag-row">${(x.operations||[]).slice(0,8).map(o=>`<span class="tag">${escapeHtml(o.operation)} · ${escapeHtml(o.method)}</span>`).join('')}${(x.operations||[]).length>8?`<span class="tag">+${(x.operations||[]).length-8}</span>`:''}</div></div><button class="secondary-button remove-connector" data-id="${escapeHtml(x.id)}">Remove</button></div>`).join(''):'<div class="empty-state">No connector yet. Add an OpenAPI URL when you want the model to talk to an external app or website bridge.</div>'}</div>
      </div></section>
      <section class="section"><div class="detail-card smart-skill-tree"><div class="skill-tree-head"><div><span class="eyebrow">AUTO CAPABILITY ROUTER</span><h3>Smart Skill Tree</h3><p>The model thinks in broad capabilities instead of memorizing one skill for every question. High-confidence guards stop small models from saying “no access” when Calendar, Files or Web are actually available.</p></div><span class="skill-auto-badge">Auto</span></div><div class="skill-flow"><span><b>1</b> Understand</span><i>→</i><span><b>2</b> Choose capability</span><i>→</i><span><b>3</b> Use minimum tool</span><i>→</i><span><b>4</b> Verify</span></div><div class="skill-family-grid">${skillCatalogHTML||'<div class="empty-state">No skill catalog available.</div>'}</div></div></section>
      <section class="section"><div class="detail-grid"><div class="detail-card"><h3>Custom skills v2</h3><div class="detail-row"><span>Folder</span><span style="max-width:72%;overflow-wrap:anywhere">${escapeHtml(a.skills_dir||'')}</span></div><p style="color:var(--muted);font-size:10.5px;line-height:1.55">Drop declarative <code>.json</code> skills here. v2 supports request headers/query/JSON body, required arguments, timeout, retries, response format and dot-path extraction. A README with examples is generated automatically.</p>${skills.length?`<div class="tag-row">${skills.map(x=>`<span class="tag">skill_${escapeHtml(x.name)} · ${escapeHtml(x.method||'GET')}</span>`).join('')}</div>`:''}</div><div class="detail-card"><h3>Agent downloads</h3><div class="detail-row"><span>Folder</span><span style="max-width:72%;overflow-wrap:anywhere">${escapeHtml(a.downloads_dir||'')}</span></div><p style="color:var(--muted);font-size:10.5px;line-height:1.55">The <code>download_file</code> skill stores explicitly requested downloads here with a configurable size limit.</p></div></div></section>
    </div>`;
    $('#agentOpenChat').onclick=()=>{setAgentEnabled(true);setRoute('chat')};
    $('#saveAgentSettings').onclick=async()=>{try{await api('/api/settings',{method:'POST',body:{agent_enabled_default:$('#agentDefault').checked,agent_allow_workspace_write:$('#agentWorkspaceWrite').checked,agent_allow_write:$('#agentWrite').checked,agent_allow_private_network:$('#agentPrivate').checked,agent_browser_headless:$('#agentHeadless').checked,agent_max_steps:Number($('#agentSteps').value)}});await refreshState(true);toast('Agent settings saved')}catch(e){toast('Could not save Agent settings',e.message,'error')}};
    if($('#installAgentBrowser'))$('#installAgentBrowser').onclick=async()=>{try{await api('/api/agent/browser/install',{method:'POST',body:{}});toast('Browser skill installation started','Selenium will use Chrome and manage its driver automatically.','info',5200);setTimeout(()=>refreshState(true),1200)}catch(e){toast('Could not install browser skill',e.message,'error')}};
    if($('#closeAgentBrowser'))$('#closeAgentBrowser').onclick=async()=>{try{await api('/api/agent/browser/close',{method:'POST',body:{}});await refreshState(true);toast('Agent browser closed')}catch(e){toast('Could not close browser',e.message,'error')}};
    $('#addRemoteApp').onclick=async()=>{const connect_url=($('#remoteAppUrl').value||'').trim();if(!connect_url){toast('Connection URL is required','','error');return}try{await api('/api/agent/app/add',{method:'POST',body:{connect_url,token:($('#remoteAppToken').value||'').trim()}});await refreshState(true);await renderAgent(view);toast('Website connected','LlamaForge will now watch it whenever the local model is ready.','ok',5200)}catch(e){toast('Could not connect website',e.message,'error',8000)}};
    $('#refreshRemoteApps').onclick=async()=>{try{await refreshState(true);await renderAgent(view);toast('Connection status refreshed')}catch(e){toast('Refresh failed',e.message,'error')}};
    $$('.test-remote-app').forEach(b=>b.onclick=async()=>{try{await api('/api/agent/app/test',{method:'POST',body:{id:b.dataset.id}});await renderAgent(view);toast('Connection test passed')}catch(e){toast('Connection test failed',e.message,'error',7000)}});
    $$('.update-remote-bridge').forEach(b=>b.onclick=async()=>{const id=b.dataset.id;try{const st=await api('/api/agent/app/bridge-status',{method:'POST',body:{id}});const local=st.local_bridge_version||'bundled';const remote=st.remote?.bridge_version||'unknown';if(local===remote){toast('Web Bridge is already up to date',local,'ok',4500);return}const ok=await confirmModal('Update Web Bridge',`Update the connected website Bridge from ${remote} to ${local}? LlamaForge will create a server-side backup first. Chat history and tokens are preserved.`,'Update Bridge');if(!ok)return;b.disabled=true;b.textContent='Updating…';const r=await api('/api/agent/app/update-bridge',{method:'POST',body:{id}});await refreshState(true);await renderAgent(view);toast('Web Bridge updated',`Installed ${r.package_version||local}. A rollback backup was created automatically.`,'ok',7000)}catch(e){toast('Bridge update failed',e.message,'error',9000)}});
    $$('.rollback-remote-bridge').forEach(b=>b.onclick=()=>showBridgeRollback(b.dataset.id));
    $$('.toggle-remote-app').forEach(b=>b.onclick=async()=>{try{await api('/api/agent/app/toggle',{method:'POST',body:{id:b.dataset.id,enabled:b.dataset.enabled!=='1'}});await refreshState(true);await renderAgent(view)}catch(e){toast('Could not change connection',e.message,'error')}});
    $$('.remove-remote-app').forEach(b=>b.onclick=async()=>{try{await api('/api/agent/app/remove',{method:'POST',body:{id:b.dataset.id}});await refreshState(true);await renderAgent(view);toast('Website disconnected')}catch(e){toast('Could not disconnect website',e.message,'error')}});
    $('#addConnector').onclick=async()=>{const schema_url=($('#connectorUrl').value||'').trim();if(!schema_url){toast('OpenAPI URL is required','','error');return}try{await api('/api/agent/connector/add',{method:'POST',body:{name:($('#connectorName').value||'').trim(),schema_url,token:($('#connectorToken').value||'').trim()}});await refreshState(true);toast('Connector added','Its OpenAPI operations are now available to the local Agent.')}catch(e){toast('Could not add connector',e.message,'error',7500)}};
    $$('.remove-connector').forEach(b=>b.onclick=async()=>{try{await api('/api/agent/connector/remove',{method:'POST',body:{id:b.dataset.id}});await refreshState(true);toast('Connector removed')}catch(e){toast('Could not remove connector',e.message,'error')}});
  }

  // ----- Smart Chat 2.0 -----
  function chatPrefs(){
    return {
      mode:localStorage.getItem('lf.chatMode')||'auto',
      reasoning:localStorage.getItem('lf.reasoning')||'auto',
      reasoningBudget:Number(localStorage.getItem('lf.reasoningBudget')||'-1'),
      maxTokens:Number(localStorage.getItem('lf.maxTokens')||'4096')
    };
  }
  function modeLabel(v){return ({auto:'Auto',general:'General',coding:'Coding',reasoning:'Reasoning',creative:'Creative',precise:'Precise',translation:'Translate'})[v]||'Auto'}
  function reasoningLabel(v){return ({auto:'Thinking auto',on:'Thinking on',off:'Thinking off'})[v]||'Thinking auto'}
  function lastAssistantProfile(){const msgs=currentThread(true).messages||[];for(let i=msgs.length-1;i>=0;i--)if(msgs[i].role==='assistant'&&msgs[i].meta?.profile)return msgs[i].meta.profile;return null}
  function smartLabel(){const p=lastAssistantProfile();return p?.task?`Auto · ${p.task[0].toUpperCase()+p.task.slice(1)}`:'Auto'}

  function renderChat(view){
    const s=App.state||{}, ss=s.server||{}, m=s.active_model, prefs=chatPrefs(), brain=s.brain||{}, agentOn=agentEnabled();
    { const stage=brain.job?.stage||''; const running=brain.job?.state==='running'; App.brainSetup=running&&['setup','auto-setup','detect-base','trainer'].includes(stage); App.brainLearning=running&&!App.brainSetup; }
    currentThread(true);
    view.className='view chat-route';
    view.innerHTML=`<div class="chat-view studio-chat">
      <header class="chat-header">
        <div class="chat-header-left">
          <button id="chatModelButton" class="model-switcher" title="Change model">${m?`<span class="model-led"></span><span class="model-switch-name">${escapeHtml(shortName(m.name,34))}</span><span class="model-switch-quant">${escapeHtml(m.quantization||'GGUF')}</span>`:'<span>Choose model</span>'}${icon('down')}</button>
        </div>
        <div class="chat-header-center"><button id="smartProfileButton" class="auto-status" title="See how LlamaForge tuned this turn">${icon('spark')}<span>${escapeHtml(smartLabel())}</span></button>${brain.enabled?`<button id="brainStatusButton" class="brain-chat-pill ${brain.adapter_ready?'learned':'armed'} ${App.brainLearning?'learning':''}" title="Personal weight learning">${icon('brain')}<span>${App.brainLearning?'Learning…':brain.setup_ready?(brain.zero_context?'Brain · ready':'Brain on'):'Brain · setup'}</span></button>`:''}</div>
        <div class="chat-header-right"><span class="local-status ${ss.ready?'ready':''}"><span></span>${ss.ready?'Ready':ss.running?'Loading':'Offline'}</span><button id="newChatTop" class="round-icon" title="New chat">${icon('plus')}</button></div>
      </header>
      <div id="chatScroll" class="chat-scroll studio-scroll"><div id="chatColumn" class="chat-column studio-column"></div><button id="jumpLatest" class="jump-latest hidden">${icon('down')}<span>Latest</span></button></div>
      <div class="composer-zone studio-composer-zone"><div class="composer-wrap studio-composer-wrap">
        <div class="composer studio-composer ${(ss.ready&&!App.brainLearning)?'':'composer-disabled'}">
          ${attachmentTrayHTML()}
          <textarea id="composerInput" rows="1" placeholder="${App.brainLearning?'Learning this turn into weights…':App.brainSetup?'Personal Brain setup is running…':ss.ready?'Message your local model':ss.running?'Model is loading…':'Run a model to start chatting'}" ${(ss.ready&&!App.brainLearning)?'':'disabled'}></textarea>
          <div class="composer-bottom studio-composer-bottom">
            <div class="composer-controls studio-controls">
              <button id="attachFiles" class="composer-action" title="Attach any file" ${ss.ready?'':'disabled'}>${icon('plus')}</button>
              <input id="chatFileInput" class="chat-file-input" type="file" multiple  tabindex="-1">
              <button id="chatTools" class="composer-action" title="Generation controls">${icon('tune')}</button>
              <button id="agentToggle" class="composer-mode agent-inline ${agentOn?'on':''}" title="Internet / website Agent">${icon('globe')}<span>${agentOn?'Agent on':'Agent off'}</span></button>
              <button id="brainInline" class="composer-mode brain-inline ${brain.enabled?'on':''}" title="Personal Brain learning">${icon('brain')}<span>${brain.enabled?(brain.setup_ready?'Learn':'Setup Brain'):'Brain off'}</span></button>
              <button id="thinkingToggle" class="composer-mode" title="Thinking / reasoning">${icon('spark')}<span>${escapeHtml(reasoningLabel(prefs.reasoning).replace('Thinking ',''))}</span></button>
              <button id="smartProfileInline" class="composer-mode smart-inline" title="Smart generation profile">${icon('spark')}<span>${escapeHtml(smartLabel())}</span></button>
            </div>
            <div class="composer-right">
              <span class="context-mini" title="Context usage"><span id="contextText">${contextLabel()}</span><span class="context-ring" style="--p:${contextPercent()}"></span></span>
              <button id="sendButton" class="send-button studio-send ${App.streaming?'stop':''}" ${(ss.ready&&!App.brainLearning)||App.streaming?'':'disabled'} aria-label="${App.streaming?'Stop generation':'Send message'}">${App.streaming?icon('stop'):icon('send')}</button>
            </div>
          </div>
        </div>
        <div class="composer-hint studio-hint"><span>${agentOn?'Agent mode: internet + skills enabled · ':''}${brain.enabled&&brain.zero_context?'Brain mode: previous turns are not sent to inference · ':''}Enter to send · Shift+Enter newline · Esc stops</span><span>${App.brainLearning?'Updating personal weights…':App.brainSetup?'Preparing learning engine…':m?`${escapeHtml(m.architecture||'GGUF')}${m.vision_capable?' · Vision':''}`:'No model'}</span></div>
      </div></div>
    </div>`;
    renderMessages();
    $('#chatModelButton').onclick=()=>setRoute('models');
    if($('#brainStatusButton'))$('#brainStatusButton').onclick=()=>setRoute('brain');
    if($('#brainInline'))$('#brainInline').onclick=()=>setRoute('brain');
    $('#smartProfileButton').onclick=()=>showSmartProfile();
    $('#smartProfileInline').onclick=()=>showSmartProfile();
    $('#chatTools').onclick=()=>showChatOptions();
    if($('#attachFiles'))$('#attachFiles').onclick=()=>$('#chatFileInput')?.click();
    if($('#chatFileInput'))$('#chatFileInput').onchange=async e=>{await addChatAttachments(e.target.files);e.target.value='';};
    $$('.attachment-remove').forEach(b=>b.onclick=()=>{App.pendingAttachments=App.pendingAttachments.filter(a=>a.id!==b.dataset.id);renderChat($('#view'));});
    $('#agentToggle').onclick=()=>{const on=!agentEnabled();setAgentEnabled(on);toast(on?'Agent enabled':'Agent disabled',on?'Smart Skills can now route to web, calendar, files, APIs and browser tools.':'This chat will use the model without external capabilities.','info',2600)};
    $('#thinkingToggle').onclick=()=>cycleThinking();
    $('#newChatTop').onclick=newThread;
    const ta=$('#composerInput');
    if(ta){ta.oninput=autoGrow;ta.onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();if(!App.streaming)sendFromComposer()}else if(e.key==='Escape'&&App.streaming){e.preventDefault();stopGeneration()}};setTimeout(()=>ta.focus(),20)}
    $('#sendButton').onclick=()=>App.streaming?stopGeneration():sendFromComposer();
    const sc=$('#chatScroll'), jump=$('#jumpLatest');
    if(sc&&jump){
      const syncFollow=()=>{const far=sc.scrollHeight-sc.scrollTop-sc.clientHeight>260;App.chatFollowTail=!far;jump.classList.toggle('hidden',!far)};
      sc.onscroll=syncFollow;
      jump.onclick=()=>{App.chatFollowTail=true;sc.scrollTo({top:sc.scrollHeight,behavior:'smooth'})};
      requestAnimationFrame(()=>{if(App.chatFollowTail)sc.scrollTop=sc.scrollHeight;syncFollow()});
    }
  }
  function attachmentTrayHTML(){
    if(!App.pendingAttachments.length)return '';
    return `<div class="attachment-tray">${App.pendingAttachments.map(a=>`<div class="attachment-chip ${a.kind}">${a.kind==='image'&&a.data_url?`<img src="${escapeHtml(a.data_url)}" alt="">`:`<span>${icon(a.kind==='image'?'models':'logs')}</span>`}<div><strong>${escapeHtml(shortName(a.name||'attachment',28))}</strong><small>${a.kind==='image'?'Image':a.kind==='text'?'Text file':'File'}</small></div><button type="button" class="attachment-remove" data-id="${escapeHtml(a.id)}" aria-label="Remove">${icon('x')}</button></div>`).join('')}</div>`;
  }
  function fileDataUrl(file){return new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result||''));r.onerror=()=>reject(r.error||new Error('Could not read file'));r.readAsDataURL(file);});}
  async function addChatAttachments(files){
    const list=[...(files||[])].slice(0,8);if(!list.length)return;
    const maxTotal=24*1024*1024;
    let agentWasEnabled=agentEnabled();
    for(const file of list){
      const used=App.pendingAttachments.reduce((n,a)=>n+Number(a.size||0),0);
      if(used+Number(file.size||0)>maxTotal){toast('Attachment limit reached','Keep the total selected attachments under 24 MB.','error');continue;}
      if(file.size>20*1024*1024){toast('File is too large',`${file.name} exceeds the 20 MB chat-workspace staging limit.`,'error');continue;}
      const id=crypto.randomUUID?crypto.randomUUID():String(Date.now()+Math.random());
      const image=(file.type||'').startsWith('image/');
      // Every non-image file is transported as an opaque file, including text,
      // code, ZIPs, Office documents, audio/video and unknown binaries. The
      // workspace exposes metadata first; the model chooses the File skill and
      // reads/inspects content only when the user's instruction actually needs it.
      App.pendingAttachments.push({id,kind:image?'image':'file',name:file.name,type:file.type||(image?'image/jpeg':'application/octet-stream'),size:file.size,data_url:await fileDataUrl(file)});
    }
    // Attachments use File Manager first so the model can decide whether content
    // is needed. This keeps store/move operations out of the model context.
    if(App.pendingAttachments.length&&!agentWasEnabled){setAgentEnabled(true);toast('File Manager Agent enabled','The model will decide whether to inspect the attachment or only organize it.','info',4200);}
    if(App.pendingAttachments.length>8)App.pendingAttachments=App.pendingAttachments.slice(-8);
    renderChat($('#view'));
  }

  function contextLabel(){ const ctx=App.state?.server?.plan?.ctx_size||App.state?.assessment?.recommended_ctx||4096; return App.contextTokens!=null?`${App.contextTokens.toLocaleString()} / ${ctx.toLocaleString()}`:`${ctx.toLocaleString()} ctx`; }
  function contextPercent(){ const ctx=App.state?.server?.plan?.ctx_size||App.state?.assessment?.recommended_ctx||4096; return App.contextTokens?Math.min(100,App.contextTokens/ctx*100):0; }
  function autoGrow(e){ const ta=e.currentTarget; ta.style.height='0px'; ta.style.height=Math.min(210,Math.max(30,ta.scrollHeight))+'px'; }

  function renderMessages(){
    const col=$('#chatColumn'); if(!col)return; const t=currentThread(true), msgs=t.messages||[];
    const sc=$('#chatScroll'), oldTop=sc?.scrollTop||0, shouldFollow=App.chatFollowTail;
    if(!msgs.length){
      col.innerHTML=chatEmpty();
      $$('.suggestion',col).forEach(b=>b.onclick=()=>{const ta=$('#composerInput');if(ta&&!ta.disabled){ta.value=b.dataset.prompt;ta.dispatchEvent(new Event('input'));ta.focus()}});
      const er=$('#emptyRun'); if(er)er.onclick=()=>App.state?.active_model?startOptimized():chooseModel();
      return;
    }
    col.innerHTML=msgs.map((m,i)=>messageHTML(m,i)).join('');
    bindMessageActions(col);
    requestAnimationFrame(()=>{if(!sc)return;if(shouldFollow)sc.scrollTop=sc.scrollHeight;else sc.scrollTop=oldTop});
  }
  function bindMessageActions(col){
    $$('.copy-message',col).forEach(b=>b.onclick=()=>copyMessage(Number(b.dataset.index)));
    $$('.regen-message',col).forEach(b=>b.onclick=()=>regenerateMessage(Number(b.dataset.index)));
    $$('.edit-message',col).forEach(b=>b.onclick=()=>editUserMessage(Number(b.dataset.index)));
    $$('.copy-code',col).forEach(b=>b.onclick=async()=>{const code=b.closest('.code-block').querySelector('code').textContent;await navigator.clipboard.writeText(code);const old=b.innerHTML;b.innerHTML=`${icon('check')} Copied`;setTimeout(()=>b.innerHTML=old,1200)});
  }
  function chatEmpty(){
    const ready=App.state?.server?.ready, m=App.state?.active_model;
    return `<div class="chat-empty studio-empty">
      <div class="studio-empty-mark"><span></span></div>
      <h2>${ready?'What are we working on?':'Start a local model'}</h2>
      <p>${ready?`Using <strong>${escapeHtml(shortName(m?.name||'your local model',32))}</strong>. LlamaForge adapts thinking, sampling and context automatically.`:'Pick a GGUF and LlamaForge will configure llama.cpp for this machine.'}</p>
      ${ready?`<div class="suggestion-grid studio-suggestions">
        <button class="suggestion" data-prompt="Review this code carefully. Find the root cause first, then give the smallest safe fix and explain why it works."><span class="suggestion-icon">${icon('terminal')}</span><span><strong>Debug code</strong><small>Root cause → safe fix</small></span></button>
        <button class="suggestion" data-prompt="Analyze this problem carefully. Compare the realistic options, identify the tradeoffs, and recommend one with reasons."><span class="suggestion-icon">${icon('spark')}</span><span><strong>Analyze deeply</strong><small>Compare before answering</small></span></button>
        <button class="suggestion" data-prompt="این موضوع را دقیق، روان و کاربردی به فارسی توضیح بده. اول نتیجهٔ اصلی را بگو و بعد جزئیات لازم را اضافه کن."><span class="suggestion-icon">${icon('chat')}</span><span><strong>پاسخ فارسی</strong><small>روان و مستقیم</small></span></button>
        <button class="suggestion" data-prompt="Turn this idea into an implementation plan. Keep the first milestone small and executable, then list risks and next steps."><span class="suggestion-icon">${icon('tune')}</span><span><strong>Plan a build</strong><small>Concrete next steps</small></span></button>
      </div>`:`<button id="emptyRun" class="primary-button big-action">${icon('play')} ${App.state?.active_model?'Run optimized':'Choose a model'}</button>`}
    </div>`;
  }
  function agentStatusHTML(m){
    const events=Array.isArray(m?.meta?.agentEvents)?m.meta.agentEvents:[];
    if(!events.length)return '';
    const recent=events.slice(-10).map(ev=>{
      if(!ev||typeof ev!=='object')return '';
      if(ev.event==='enabled')return `Agent ready · write ${ev.permissions?.write?'on':'off'}`;
      if(ev.event==='route_decision')return `${ev.route==='skills'?'Model chose Skills':'Model chose direct chat'}${ev.confidence?` · ${Number(ev.confidence)}%`:''}${ev.model_seconds!=null?` · ${Number(ev.model_seconds).toFixed(1)}s`:''}`;
      if(ev.event==='route')return ev.route==='direct'?'Direct response · no skill needed':'Agent route · external skills enabled';
      if(ev.event==='context_policy')return `Context budget · ${Number(ev.context_limit||0).toLocaleString()} ctx · ${Number(ev.skill_limit||0)} skills · ${Number(ev.observation_keep||0)} observations`;
      if(ev.event==='direct_complete')return `Direct model response ready${ev.model_seconds!=null?` · ${Number(ev.model_seconds).toFixed(1)}s`:''}`;
      if(ev.event==='phase')return escapeHtml(ev.label||ev.phase||'Agent update');
      if(ev.event==='capabilities')return `Skill family: ${escapeHtml((ev.families||ev.categories||[]).join(', ')||'auto')} · candidates: ${escapeHtml((ev.skills||[]).join(', '))}`;
      if(ev.event==='thinking')return `Local model planning · step ${Number(ev.step||1)}/${Number(ev.max_steps||1)}`;
      if(ev.event==='decision'&&ev.action==='tool')return `Plan: ${escapeHtml(ev.summary||'Use a skill')} → ${escapeHtml(ev.skill||'tool')}`;
      if(ev.event==='decision'&&ev.action==='final')return `Enough evidence · ${escapeHtml(ev.summary||'compose final answer')}`;
      if(ev.event==='tool_start')return `Running ${escapeHtml(ev.tool||'tool')}…`;
      if(ev.event==='tool_result'){const err=ev.error_preview?` · ${escapeHtml(String(ev.error_preview).slice(0,220))}`:'';return `${escapeHtml(ev.tool||'tool')}: ${ev.ok?'observation received':'failed'}${ev.tool_seconds!=null?` · ${Number(ev.tool_seconds).toFixed(1)}s`:''}${ev.observation_chars?` · ${Number(ev.observation_chars).toLocaleString()} chars`:''}${err}`;}
      if(ev.event==='policy')return `Runtime policy: ${escapeHtml(ev.label||ev.policy||'adjusted execution')}${ev.error_preview?` · ${escapeHtml(String(ev.error_preview).slice(0,180))}`:''}`;
      if(ev.event==='preflight_failed')return `Preflight blocked ${escapeHtml(ev.tool||'skill')}: ${escapeHtml(ev.error||'invalid call')}${(ev.fallbacks||[]).length?` · fallback: ${escapeHtml(ev.fallbacks.join(', '))}`:''}`;
      if(ev.event==='decision_error')return 'Control JSON was invalid; recovery path used.';
      return '';
    }).filter(Boolean);
    if(!recent.length)return '';
    const last=recent[recent.length-1]||'Agent activity';
    return `<details class="agent-trace ${m.streaming?'running':'complete'}" ${m.streaming?'open':''}><summary><span>Agent activity</span><small>${last}</small></summary><div class="agent-trace-list">${recent.map(x=>`<div class="agent-trace-row">${x}</div>`).join('')}</div></details>`;
  }

  function messageHTML(m,i){
    if(m.role==='user'){const atts=Array.isArray(m.attachments)?m.attachments:[];const attHtml=atts.length?`<div class="message-attachments">${atts.map(a=>a.kind==='image'&&a.data_url?`<img class="message-image" src="${escapeHtml(a.data_url)}" alt="${escapeHtml(a.name||'image')}">`:`<span class="message-file">${icon(a.kind==='image'?'models':'logs')} ${escapeHtml(shortName(a.name||'file',30))}${a.unavailable?' · reload to reattach':''}</span>`).join('')}</div>`:'';return `<article class="message user studio-user ${hasRTL(m.content)?'rtl':''}" data-message-index="${i}"><div class="user-message-shell">${attHtml}${m.content?`<div class="user-bubble">${escapeHtml(m.content).replace(/\n/g,'<br>')}</div>`:''}<div class="message-actions user-actions"><button class="message-action edit-message" data-index="${i}" title="Edit">${icon('edit')}</button><button class="message-action copy-message" data-index="${i}" title="Copy">${icon('copy')}</button></div></div></article>`;}
    const profile=m.meta?.profile, quality=m.meta?.quality, contentDir=hasRTL(m.content)?'rtl':'';
    const reasoning=(m.reasoning||'').trim();
    const thoughtLabel=m.reasoningStreaming?'Thinking…':m.meta?.thinkingTime?`Thought for ${m.meta.thinkingTime}s`:'Thinking';
    const reasoningVisible=!!(reasoning||m.reasoningStreaming);
    const reasoningInner=m.reasoningStreaming?`<span class="stream-text">${escapeHtml(reasoning)}</span>`:renderMarkdown(reasoning||'');
    const reasoningBlock=`<details class="reasoning-panel studio-reasoning ${reasoningVisible?'':'hidden'}" ${m.reasoningStreaming?'open':''}><summary><span class="reasoning-spark">${icon('spark')}</span><span class="reasoning-label">${thoughtLabel}</span>${m.reasoningStreaming?'<span class="reasoning-pulse"></span>':''}<span class="reasoning-chevron">${icon('down')}</span></summary><div class="reasoning-body ${hasRTL(reasoning)?'rtl':''} ${m.reasoningStreaming?'is-streaming':''}">${reasoningInner}</div></details>`;
    const metaBits=[]; if(m.meta?.agent){const n=(m.meta?.agentEvents||[]).filter(x=>x.event==='tool_start').length;metaBits.push(`agent${n?` · ${n} tool${n===1?'':'s'}`:''}`)} if(profile?.task)metaBits.push(profile.task); if(m.meta?.speed)metaBits.push(`${m.meta.speed} tok/s`); if(m.meta?.elapsed)metaBits.push(`${m.meta.elapsed}s`); if(m.meta?.repaired)metaBits.push('auto repaired');
    const qualityBadge=quality&&!quality.ok?`<button class="quality-warning" title="${escapeHtml((quality.issues||[]).join(', '))}">${icon('spark')} improved automatically</button>`:'';
    const ctxNote=m.meta?.context?.trimmed_turns?`<span class="context-note">Older context compacted</span>`:'';
    return `<article class="message assistant studio-assistant" data-message-index="${i}">
      <div class="assistant-rail"><span class="assistant-glyph">${icon('spark')}</span></div>
      <div class="assistant-stack">
        ${reasoningBlock}
        <div class="assistant-content ${contentDir} ${m.streaming?'is-streaming':''}" data-stream-index="${i}">${m.streaming?`<span class="stream-text">${escapeHtml(m.content||'')}</span><span class="typing-cursor"></span>`:renderMarkdown(m.content||'')}</div>
        ${agentStatusHTML(m)}
        <div class="message-footer studio-message-footer"><div class="message-actions"><button class="message-action copy-message" data-index="${i}" title="Copy">${icon('copy')}</button>${!m.streaming?`<button class="message-action regen-message" data-index="${i}" title="Regenerate">${icon('regen')}</button>`:''}</div><div class="response-meta">${ctxNote}${qualityBadge}${metaBits.length?`<span>${escapeHtml(metaBits.join(' · '))}</span>`:''}</div></div>
      </div>
    </article>`;
  }
  function renderMarkdown(raw=''){
    const chunks=String(raw).split(/```/); let html='';
    chunks.forEach((chunk,idx)=>{
      if(idx%2===1){const nl=chunk.indexOf('\n'),lang=nl>=0?chunk.slice(0,nl).trim():'',code=nl>=0?chunk.slice(nl+1):chunk;html+=`<div class="code-block studio-code"><div class="code-head"><span>${escapeHtml(lang||'code')}</span><button class="copy-code">${icon('copy')} Copy</button></div><pre><code>${escapeHtml(code.replace(/\n$/,''))}</code></pre></div>`;return;}
      let x=escapeHtml(chunk);
      x=x.replace(/^### (.+)$/gm,'<h3>$1</h3>').replace(/^## (.+)$/gm,'<h2>$1</h2>').replace(/^# (.+)$/gm,'<h1>$1</h1>');
      x=x.replace(/^---$/gm,'<hr>');
      x=x.replace(/^&gt; (.+)$/gm,'<blockquote>$1</blockquote>');
      x=x.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').replace(/`([^`]+)`/g,'<code>$1</code>');
      x=x.replace(/(?:^|\n)((?:[-*] .+(?:\n|$))+)/g,(all,block)=>'<ul>'+block.trim().split('\n').map(line=>`<li>${line.replace(/^[-*] /,'')}</li>`).join('')+'</ul>');
      x=x.replace(/(?:^|\n)((?:\d+\. .+(?:\n|$))+)/g,(all,block)=>'<ol>'+block.trim().split('\n').map(line=>`<li>${line.replace(/^\d+\. /,'')}</li>`).join('')+'</ol>');
      x=x.split(/\n{2,}/).map(p=>{p=p.trim();if(!p)return'';if(/^<(h\d|ul|ol|blockquote|hr)/.test(p))return p;return `<p>${p.replace(/\n/g,'<br>')}</p>`}).join('');
      html+=x;
    }); return html;
  }
  async function copyMessage(i){const m=currentThread().messages[i];if(!m)return;await navigator.clipboard.writeText(m.content);toast('Copied')}
  async function editUserMessage(i){
    const t=currentThread(),m=t.messages[i];if(!m||m.role!=='user')return;const root=$('#modalRoot');
    root.innerHTML=`<div class="modal-backdrop"><div class="modal premium-modal"><div class="modal-kicker">EDIT MESSAGE</div><h3>Edit and retry</h3><textarea id="editMessageText" class="modal-textarea">${escapeHtml(m.content)}</textarea><div class="inline-actions"><button class="secondary-button" data-no>Cancel</button><button class="primary-button" data-yes>Save & retry</button></div></div></div>`;
    $('[data-no]',root).onclick=()=>root.innerHTML='';$('[data-yes]',root).onclick=async()=>{const text=$('#editMessageText').value.trim();if(!text)return;t.messages=t.messages.slice(0,i);t.messages.push({role:'user',content:text});root.innerHTML='';saveThreads();renderMessages();await generateAssistant();};
  }
  async function regenerateMessage(i){const t=currentThread();let userIndex=-1;for(let j=i-1;j>=0;j--)if(t.messages[j].role==='user'){userIndex=j;break}if(userIndex<0)return;t.messages=t.messages.slice(0,i);saveThreads();renderMessages();await generateAssistant();}
  function setStreamingUI(active){
    App.streaming=active;
    document.querySelector('.chat-view')?.classList.toggle('is-streaming',active);
    const btn=$('#sendButton');
    if(btn){btn.classList.toggle('stop',active);btn.setAttribute('aria-label',active?'Stop generation':'Send message');btn.innerHTML=active?icon('stop'):icon('send');btn.disabled=active?false:(!App.state?.server?.ready||App.brainLearning);}
  }
  function finalizeAssistantRow(target=null){
    const t=currentThread(false);if(!t)return;const i=target?t.messages.indexOf(target):t.messages.length-1;if(i<0)return;const m=t.messages[i];if(!m||m.role!=='assistant')return;
    const row=document.querySelector(`[data-message-index="${i}"]`);if(!row){renderMessages();return}
    const sc=$('#chatScroll'),follow=App.chatFollowTail,oldTop=sc?.scrollTop||0;
    const content=$('.assistant-content',row);if(content){content.classList.remove('is-streaming');content.innerHTML=renderMarkdown(m.content||'')}
    const panel=$('.reasoning-panel',row);if(panel){const hasReason=!!(m.reasoning||'').trim();panel.classList.toggle('hidden',!hasReason);panel.open=false;const label=$('.reasoning-label',panel);if(label)label.textContent=m.meta?.thinkingTime?`Thought for ${m.meta.thinkingTime}s`:'Thinking';const body=$('.reasoning-body',panel);if(body){body.classList.remove('is-streaming');body.innerHTML=renderMarkdown(m.reasoning||'')}}
    // Re-render only this message once so footer metadata/actions are current. The chat shell and other messages stay mounted.
    const holder=document.createElement('div');holder.innerHTML=messageHTML(m,i);const fresh=holder.firstElementChild;if(fresh){row.replaceWith(fresh);bindMessageActions(fresh)}
    requestAnimationFrame(()=>{if(!sc)return;if(follow)sc.scrollTop=sc.scrollHeight;else sc.scrollTop=oldTop});
  }
  function stopGeneration(){if(App.chatAbort)App.chatAbort.abort();const t=currentThread(),last=t.messages.at(-1);if(last?.streaming){last.streaming=false;last.reasoningStreaming=false}saveThreads();setStreamingUI(false);finalizeAssistantRow(last);}
  async function ensureBrainReadyBeforeSend(){
    const brain=App.state?.brain||{};
    if(!brain.enabled||brain.setup_ready)return true;
    // Chat must stay usable while the trainable source/toolchain is prepared.
    // The completed turn is held by learnTurnIfNeeded and trained as soon as
    // setup becomes ready, so the first correction is not silently lost.
    if(!['running','cancelling'].includes(brain.job?.state)){
      try{const st=await api('/api/brain/autosetup',{method:'POST',body:{}});if(App.state)App.state.brain=st;patchBrainChatState(st);toast('Preparing learning in the background','You can chat now. This turn will be trained after setup finishes.','info',5200)}catch(e){toast('Personal Brain needs attention',e.message,'error',7500)}
    }
    return true;
  }
  async function sendFromComposer(){const ta=$('#composerInput'),text=(ta?.value||'').trim(),attachments=App.pendingAttachments.map(a=>({...a}));if((!text&&!attachments.length)||App.streaming)return;if(App.brainLearning){toast('Brain is still learning','Wait for the current weight update and model reload to finish.','info');return;}if(!(await ensureBrainReadyBeforeSend()))return;const t=currentThread();t.messages.push({role:'user',content:text,attachments});if(t.title==='New chat')t.title=titleFromPrompt(text||attachments[0]?.name||'Attachment');t.updated=Date.now();App.pendingAttachments=[];saveThreads();ta.value='';ta.style.height='';App.chatFollowTail=true;await generateAssistant();}
  function patchBrainPageState(brain){
    if(!brain||App.route!=='brain')return;
    const power=$('#brainPower');
    if(power&&!App.brainTogglePending){power.classList.toggle('on',!!brain.enabled);power.classList.toggle('off',!brain.enabled);power.setAttribute('aria-checked',brain.enabled?'true':'false');power.setAttribute('aria-label',brain.enabled?'Turn Personal Brain off':'Turn Personal Brain on');const strong=$('strong',power);if(strong)strong.textContent=brain.enabled?'ON':'OFF'}
    const j=brain.job||{},pct=Math.max(0,Math.min(100,Number(j.progress||0)*100));
    const card=$('.brain-job');
    if(card){card.className=`brain-job ${j.state||''}`;card.dataset.brainJobState=j.state||'';const msg=$('[data-brain-job-message]',card),stage=$('[data-brain-job-stage]',card),per=$('[data-brain-job-percent]',card),bar=$('[data-brain-job-progress]',card),transfer=$('[data-brain-transfer]',card);if(msg)msg.textContent=j.message||j.state||'';if(stage)stage.textContent=(j.stage||'brain').toUpperCase();if(per)per.textContent=`${Math.round(pct)}%`;if(bar)bar.style.width=`${pct}%`;if(transfer){const total=Number(j.total||0);transfer.hidden=!(total>0);if(total>0)transfer.innerHTML=`<span>${formatBytes(j.done||0)} / ${formatBytes(total)}</span>${Number(j.bytes_per_sec||0)>0?`<span>${formatBytes(j.bytes_per_sec)}/s</span>`:''}${j.eta_seconds!=null?`<span>ETA ${formatEta(j.eta_seconds)}</span>`:''}`}}
  }
  function patchBrainChatState(brain){
    if(!brain)return;if(App.state)App.state.brain=brain;{const stage=brain.job?.stage||'';const running=brain.job?.state==='running';App.brainSetup=running&&['setup','auto-setup','detect-base','trainer'].includes(stage);App.brainLearning=running&&!App.brainSetup;}
    const ta=$('#composerInput'),btn=$('#sendButton'),turnLocked=App.brainLearning||App.brainTurnPending;
    if(ta){ta.disabled=turnLocked||!App.state?.server?.ready;ta.placeholder=App.brainTurnPending?'Saving this turn into model weights…':App.brainLearning?'Learning this turn into weights…':App.brainSetup?'Learning setup is running in the background…':App.state?.server?.ready?'Message your local model…':'Model is reloading…'}
    if(btn&&!App.streaming)btn.disabled=turnLocked||!App.state?.server?.ready;
    const pill=$('#brainStatusButton');if(pill){pill.classList.toggle('learning',App.brainLearning);const sp=$('span',pill);if(sp)sp.textContent=App.brainLearning?'Learning…':brain.setup_ready?(brain.zero_context?'Brain · ready':'Brain on'):'Brain · setup'}
    const inline=$('#brainInline span');if(inline)inline.textContent=App.brainLearning?'Learning…':brain.enabled?(brain.setup_ready?'Learn':'Setup Brain'):'Brain off';
  }
  async function learnTurnIfNeeded(assistant){
    const brain=App.state?.brain||{};if(!brain.enabled||!assistant||assistant.error)return;
    const t=currentThread(false);if(!t)return;const ai=t.messages.indexOf(assistant);let user=null;for(let i=ai-1;i>=0;i--){if(t.messages[i].role==='user'){user=t.messages[i];break}}
    if(!user?.content||!assistant.content)return;
    if(!brain.setup_ready){
      App.brainTurnPending=true;patchBrainChatState(brain);
      try{
        if(!['running','cancelling'].includes(brain.job?.state)){const st=await api('/api/brain/autosetup',{method:'POST',body:{}});if(App.state)App.state.brain=st;patchBrainChatState(st)}
        toast('Answer ready · preparing learning','This exact user turn is kept pending until the learning engine is ready.','info',5200);
        const setupStarted=Date.now();let ready=null;
        while(Date.now()-setupStarted<2*60*60*1000){
          await sleep(1000);ready=await api('/api/brain/status');if(App.state)App.state.brain=ready;patchBrainChatState(ready);
          if(ready.job?.state==='error')throw new Error(ready.job.error||ready.job.message||'Personal Brain setup failed');
          if(!ready.enabled)throw new Error('Personal learning was turned off before this turn could be trained.');
          if(ready.setup_ready)break;
        }
        if(!ready?.setup_ready)throw new Error('Personal Brain setup timed out before this turn could be trained.');
      }catch(e){App.brainTurnPending=false;patchBrainChatState(App.state?.brain||brain);toast('Personal Brain needs attention',e.message,'error',8000);return}
    }
    try{
      App.brainTurnPending=true;patchBrainChatState(App.state?.brain||brain);
      const st=await api('/api/brain/learn',{method:'POST',body:{user:user.content,assistant:assistant.content}});patchBrainChatState(st);
      if(!brain.strict_learning){toast('Learning started','The model will reload when the personal weights are updated.','info');return;}
      App.brainLearning=true;patchBrainChatState(st);
      const started=Date.now();
      while(Date.now()-started<60*60*1000){
        await sleep(850);const b=await api('/api/brain/status');patchBrainChatState(b);
        if(b.job?.state==='error')throw new Error(b.job.error||b.job.message||'Brain learning failed');
        if(b.job?.state==='cancelled'){App.brainLearning=false;App.brainTurnPending=false;await refreshState(false);toast('Brain learning stopped','No unconfirmed personal weights were kept.','info',4200);return;}
        if(b.job?.state==='done'){
          if(b.job?.stage==='no-op'){
            App.brainLearning=false;App.brainTurnPending=false;await refreshState(false);toast('Message checked','No explicit fact or correction was present, so the model was not trained on its own answer.','info',4200);return;
          }
          // Learning is only complete for chat once the reloaded llama-server is healthy.
          let server=null;for(let k=0;k<240;k++){server=await api('/api/server/status');if(server.ready)break;if(server.error)throw new Error(server.error);await sleep(500)}
          if(!server?.ready)throw new Error('Weights were updated but the model did not become ready after reload.');
          App.brainLearning=false;App.brainTurnPending=false;await refreshState(false);toast('Learned into personal weights',`Brain generation ${b.generation||'updated'} · next turn starts with zero previous context.`,'ok',5200);return;
        }
      }
      throw new Error('Brain learning timed out');
    }catch(e){App.brainLearning=false;App.brainTurnPending=false;toast('Brain learning failed',e.message,'error',8000);try{await refreshState(false)}catch{}}
  }

  async function generateAssistant(opts={}){
    const repair=!!opts.repair,t=currentThread();if(!App.state?.server?.ready){toast('Model is not ready','Run the selected model first.','info');return;}
    const prefs=chatPrefs(),assistant={role:'assistant',content:'',reasoning:'',reasoningStreaming:false,streaming:true,meta:{repaired:repair,repairIssues:opts.issues||[]}};t.messages.push(assistant);saveThreads();setStreamingUI(true);renderMessages();
    const started=performance.now();let first=0,reasoningStarted=0,chars=0,quality=null;App.chatAbort=new AbortController();
    try{
      const payload={messages:t.messages.filter(m=>!m.streaming).map(({role,content,attachments})=>({role,content,attachments})),mode:prefs.mode,reasoning:prefs.reasoning,reasoning_budget:prefs.reasoningBudget,max_tokens:prefs.maxTokens,repair,repair_issues:opts.issues||[],agent:agentEnabled()};
      const res=await fetch('/api/chat/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:App.chatAbort.signal});if(!res.ok)throw new Error(`Chat failed (${res.status})`);
      const reader=res.body.getReader(),decoder=new TextDecoder();let buf='';
      while(true){const {value,done}=await reader.read();if(done)break;buf+=decoder.decode(value,{stream:true});const parts=buf.split('\n\n');buf=parts.pop()||'';for(const part of parts){const line=part.split('\n').find(x=>x.startsWith('data:'));if(!line)continue;const data=line.slice(5).trim();if(data==='[DONE]')continue;let obj;try{obj=JSON.parse(data)}catch{continue}if(obj.error)throw new Error(obj.error);
        if(obj.type==='profile'&&obj.profile){assistant.meta.profile=obj.profile;updateStreamingAssistant();updateSmartLabels(obj.profile);}
        else if(obj.type==='reasoning'&&obj.delta){if(!reasoningStarted)reasoningStarted=performance.now();assistant.reasoningStreaming=true;assistant.reasoning+=obj.delta;updateStreamingAssistant();}
        else if((obj.type==='text'||obj.delta)&&obj.delta){assistant.reasoningStreaming=false;if(!first){first=performance.now();if(reasoningStarted)assistant.meta.thinkingTime=((first-reasoningStarted)/1000).toFixed(1)}assistant.content+=obj.delta;chars+=obj.delta.length;updateStreamingAssistant();}
        else if(obj.type==='agent'){assistant.meta.agent=true;assistant.meta.agentEvents=assistant.meta.agentEvents||[];assistant.meta.agentEvents.push(obj);if(obj.event==='tool_start'){assistant.meta.agentTool=obj.tool||'tool';toast('Agent tool',obj.tool||'Running tool','info',1800)}renderMessages();const sc=$('#chatScroll');if(sc&&App.chatFollowTail)sc.scrollTop=sc.scrollHeight;}
        else if(obj.type==='quality'&&obj.quality){quality=obj.quality;assistant.meta.quality=quality;}
        else if(obj.type==='meta'&&obj.usage){assistant.meta.usage=obj.usage;}
        else if(obj.type==='meta'&&obj.context){assistant.meta.context=obj.context;if(obj.context.trimmed_turns)toast('Context managed',`${obj.context.trimmed_turns} older turn${obj.context.trimmed_turns===1?'':'s'} removed to stay inside the model context.`,'info',3600);}
      }}
      assistant.streaming=false;assistant.reasoningStreaming=false;const elapsed=(performance.now()-started)/1000;assistant.meta.elapsed=elapsed.toFixed(1);if(first)assistant.meta.ttft=((first-started)/1000).toFixed(1);assistant.meta.speed=(chars/4/Math.max(.2,elapsed)).toFixed(1);t.updated=Date.now();saveThreads();await updateContextCount();
      const repairable=['echo','empty','repetition','wrong_language','too_short','template_leak'];
      if(!repair&&quality&&!quality.ok&&(quality.issues||[]).some(x=>repairable.includes(x))){
        const issues=quality.issues||[];t.messages.pop();saveThreads();App.streaming=false;App.chatAbort=null;toast('Auto repair',humanizeIssues(issues),'info',4200);return await generateAssistant({repair:true,issues});
      }
      // Do not train on a failed first attempt. Only the final accepted response is learned.
      setStreamingUI(false);finalizeAssistantRow(assistant);
      await learnTurnIfNeeded(assistant);
    }catch(e){assistant.streaming=false;assistant.reasoningStreaming=false;if(e.name==='AbortError'){if(!assistant.content&&!assistant.reasoning)t.messages.pop()}else{assistant.content=assistant.content||`Generation failed: ${e.message}`;assistant.error=true;toast('Generation failed',e.message,'error',6500)}saveThreads();}
    finally{App.chatAbort=null;setStreamingUI(false);if(App.route==='chat'){finalizeAssistantRow(assistant);updateContextUI();}}
  }
  function humanizeIssues(issues){const names={echo:'The model echoed your prompt',empty:'The model returned an empty answer',repetition:'The model fell into a repetition loop',wrong_language:'The answer came back in the wrong language',too_short:'The answer was suspiciously incomplete',template_leak:'The model exposed chat-template tokens'};return (issues||[]).map(x=>names[x]||x).join(' · ')+' — retrying once with a targeted recovery.'}
  function updateSmartLabels(profile){const txt=profile?.task?`Auto · ${profile.task[0].toUpperCase()+profile.task.slice(1)}`:'Auto';$$('#smartProfileButton span, #smartProfileInline span').forEach(el=>el.textContent=txt)}
  let streamRAF=0,streamPaintTimer=0;
  function updateStreamingAssistant(){
    if(streamRAF||streamPaintTimer)return;
    const paint=()=>{
      streamRAF=0;streamPaintTimer=0;if(App.route!=='chat')return;
      const t=currentThread(),i=t.messages.length-1,m=t.messages[i],row=document.querySelector(`[data-message-index="${i}"]`);if(!row){renderMessages();return}
      const content=$('.assistant-content',row);if(content){content.classList.add('is-streaming');let text=$('.stream-text',content);if(!text){content.innerHTML='<span class="stream-text"></span><span class="typing-cursor"></span>';text=$('.stream-text',content)}text.textContent=m.content||''}
      const panel=$('.reasoning-panel',row);if(panel){const visible=!!(m.reasoning||m.reasoningStreaming);panel.classList.toggle('hidden',!visible);if(m.reasoningStreaming)panel.open=true;const label=$('.reasoning-label',panel);if(label)label.textContent=m.reasoningStreaming?'Thinking…':m.meta?.thinkingTime?`Thought for ${m.meta.thinkingTime}s`:'Thinking';const body=$('.reasoning-body',panel);if(body){body.classList.add('is-streaming');let text=$('.stream-text',body);if(!text){body.innerHTML='<span class="stream-text"></span>';text=$('.stream-text',body)}text.textContent=m.reasoning||''}}
      const sc=$('#chatScroll');if(sc&&App.chatFollowTail)sc.scrollTop=sc.scrollHeight;
    };
    // At most ~20 paints/sec. Token arrival can be much faster; DOM churn should not be.
    streamPaintTimer=setTimeout(()=>{streamRAF=requestAnimationFrame(paint)},50);
  }
  function updateContextUI(){const text=$('#contextText');if(text)text.textContent=contextLabel();const ring=document.querySelector('.context-ring');if(ring)ring.style.setProperty('--p',contextPercent())}
  async function updateContextCount(){try{const t=currentThread(),x=await api('/api/chat/tokens',{method:'POST',body:{messages:t.messages.map(({role,content,attachments})=>({role,content,attachments}))}});App.contextTokens=x.tokens??null;updateContextUI()}catch{}}
  function cycleThinking(){const p=chatPrefs(),next=p.reasoning==='auto'?'on':p.reasoning==='on'?'off':'auto';localStorage.setItem('lf.reasoning',next);renderChat($('#view'));toast('Thinking',reasoningLabel(next),'info',1500)}
  async function showSmartProfile(){
    const prefs=chatPrefs(),t=currentThread(),root=$('#modalRoot');let profile=null;try{profile=await api('/api/chat/profile',{method:'POST',body:{messages:t.messages.map(({role,content,attachments})=>({role,content,attachments})),mode:prefs.mode,reasoning:prefs.reasoning,reasoning_budget:prefs.reasoningBudget,max_tokens:prefs.maxTokens}})}catch{}
    root.innerHTML=`<div class="modal-backdrop"><div class="modal premium-modal smart-profile-modal"><div class="modal-kicker">SMART GENERATION</div><h3>${profile?`${escapeHtml(profile.family)} · ${escapeHtml(profile.task)}`:'Automatic tuning'}</h3><p>Auto adapts this turn using model metadata, architecture, language and intent. It also decides whether thinking is useful instead of forcing it on every prompt.</p>${profile?`<div class="confidence-row"><span>Decision confidence</span><strong>${profile.confidence||0}%</strong></div><div class="tuning-grid"><div><span>Temperature</span><strong>${profile.temperature}</strong></div><div><span>Top P</span><strong>${profile.top_p}</strong></div><div><span>Top K</span><strong>${profile.top_k}</strong></div><div><span>Repeat</span><strong>${profile.repeat_penalty}</strong></div><div><span>Thinking</span><strong>${escapeHtml(profile.effective_reasoning||profile.reasoning)}</strong></div><div><span>Output cap</span><strong>${profile.max_tokens}</strong></div></div><div class="smart-notes">${(profile.notes||[]).map(n=>`<div>${icon('check')}<span>${escapeHtml(n)}</span></div>`).join('')}</div>`:''}<div class="inline-actions"><button class="primary-button" data-yes>Done</button></div></div></div>`;$('[data-yes]',root).onclick=()=>root.innerHTML='';
  }
  function showChatOptions(){
    const p=chatPrefs(),root=$('#modalRoot');root.innerHTML=`<div class="modal-backdrop"><div class="modal premium-modal"><div class="modal-kicker">GENERATION CONTROLS</div><h3>Automatic by default</h3><p>Most models work best when LlamaForge chooses settings per turn. Manual controls are here for experiments, not as a requirement.</p><div class="form-grid modal-form"><div class="field"><label>Mode</label><select id="chatMode"><option value="auto">Auto</option><option value="general">General</option><option value="coding">Coding</option><option value="reasoning">Reasoning</option><option value="creative">Creative</option><option value="precise">Precise</option><option value="translation">Translation</option></select></div><div class="field"><label>Thinking</label><select id="reasoningMode"><option value="auto">Auto per request</option><option value="on">Always on</option><option value="off">Always off</option></select></div><div class="field"><label>Thinking budget</label><input id="reasoningBudget" type="number" min="-1" max="32768" value="${p.reasoningBudget}"><small>-1 = model default</small></div><div class="field"><label>Maximum new tokens</label><input id="maxTokens" type="number" min="16" max="32768" value="${p.maxTokens}"></div></div><div class="inline-actions"><button class="secondary-button" data-no>Cancel</button><button class="primary-button" data-yes>Save</button></div></div></div>`;
    $('#chatMode').value=p.mode;$('#reasoningMode').value=p.reasoning;$('[data-no]',root).onclick=()=>root.innerHTML='';$('[data-yes]',root).onclick=()=>{localStorage.setItem('lf.chatMode',$('#chatMode').value);localStorage.setItem('lf.reasoning',$('#reasoningMode').value);localStorage.setItem('lf.reasoningBudget',$('#reasoningBudget').value);localStorage.setItem('lf.maxTokens',$('#maxTokens').value);root.innerHTML='';renderChat($('#view'));toast('Generation controls saved')};
  }

  async function refreshState(forceRender=false){
    try{
      const next=await api('/api/state'); App.state=next; App.lastStateError='';
      if(!(App.route==='chat'&&App.streaming))updateChrome();
      const key=stateRenderKey(next);
      // Do not destroy/recreate the Settings controls while a memory-mode choice
      // is pending. Replacing that DOM node was the reason the selector appeared
      // to "jump" back to Hybrid before the user could apply it.
      if(App.route==='settings'&&(App.settingsMemoryDirty||App.settingsFormDirty)&&!forceRender){
        updateLiveMetrics(next.live,next.performance||next.server?.performance);
      }else if(forceRender || key!==App.renderKey){App.renderKey=key;if(App.route==='chat'&&$('#chatScroll'))updateChatStateOnly();else render();}
      else updateLiveMetrics(next.live,next.performance||next.server?.performance);
      if(App.route==='logs')refreshLogs();
    }catch(e){
      if(App.lastStateError!==e.message){App.lastStateError=e.message;toast('LlamaForge backend is unavailable',e.message,'error',6000)}
    }
  }
  function updateLiveMetrics(live={},perf={}){
    if(App.state){App.state.live={...(App.state.live||{}),...live};App.state.performance={...(App.state.performance||{}),...perf};if(App.state.server)App.state.server.performance=App.state.performance;}
    const set=(sel,text)=>{const el=$(sel);if(el&&el.textContent!==text)el.textContent=text};
    set('[data-live="process-cpu"]',`${Number(live.process_cpu_percent||0).toFixed(0)}%`);
    set('[data-live="system-cpu"]',`System ${Number(live.cpu_percent||0).toFixed(0)}%`);
    set('[data-live="ram-used"]',`${Number(live.ram_used_gb||0).toFixed(1)} / ${Number(live.ram_total_gb||0).toFixed(1)} GB`);
    set('[data-live="ram-free"]',`${Number(live.ram_available_gb||0).toFixed(1)} GB available`);
    set('[data-live="cpu-pill"]',`llama.cpp ${Number(live.process_cpu_percent||0).toFixed(0)}%`);
    set('#perfProcessCpu',`${Number(perf.process_cpu_percent||0).toFixed(0)}%`);
    set('#perfTarget',`${Number(perf.target_percent||cpuThreadPrefs().target_percent)}%`);
    const names={"cpu-saturated":"CPU saturated","paging-bound":"SSD/page-cache bound","memory-bound":"Memory-bandwidth bound","not-cpu-bound":"Not CPU-bound","balanced":"Balanced workload","loading":"Loading model","idle":"Waiting for model"};
    if(perf.state)set('#perfTitle',names[perf.state]||'Performance status');
    if(perf.detail)set('#perfDetail',perf.detail);
  }
  let eventRefreshTimer=0;
  function scheduleStateRefresh(){
    if(eventRefreshTimer)return;
    eventRefreshTimer=setTimeout(()=>{eventRefreshTimer=0;refreshState(false)},80);
  }
  function connectEvents(){
    if(App.eventSource)try{App.eventSource.close()}catch{}
    const es=new EventSource('/api/events'); App.eventSource=es;
    es.onmessage=e=>{
      let ev;try{ev=JSON.parse(e.data)}catch{return}
      if(ev.type==='metrics'){updateLiveMetrics(ev.live||{},ev.performance||{});return}
      if(ev.type==='log'){
        if(App.route==='logs'){const out=$('#logOutput');if(out){const near=out.scrollTop+out.clientHeight>=out.scrollHeight-80;out.textContent+=(out.textContent?'\n':'')+(ev.line||'');const count=$('#logCount');if(count)count.textContent=`${out.textContent?out.textContent.split('\n').length:0} lines`;if(near)out.scrollTop=out.scrollHeight;}}
        return;
      }
      if(ev.type==='brain'){if(ev.brain){const before=brainStructuralKey(App.state?.brain||{});if(App.state)App.state.brain=ev.brain;patchBrainChatState(ev.brain);patchBrainPageState(ev.brain);if(App.route==='brain'&&brainStructuralKey(ev.brain)!==before)scheduleStateRefresh();}return;}
      if(['state','models','job','connected'].includes(ev.type))scheduleStateRefresh();
    };
    es.onerror=()=>{ /* EventSource reconnects automatically; never rebuild the UI here. */ };
  }
  function updateChatStateOnly(){
    updateChrome();const ss=App.state?.server||{},m=App.state?.active_model;
    const brain=App.state?.brain||{};App.brainLearning=brain.job?.state==='running'&&brain.job?.stage!=='setup';const ta=$('#composerInput'),send=$('#sendButton');if(ta){ta.disabled=!ss.ready||App.brainLearning;ta.placeholder=App.brainLearning?'Learning this turn into weights…':ss.ready?'Message your local model…':ss.running?'Model is loading…':'Load a model to start chatting'}if(send&&!App.streaming)send.disabled=!ss.ready||App.brainLearning;
    const status=$('.local-status');if(status){status.classList.toggle('ready',!!ss.ready);status.innerHTML=`<span></span>${ss.ready?'Ready':ss.running?'Loading':'Offline'}`}
    const mb=$('#chatModelButton');if(mb&&m){const name=$('.model-switch-name',mb),quant=$('.model-switch-quant',mb);if(name)name.textContent=shortName(m.name,34);if(quant)quant.textContent=m.quantization||'GGUF'}
    updateContextUI();
  }

  function showCommandPalette(){
    const root=$('#modalRoot');
    const commands=[
      ['New chat','Start a fresh conversation','plus',()=>newThread()],
      ['Chat','Return to the active conversation','chat',()=>setRoute('chat')],
      ['Models','Choose or inspect a GGUF','models',()=>setRoute('models')],
      ['Brain','Weight-based personal learning','brain',()=>setRoute('brain')],
      ['Optimize','See smart launch plans','tune',()=>setRoute('optimize')],
      ['Runtime','Manage llama.cpp','runtime',()=>setRoute('runtime')],
      ['Logs','Open runtime diagnostics','logs',()=>setRoute('logs')],
      ['Settings','Open LlamaForge preferences','settings',()=>setRoute('settings')],
      ...(App.state?.server?.running?[['Unload model','Stop llama-server and release model memory','stop',()=>unloadModel()]]:[]),
      ['Exit LlamaForge','Unload model and stop the local control plane','x',()=>exitLlamaForge()],
    ];
    if(App.streaming)commands.unshift(['Stop generation','Stop the current local response','stop',()=>stopGeneration()]);
    root.innerHTML=`<div class="modal-backdrop command-backdrop"><div class="command-palette"><div class="command-search">${icon('search')}<input id="commandInput" placeholder="Search commands…" autocomplete="off"></div><div id="commandList" class="command-list"></div><div class="command-footer"><span>↑↓ navigate</span><span>Enter select</span><span>Esc close</span></div></div></div>`;
    const list=$('#commandList'); let visible=commands.slice(),active=0;
    const paint=()=>{list.innerHTML=visible.map((c,i)=>`<button class="command-item ${i===active?'active':''}" data-command="${i}"><span class="command-icon">${icon(c[2])}</span><span><strong>${escapeHtml(c[0])}</strong><small>${escapeHtml(c[1])}</small></span><span class="command-enter">↵</span></button>`).join('')||'<div class="command-empty">No matching commands</div>';$$('[data-command]',list).forEach(b=>b.onclick=()=>run(Number(b.dataset.command)))};
    const run=i=>{const c=visible[i];if(!c)return;root.innerHTML='';c[3]()};
    const input=$('#commandInput'); input.oninput=()=>{const q=input.value.trim().toLowerCase();visible=commands.filter(c=>(c[0]+' '+c[1]).toLowerCase().includes(q));active=0;paint()};
    input.onkeydown=e=>{if(e.key==='ArrowDown'){e.preventDefault();active=Math.min(visible.length-1,active+1);paint()}else if(e.key==='ArrowUp'){e.preventDefault();active=Math.max(0,active-1);paint()}else if(e.key==='Enter'){e.preventDefault();run(active)}else if(e.key==='Escape'){root.innerHTML=''}};
    $('.command-backdrop',root).onclick=e=>{if(e.target.classList.contains('command-backdrop'))root.innerHTML=''};
    paint();setTimeout(()=>input.focus(),10);
  }

  // shell events
  $('#newChatButton').onclick=newThread; if($('#clearLocalChats'))$('#clearLocalChats').onclick=clearAllThreads;
  $('#openSettings').onclick=()=>setRoute('settings');
  $('#modelPill').onclick=()=>setRoute('models');
  $('#connectionPill').onclick=()=>setRoute(App.state?.server?.error?'logs':'runtime');
  $('#collapseSidebar').onclick=()=>{App.sidebarCollapsed=!App.sidebarCollapsed;localStorage.setItem('lf.sidebarCollapsed',App.sidebarCollapsed?'1':'0');renderNav()};
  $('#mobileNavButton').onclick=()=>document.querySelector('.app-shell').classList.add('mobile-menu');
  $('#sidebarToggle').onclick=()=>document.querySelector('.app-shell').classList.remove('mobile-menu');
  window.addEventListener('hashchange',()=>{const r=location.hash.slice(1);if(r&&r!==App.route){App.route=r;renderNav();render()}});
  window.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();showCommandPalette()}if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='n'){e.preventDefault();newThread()}if(e.key==='Escape'&&App.streaming){e.preventDefault();stopGeneration()}});

  // first paint
  renderNav();
  $('#view').innerHTML='<div class="page"><div style="height:55vh;display:grid;place-items:center;color:var(--muted)"><div><div class="brand-mark" style="margin:0 auto 15px"></div><div>Starting local workspace…</div></div></div></div>';
  refreshState(true).then(()=>{if(App.route==='chat'&&!App.activeThreadId)newThread();connectEvents()});
})();
