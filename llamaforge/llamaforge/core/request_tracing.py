"""Local, bounded request transcripts. Credentials are removed before disk I/O.

This records observable model/tool inputs and outputs, not inferred reasoning.
Streaming stays live; adjacent output deltas are joined before redaction so a
credential split across transport chunks cannot leak into a persistent log.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid
import zipfile

from .redaction import redact, secret_key

_TRACE = ContextVar('llamaforge_request_trace', default=None)
_CALL = ContextVar('llamaforge_trace_call', default='')
_ID = re.compile(r'trace_[a-f0-9]{32}\Z')


def current_trace():
    return _TRACE.get()


def record(event, /, **data):
    trace = current_trace()
    if trace is not None:
        trace.emit(event, **data)


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _reject_constant(value):
    raise ValueError('Non-finite JSON value')


def _plain(value):
    if isinstance(value, dict): return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_plain(v) for v in value]
    if isinstance(value, Path): return str(value)
    if value is None or isinstance(value, (str, int, float, bool)): return value
    if isinstance(value, bytes): return {'binary_omitted': True, 'bytes': len(value), 'sha256': hashlib.sha256(value).hexdigest()}
    return {'unserialized_type': type(value).__name__}


def _binary(value, key=''):
    if isinstance(value, dict): return {k: _binary(v, k) for k, v in value.items()}
    if isinstance(value, list): return [_binary(v) for v in value]
    if isinstance(value, str) and (value.startswith('data:') or key.lower() in {'data_base64','base64'}):
        return {'binary_omitted': True, 'encoded_chars': len(value),
                'sha256': hashlib.sha256(value.encode()).hexdigest(),
                'media_type': value.split(';', 1)[0][5:] if value.startswith('data:') else 'base64'}
    return value


class RequestTrace:
    def __init__(self, store, metadata):
        self.store = store
        self.id = 'trace_' + uuid.uuid4().hex
        self.path = store.root / (self.id + '.jsonl')
        self.meta_path = store.root / (self.id + '.json')
        self.lock = threading.RLock()
        self.started = time.monotonic()
        self.seq = self.size = 0
        self.limited = False
        self.closed = False
        self.secrets = set()
        self.meta = {'id': self.id, 'schema': 1, 'origin': metadata.get('origin','local'),
                     'started_at': datetime.now(timezone.utc).isoformat(), 'status':'running', 'complete':True,
                     'message_id': metadata.get('message_id',''), 'request_id':metadata.get('request_id','')}
        messages=metadata.get('messages') or []
        self.meta['preview']=next((str(m.get('content') or '')[:180] for m in reversed(messages) if isinstance(m,dict) and m.get('role')=='user'),'')
        self.emit('request.start', **metadata)
        self._save_meta()

    def sanitize(self, value):
        value = _plain(value)
        def collect(v):
            if isinstance(v, dict):
                for k, item in v.items():
                    if secret_key(k) and isinstance(item,str) and len(item)>=4: self.secrets.add(item)
                    else: collect(item)
            elif isinstance(v,list):
                for item in v: collect(item)
        collect(value)
        value = redact(_binary(value))
        def replace(v):
            if isinstance(v,str):
                for secret in sorted(self.secrets,key=len,reverse=True): v=v.replace(secret,'[redacted]')
                return v
            if isinstance(v,dict): return {k:replace(x) for k,x in v.items()}
            if isinstance(v,list): return [replace(x) for x in v]
            return v
        return replace(value)

    def _save_meta(self):
        try:
            self.meta.update(events=self.seq, bytes=self.size, elapsed_ms=round((time.monotonic()-self.started)*1000,3))
            tmp=self.meta_path.with_suffix('.tmp')
            tmp.write_text(_json(self.sanitize(self.meta)), encoding='utf-8')
            tmp.replace(self.meta_path)
        except Exception as exc:
            self.store.last_error=redact(str(exc));self.meta['complete']=False

    def emit(self, event, /, **data):
        with self.lock:
            if self.closed or (self.limited and event!='request.end'): return
            try:
                row={'schema':1,'trace_id':self.id,'seq':self.seq+1,'at':datetime.now(timezone.utc).isoformat(),
                     'elapsed_ms':round((time.monotonic()-self.started)*1000,3),'thread':threading.current_thread().name,
                     'call_id':_CALL.get(),'event':event,'data':self.sanitize(data)}
                encoded=(_json(row)+'\n').encode('utf-8')
                if self.size+len(encoded)>self.store.max_trace_bytes and event!='request.end':
                    self.limited=True;self.meta['complete']=False
                    row.update(event='trace.limit',data={'limit_bytes':self.store.max_trace_bytes,'dropped_event':event,
                        'reason':'Transcript size limit reached; subsequent payloads omitted.'})
                    encoded=(_json(row)+'\n').encode('utf-8')
                with self.path.open('ab') as f:f.write(encoded)
                self.seq+=1;self.size+=len(encoded)
                self.meta.update(events=self.seq,bytes=self.size)
            except Exception as exc:
                self.store.last_error=redact(str(exc));self.meta['complete']=False

    def finish(self,status,error=''):
        with self.lock:
            self.meta.update(status=status,error=redact(error))
            self.emit('request.end',status=status,error=error,complete=self.meta['complete'])
            self._save_meta()
            self.closed=True


class TraceStore:
    def __init__(self, root, *, max_traces=100, max_trace_bytes=32*1024*1024, max_total_bytes=256*1024*1024):
        self.root=Path(root);self.max_traces=max(1,max_traces);self.max_trace_bytes=max_trace_bytes
        self.max_total_bytes=max_total_bytes;self.enabled=True;self.last_error=''
        self.lock=threading.RLock();self.active={}

    @contextmanager
    def request(self, **metadata):
        trace=None
        if self.enabled:
            try:
                with self.lock:
                    self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
                    trace=RequestTrace(self,metadata);self.active[trace.id]=trace
                    self._prune()
            except Exception as exc:self.last_error=redact(str(exc))
        token=_TRACE.set(trace);call_token=_CALL.set('')
        try:
            yield trace
        except GeneratorExit:
            if trace:trace.finish('disconnected')
            raise
        except BaseException as exc:
            if trace:trace.finish('cancelled' if 'cancel' in str(exc).lower() else 'error',str(exc))
            raise
        else:
            if trace:trace.finish(trace.meta.get('outcome','completed'),trace.meta.get('error',''))
        finally:
            _CALL.reset(call_token);_TRACE.reset(token)
            if trace:
                with self.lock:
                    self.active.pop(trace.id,None);self._prune()

    def _path(self, trace_id, suffix='.jsonl'):
        if not _ID.fullmatch(str(trace_id)): raise ValueError('Invalid trace id')
        path=self.root/(trace_id+suffix)
        if path.is_symlink():raise ValueError('Trace symlinks are not allowed')
        return path

    def list(self):
        rows=[]
        with self.lock:
            ids={p.stem for pattern in ('trace_*.json','trace_*.jsonl') for p in self.root.glob(pattern)}
            for trace_id in ids:
                if not _ID.fullmatch(trace_id):continue
                try:
                    self._path(trace_id);self._path(trace_id,'.json')
                    row=self._metadata(trace_id)
                    rows.append(row)
                except (ValueError,OSError):continue
        return sorted(rows,key=lambda x:x['started_at'],reverse=True)

    def _metadata(self, trace_id):
        """Called under the store lock. Corrupt sidecars cannot hide good logs."""
        active=self.active.get(trace_id)
        if active:
            with active.lock:return active.sanitize(dict(active.meta))
        path=self._path(trace_id,'.json')
        try:
            row=json.loads(path.read_text(encoding='utf-8'),parse_constant=_reject_constant)
            if (not isinstance(row,dict) or row.get('id')!=trace_id
                    or not isinstance(row.get('started_at'),str)
                    or not isinstance(row.get('complete'),bool)
                    or row.get('status') not in {'running','completed','error','cancelled','disconnected','interrupted'}):
                raise ValueError('Invalid trace metadata')
            datetime.fromisoformat(row['started_at'])
            if row['status']=='running':row.update(status='interrupted',complete=False)
            return redact(row)
        except (ValueError,OSError,TypeError):
            source=path if path.is_file() else self._path(trace_id)
            started=datetime.fromtimestamp(source.stat().st_mtime,timezone.utc).isoformat()
            return {'id':trace_id,'schema':1,'origin':'unknown','started_at':started,
                    'status':'interrupted','complete':False,'events':0,
                    'read_errors':['Metadata is missing or invalid; recovered event data only.']}

    def _snapshot(self, trace_id):
        """Freeze bytes + metadata together; parse/compress outside writer locks."""
        with self.lock:
            path=self._path(trace_id)
            active=self.active.get(trace_id)
            with active.lock if active else nullcontext():
                meta=self._metadata(trace_id)
                errors=list(meta.get('read_errors') or [])
                try:
                    with path.open('rb') as f:
                        data=f.read(self.max_trace_bytes+65536)
                        if f.read(1):errors.append('Event file exceeds the capture limit; remaining bytes omitted.')
                except FileNotFoundError:
                    if not self._path(trace_id,'.json').exists():raise
                    data=b'';errors.append('Event file is missing.')
        try:text=data.decode('utf-8')
        except UnicodeDecodeError:
            text=data.decode('utf-8',errors='replace');errors.append('Event file contains incomplete UTF-8.')
        rows=[]
        previous=0
        for line_number,line in enumerate(text.splitlines(),1):
            if not line.strip():continue
            try:
                row=json.loads(line,parse_constant=_reject_constant)
                if (not isinstance(row,dict) or row.get('trace_id')!=trace_id
                        or not isinstance(row.get('event'),str) or not isinstance(row.get('data'),dict)
                        or type(row.get('seq')) is not int or row['seq']<=previous
                        or not isinstance(row.get('elapsed_ms'),(int,float))):
                    raise ValueError('Invalid event record')
                if row['seq']!=previous+1:errors.append(f'Missing event before line {line_number}.')
                rows.append(row);previous=row['seq']
            except (ValueError,TypeError):
                errors.append(f'Invalid or partial event at line {line_number}; omitted.')
        terminal=bool(rows and rows[-1]['event']=='request.end' and rows[-1]['data'].get('complete'))
        complete=bool(meta.get('complete') and terminal and not errors
                      and meta.get('events')==len(rows) and meta.get('status') not in {'running','interrupted'})
        meta={**meta,'events':len(rows),'complete':complete,'read_errors':errors}
        return meta,rows

    def _prune(self):
        try:
            # Retention needs age/size only. Do not parse and redact the entire
            # transcript index twice on every request; reserve that for the UI.
            rows={}
            with os.scandir(self.root) as entries:
                for entry in entries:
                    trace_id, _, suffix=entry.name.rpartition('.')
                    if suffix not in {'json','jsonl'} or not _ID.fullmatch(trace_id):continue
                    if not entry.is_file(follow_symlinks=False):continue
                    info=entry.stat(follow_symlinks=False)
                    row=rows.setdefault(trace_id,{'id':trace_id,'updated':0,'bytes':0})
                    row['updated']=max(row['updated'],info.st_mtime_ns)
                    row['bytes']+=info.st_size
            total=sum(row['bytes'] for row in rows.values());count=len(rows)
            for row in sorted(rows.values(),key=lambda row:row['updated']):
                if count<=self.max_traces and total<=self.max_total_bytes:break
                if row['id'] in self.active:continue
                paths=(self._path(row['id']),self._path(row['id'],'.json'))
                for path in paths:path.unlink(missing_ok=True)
                total-=row['bytes'];count-=1
        except Exception as exc:self.last_error=redact(str(exc))

    def events(self, trace_id):
        return self._snapshot(trace_id)[1]

    def export(self, trace_id):
        meta,rows=self._snapshot(trace_id)
        meta={**meta,'exported_at':datetime.now(timezone.utc).isoformat(),
              'notes':['Credential fields redacted before storage. Binary attachments represented by metadata.',
                       'Prompts and model/tool outputs are diagnostic data. No inferred internal reasoning.',
                       'Stream output is assembled for redaction; delivery to the user remains live.']}
        report=['# LlamaForge request transcript',f"Trace: {trace_id}",f"Status: {meta['status']} · complete: {meta['complete']}",
                'Contains user prompts, model input/output and tool observations. Review before sharing.']
        if meta['read_errors']:report.append('Recovery notes:\n'+'\n'.join(meta['read_errors']))
        def human(value, path='data'):
            if isinstance(value, dict) and value:
                return '\n\n'.join(human(v, path+'.'+k) for k,v in value.items())
            if isinstance(value, list) and value:
                return '\n\n'.join(human(v, f'{path}[{i}]') for i,v in enumerate(value))
            return path+':\n\n'+(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False))
        for row in rows:
            report.extend([f"\n## {row['seq']} · {row['event']} · {row['elapsed_ms']} ms",human(row['data'])])
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('manifest.json',json.dumps(meta,ensure_ascii=False,indent=2))
            z.writestr('events.jsonl',''.join(_json(row)+'\n' for row in rows))
            z.writestr('report.md','\n\n'.join(report))
        return out.getvalue()

    def status(self):
        return {'enabled':self.enabled,'directory':str(self.root),'max_traces':self.max_traces,
                'max_trace_bytes':self.max_trace_bytes,'max_total_bytes':self.max_total_bytes,'error':self.last_error}


def model_stage(messages):
    text=str((messages[0] if messages else {}).get('content',''))
    if 'previous' in text[:100].lower() and ('invalid' in text[:200].lower() or 'not valid' in text[:200].lower()):return 'repair'
    if 'stage 0' in text[:100]:return 'router'
    if 'stage 1' in text[:100]:return 'capabilities'
    if 'control brain' in text[:100]:return 'planner'
    return 'answer'


def logged_model(fn,messages,*,stage='',**options):
    if current_trace() is None:return fn()
    started=time.monotonic();token=_CALL.set('model_'+uuid.uuid4().hex[:16])
    record('model.start',stage=stage or model_stage(messages),messages=messages,options=options)
    try:
        result=fn()
        record('model.end',status='completed',result=result,elapsed_ms=round((time.monotonic()-started)*1000,3))
        return result
    except BaseException as exc:
        record('model.end',status='cancelled' if 'cancel' in str(exc).lower() else 'error',error=str(exc),elapsed_ms=round((time.monotonic()-started)*1000,3))
        raise
    finally:_CALL.reset(token)


def logged_stream(iterator,messages,*,stage='final',**options):
    if current_trace() is None:
        try:yield from iterator
        finally:
            if hasattr(iterator,'close'):iterator.close()
        return
    started=time.monotonic();token=_CALL.set('model_'+uuid.uuid4().hex[:16])
    record('model.start',stage=stage,messages=messages,stream=True,options=options)
    parts={'text':[],'reasoning':[]};count=0;first=None;status='completed';error='';usage={};chars=0;limited=False
    try:
        for event in iterator:
            typ=event.get('type') if isinstance(event,dict) else ''
            if typ in parts and event.get('delta'):
                count+=1
                if first is None and typ=='text':first=round((time.monotonic()-started)*1000,3)
                text=str(event['delta']);chars+=len(text)
                # Bound memory even when a broken runtime ignores max_tokens.
                if chars<=current_trace().store.max_trace_bytes:parts[typ].append(text)
                elif not limited:
                    limited=True;current_trace().meta['complete']=False
                    record('stream.limit',reason='Output capture limit reached')
            elif typ=='meta':
                if event.get('usage'):usage=event['usage']
                record('model.meta',**event)
            elif typ=='error':status='error';error=str(event.get('error',''))
            yield event
    except GeneratorExit:
        status='disconnected';raise
    except BaseException as exc:
        status='cancelled' if 'cancel' in str(exc).lower() else 'error';error=str(exc);raise
    finally:
        try:
            if hasattr(iterator,'close'):iterator.close()
        finally:
            record('model.end',status=status,error=error,result={k:''.join(v) for k,v in parts.items()},usage=usage,
                   delta_count=count,ttft_ms=first,elapsed_ms=round((time.monotonic()-started)*1000,3),output_capture_limited=limited)
            _CALL.reset(token)
