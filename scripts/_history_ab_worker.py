"""Internal subprocess entry point; use benchmark_history_ab.py."""
import argparse,hashlib,importlib.util,json,os,sys
from pathlib import Path
D=Path(__file__).resolve().parent
p=argparse.ArgumentParser();p.add_argument('variant',choices=['A','B']);p.add_argument('action',choices=['fixtures','sample','cross']);p.add_argument('--mode');p.add_argument('--H',type=int);p.add_argument('--op');p.add_argument('--kind',default='plain');p.add_argument('--repeat',type=int,default=0);p.add_argument('--other');a=p.parse_args()
variants=json.loads((D/'variants.json').read_text());v=variants[a.variant];root=Path(v['root']);out=Path(v['out']);out.mkdir(parents=True,exist_ok=True)
spec=importlib.util.spec_from_file_location('h',D/'fixed_harness.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.ROOT=root;m.OUT=out;m.SIZES=v['sizes'];m.SHA=v['commit']

def guard():
 assert Path(m.fj.__file__).resolve().parent==root/'src/market_microstructure'
 for name,digest in v['source_sha256'].items():assert hashlib.sha256((root/'src/market_microstructure'/name).read_bytes()).hexdigest()==digest
m.baseline=guard;guard()
if a.action=='fixtures':
 m.fixtures();env=json.loads((out/'environment.json').read_text());env.update(variant=a.variant,base_commit=m.SHA,source_root=str(root));(out/'environment.json').write_text(json.dumps(env,indent=2)+'\n')
elif a.action=='cross':
 other=json.loads((Path(variants[a.other]['out'])/'cases.json').read_text());rows=[]
 for mode,cls in m.MODES.items():
  c=next(c for c in other if c['mode']==mode and c['H']==10);path=Path(c['raw']);before=path.read_bytes()
  try:cls.open(path)
  except ValueError as e:assert str(e)=='Funded journal schema or code definition changed'
  else:raise AssertionError('Cross-version database unexpectedly opened')
  assert path.read_bytes()==before;rows.append(dict(mode=mode,result='code hash refused; bytes unchanged'))
 print(json.dumps(rows))
else:
 cases=json.loads((out/'cases.json').read_text());c=next(c for c in cases if c['mode']==a.mode and c['H']==a.H)
 for key in ['raw','steady']:assert hashlib.sha256(Path(c[key]).read_bytes()).hexdigest()==c[key+'_sha256']
 work=out/'work';work.mkdir(exist_ok=True)
 fn=m.simple if a.kind=='plain' else m.profiler if a.kind=='profile' else m.memory if a.kind=='memory' else m.Phases().measure
 row=m.operation(c,a.op,work/'sample.sqlite',fn)
 row.update(variant=a.variant,kind=a.kind,repeat=a.repeat)
 print(json.dumps(row,allow_nan=False))
 guard()
