"""Published workload/instrumentation from the reviewed A/B experiment.

Loaded by _history_ab_worker.py under an archived source version.
Timing, accounting, replay and instrumentation functions retain the measured logic.
"""
import argparse, cProfile, functools, gc, hashlib, json, math, os, platform, pstats
from pathlib import Path
import random, shutil, sqlite3, statistics, subprocess, sys, time, tracemalloc
from decimal import Decimal
from collections import defaultdict
import market_microstructure.funded_journal as fj
from market_microstructure.funded_journal import FundedJournal
from market_microstructure.funded_orders import FundedOrderManager
from market_microstructure.oms_operations import OperationalJournal
from market_microstructure.order_lifecycle import OrderManager
from market_microstructure.fix_adapter import FixJournal, new_order
from market_microstructure.simulated_venue import SimulatedVenue
from market_microstructure.synthetic_fix import SyntheticFixVenue
from market_microstructure.trading_session import TradingSession

ROOT=Path(__file__).resolve().parent
OUT=Path(__file__).resolve().parent
SHA='7ac443302ed6b5c29114c7ebf4522c05ee0c79af'
SIZES=[10,50,150,500]; MODES={'native':FundedJournal,'operational':OperationalJournal,'fix':FixJournal}
REG={'A':dict(price_tick='1',quantity_step='1')}
KIND='SYNTHETIC_TEST_ONLY'
POLICY=dict(source_kind=KIND,initial_cash='1000000',session_id='bench',max_quote_age_ns=10,
 risk_limits=dict(max_order_notional='1000000',max_symbol_gross={'A':'1000000'},max_portfolio_gross='1000000'))
MARKS={'A':dict(quote_id='q',timestamp_ns=0,available_ns=0,session_id='bench',valid=True,price='100')}
def order(key,quantity='1'):return dict(source_kind=KIND,client_order_id=key,symbol='A',side='buy',quantity=quantity,limit_price='100')
def save(name,obj):(OUT/name).write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n')
def log(*args):print(time.strftime('%H:%M:%S'),*args,flush=True)
def baseline():
 raise RuntimeError('Use benchmark_history_ab.py; the worker installs source guards')

def environment():
 def optional(*args):
  try:return subprocess.check_output(args,text=True,stderr=subprocess.DEVNULL).strip()
  except (OSError,subprocess.CalledProcessError):return None
 with sqlite3.connect(':memory:') as c:options=[x[0] for x in c.execute('PRAGMA compile_options')]
 return dict(commit=SHA,python=sys.version,platform=platform.platform(),machine=platform.machine(),
 cpu=optional('/usr/sbin/sysctl','-n','machdep.cpu.brand_string') or platform.processor() or None,
 physical_cores=optional('/usr/sbin/sysctl','-n','hw.physicalcpu'),logical_cores=os.cpu_count(),
 ram_bytes=optional('/usr/sbin/sysctl','-n','hw.memsize'),sqlite=sqlite3.sqlite_version,
 sqlite_compile_options=options,gc_enabled=gc.isenabled(),gc_threshold=gc.get_threshold(),
 pythonhashseed=os.environ.get('PYTHONHASHSEED'),random_seed=314159,
 source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'src/market_microstructure').glob('*.py'))},
 harness_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
 protocol='Fresh process/DB copy per observation; warm setup; full replay and durability retained; normal timing separate from instrumentation.',
 tail_policy='Empirical p95 only; 21 submit pairs or 7 other pairs in full mode. Smoke mode is not performance evidence.',
 fixture_note='Startup validates RECOVERY entry from a synthetic idle backup, not cold-disk or power-loss timing.')

def fixtures():
 baseline(); fd=OUT/'fixtures';fd.mkdir(exist_ok=True)
 assert not (OUT/'cases.json').exists(),'Refuse to replace fixtures; use a fresh output directory for a new run'
 cases=[];settings={}
 for mode,cls in MODES.items():
  groups={1:SIZES}
  for active,hs in groups.items():
   path=fd/f'seed-{mode}-{active}.sqlite'
   owner=cls.create(path,REG,POLICY) if mode=='native' else TradingSession.create(path,REG,POLICY,journal_type=cls)
   journal=owner if mode=='native' else owner.journal
   venue=SyntheticFixVenue(REG) if mode=='fix' else SimulatedVenue(REG)
   if mode!='native':owner.control('seed-normal','NORMAL',now_ns=0,reason='empty')
   for i in range(active):
    key=f'active-{i}';outgoing=owner.submit('submit-'+key,order(key,'10'),now_ns=0,marks=MARKS)['outgoing'];assert outgoing
    if mode=='fix':
     ack=venue.new(new_order(outgoing));owner.receive('ack-'+key,ack)
     first=venue.execute(key,'1','99');owner.receive('fill-'+key,first)
    else:
     ack=venue.submit(outgoing);owner.apply('ack-'+key,ack)
     first=venue.execute(key,'1','99');owner.apply('fill-'+key,first)
    if i==0:duplicate=first
   queries=[venue.query(f'active-{i}') for i in range(active)]
   query=venue.query('active-0');fill=venue.execute('active-0','1','99')
   targets={h:(h if mode=='native' else h-active-2) for h in hs}
   checkpoints=set(hs)|set(targets.values())
   count=owner.recover()['committed_commands'];assert min(checkpoints)>=count
   captured={};pad=0
   while count<=max(checkpoints):
    if count in checkpoints:
     target=fd/f'{mode}-a{active}-h{count}.sqlite'
     with sqlite3.connect(target) as dest:journal._connection.backup(dest)
     captured[count]=str(target)
     log('fixture',mode,'A',active,'H',count)
    if count==max(checkpoints):break
    result=owner.submit(f'pad-{pad}',order('denied'),now_ns=0,marks={});assert not result['result']['accepted']
    count+=1;pad+=1
   settings[mode]={p:journal._connection.execute('PRAGMA '+p).fetchone()[0] for p in ['journal_mode','synchronous','busy_timeout','page_size','cache_size','locking_mode','temp_store','fullfsync']}
   owner.close()
   for h in hs:
    families=['fixed'] if active==1 else ['growing']
    for family in families:
     cases.append(dict(mode=mode,H=h,active=active,family=family,raw=captured[h],steady=captured[targets[h]],
      queries=queries,query=query,fill=fill,duplicate=duplicate,
      raw_sha256=hashlib.sha256(Path(captured[h]).read_bytes()).hexdigest(),
      steady_sha256=hashlib.sha256(Path(captured[targets[h]]).read_bytes()).hexdigest()))
 save('cases.json',cases);env=environment();env['settings']=settings;save('environment.json',env)
 log('fixtures complete',len(cases),'cases')

OPS=['submit','policy_reject','invalid_rollback','fill','reconcile','recover','validate','reopen','duplicate_command','duplicate_execution']
def reconcile(owner,mode,key,message,command):
 if mode=='fix':return owner.reconcile_fix(command,message,now_ns=0,deadline_ns=5)
 if mode=='operational':return owner.reconcile_at(command,key,message,now_ns=0,deadline_ns=5)
 return owner.reconcile(command,key,message)
def economics(snapshot,active,extra=0,reserved_extra=0):
 a=snapshot['account'];assert Decimal(a['cash'])==Decimal(1000000)-99*(active+extra)
 assert Decimal(a['inventory']['A'])==active+extra
 assert Decimal(a['reserved_cash'])==900*active-100*extra+reserved_extra
 assert Decimal(a['cash'])==Decimal(a['available_cash'])+Decimal(a['reserved_cash'])
 assert Decimal(a['available_cash'])>=0 and Decimal(a['available_inventory']['A'])>=0
 assert snapshot['lifecycle']['executions']==active+extra
 for row in snapshot['lifecycle']['orders'].values():
  assert Decimal(row['filled_quantity'])+Decimal(row['remaining_quantity'])==Decimal(row['order']['quantity'])

def operation(case,op,path,measure):
 mode=case['mode'];cls=MODES[mode];active=case['active'];H=case['H']
 owner=None;journal=None
 source=case['raw'] if op in {'reopen','startup'} else case['steady']
 shutil.copyfile(source,path)
 if op not in {'reopen','startup'}:
  owner=cls.open(path) if mode=='native' else TradingSession.open(path,journal_type=cls)
  if mode!='native':
   for i,msg in enumerate(case['queries']):assert reconcile(owner,mode,f'active-{i}',msg,f'setup-query-{i}')['result']['matched']
   owner.control('setup-normal','NORMAL',now_ns=0,reason='fresh-queries')
  journal=owner if mode=='native' else owner.journal
  before=owner.recover();assert before['committed_commands']==H;economics(before['snapshot'],active)
  if op in {'invalid_rollback','overflow_rollback'}:before_sql=list(journal._connection.iterdump())
 else:
  with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as c:
   assert c.execute('SELECT count(*) FROM commands').fetchone()[0]==H
 def invoke():
  if op=='submit':return owner.submit('probe',order('probe'),now_ns=0,marks=MARKS)
  if op=='policy_reject':return owner.submit('probe',order('probe'),now_ns=0,marks={})
  if op in {'invalid_rollback','overflow_rollback'}:
   q='0' if op=='invalid_rollback' else '922337203685477581'
   try:owner.submit('probe',order('probe',q),now_ns=0,marks=MARKS)
   except ValueError as e:
    if op=='overflow_rollback':assert 'FIX revision' in str(e)
    return dict(expected_error=type(e).__name__)
   raise AssertionError('Invalid input was not rejected')
  if op=='fill':return owner.receive('probe',case['fill']) if mode=='fix' else owner.apply('probe',case['fill'])
  if op=='reconcile':return reconcile(owner,mode,'active-0',case['query'],'probe')
  if op=='recover':return owner.recover()
  if op=='validate':
   journal._connection.execute('BEGIN')
   try:value=journal._verify();journal._connection.commit();return value
   except BaseException:journal._connection.rollback();raise
  if op=='reopen':return cls.open(path)
  if op=='startup':return TradingSession.open(path,journal_type=cls)
  if op=='duplicate_command':return owner.submit('pad-0',order('denied'),now_ns=0,marks={})
  if op=='duplicate_execution':return owner.receive('probe',case['duplicate']) if mode=='fix' else owner.apply('probe',case['duplicate'])
  raise AssertionError(op)
 try:
  result,metrics=measure(invoke,journal)
  if op in {'reopen','startup'}:
   owner=result;journal=owner if op=='reopen' else owner.journal
  after=owner.recover()
  logical_result={'opened':True} if op in {'reopen','startup'} else {'validated':True} if op=='validate' else result
  metrics['logical_result']=normalize(logical_result)
  metrics['logical_recovery']=normalize(after)
  journal._connection.execute('BEGIN')
  try:
   manager,_=journal._verify()
   history=manager.export_log()
   journal._connection.commit()
  except BaseException:
   journal._connection.rollback();raise
  metrics['history_sha256']=hashlib.sha256(json.dumps(history,sort_keys=True,separators=(',',':')).encode()).hexdigest()
  economics(after['snapshot'],active,extra=int(op=='fill'),reserved_extra=100*int(op=='submit'))
  commits=0 if op in {'recover','validate','reopen','duplicate_command','invalid_rollback','overflow_rollback'} else 1
  assert after['committed_commands']==H+commits
  if op=='submit':assert result['result']['accepted'] and result['outgoing']
  if op=='policy_reject':assert not result['result']['accepted'] and result['outgoing'] is None
  if op=='duplicate_command':assert result['status']=='duplicate' and result['outgoing'] is None
  if op in {'invalid_rollback','overflow_rollback'}:
   assert after==before and list(journal._connection.iterdump())==before_sql
  if op=='startup':assert after['snapshot']['operations']['mode']=='RECOVERY'
  elif mode!='native':assert after['snapshot']['operations']['mode']=='NORMAL'
  if op=='reconcile' and mode!='native':assert result['result']['matched']
  metrics.update(mode=mode,H=H,active=active,family=case['family'],operation=op,db_bytes=path.stat().st_size,
   committed_delta=commits,correctness='passed')
  return metrics
 finally:
  if owner is not None:owner.close()
  for suffix in ['', '-journal','.owner.lock']:
   p=Path(str(path)+suffix)
   if p.exists():p.unlink()

def simple(call,journal):
 cpu=time.process_time_ns();wall=time.perf_counter_ns();value=call()
 return value,dict(wall_ns=time.perf_counter_ns()-wall,cpu_ns=time.process_time_ns()-cpu)

INTEREST=['deepcopy','_deepcopy_dict','_deepcopy_list','_verify','_dump','_load','_definition_sha','reduce_fix','reduce_operations','submit','apply','snapshot','account','assess','decode_history','_decode_history','average','recover','open','_record']
def profiler(call,journal):
 p=cProfile.Profile(timer=time.process_time_ns,timeunit=1e-9)
 wall=time.perf_counter_ns();cpu=time.process_time_ns();value=p.runcall(call)
 elapsed=dict(instrumented_wall_ns=time.perf_counter_ns()-wall,instrumented_cpu_ns=time.process_time_ns()-cpu)
 rows=[]
 for (file,line,name),(primitive,total,self_s,cumulative,callers) in pstats.Stats(p).stats.items():
  if True:
   rows.append(dict(file=file,line=line,function=name,primitive_calls=primitive,total_calls=total,self_cpu_s=self_s,cumulative_cpu_s=cumulative))
 elapsed['copy_callers']=[dict(caller_file=k[0],caller_line=k[1],caller_function=k[2],raw_stats=list(v))
  for key,stats in pstats.Stats(p).stats.items() if key[2]=='deepcopy'
  for k,v in stats[4].items()]
 elapsed['total_self_cpu_s']=sum(v[2] for v in pstats.Stats(p).stats.values())
 elapsed['functions']=sorted(rows,key=lambda x:x['self_cpu_s'],reverse=True)
 return value,elapsed

def memory(call,journal):
 tracemalloc.start(1);tracemalloc.reset_peak();base,_=tracemalloc.get_traced_memory()
 wall=time.perf_counter_ns();value=call();elapsed=time.perf_counter_ns()-wall
 current,peak=tracemalloc.get_traced_memory();snapshot=tracemalloc.take_snapshot();tracemalloc.stop()
 return value,dict(instrumented_wall_ns=elapsed,python_peak_bytes=peak-base,python_retained_bytes=current-base,
  retained_top=[dict(location=str(x.traceback),bytes=x.size,count=x.count) for x in snapshot.statistics('lineno')[:12]])

class Phases:
 def __init__(self):self.rows=defaultdict(lambda:dict(calls=0,wall_ns=0,cpu_ns=0,exclusive_wall_ns=0,exclusive_cpu_ns=0));self.stack=[];self.patches=[]
 def wrap(self,name,fn):
  @functools.wraps(fn)
  def wrapped(*a,**kw):
   frame=[time.perf_counter_ns(),time.process_time_ns(),0,0];self.stack.append(frame)
   try:return fn(*a,**kw)
   finally:
    w=time.perf_counter_ns()-frame[0];c=time.process_time_ns()-frame[1];self.stack.pop()
    row=self.rows[name];row['calls']+=1;row['wall_ns']+=w;row['cpu_ns']+=c
    row['exclusive_wall_ns']+=w-frame[2];row['exclusive_cpu_ns']+=c-frame[3]
    if self.stack:self.stack[-1][2]+=w;self.stack[-1][3]+=c
  return wrapped
 def patch(self,obj,key,name):
  old=getattr(obj,key);self.patches.append((obj,key,old));setattr(obj,key,self.wrap(name,old))
 def measure(self,call,journal):
  if journal is None:raise AssertionError('phase instrumentation requires an open journal')
  self.patch(journal,'_verify','verify')
  reducer=journal._reducer
  # Wrap verification with a depth marker, then classify current/replayed reductions.
  original_verify=journal._verify;depth=[0]
  def verify():
   depth[0]+=1
   try:return original_verify()
   finally:depth[0]-=1
  journal._verify=verify
  journal._reducer=lambda *a,**kw:self.wrap('replay_reducer' if depth[0] else 'current_reducer',reducer)(*a,**kw)
  self.patches.append((journal,'_reducer',reducer))
  for key in ['_dump','_load','_definition_sha']:self.patch(fj,key,key)
  import copy
  # Wrap production module aliases only. copy.py's recursive global remains
  # untouched, so this times complete root copies without per-recursion hooks.
  for module_name,module in list(sys.modules.items()):
   if module_name.startswith('market_microstructure.') and getattr(module,'deepcopy',None) is copy.deepcopy:
    self.patch(module,'deepcopy','copy_roots__'+module_name.rsplit('.',1)[-1])
  self.patch(FundedOrderManager,'snapshot','funded_snapshot');self.patch(OrderManager,'snapshot','lifecycle_snapshot')
  actual=journal._connection; phases=self
  class Cursor:
   def __init__(self,c):self.c=c
   def __getattr__(self,key):
    value=getattr(self.c,key)
    return phases.wrap('sqlite_fetch',value) if key in {'fetchone','fetchall'} else value
  class Connection:
   def __getattr__(self,key):return getattr(actual,key)
   def execute(self,sql,*a,**kw):
    key='sqlite_begin' if sql.startswith('BEGIN') else 'sqlite_write' if sql.startswith(('INSERT','UPDATE')) else 'sqlite_execute'
    return Cursor(phases.wrap(key,actual.execute)(sql,*a,**kw))
   def commit(self):return phases.wrap('sqlite_commit',actual.commit)()
   def rollback(self):return phases.wrap('sqlite_rollback',actual.rollback)()
  journal._connection=Connection()
  try:value,elapsed=simple(call,journal);elapsed['phases']=dict(self.rows);return value,elapsed
  finally:
   journal._connection=actual
   for obj,key,old in reversed(self.patches):setattr(obj,key,old)

# Only session ownership diagnostics are run-specific. All business fields remain.
def normalize(value,path=()):
 if isinstance(value,dict):
  return {k:normalize(v,path+(k,)) for k,v in value.items()
          if not(path[-2:]==('operations','session') and k in {'session_id','pid'})}
 if isinstance(value,list):return [normalize(x,path) for x in value]
 return value
