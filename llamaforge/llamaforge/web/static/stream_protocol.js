(function(root) {
  'use strict';
  class SSEDecoder {
    constructor() { this.buffer=''; this.lastId=0; this.done=false; }
    push(chunk) {
      this.buffer+=chunk;
      if(this.buffer.length>1048576)throw new Error('Stream event exceeds limit');
      const frames=this.buffer.split(/\r?\n\r?\n/); this.buffer=frames.pop();
      const out=[];
      for(const frame of frames){
        const lines=frame.split(/\r?\n/), data=lines.filter(x=>x.startsWith('data:')).map(x=>x.slice(5).trimStart()).join('\n');
        if(!data)continue;
        if(data==='[DONE]'){this.done=true;continue;}
        const id=Number((lines.find(x=>x.startsWith('id:'))||'').slice(3).trim());
        if(id&&id<=this.lastId)continue;
        const event=JSON.parse(data);
        if(id)this.lastId=id;
        out.push(event);
      }
      return out;
    }
  }
  class EventCursor {
    constructor(){this.revision=0;}
    accept(event){
      const n=Number(event.revision||0);
      if(event.type==='resync'){this.revision=n;return true;}
      if(!n)return true;
      if(n<=this.revision)return false;
      this.revision=n;return true;
    }
  }
  const draftValue=(draft,key,fallback)=>Object.prototype.hasOwnProperty.call(draft,key)?draft[key]:fallback;
  function contextValue(text,max){
    const n=Number(text);
    if(!String(text).trim()||!Number.isInteger(n)||n<512||n>max)throw new Error(`Context must be an integer between 512 and ${max}`);
    return n;
  }
  function brainActivity(brain,pending=false){
    const job=brain.job||{};
    const busy=['running','cancelling'].includes(job.state);
    const setup=busy&&['setup','auto-setup','detect-base','trainer'].includes(job.stage);
    if(['done','error','cancelled'].includes(job.state))pending=false;
    return {setup,learning:busy&&!setup,pending,locked:(busy&&!setup)||pending};
  }
  const api={SSEDecoder,EventCursor,draftValue,contextValue,brainActivity};
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.LFProtocol=api;
})(typeof globalThis!=='undefined'?globalThis:this);
