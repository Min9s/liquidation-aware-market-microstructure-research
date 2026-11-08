"""README figures. Standard library only. Source evidence is read-only.
Run from either repository: python assets/readme/build_portfolio.py
"""
from pathlib import Path
from html import escape
import json
R=Path(__file__).resolve().parents[2]; O=R/'assets/readme'
BLUE='#2563A6'; TEAL='#087F8C'; INK='#182C40'; GRAY='#627487'; AMBER='#A56518'
class SVG:
 def __init__(self,title,subtitle,h=480):
  self.h=h;self.a=[f'<svg xmlns="http://www.w3.org/2000/svg" width="720" height="{h}" viewBox="0 0 720 {h}" role="img"><title>{escape(title)}</title><rect width="720" height="{h}" rx="12" fill="#F8FAFC"/>'];self.t(28,42,title,26,INK,700);self.t(28,74,subtitle,17,GRAY)
 def t(self,x,y,s,z=20,c=INK,w=400):self.a.append(f'<text x="{x}" y="{y}" font-family="Apple SD Gothic Neo,Noto Sans CJK KR,sans-serif" font-size="{z}" font-weight="{w}" fill="{c}">{escape(str(s))}</text>')
 def line(self,x,y,xx,yy,c='#CBD5E1',dash=False):self.a.append(f'<path d="M{x} {y} L{xx} {yy}" stroke="{c}" stroke-width="2" '+('stroke-dasharray="6 5" ' if dash else '')+'/>')
 def rect(self,x,y,w,h,c):self.a.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="5" fill="{c}"/>')
 def dot(self,x,y,c,r=6):self.a.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{c}"/>')
 def save(self,name): (O/(name+'.svg')).write_text(''.join(self.a)+'</svg>')
if (R/'results/final_v1/primary_holdout.json').exists():
 p=json.loads((R/'results/final_v1/primary_holdout.json').read_text());e=json.loads((R/'results/final_v1/exploration.json').read_text());rows=[p['horizons'][str(h)] for h in (1,5,10)]
 for row in rows:
  assert row['labels']==row['directional']+row['zero']
  for m in row['models'].values():
   cm=m['confusion_matrix'];assert abs(sum(cm[i][i]/sum(cm[i]) for i in (0,1))/2-m['balanced_accuracy'])<1e-12
 s=SVG('가까운 미래일수록 방향 구분이 강하다','최종 holdout · 가격 변화 표본만 · Balanced Accuracy',510)
 for j,(label,key,c) in enumerate([('OBI','obi',TEAL),('과거 가격 방향','price_change',GRAY)]):s.dot(34+j*240,111,c);s.t(48+j*240,118,label,19,c)
 for k,v in enumerate([0,.25,.5,.75,1]):
  x=110+v*540;s.line(x,153,x,407);s.t(x-14,438,f'{v:g}',16,GRAY)
 for i,row in enumerate(rows):
  y=187+i*90;s.t(28,y+8,f'{[1,5,10][i]}초',25,INK,700)
  for key,c,dy in [('obi',TEAL,-12),('price_change',GRAY,20)]:
   v=row['models'][key]['balanced_accuracy'];x=110+v*540;s.dot(x,y+dy,c);s.t(x+12,y+dy+6,f'{v:.3f}',19,c,700)
 s.t(28,484,'상승·하락 재현율의 평균. 상수 방향 기준 모델 = 0.500',17,GRAY);s.save('research-performance')
 s=SVG('방향 모델은 무변화 시점을 예측하지 않는다','최종 holdout · 유효 라벨 전체의 사후 구성',440)
 for i,row in enumerate(rows):
  y=128+i*86;n=row['labels'];frac=row['directional']/n;s.t(28,y+22,f'{[1,5,10][i]}초',24,INK,700);s.rect(100,y,550*frac,32,TEAL);s.rect(100+550*frac,y,550*(1-frac),32,'#CBD5E1');s.t(100,y+60,f"변화 {row['directional']:,} / 전체 {n:,}  ·  무변화 {row['zero']/n:.1%}",18,GRAY)
 s.t(28,414,'1초: 방향 Accuracy 0.767 → 전체 강제 방향 일치율 0.456',19,INK,700);s.save('research-coverage')
 s=SVG('OBI가 높은 구간에서 평균 수익률도 높았다','탐색 분석 · 1초 mid-price 변화 · 무변화 포함',500)
 qs=rows[0]['quantiles'];s.line(80,285,670,285,GRAY)
 for i,q in enumerate(qs):
  x=112+i*122;v=q['mean_return_bps'];y=285-v*180;s.rect(x-22,min(y,285),44,abs(y-285),TEAL if v>=0 else BLUE);s.t(x-32,y-12 if v>=0 else y+19,f'{v:+.2f}',20,INK,700);s.t(x-15,424,f'Q{i+1}',19);s.t(x-32,446,f"n={q['n']:,}",15,GRAY)
 s.t(28,112,'평균 변화 (bps)',17,GRAY);s.t(80,480,'낮은 OBI  ←  학습 구간에서 고정한 경계  →  높은 OBI',17,GRAY);s.save('research-shape')
 s=SVG('방향 정보와 거래 여력은 다른 질문이다','탐색 분석 · 학습 OBI 구간 평균 예측 vs 현재 quoted spread',470)
 for i,h in enumerate((1,5,10)):
  r=e['holdout'][str(h)]['point'];assert r['forecast_exceeds_spread_plus1']==0;y=145+i*90;s.t(28,y+12,f'{h}초',24,INK,700)
  for v,c,dy,label in [(r['forecast_abs'],TEAL,0,'예측'),(r['spread'],GRAY,27,'스프레드')]:
   s.rect(110,y+dy,1100*v,15,c);s.t(125+1100*v,y+dy+14,f'{v:.3f} bps',18,c)
 s.t(28,111,'청록: 평균 |학습 구간 예측|   회색: 평균 전체 호가 간격',17,GRAY)
 s.t(28,439,'어느 구간도 개별 예측이 spread + 1bps를 넘지 않았다.',19,INK,700);s.save('research-execution')
else:
 s=SVG('COMMIT 이후에만 주문을 전달한다','호출자 → OMS → SQLite의 명령 경계와 보고 경로',640)
 for x,label in [(90,'호출자'),(350,'OMS'),(610,'SQLite')]:
  s.t(x-35,125,label,22,BLUE,700);s.line(x,145,x,558)
 steps=[(90,350,185,'submit / 주문 검증',False),(350,610,260,'명령·결과·예약 저장',True),(610,350,330,'COMMIT 완료',True),(350,90,405,'outgoing 반환',False),(90,350,515,'거래소 보고 전달',False)]
 for x,xx,y,label,dash in steps:
  s.line(x,y,xx,y,BLUE,dash);s.dot(xx,y,BLUE,4);s.t(min(x,xx)+15,y-15,label,19)
 s.rect(30,432,260,45,'#EAF0F6');s.t(45,461,'호출자가 모의 거래소로 전송',17,BLUE,700)
 s.t(28,594,'OMS: TradingSession → FixJournal → FundedJournal',19,INK,700)
 s.t(28,623,'실선은 요청·반환·보고, 점선은 저장. 끝점은 수신 측입니다.',17,GRAY);s.save('oms-architecture')
 s=SVG('취소 요청 중에도 체결은 계속된다','대표 경로 · 주문 상태와 취소 상태를 별도로 보존',650)
 s.t(150,126,'주문',22,BLUE,700);s.t(440,126,'취소',22,AMBER,700)
 s.line(170,155,170,555,BLUE);s.line(460,155,460,555,AMBER)
 states=[(175,'pending_new','none','승인·예약'),(260,'partially_filled','none','첫 체결'),(345,'partially_filled','pending','취소 요청'),(430,'partially_filled','pending','추가 체결'),(515,'canceled','canceled','잔여 취소 확정')]
 for y,a,b,note in states:
  s.rect(115,y-20,240,40,'#EAF0F6');s.rect(425,y-20,250,40,'#FFF2DF');s.t(126,y+6,a,20,BLUE,700);s.t(438,y+6,b,20,AMBER,700);s.t(20,y+40,note,17,GRAY)
 s.t(28,600,'전량 체결이 먼저라면: 주문 filled / 취소 too_late',20,INK,700)
 s.t(28,631,'미응답 unknown·거부 rejected 등 전체 상태는 본문 계약 참조.',17,GRAY);s.save('oms-lifecycle')
 s=SVG('재시작은 복원, 대사는 확인이다','FIX 복구 데모 · 수수료 0 · 수동 체결 · 합성 계정',720)
 stages=[('주문 승인','현금 1,000 / 예약 1,000','10개 × 지정가 100. 전송 전에 저장.'),('4개 체결 → 취소 요청','현금 604 / 예약 600 / 보유 4','4 × 99 체결. 취소 요청만으로 예약 해제하지 않음.'),('보고 유실 → SIGKILL','로컬 체결 4 ≠ 거래소 체결 7','거래소: 3 × 100 추가 체결, 잔여 3 취소.'),('재시작 / RECOVERY','현금 604 / 예약 600 / 보유 4','SQLite 복원. 조회만으로 누락 Trade ID를 알 수 없음.'),('보고 재전달 + 새 조회 / NORMAL','현금 304 / 예약 0 / 보유 7','중복·역순 전달 후 검산: 체결 7 + 취소 3 = 주문 10.')]
 for i,(title,value,note) in enumerate(stages):
  y=116+i*117;c=AMBER if i==2 else BLUE;s.rect(28,y,5,94,c);s.t(50,y+21,title,22,c,700);s.t(50,y+55,value,25,INK,700);s.t(50,y+85,note,17,GRAY)
 s.save('oms-recovery')
print('Generated portfolio SVGs; original evidence unchanged.')
