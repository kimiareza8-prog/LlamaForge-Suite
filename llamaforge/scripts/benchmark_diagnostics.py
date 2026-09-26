"""Run from llamaforge/: PYTHONPATH=. python scripts/benchmark_diagnostics.py.
Synthetic observability overhead only; does not measure model inference.
"""
import json,time,statistics,sys,tempfile,tracemalloc
from pathlib import Path
from llamaforge.core.request_tracing import TraceStore,record,logged_model
workspace=tempfile.TemporaryDirectory(prefix='llamaforge-trace-benchmark-')
root=Path(workspace.name)
messages=[{'role':'user','content':('برای فردا جلسه بساز.\n'+'Available operations: now, create, list; external_write=false.\n')*100}]
def run(enabled):
 store=TraceStore(root/('on' if enabled else 'off'),max_traces=8);store.enabled=enabled
 times=[];tracemalloc.start()
 for i in range(35):
  start=time.perf_counter()
  with store.request(origin='benchmark',messages=messages) as t:
   logged_model(lambda:{'content':'{"action":"tool","skill":"calendar","arguments":{"operation":"now"}}'},messages,stage='planner')
   record('tool.start',name='calendar',arguments={'operation':'now'})
   record('tool.end',result={'ok':True,'result':{'iso':'2026-09-26T10:00+03:30'}})
   record('response.output',text='fixture response')
  times.append((time.perf_counter()-start)*1000)
 _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
 return {'median_ms':round(statistics.median(times[5:]),3),'p95_ms':round(sorted(times[5:])[28],3),'python_peak_bytes':peak,'retained_files':len(store.list()),'retained_bytes':sum(p.stat().st_size for p in store.root.glob('*'))}
result={'fixture':'35 requests / 5 warmup, approximately 8 KB prompt, mocked model; no inference benchmark','disabled':run(False),'enabled':run(True)}
print(json.dumps(result,indent=2))
workspace.cleanup()
