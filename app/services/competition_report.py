"""Offline HTML and SVG charts built solely from allowlisted scored batch records."""
import html
import math
import re
import unicodedata
from app.services.competition import analytics

DIMENSIONS=[('L2_main','L2 主问题'),('L3_main','L3 主问题'),('L1_sub','L1 子问题'),('L2_sub','L2 子问题')]
COLORS=['#235d91','#d97732','#24856d','#8256a3']
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
    def __init__(self,title,height,subtitle):
        self.height=height
        self.parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="{height}" viewBox="0 0 1100 {height}" role="img" aria-label="{esc(title)}">',
                    '<rect width="1100" height="100%" fill="white"/>',
                    '<g font-family="Microsoft YaHei,Noto Sans CJK SC,Arial,sans-serif" fill="#24344a">']
        self.text(30,35,title,22);self.text(30,65,subtitle,13,'#61748b')
    def text(self,x,y,text,size=13,color='#24344a',anchor='start'):
        self.parts.append(f'<text x="{x:.3f}" y="{y:.3f}" font-size="{size}" fill="{color}" text-anchor="{anchor}">{esc(text)}</text>')
    def line(self,x1,y1,x2,y2,color='#dbe3ec'):
        self.parts.append(f'<line x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y2:.3f}" stroke="{color}"/>')
    def label(self,x,y,value,width=28,color='#24344a'):
        lines=wrap_label(value,width)
        for n,line in enumerate(lines[:3]):
            self.text(x,y+n*17,line+('…' if n==2 and len(lines)>3 else ''),12,color)
        self.parts.append('<title>'+esc(value)+'</title>')
    def finish(self):return ''.join(self.parts)+'</g></svg>'


def chart_svg(group,chart):
    if chart not in CHARTS:raise ValueError('未知图表类型')
    rows=group['top_entries'];kind={'mock':'Mock 联调（非正式）','partial_test':'真实模型测试（非正式）','official':'正式全量'}[group['kind']]
    subtitle=f'{kind} · {group["mode"]} · 每题最多 {group["calls"]} 次 · {len(rows)} 支队伍 · 组 {group["id"]}'
    if chart in ('ranking','dimensions'):
        step=85 if chart=='ranking' else 126
        s=SVG(CHARTS[chart],150+step*len(rows),subtitle)
        x,w=285,735
        for tick in (0,20,40,60,80,100):
            xx=x+w*tick/100;s.line(xx,100,xx,s.height-45);s.text(xx,95,tick,12,anchor='middle')
        s.text(x+w,s.height-12,'综合得分 / 100 分' if chart=='ranking' else '准确率 / %',12,anchor='end')
        for i,row in enumerate(rows):
            y=118+i*step;s.label(30,y+14,f'{row["rank"]}. {row["name"]}')
            if chart=='ranking':
                score=row['weighted_score']
                s.parts.append(f'<rect x="{x}" y="{y}" width="{w*score/100:.4f}" height="28" fill="{COLORS[0]}"/>')
                s.text(x+w*score/100+7,y+20,number(score),12)
            else:
                for j,(key,label) in enumerate(DIMENSIONS):
                    yy=y+j*25;v=row['dimensions'][key]['accuracy']
                    s.text(x-8,yy+13,label,11,anchor='end')
                    if v is not None:s.parts.append(f'<rect x="{x}" y="{yy}" width="{w*v:.4f}" height="17" fill="{COLORS[j]}"/>')
                    s.text(x+w*(v or 0)+6,yy+13,'缺失' if v is None else number(v*100)+'%',11)
        return s.finish()
    if chart=='radar':
        s=SVG(CHARTS[chart],675,subtitle+' · 展示当前筛选中排名前两支队伍')
        cx,cy,radius=500,300,165
        for tick in (.2,.4,.6,.8,1):
            pts=[(cx,cy-radius*tick),(cx+radius*tick,cy),(cx,cy+radius*tick),(cx-radius*tick,cy)]
            s.parts.append('<polygon points="'+' '.join(f'{x},{y}' for x,y in pts)+'" fill="none" stroke="#dbe3ec"/>')
            s.text(cx+5,cy-radius*tick,round(tick*100),10)
        positions=[(cx,cy-radius-20),(cx+radius+30,cy),(cx,cy+radius+30),(cx-radius-35,cy)]
        for (_,label),(x,y) in zip(DIMENSIONS,positions):s.text(x,y,label,14,anchor='middle')
        for index,row in enumerate(rows[:2]):
            vals=[row['dimensions'][key]['accuracy'] for key,_ in DIMENSIONS]
            color=COLORS[index]
            s.label(40,530+index*60,row['name']+' · '+', '.join('缺失' if v is None else number(v*100)+'%' for v in vals),140,color)
            if any(v is None for v in vals):
                s.text(40,665,'缺失维度不补零；对应队伍不绘制完整雷达多边形。',12);continue
            points=[(cx,cy-radius*vals[0]),(cx+radius*vals[1],cy),(cx,cy+radius*vals[2]),(cx-radius*vals[3],cy)]
            s.parts.append('<polygon points="'+' '.join(f'{x},{y}' for x,y in points)+f'" fill="{color}" fill-opacity="0.08" stroke="{color}" stroke-width="2"'+(' stroke-dasharray="6 3"' if index else '')+'/>')
        return s.finish()
    s=SVG(CHARTS[chart],540+len(rows)*60,subtitle)
    x,y,w,h=110,105,880,360
    usable=[r for r in rows if r['mean_question_seconds'] is not None]
    maximum=max([r['mean_question_seconds'] for r in usable]+[.001])*1.15
    for tick in range(6):
        xx=x+w*tick/5;s.line(xx,y,xx,y+h);s.text(xx,y+h+25,number(maximum*tick/5,4),11,anchor='middle')
        yy=y+h-h*tick/5;s.line(x,yy,x+w,yy);s.text(x-12,yy+4,tick*20,12,anchor='end')
    s.text(x,y-15,'综合得分 / 100 分',13);s.text(x+w,y+h+50,'平均单题推理耗时 / 秒',13,anchor='end')
    label_boxes=[]
    def intersects(a,b):return a[0]<b[2]+3 and a[2]+3>b[0] and a[1]<b[3]+3 and a[3]+3>b[1]
    for i,row in enumerate(usable,1):
        xx=x+w*row['mean_question_seconds']/maximum;yy=y+h-h*row['weighted_score']/100
        radius=5+min(row['mean_calls'],10)*2
        tip=f'{row["name"]}；得分 {number(row["weighted_score"])}；单题 {number(row["mean_question_seconds"],4)} 秒；平均调用 {number(row["mean_calls"])} 次'
        s.parts.append(f'<circle cx="{xx}" cy="{yy}" r="{radius}" fill="{COLORS[0]}" fill-opacity="0.3" stroke="{COLORS[0]}"><title>{esc(tip)}</title></circle>')
        offsets=[(radius+5,-10),(radius+5,22),(-radius-22,-10),(-radius-22,22),(0,-35),(0,45),(35,-30),(-40,40),(55,10)]
        for dx,dy in offsets:
            tx,ty=xx+dx,yy+dy;box=(tx,ty-13,tx+max(12,len(str(i))*8),ty+3)
            if box[0]>=x and box[2]<=x+w and box[1]>=y and box[3]<=y+h and not any(intersects(box,b) for b in label_boxes):break
        label_boxes.append(box)
        s.line(xx,yy,tx+4,ty-5,'#879aaf');s.text(tx,ty,i,12)
        s.label(40,540+(i-1)*60,f'{i}. {tip}',140)
    if len(usable)!=len(rows):s.text(40,s.height-10,'无完整耗时记录的队伍不绘制为零耗时。',12)
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
