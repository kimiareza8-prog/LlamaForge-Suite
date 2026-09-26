/* UI values that must be independent of the browser's timezone and DOM. */
(function(root){
  'use strict';
  function calendarPayload(form){
    const title=String(form.title||'').trim(),date=String(form.date||'');
    if(!title)throw new Error('عنوان رویداد را بنویسید.');
    const day=new Date(date+'T00:00:00Z');
    if(!/^\d{4}-\d{2}-\d{2}$/.test(date)||!Number.isFinite(+day)||day.toISOString().slice(0,10)!==date)throw new Error('تاریخ معتبر انتخاب کنید.');
    const time=form.allDay?'00:00':String(form.time||'');
    if(!/^([01]\d|2[0-3]):[0-5]\d$/.test(time))throw new Error('ساعت معتبر انتخاب کنید.');
    const minutes=form.allDay?1440:Number(form.duration);
    if(!Number.isInteger(minutes)||minutes<=0||minutes>525600)throw new Error('مدت رویداد باید مثبت باشد.');
    // Arithmetic uses UTC only as a wall-clock calculator. Naive timestamps are
    // interpreted by CalendarStore in the server timezone shown in the dialog.
    const start=date+'T'+time+':00';
    const end=new Date(Date.parse(start+'Z')+minutes*60000).toISOString().slice(0,19);
    const reminder=form.reminder===''||form.reminder==null?null:Number(form.reminder);
    if(reminder!==null&&(!Number.isInteger(reminder)||reminder<0))throw new Error('زمان یادآوری معتبر نیست.');
    return {operation:form.id?'update':'create',...(form.id?{id:form.id}:{}),title,start,end,all_day:!!form.allDay,
      notes:String(form.notes||''),location:String(form.location||''),reminders:reminder===null?[]:[reminder]};
  }
  function eventsForDay(events,day){
    const start=day+'T00:00:00';
    const end=new Date(Date.parse(start+'Z')+86400000).toISOString().slice(0,19);
    return events.filter(e=>String(e.start).slice(0,19)<end&&String(e.end||e.start).slice(0,19)>start);
  }
  const api={calendarPayload,eventsForDay};
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.LFWorkspaceUI=api;
})(typeof globalThis!=='undefined'?globalThis:this);
