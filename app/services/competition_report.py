"""Offline HTML and SVG charts built solely from allowlisted scored batch records."""
import html
import math
import re
import unicodedata
from app.services.competition import analytics

DIMENSIONS=[('L2_main','L2 主问题'),('L3_main','L3 主问题'),('L1_sub','L1 子问题'),('L2_sub','L2 子问题')]
# Team colors follow the Apple system palette (light): blue, orange, green, purple, pink, teal. Reused by the web page.
TEAM_COLORS=['#007aff','#ff9500','#34c759','#af52de','#ff2d55','#5ac8fa']
# Dimension colors: one blue family, darker for main questions, lighter for sub-questions.
DIM_COLORS=['#0a4fbf','#007aff','#5aa9ff','#a9d0ff']
INK,INK2,INK3,GRID,TRACK,PANEL='#1c1c1e','#48484a','#6c6c70','#e9e9ee','#f2f2f7','#f7f7fa'
FONT='-apple-system,SF Pro Text,PingFang SC,Inter,Noto Sans CJK SC,Microsoft YaHei,Arial,sans-serif'
def team_color(i):return TEAM_COLORS[i%len(TEAM_COLORS)]
CHARTS={'ranking':'综合分排行榜','dimensions':'四维准确率对比','radar':'能力雷达图','efficiency':'推理效率分析'}


def esc(value):return html.escape(re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',str(value)),quote=True)
def number(value,digits=2):return '—' if value is None else f'{value:.{digits}f}'


def wrap_label(text,width):
    lines=[];line='';used=0
    for char in str(text):
        size=2 if unicodedata.east_asian_width(char) in ('W','F') else 1
        if used+size>width:lines.append(line);line='';used=0
        line+=char;used+=size
    return lines+[line]


class SVG:
    """Small drawing helper. Looks like Swift Charts: quiet hairline grid, capsule marks, semibold values."""
    def __init__(self,title,height,subtitle):
        self.height=height
        defs=''.join(f'<linearGradient id="t{i}" x1="0" x2="1" y1="0" y2="0"><stop offset="0" stop-color="{c}" stop-opacity=".72"/><stop offset="1" stop-color="{c}"/></linearGradient>' for i,c in enumerate(TEAM_COLORS))
        defs+=''.join(f'<linearGradient id="d{i}" x1="0" x2="1" y1="0" y2="0"><stop offset="0" stop-color="{c}" stop-opacity=".78"/><stop offset="1" stop-color="{c}"/></linearGradient>' for i,c in enumerate(DIM_COLORS))
        self.parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="{height}" viewBox="0 0 1100 {height}" role="img" aria-label="{esc(title)}">',
                    f'<defs>{defs}</defs><rect width="1100" height="100%" fill="#ffffff"/>',
                    f'<g font-family="{FONT}" fill="{INK}">']
        self.text(32,40,title,18,INK,weight=600);self.text(32,62,subtitle,12,INK3)
    def text(self,x,y,text,size=13,color=INK,anchor='start',weight=400):
        w=f' font-weight="{weight}"' if weight!=400 else ''
        self.parts.append(f'<text x="{x:.3f}" y="{y:.3f}" font-size="{size}" fill="{color}" text-anchor="{anchor}"{w}>{esc(text)}</text>')
    def line(self,x1,y1,x2,y2,color=GRID,dash=''):
        d=f' stroke-dasharray="{dash}"' if dash else ''
        self.parts.append(f'<line x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y2:.3f}" stroke="{color}"{d}/>')
    def capsule(self,x,y,w,h,fill,opacity=1):
        w=max(w,h);self.parts.append(f'<rect x="{x:.3f}" y="{y:.3f}" width="{w:.3f}" height="{h}" rx="{h/2}" fill="{fill}" fill-opacity="{opacity}"/>')
    def label(self,x,y,value,width=28,color=INK,size=14,weight=600):
        lines=wrap_label(value,width)
        for n,line in enumerate(lines[:3]):
            self.text(x,y+n*18,line+('…' if n==2 and len(lines)>3 else ''),size,color,weight=weight)
        self.parts.append('<title>'+esc(value)+'</title>')
    def finish(self):return ''.join(self.parts)+'</g></svg>'


def chart_svg(group,chart):
    if chart not in CHARTS:raise ValueError('未知图表类型')
    rows=group['top_entries'];kind={'mock':'Mock 联调（非正式）','partial_test':'真实模型测试（非正式）','official':'正式全量'}[group['kind']]
    subtitle=f'{kind} · {group["mode"]} · 每题最多 {group["calls"]} 次 · {len(rows)} 支队伍 · 组 {group["id"]}'
    if chart in ('ranking','dimensions'):
        step=64 if chart=='ranking' else 132
        s=SVG(CHARTS[chart],140+step*len(rows),subtitle)
        x,w=300,690
        for tick in (0,25,50,75,100):
            xx=x+w*tick/100;s.line(xx,96,xx,s.height-44);s.text(xx,s.height-22,tick,11,INK3,'middle')
        s.text(x+w+40,s.height-22,'综合得分 / 100 分' if chart=='ranking' else '准确率 / %',11,INK3,'end')
        for i,row in enumerate(rows):
            y=112+i*step
            if chart=='ranking':
                score=row['weighted_score'];s.text(32,y+14,f'{row["rank"]:02d}',13,INK3)
                s.label(64,y+14,row['name'],24)
                s.capsule(x,y,w,18,TRACK);s.capsule(x,y,w*score/100,18,f'url(#t{i%len(TEAM_COLORS)})')
                s.text(x+max(w*score/100,18)+12,y+14,number(score),14,INK,weight=600)
            else:
                s.text(32,y+14,f'{row["rank"]:02d}',13,INK3);s.label(64,y+14,row['name'],24)
                for j,(key,label) in enumerate(DIMENSIONS):
                    yy=y+j*26;v=row['dimensions'][key]['accuracy']
                    s.text(x-14,yy+12,label,11,INK3,'end');s.capsule(x,yy,w,14,TRACK)
                    if v is not None:s.capsule(x,yy,w*v,14,f'url(#d{j})')
                    s.text(x+max(w*(v or 0),14)+12,yy+12,'缺失' if v is None else number(v*100,1)+'%',12,INK,weight=600)
        return s.finish()
    if chart=='radar':
        shown=rows[:4]
        s=SVG(CHARTS[chart],560,subtitle+f' · 展示当前筛选中排名前 {len(shown)} 支队伍')
        cx,cy,radius=330,320,180
        complete=[[row['dimensions'][key]['accuracy'] for key,_ in DIMENSIONS] for row in shown]
        known=[v for vals in complete for v in vals if v is not None]
        # Zoom the axis to the data so close teams stay readable; the center value is printed on the chart.
        low=max(0,int((min(known)*100-10)//10)*10) if known else 0
        def scale(v):return radius*max(0,(v*100-low))/(100-low)
        for tick in range(4):
            value=low+(100-low)*(tick+1)/4;t=(tick+1)/4
            pts=[(cx,cy-radius*t),(cx+radius*t,cy),(cx,cy+radius*t),(cx-radius*t,cy)]
            fill=PANEL if tick==3 else 'none';dash='' if tick==3 else ' stroke-dasharray="3 5"'
            s.parts.append('<polygon points="'+' '.join(f'{x},{y}' for x,y in pts)+f'" fill="{fill}" stroke="{GRID}" stroke-linejoin="round"{dash}/>')
            s.parts.append(f'<text x="{cx+6}" y="{cy-radius*t-4}" font-size="10" fill="{INK3}" stroke="#fff" stroke-width="3" paint-order="stroke">{value:.0f}%</text>')
        s.parts.append(f'<text x="{cx+6}" y="{cy+16}" font-size="10" fill="{INK3}" stroke="#fff" stroke-width="3" paint-order="stroke">中心 {low}%</text>')
        for x1,y1 in [(cx,cy-radius),(cx+radius,cy),(cx,cy+radius),(cx-radius,cy)]:s.line(cx,cy,x1,y1,GRID)
        positions=[(cx,cy-radius-18),(cx+radius+18,cy+5),(cx,cy+radius+32),(cx-radius-18,cy+5)]
        anchors=['middle','start','middle','end']
        for (_,label),(x,y),anchor in zip(DIMENSIONS,positions,anchors):s.text(x,y,label,14,INK,anchor,600)
        for index,row in enumerate(shown):
            vals=complete[index];color=team_color(index);y0=132+index*104
            s.parts.append(f'<rect x="676" y="{y0-32}" width="392" height="88" rx="18" fill="{PANEL}"/>')
            s.parts.append(f'<circle cx="702" cy="{y0-8}" r="6" fill="{color}"/>')
            s.text(718,y0-3,row['name'],15,INK,weight=600)
            for k,((_,label),v) in enumerate(zip(DIMENSIONS,vals)):
                xx=700+k*92;s.text(xx,y0+20,label[:4],11,INK3);s.text(xx,y0+40,'缺失' if v is None else number(v*100,1)+'%',14,INK,weight=600)
            if any(v is None for v in vals):
                s.text(718,y0+58,'缺失维度不补零，不绘制该队多边形',11,'#9a6400');continue
            points=[(cx,cy-scale(vals[0])),(cx+scale(vals[1]),cy),(cx,cy+scale(vals[2])),(cx-scale(vals[3]),cy)]
            s.parts.append('<polygon points="'+' '.join(f'{x},{y}' for x,y in points)+f'" fill="{color}" fill-opacity="0.16" stroke="{color}" stroke-width="2.5" stroke-linejoin="round"/>')
            for x,y in points:s.parts.append(f'<circle cx="{x}" cy="{y}" r="5" fill="#fff" stroke="{color}" stroke-width="2.5"/>')
        return s.finish()
    s=SVG(CHARTS[chart],560+len(rows)*54,subtitle)
    x,y,w,h=70,110,900,360
    usable=[r for r in rows if r['mean_question_seconds'] is not None]
    secs=[r['mean_question_seconds'] for r in usable] or [1.0]
    scores=[r['weighted_score'] for r in usable] or [50.0]
    # Zoom to the data so teams do not pile up in one corner; ticks are round numbers.
    def nice(span):
        raw=max(span,1e-9)/5;mag=10**math.floor(math.log10(raw))
        return next(m*mag for m in (1,2,2.5,5,10) if m*mag>=raw)
    pad=max((max(secs)-min(secs))*.25,max(secs)*.15,.5)
    sx=nice(max(secs)-min(secs)+2*pad);xlo=max(0,math.floor((min(secs)-pad)/sx)*sx);xhi=math.ceil((max(secs)+pad)/sx)*sx
    ylo=max(0,math.floor((min(scores)-10)/10)*10);yhi=min(100,math.ceil((max(scores)+10)/10)*10)
    if yhi-ylo<20:ylo=max(0,yhi-20)
    xticks=[];t=xlo
    while t<=xhi+1e-9:xticks.append(t);t+=sx
    digits=0 if sx>=1 else 1
    # Swift Charts style: horizontal hairlines only, value labels on the trailing edge.
    for k in range(int((yhi-ylo)//10)+1):
        v=ylo+k*10;yy=y+h-h*(v-ylo)/(yhi-ylo);s.line(x,yy,x+w,yy);s.text(x+w+14,yy+4,v,11,INK3)
    for tick in xticks:
        xx=x+w*(tick-xlo)/(xhi-xlo);s.text(xx,y+h+26,number(tick,digits),11,INK3,'middle')
    s.text(x,y-18,'综合得分',12,INK2,weight=600);s.text(x+w,y+h+54,'平均单题推理耗时 / 秒',12,INK2,'end',600)
    label_boxes=[]
    def intersects(a,b):return a[0]<b[2]+3 and a[2]+3>b[0] and a[1]<b[3]+3 and a[3]+3>b[1]
    for i,row in enumerate(usable,1):
        xx=x+w*(row['mean_question_seconds']-xlo)/(xhi-xlo);yy=y+h-h*(row['weighted_score']-ylo)/(yhi-ylo)
        radius=7+min(row['mean_calls'],10)*1.6
        tip=f'{row["name"]}；得分 {number(row["weighted_score"])}；单题 {number(row["mean_question_seconds"],1)} 秒；平均调用 {number(row["mean_calls"])} 次'
        c=team_color(i-1)
        s.parts.append(f'<circle cx="{xx}" cy="{yy}" r="{radius+8}" fill="{c}" fill-opacity="0.14"/><circle cx="{xx}" cy="{yy}" r="{radius}" fill="{c}" stroke="#fff" stroke-width="2.5"><title>{esc(tip)}</title></circle>')
        offsets=[(radius+12,-10),(radius+12,22),(-radius-26,-10),(-radius-26,22),(0,-radius-20),(0,radius+34),(35,-30),(-40,40),(55,10)]
        for dx,dy in offsets:
            tx,ty=xx+dx,yy+dy;box=(tx,ty-13,tx+max(12,len(str(i))*8),ty+3)
            if box[0]>=x and box[2]<=x+w and box[1]>=y and box[3]<=y+h and not any(intersects(box,b) for b in label_boxes):break
        label_boxes.append(box)
        s.text(tx,ty,i,13,INK,weight=600)
    # Legend as a quiet list below the plot
    for i,row in enumerate(usable,1):
        yy=y+h+96+(i-1)*54
        s.parts.append(f'<rect x="32" y="{yy-24}" width="1036" height="44" rx="14" fill="{PANEL}"/><circle cx="58" cy="{yy-2}" r="6" fill="{team_color(i-1)}"/>')
        s.text(76,yy+3,f'{i}. {row["name"]}',14,INK,weight=600)
        s.text(1048,yy+3,f'得分 {number(row["weighted_score"])} · 单题 {number(row["mean_question_seconds"],1)} 秒 · 平均调用 {number(row["mean_calls"])} 次',12,INK2,'end')
    if len(usable)!=len(rows):s.text(32,s.height-14,'无完整耗时记录的队伍不绘制为零耗时。',12,INK3)
    return s.finish()


REPORT_STYLE='''body{font:15px/1.7 "Microsoft YaHei",Arial,sans-serif;color:#24344a;background:#f2f5f9;margin:0}main{max-width:1160px;margin:35px auto;padding:25px}h1{font-size:30px}h2{font-size:23px}h3{font-size:18px}.panel{background:white;border:1px solid #dce4ec;border-radius:8px;padding:22px;margin:20px 0;overflow:auto}.metrics{display:flex;gap:15px;flex-wrap:wrap}.metric{padding:15px 22px;background:white;min-width:150px}.metric b{display:block;font-size:25px}table{border-collapse:collapse;width:100%;font-size:12px}td,th{padding:10px;border-bottom:1px solid #dde5ee;text-align:left}th{background:#edf3f9}svg{display:block;width:100%;min-width:650px;height:auto}p{color:#62748a}.tag{display:inline-block;background:#f6ead3;padding:5px 12px}a{color:#235d91}@media(max-width:700px){main{padding:12px;margin:0}svg{min-width:600px}.panel{padding:12px}}@media print{body{background:white}main{max-width:none;margin:0;padding:0}.panel{border:0;overflow:visible;break-inside:avoid}svg{min-width:0}h2{break-after:avoid}.metrics{gap:5px}}'''


def render_html(data):
    parts=['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(data['name'])+' · 竞赛综合报告</title><style>'+REPORT_STYLE+'</style></head><body><main>',
           '<h1>'+esc(data['name'])+' · 竞赛综合报告</h1><span class="tag">'+('公开版' if data['audience']=='public' else '管理员统计详细版')+'</span>',
           '<p>生成时间（UTC）：'+esc(data['generated_at'])+' · 批次 '+esc(data['batch_id'])+'</p>',
           '<p>'+esc(data['scope_note'])+' Mock 和局部测试均不是正式 Benchmark 成绩。</p><div class="metrics">']
    for label,value in [('整批队伍',data['teams']),('已完成队伍',data['finished']),('已完成 QA',f'{data["qa_completed"]} / {data["qa_total"]}'),('累计请求',data['actual_calls'])]:
        parts.append('<div class="metric">'+label+'<b>'+esc(value)+'</b></div>')
    parts.append('</div><p>累计外部 API 请求 '+str(data['external_api_calls'])+' 次；整批耗时 '+number(data['elapsed_seconds'])+' 秒。</p>')
    if not data['groups']:parts.append('<div class="panel"><h2>暂无可排名成绩</h2><p>筛选范围内没有已完整完成且符合条件的队伍。</p></div>')
    for group in data['groups']:
        parts.append('<section class="panel"><h2>'+esc(group['kind']+' · '+group['mode'])+'</h2><p>比较组 '+esc(group['id'])+'；每题预算 '+str(group['calls'])+' 次；提前结束 '+str(group['early_stop'])+'。</p>')
        parts.append('<table><thead><tr>'+''.join('<th>'+h+'</th>' for h in ['排名','队伍','模型','L2主','L3主','L1子','L2子','综合分','平均调用','平均秒/题'])+'</tr></thead><tbody>')
        for row in group['entries']:
            cells=[row['rank'],row['name'],row['model']['model_id'],*[number(row['dimensions'][key]['accuracy']*100)+'%' if row['dimensions'][key]['accuracy'] is not None else '—' for key,_ in DIMENSIONS],number(row['weighted_score']),number(row['mean_calls']),number(row['mean_question_seconds'],4)]
            parts.append('<tr>'+''.join('<td>'+esc(c)+'</td>' for c in cells)+'</tr>')
        parts.append('</tbody></table>')
        for chart in CHARTS:parts.append('<figure>'+chart_svg(group,chart)+'</figure>')
        if data['audience']=='admin':
            parts.append('<h3>运行追溯与错误统计</h3><table><tr><th>队伍</th><th>run_id</th><th>提交 / Prompt 版本</th><th>请求错误数</th><th>Token Usage</th></tr>')
            for r in group['entries']:parts.append('<tr>'+''.join('<td>'+esc(c)+'</td>' for c in [r['name'],r['run_id'],str(r['submission_version'])+' / '+r['prompt_version'],r['error_count'],json_safe(r['token_usage'])])+'</tr>')
            parts.append('</table>')
        parts.append('</section>')
    if data['excluded']:
        parts.append('<section class="panel"><h2>未进入最终排名的队伍</h2><ul>')
        for row in data['excluded']:parts.append('<li>'+esc(row['name']+' · '+row['status']+' · '+row['reason'])+'</li>')
        parts.append('</ul></section>')
    parts.append('<section class="panel"><h2>口径说明</h2><p>综合分 = 100 × (0.35 L2主 + 0.35 L3主 + 0.15 L1子 + 0.15 L2子)。维度均为普通准确率；多选集合完全匹配。缺失维度不伪造观察值，在评分公式中按零贡献处理。</p><p>平均单题耗时来自逐题执行记录，不含队列等待；点大小表示平均每题请求数。耗时缺失时不绘制散点；Mock 耗时只用于联调。</p><p>图表采用当前队伍筛选与 Top N；表格保留该筛选下全部可排名队伍。雷达图展示图表范围中前两支队伍，缺失维度不补零。图表数值来自本批实际 run 评分记录，不推断其他模式成绩。</p><p>报告为离线自包含 HTML，不加载 CDN。公开版和管理员版均不含 API 凭据、环境变量引用、Prompt 正文、隐藏 GT 或模型逐题输出。</p></section></main></body></html>')
    return ''.join(parts)


def json_safe(value):
    import json
    return json.dumps(value,ensure_ascii=False,sort_keys=True)
