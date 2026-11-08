"""Build two README-only SVGs using the standard library.

The recovery numbers are recomputed from the manual fills in
examples/fix_order_recovery.py and checked by that demo's independent ledger.
Run from the repository root: python assets/readme/build_visuals.py
No model, order processing code, tests or historical evidence is modified.
"""
from pathlib import Path
from decimal import Decimal
import xml.etree.ElementTree as ET

OUT = Path(__file__).resolve().parent
NS = 'http://www.w3.org/2000/svg'
ET.register_namespace('', NS)
INK, MUTED, BLUE, LINE = '#233143', '#647180', '#245C91', '#CBD5DF'


def el(parent, tag, **attrs):
    return ET.SubElement(parent, '{'+NS+'}'+tag, {k.replace('_','-'):str(v) for k,v in attrs.items()})


def canvas(height, title):
    root = ET.Element('{'+NS+'}svg', {'viewBox':f'0 0 680 {height}', 'width':'680', 'height':str(height), 'role':'img', 'aria-label':title})
    el(root,'title').text=title
    el(root,'rect',x=0,y=0,width=680,height=height,rx=8,fill='white')
    return root


def text(root,x,y,value,size=21,color=INK,weight='400',anchor='start'):
    node=el(root,'text',x=x,y=y,fill=color,font_size=size,font_weight=weight,text_anchor=anchor,
            font_family='Apple SD Gothic Neo, Noto Sans CJK KR, Malgun Gothic, sans-serif')
    node.text=value
    return node


def box(root,x,y,w,h,title,detail):
    text(root,x+w/2,y+33,title,28,BLUE,'600','middle')
    el(root,'line',x1=x+16,y1=y+47,x2=x+w-16,y2=y+47,stroke=LINE,stroke_width=2)
    text(root,x+w/2,y+76,detail,22,MUTED,anchor='middle')


def arrow(root,x1,y1,x2,y2):
    el(root,'line',x1=x1,y1=y1,x2=x2,y2=y2,stroke='#8A97A6',stroke_width=2)
    if y1==y2:
        delta=7 if x2>x1 else -7
        points=f'{x2-delta},{y2-5} {x2},{y2} {x2-delta},{y2+5}'
    else:
        delta=7 if y2>y1 else -7
        points=f'{x2-5},{y2-delta} {x2},{y2} {x2+5},{y2-delta}'
    el(root,'polyline',points=points,fill='none',stroke='#8A97A6',stroke_width=2)


def architecture():
    r=canvas(360,'호출자, OMS Core, SQLite와 모의 거래소의 역할')
    box(r,24,24,266,94,'호출자','주문·취소·보고')
    box(r,390,24,266,94,'모의 거래소','수동 체결·상태 조회')
    arrow(r,290,48,390,48);text(r,340,36,'요청',16,MUTED,anchor='middle')
    arrow(r,390,91,290,91);text(r,340,114,'보고·조회',20,MUTED,anchor='middle')
    box(r,24,223,266,94,'OMS Core','상태·예약·FIX 검증')
    box(r,390,223,266,94,'SQLite','명령·결과·상태')
    arrow(r,95,118,95,223);text(r,105,167,'요청·보고',20,MUTED)
    arrow(r,220,223,220,118);text(r,232,196,'처리 결과',20,MUTED)
    arrow(r,290,253,390,253);text(r,340,239,'COMMIT',18,MUTED,anchor='middle')
    arrow(r,390,292,290,292);text(r,340,316,'재생·검산',20,MUTED,anchor='middle')
    ET.ElementTree(r).write(OUT/'architecture.svg',encoding='utf-8',xml_declaration=True)


def recovery():
    initial=Decimal('1000');first=Decimal('4');second=Decimal('3');limit=Decimal('100')
    cash_before=initial-first*Decimal('99');reserve_before=(Decimal('10')-first)*limit
    final_cash=cash_before-second*limit;final_filled=first+second;final_canceled=Decimal('10')-final_filled
    assert (cash_before,reserve_before,final_cash,final_filled,final_canceled)==tuple(map(Decimal,['604','600','304','7','3']))
    r=canvas(640,'취소·체결 경합, 보고 유실, 재시작과 대사 후 회계 검산')
    stages=[
      ('01','10 @ 100 주문 승인','현금 1,000  ·  예약 1,000  ·  체결 0','주문 전달 전에 자금 예약을 저장'),
      ('02','4 @ 99 부분 체결 후 취소 요청','현금 604  ·  예약 600  ·  체결 4','취소 요청은 완료가 아니므로 예약 유지'),
      ('03','추가 체결·취소 보고 유실 → SIGKILL','로컬 체결 4  ≠  거래소 체결 7','거래소: 3 @ 100 추가 체결, 잔여 3 취소'),
      ('04','재시작: RECOVERY','현금 604  ·  예약 600  ·  로컬 체결 4','저장 상태 복구 후에도 새 조회·누락 보고 필요'),
      ('05','보고 재전달·새 조회 일치 → NORMAL','현금 304  ·  예약 0  ·  보유 7','체결 7 + 취소 3 = 주문 10  ·  상태 canceled')]
    el(r,'line',x1=30,y1=35,x2=30,y2=515,stroke=LINE,stroke_width=2)
    for i,(n,title,value,note) in enumerate(stages):
        y=35+i*120
        color=BLUE if i!=2 else '#94612D'
        el(r,'circle',cx=30,cy=y,r=6,fill=color)
        text(r,58,y+7,title,24,color,'600')
        text(r,58,y+48,value,26,INK,'600')
        text(r,58,y+82,note,21,MUTED)
    ET.ElementTree(r).write(OUT/'recovery.svg',encoding='utf-8',xml_declaration=True)


if __name__=='__main__':
    architecture();recovery();print('Created two SVGs; recovery cash/quantity independently recomputed.')
