"""Generate README presentation PNG from published evidence; never modifies evidence.
Run: python assets/readme/editorial.py (Matplotlib + Korean font required).
"""
from pathlib import Path
import json
from decimal import Decimal
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
R=Path(__file__).resolve().parents[2];O=R/'assets/readme'
font=next(f for f in ['Apple SD Gothic Neo','AppleGothic','Noto Sans CJK KR'] if f in {x.name for x in font_manager.fontManager.ttflist})
plt.rcParams.update({'font.family':font,'font.size':18,'text.color':'#182B3A','axes.labelcolor':'#52616B','xtick.color':'#52616B','ytick.color':'#182B3A','axes.unicode_minus':False})
BLUE='#176987';GRAY='#89949D';INK='#182B3A'
if (R/'results/final_v1').exists():
 d=json.loads((R/'results/final_v1/primary_holdout.json').read_text());rows=[d['horizons'][str(h)] for h in [1,5,10]]
 for r in rows:
  for m in r['models'].values():
   c=m['confusion_matrix'];assert abs(sum(c[i][i]/sum(c[i]) for i in (0,1))/2-m['balanced_accuracy'])<1e-12
 fig,ax=plt.subplots(figsize=(8,5.5));fig.patch.set_facecolor('white')
 fig.text(.07,.92,'OBI의 방향 정보는 1초에서 가장 강했다',fontsize=23,weight='bold')
 fig.text(.07,.85,'최종 holdout · 미래 가격이 변한 표본만 평가',fontsize=16,color='#52616B')
 import numpy as np
 y=np.arange(3)
 for key,offset,c,label in [('obi',-.16,BLUE,'OBI Logistic'),('price_change',.16,GRAY,'과거 1초 가격 방향')]:
  vals=[r['models'][key]['balanced_accuracy'] for r in rows];ax.barh(y+offset,vals,height=.22,color=c,label=label)
  for i,v in enumerate(vals):ax.text(v+.015,i+offset,f'{v:.3f}',va='center',fontsize=18,color=c,weight='bold')
 ax.set(yticks=y,yticklabels=['1초','5초','10초'],xlim=(0,1),ylim=(2.5,-.5),xticks=[0,.5,1],xlabel='Balanced Accuracy (0–1)')
 ax.axvline(.5,color='#CBD2D8',lw=1,ls='--',zorder=0)
 ax.tick_params(length=0,pad=10)
 for spine in ax.spines.values():spine.set_visible(False)
 fig.legend(loc='lower left',bbox_to_anchor=(.07,.01),ncol=2,frameon=False,fontsize=16)
 fig.subplots_adjust(left=.12,right=.94,top=.75,bottom=.27)
 fig.savefig(O/'finding.png',dpi=220,facecolor='white');plt.close(fig)
else:
 # Exact manual fills from examples/fix_order_recovery.py, independently checked there.
 cash=Decimal(1000)-Decimal(4)*99-Decimal(3)*100;assert cash==304
 fig=plt.figure(figsize=(8,6.2),facecolor='white')
 fig.text(.06,.93,'재시작 후에도, 모르는 체결은 남아 있다',fontsize=23,weight='bold')
 fig.text(.06,.86,'10개 매수 · 4개 체결 뒤 취소 요청 · 추가 3개 체결 보고 유실',fontsize=16,color='#52616B')
 for x,title,note,c in [(.06,'재시작 직후','저장된 사실만 복원',GRAY),(.57,'보고 재전달·대사 후','거래소의 사실과 일치',BLUE)]:
  fig.text(x,.73,title,fontsize=21,color=c,weight='bold');fig.text(x,.67,note,fontsize=16,color='#52616B')
 for y,label,before,after in [(.53,'현금','604','304'),(.37,'예약금','600','0'),(.21,'보유 수량','4','7')]:
  fig.text(.06,y,label,fontsize=17,color='#52616B');fig.text(.25,y,before,fontsize=32,color=GRAY);fig.text(.57,y,after,fontsize=32,color=BLUE,weight='bold')
 fig.add_artist(plt.Line2D([.49,.49],[.18,.76],color='#DCE2E6',lw=1,transform=fig.transFigure))
 fig.text(.06,.075,'RECOVERY',fontsize=16,color=GRAY);fig.text(.57,.075,'NORMAL · 체결 7 / 취소 3',fontsize=16,color=BLUE)
 fig.savefig(O/'recovery-case.png',dpi=220,facecolor='white');plt.close(fig)
print('Presentation rendered; evidence read-only.')
