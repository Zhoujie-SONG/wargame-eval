'use strict';
(() => {
 const root=$('#run-comparison');
 if(!root)return;
 const storageKey='wargame.runComparison.selected.v1',maxSelected=8;
 const dimensions=[['L2_main','L2 主'],['L3_main','L3 主'],['L1_sub','L1 子'],['L2_sub','L2 子']];
 const cmp={rows:[],selected:[],initialized:false,loaded:false,loading:false,generating:false,listRevision:0,resultRevision:0};
 const node=id=>$('#comparison-'+id);
 const shortId=id=>String(id||'').slice(0,10);
 const score=value=>Number.isFinite(value)?value.toFixed(3):'—';
 const accuracy=value=>Number.isFinite(value)?(value*100).toFixed(2)+'%':'—';
 const kind=value=>({official:'正式评测',partial_test:'局部测试',mock:'Mock 联调'}[value]||value||'—');
 const mode=value=>({single_pass:'单次',self_refine:'多轮修正',majority_vote:'独立采样'}[value]||value||'—');
 // Keep in sync with app.services.run_comparison.COLORS; classes obey style-src 'self'.
 const palette=['#2f6fa8','#d98224','#248267','#8661b2','#c34966','#65758c','#8c692d','#2295a5'];
 const colorClass=entry=>{const index=palette.indexOf(String(entry.color||'').toLowerCase());return index<0?'comparison-color-fallback':'comparison-color-'+index};
 function dateValue(row){return row.finished_at||row.created_at||''}
 function dateText(value){
  if(!value)return '日期未知';
  const date=new Date(value);
  if(!Number.isFinite(date.getTime()))return String(value);
  return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(date);
 }
 function version(row){return row.submission_version?'参赛版本 '+row.submission_version:'Prompt '+(row.prompt_version||'未标注')}
 function identity(row){return row.label||[row.name,dateText(dateValue(row)),version(row),shortId(row.run_id)].join(' · ')}
 function visibleRows(){
  const query=node('search').value.trim().toLocaleLowerCase();
  return cmp.rows.filter(row=>!query||[identity(row),row.name,row.model_id,row.batch_name,row.submission_version,row.prompt_version,row.run_id,dateText(dateValue(row))].join(' ').toLocaleLowerCase().includes(query));
 }
 function storageWarning(message){node('storage-warning').hidden=false;node('storage-warning').textContent=message}
 function savedSelection(){
  try{
   const raw=localStorage.getItem(storageKey);
   if(raw===null)return null;
   const ids=JSON.parse(raw);
   if(!Array.isArray(ids)||ids.some(id=>typeof id!=='string'))throw Error('invalid selection');
   return [...new Set(ids)].slice(0,maxSelected);
  }catch(_){storageWarning('无法读取已保存的选择，本次按最近记录初始化。');return null}
 }
 function persistSelection(){
  try{localStorage.setItem(storageKey,JSON.stringify(cmp.selected))}
  catch(_){storageWarning('浏览器未能保存选择，刷新页面后可能需要重新选择。')}
 }
 function selectionKey(){return JSON.stringify(cmp.selected)}
 function updateControls(){
  const ready=cmp.loaded&&!cmp.loading,visible=visibleRows(),count=cmp.selected.length;
  node('refresh').disabled=cmp.loading;
  node('refresh').textContent=cmp.loading?'正在刷新…':'刷新列表';
  node('select-visible').disabled=!ready||!visible.some(row=>row.selectable);
  node('clear').disabled=!ready||!count;
  node('generate').disabled=!ready||cmp.generating||count<2||count>maxSelected;
  node('generate').textContent=cmp.generating?'正在生成…':'生成对比';
  node('selection').textContent='已选 '+count+' / '+maxSelected+' 项 · 当前显示 '+visible.length+' 条（其中已选 '+visible.filter(row=>cmp.selected.includes(row.run_id)).length+' 条）'+(count<2?' · 至少选择 2 项':'');
 }
 function renderCandidates(){
  const rows=visibleRows(),ready=cmp.loaded&&!cmp.loading;
  if(!cmp.rows.length){node('candidates').innerHTML='<p class="comparison-empty">'+(cmp.loading?'正在读取评测记录…':cmp.loaded?'暂无评测记录，完成评测后点击“刷新列表”。':'列表尚未加载，请点击“刷新列表”重试。')+'</p>';updateControls();return}
  if(!rows.length){node('candidates').innerHTML='<p class="comparison-empty">没有匹配的记录。调整搜索内容；已选记录仍然保留。</p>';updateControls();return}
  node('candidates').innerHTML=table(['选择','测评 / ID','模型 / 版本','完成时间（北京时间）','综合分','来源 / 状态'],rows.map(row=>{
   const checked=cmp.selected.includes(row.run_id),unavailable=!row.selectable;
   return [
    '<input type="checkbox" data-comparison-id="'+e(row.run_id)+'" aria-label="'+e('选择 '+identity(row))+'"'+(checked?' checked':'')+(!ready||unavailable?' disabled':'')+'>',
    '<strong>'+e(row.name)+'</strong><span class="comparison-meta">ID '+e(shortId(row.run_id))+'</span>',
    e(row.model_id||'未标注模型')+'<span class="comparison-meta">'+e(version(row))+'</span>',
    '<span class="comparison-date">'+e(dateText(dateValue(row)))+'</span>',score(row.weighted_score),
    e(row.batch_name||'独立测评')+'<span class="comparison-meta">'+e(kind(row.kind))+(unavailable?' · '+e(row.reason||'该记录暂不可对比'):'')+'</span>'
   ];
  }));
  node('candidates').querySelectorAll('[data-comparison-id]').forEach(box=>{const row=box.closest('tr');row.classList.toggle('comparison-picked',box.checked);row.classList.toggle('comparison-unavailable',box.disabled)});
  updateControls();
 }
 function invalidateResult(message){
  cmp.resultRevision++;cmp.generating=false;
  node('results').hidden=true;node('results').innerHTML='';
  node('result-status').textContent=message||'选择已变更，请重新生成对比。';
  updateControls();
 }
 function changeSelection(ids){
  const next=[...new Set(ids)];
  if(JSON.stringify(next)===selectionKey())return;
  cmp.selected=next;persistSelection();node('status').textContent='已更新选择，点击“生成对比”查看。';invalidateResult();renderCandidates();
 }
 function toggleSelection(id,checked){
  if(!cmp.loaded||cmp.loading)return;
  const row=cmp.rows.find(item=>item.run_id===id);
  if(!row?.selectable)return;
  if(checked&&!cmp.selected.includes(id)&&cmp.selected.length>=maxSelected){node('status').textContent='最多选择 8 个评测记录，请先取消一项。';renderCandidates();return}
  changeSelection(checked?[...cmp.selected,id]:cmp.selected.filter(value=>value!==id));
 }
 function selectVisible(){
  if(!cmp.loaded||cmp.loading)return;
  const next=[...new Set([...cmp.selected,...visibleRows().filter(row=>row.selectable).map(row=>row.run_id)])];
  if(next.length>maxSelected){node('status').textContent='全选可见记录后共 '+next.length+' 项，超过 8 项上限。请缩小搜索范围或逐项勾选；当前选择未改变。';return}
  changeSelection(next);
 }
 async function loadCandidates(){
  const revision=++cmp.listRevision;
  cmp.loading=true;invalidateResult('列表刷新后，请核对选择并生成对比。');
  node('status').textContent='正在读取评测记录…';renderCandidates();
  try{
   const data=await api('/run-comparisons/candidates');
   if(revision!==cmp.listRevision)return;
   if(!Array.isArray(data.candidates))throw Error('评测列表格式异常');
   const unique=new Map(data.candidates.filter(row=>typeof row.run_id==='string').map(row=>[row.run_id,row]));
   cmp.rows=[...unique.values()].sort((a,b)=>(Date.parse(dateValue(b))||0)-(Date.parse(dateValue(a))||0)||a.run_id.localeCompare(b.run_id));
   const available=cmp.rows.filter(row=>row.selectable).map(row=>row.run_id);
   let selected=cmp.selected;
   if(!cmp.initialized){const saved=savedSelection();selected=saved===null?available.slice(0,3):saved;cmp.initialized=true}
   cmp.selected=selected.filter(id=>available.includes(id)).slice(0,maxSelected);
   const removed=selected.length-cmp.selected.length;
   cmp.loaded=true;persistSelection();
   node('status').textContent='共 '+cmp.rows.length+' 条记录，'+available.length+' 条可对比。'+(removed?'已清理 '+removed+' 条失效选择，未自动加入其他记录。':'')+(available.length<2?'至少需要 2 条已完成且可用的评测记录。':'');
  }catch(err){
   if(revision!==cmp.listRevision)return;
   cmp.loaded=false;cmp.rows=[];
   node('status').textContent='加载失败：'+err.message+'。请点击“刷新列表”重试，已保存选择保留。';
  }finally{if(revision===cmp.listRevision){cmp.loading=false;renderCandidates()}}
 }
 function chart(name,title,query){
  const url='/api/run-comparisons/charts/'+name+'.svg?'+query;
  return '<article class="comparison-chart"><div class="chart-head"><h3>'+e(title)+'</h3><span class="row"><a href="'+e(url)+'" download="run-comparison-'+name+'.svg">导出 SVG</a><button type="button" class="secondary" data-png="'+e(url)+'" data-chart-name="run-comparison-'+name+'">导出 PNG</button></span></div><img data-comparison-chart src="'+e(url)+'" alt="'+e(title)+'"><p class="warn" hidden></p></article>';
 }
 function renderResult(data,query){
  const entries=data.entries;
  const legend='<ul class="comparison-legend">'+entries.map(row=>'<li><span class="sw '+colorClass(row)+'"></span><span>'+e(identity(row))+'</span></li>').join('')+'</ul>';
  const warnings=Array.isArray(data.warnings)&&data.warnings.length?'<ul class="warn">'+data.warnings.map(warning=>'<li>'+e(warning)+'</li>').join('')+'</ul>':'';
  const detail=table(['排名','测评 / 模型 / 版本','类型 / 推理','题数',...dimensions.map(([,label])=>label),'综合分'],entries.map(row=>[
   e(row.rank),'<span class="sw '+colorClass(row)+'"></span>'+e(identity(row))+'<span class="comparison-meta">'+e(row.model_id||'未标注模型')+' · '+e(version(row))+'</span>',
   e(kind(row.kind))+'<span class="comparison-meta">'+e(mode(row.mode))+' · '+e(row.calls_per_question)+' 次/题'+(row.early_stop?' · 提前结束':'')+'</span>',e(row.total),
   ...dimensions.map(([key])=>accuracy(row.dimensions?.[key]?.accuracy)),score(row.weighted_score)
  ]));
  node('results').innerHTML='<p class="result-note">'+e(data.scope_note||'仅比较所选记录，结果不改变正式排名。')+'</p>'+warnings+legend+'<div class="comparison-charts">'+chart('radar','四维准确率雷达图 · 全部所选记录 · 固定 0–100%',query)+chart('ranking','综合分排行 · 仅当前选择',query)+'</div><div class="comparison-details"><h3>对比明细</h3>'+detail+'</div>';
  node('results').hidden=false;
  node('results').querySelectorAll('[data-comparison-chart]').forEach(img=>img.addEventListener('error',()=>{img.hidden=true;img.nextElementSibling.hidden=false;img.nextElementSibling.textContent='图表加载失败，请重新生成对比。下方明细仍可查看。'}));
 }
 async function generateComparison(){
  if(cmp.generating||cmp.loading||!cmp.loaded)return;
  if(cmp.selected.length<2||cmp.selected.length>maxSelected){node('result-status').textContent='请选择 2–8 个已完成的评测记录。';return}
  const ids=[...cmp.selected],key=selectionKey(),revision=++cmp.resultRevision,query=new URLSearchParams();
  ids.forEach(id=>query.append('run_id',id));
  cmp.generating=true;node('results').hidden=true;node('results').innerHTML='';
  node('result-status').textContent='正在生成 '+ids.length+' 项评测对比…';updateControls();
  try{
   const data=await api('/run-comparisons?'+query);
   if(revision!==cmp.resultRevision||key!==selectionKey()||cmp.loading)return;
   if(!Array.isArray(data.entries)||data.entries.length!==ids.length||new Set(data.entries.map(row=>row.run_id)).size!==ids.length||data.entries.some(row=>!ids.includes(row.run_id)))throw Error('返回记录与当前选择不一致，请刷新列表后重试');
   renderResult(data,query.toString());node('result-status').textContent='已生成 '+ids.length+' 项对比。两张图与明细使用相同颜色；同分并列。';
  }catch(err){if(revision===cmp.resultRevision){node('result-status').textContent='生成失败：'+err.message;node('results').hidden=true;node('results').innerHTML=''}}
  finally{if(revision===cmp.resultRevision){cmp.generating=false;updateControls()}}
 }
 node('search').oninput=renderCandidates;
 node('refresh').onclick=loadCandidates;
 node('select-visible').onclick=selectVisible;
 node('clear').onclick=()=>{if(cmp.loaded&&!cmp.loading)changeSelection([])};
 node('generate').onclick=generateComparison;
 node('candidates').onchange=ev=>{const box=ev.target.closest('[data-comparison-id]');if(box)toggleSelection(box.dataset.comparisonId,box.checked)};
 document.addEventListener('wargame:tab',ev=>{if(ev.detail==='analytics'&&!cmp.loaded&&!cmp.loading)loadCandidates()});
 if(state.tab==='analytics')loadCandidates();
})();
