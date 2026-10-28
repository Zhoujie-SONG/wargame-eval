'use strict';
(()=>{
Object.assign(labels,{submissions:'参赛名单',batches:'设置并启动',analytics:'监控与结果'});
const cs={submissions:[],preview:null,requestKey:crypto.randomUUID(),selected:new Set(),batchId:null,revision:0,analysisRevision:0,launching:false,pendingLaunch:null};
const dims=['L2_main','L3_main','L1_sub','L2_sub'];
const charts={ranking:'综合分排行榜',dimensions:'四维准确率对比',radar:'能力雷达图',efficiency:'推理效率分析'};
cs.progressRevision=0;cs.progressPolling=false;cs.listPolling=false;cs.batchRunning=false;
const fmt=n=>Number(n||0).toLocaleString('en-US');
const TEAM_COLORS=['#2f6fa8','#e08a3c','#3a9d7a','#8e6bbf','#d0586a','#6b7f95'];
const DIM_NAMES={L2_main:'L2 主',L3_main:'L3 主',L1_sub:'L1 子',L2_sub:'L2 子'};
const pct1=v=>v===null||v===undefined?'—':(v*100).toFixed(1)+'%';
const teamIdx=runs=>Object.fromEntries([...new Set(runs.map(r=>String(r.team_id)))].sort().map((id,i)=>[id,i%6]));
const bar=(cls,value,max)=>'<progress class="'+cls+'" max="'+(max||1)+'" value="'+(value||0)+'"></progress>';
const kpi=(label,value,note,extra='')=>'<div class="kpi"><small>'+e(label)+'</small><strong>'+e(value)+'</strong><em>'+e(note)+'</em>'+extra+'</div>';
function badge(status){const l=statusLabel(status);return '<span class="status status-'+l.cls+'">'+e(l.text)+'</span>'}
function fmtDuration(sec){const m=Math.round((sec||0)/60);if(m<1)return '不足 1 分钟';const h=Math.floor(m/60);return h?h+' 小时 '+(m%60)+' 分':m+' 分钟'}
const MODE_NAMES={single_pass:'Single-pass · 单次推理',self_refine:'Self-refine · 多轮检查修正',majority_vote:'Majority-vote · 独立采样投票'};
const concurrencyNow=()=>{const f=$('#batch-form');return +(f&&f.elements&&f.elements.concurrency&&f.elements.concurrency.value||4)};
function renderProbe(){const box=$('#probe-result');if(!box||!cs.probe)return;const list=cs.probe,names=new Map((cs.submissions||[]).map(x=>[x.id,x.team_name]));
 const LV=[1,2,4,8,16],cell=(r,l)=>{const x=(r.levels||[]).find(v=>v.level===l);return x?(x.ok===l?'<span class="probe-ok">✓</span>':'<span class="probe-bad">'+(x.limited?'限流':'失败')+'</span>'):'—'};
 const rows=list.map(r=>[e(names.get(r.submission_id)||r.team_id||r.submission_id),...LV.map(l=>r.error?'—':cell(r,l)),r.error?'<span class="probe-bad">'+e(r.error)+'</span>':'<b>'+r.stable+' 套</b>',e(r.error?'':r.note)]);
 const ok=list.filter(r=>!r.error),bad=list.length-ok.length,min=ok.length?Math.min(...ok.map(r=>r.stable)):0;
 const options=[...$('#batch-form').elements.concurrency.options].map(o=>+o.value),pick=Math.max(0,...options.filter(v=>v<=min));
 cs.probePick=pick;
 box.innerHTML=table(['队伍',...LV.map(l=>l+' 路'),'稳定并发','说明'],rows)
  +(bad||!pick?'<p class="roster-warning" role="alert">'+(bad?bad+' 支队伍检测失败；':'')+(pick?'':'有队伍连 1 路都不稳定；')+'请先处理这些队伍的接口问题再开赛。</p>':'')
  +(pick?'<p>各队稳定并发的最小值是 <b>'+min+' 路</b>，建议全队统一使用 <b>'+pick+' 路</b>。 <button type="button" class="secondary" id="probe-apply">采用 '+pick+' 路</button></p>':'')}
function renderLaunchSummary(){const f=$('#batch-form');if(!f||!f.elements)return;const mode=f.elements.mode.value,calls=mode==='single_pass'?1:(+f.elements.calls.value||1),teams=cs.selected.size;
 const rows=[['参赛队伍',teams+' 支（在“参赛名单”调整）'],['每队题数','1,279 道 QA'],['推理策略',MODE_NAMES[mode]||mode],['每题最大调用',calls+' 次'],['队内并发',concurrencyNow()+' 套题同时做（全队统一，各队同时开始；套内先子问题后主问题）'],['最大 API 请求数',teams?fmt(teams)+' × 1,279 × '+calls+' = '+fmt(teams*1279*calls):'—']];
 $('#launch-summary').innerHTML='<dl class="kv">'+rows.map(([k,v])=>'<div><dt>'+e(k)+'</dt><dd>'+e(v)+'</dd></div>').join('')+'</dl>'+(cs.probePick&&concurrencyNow()>cs.probePick?'<p class="roster-warning">所选并发高于检测结果（'+cs.probePick+' 路），可能触发限流；限流会自动等待重试，但会拖慢比赛。</p>':'')}
function askConfirm(title,text){const dialog=$('#confirm-dialog');$('#confirm-title').textContent=title;$('#confirm-text').textContent=text;
 return new Promise(resolve=>{dialog.addEventListener('close',()=>resolve(dialog.returnValue==='confirm'),{once:true});dialog.returnValue='cancel';dialog.showModal()})}
function selectionNotice(list){return (list||[]).map(v=>v.previous_version?'队伍 '+v.team_name+' 已从 '+v.previous_version+' 切换到 '+v.submission_version:'队伍 '+v.team_name+' 的 '+v.submission_version+' 已加入参赛名单').join('；')}
function invalidate(){renderLaunchSummary();if(cs.launching||cs.pendingLaunch)return;cs.revision++;cs.requestKey=crypto.randomUUID();$('#batch-launch-status').textContent=''}
function modeLabel(official){return official?'正式比赛｜全量 1,279 道 QA':'历史测试｜非正式成绩'}
function setLaunchState(busy,message=''){cs.launching=busy;const locked=busy||!!cs.pendingLaunch;for(const control of $('#batch-form').querySelectorAll('input,select'))control.disabled=locked;for(const control of document.querySelectorAll('[data-team-version],#reset-roster-latest,#clear-teams,#team-filter,#probe-concurrency'))control.disabled=locked;$('#start-official-batch').disabled=busy;$('#start-official-batch').textContent=busy?'正在处理…':cs.pendingLaunch?'重试 / 查看本批次启动结果':'开始正式测评';$('#batch-launch-status').textContent=message;if(!locked)updateCallsControl($('#batch-form'),'#batch-calls-help')}
async function confirmOfficialStart(batch){
 const dialog=$('#official-start-dialog'),p=batch.plan;
 const items=p.items||[];
 const latest=new Map();
 for(const s of cs.submissions)if(s.is_latest||!latest.has(s.team_id))latest.set(s.team_id,s);
 const outdated=items.filter(item=>{const newer=latest.get(item.team_id);return newer&&newer.submission_version!==item.submission_version});
 const summary=table(['确认项目','本次正式比赛配置'],[
  ['比赛名称',e(batch.name)],['参赛队伍',p.teams+' 支'],['每队 QA',p.questions_per_team+' 道'],
  ['推理模式',e(p.mode)],['每题最大调用',p.calls+' 次'],['每队并发',(p.concurrency||1)+' 套题同时做（全队统一，各队同时开始；套内先子问题后主问题）'],
  ['预计最大 API 请求数',p.teams+' × '+p.questions_per_team+' × '+p.calls+' = '+p.max_requests],
  ['截图状态',p.real_images_complete?'完整':'不完整'],
  ['模型状态',p.max_external_requests===p.max_requests?'全部真实模型':'存在不合法配置']]);
 const showPreflight=items.some(item=>item.preflight_result!==undefined&&item.preflight_result!==null);
 const heads=['队伍 / ID','方案版本','模型 ID','聚合代理','Prompt 版本','凭据来源'];
 if(showPreflight)heads.push('最近连通性检查');
 const roster=items.length?'<h3>即将冻结的参赛名单</h3>'+table(heads,items.map(item=>{
    const newer=latest.get(item.team_id),old=newer&&newer.submission_version!==item.submission_version;
    const cells=[e(item.team_name)+' / '+e(item.team_id),
      e(item.submission_version)+(old?'<br><span class="roster-outdated">有更新版本 '+e(newer.submission_version)+'（未选中）</span>':''),
      e(item.model_id),item.is_aggregator?'是':'否',e(item.prompt_version),
      e(item.credential_source||'已提供')];
    if(showPreflight)cells.push(item.preflight_result==null?'—':e(String(item.preflight_result)));
    return cells;
 })):'<p>未返回参赛名单；请取消并重新检查批次预览。</p>';
 const warning=outdated.length?'<p class="roster-warning" role="alert">注意：有 '+outdated.length+' 支队伍存在未选中的更新版本；请核对后再确认启动。</p>':'';
 $('#official-start-summary').innerHTML=summary+warning+roster;
 return new Promise(resolve=>{dialog.addEventListener('close',()=>resolve(dialog.returnValue==='confirm'),{once:true});dialog.returnValue='cancel';dialog.showModal()});
}
async function startOfficial(){if(cs.launching)return;let pending=cs.pendingLaunch;setLaunchState(true,'正在校验题库、截图和队伍配置…');try{if(!pending){const payload=batchPayload(),revision=cs.revision;const plan=await api('/evaluation-batches/preview','POST',payload);if(revision!==cs.revision)return;if(!await confirmOfficialStart({name:payload.name,plan}))return;pending={payload,preview_fingerprint:plan.fingerprint};sessionStorage.setItem('wargame.pendingOfficialLaunch',JSON.stringify(pending));cs.pendingLaunch=pending}setLaunchState(true,'正在冻结配置并启动队伍任务…');const result=await api('/evaluation-batches/start-new','POST',{...pending.payload,confirm_official:true,preview_fingerprint:pending.preview_fingerprint});sessionStorage.removeItem('wargame.pendingOfficialLaunch');cs.pendingLaunch=null;cs.batchId=result.batch_id;notice('正式比赛已启动，所有队伍已进入队列');try{if(typeof switchTab==='function')await switchTab('analytics');else await loadBatches();await showBatch(result.batch_id)}catch(err){notice('正式批次 '+result.batch_id+' 已启动；监控暂时加载失败，请刷新批次列表。'+err.message,true)}}catch(err){if(err.status===400||err.status===422){sessionStorage.removeItem('wargame.pendingOfficialLaunch');cs.pendingLaunch=null}throw err}finally{setLaunchState(false,cs.pendingLaunch?'启动结果尚未确认。配置已锁定，请点击重试；系统将沿用原幂等键，不会重复创建或调用。':'')}}
try{const saved=sessionStorage.getItem('wargame.pendingOfficialLaunch');if(saved){cs.pendingLaunch=JSON.parse(saved);cs.selected=new Set(cs.pendingLaunch.payload.submission_ids);cs.requestKey=cs.pendingLaunch.payload.request_key;const f=$('#batch-form');f.elements.name.value=cs.pendingLaunch.payload.name;f.elements.mode.value=cs.pendingLaunch.payload.mode;f.elements.early_stop.checked=cs.pendingLaunch.payload.early_stop;setLaunchState(false,'发现尚未确认启动结果的正式批次，请点击重试以获取原批次状态。')}}catch(err){notice('无法读取待确认批次，请保留原 request_key 后再重试',true)}
async function loadSubmissions(){
 const [latest,roster]=await Promise.all([api('/submissions'),api('/submissions/official-roster')]);
 cs.submissions=latest;
 if(!cs.launching&&!cs.pendingLaunch){
   cs.selected=new Set(roster.submission_ids.filter(id=>latest.some(s=>s.id===id&&s.provider!=='mock'&&s.available)));
 }
 const hasTeams=latest.some(s=>s.provider!=='mock');const panel=$('#import-panel');if(panel)panel.open=!hasTeams;
  renderTeamSelection();
}
async function saveRosterSelection(){
 await api('/submissions/official-roster','PUT',{submission_ids:[...cs.selected]});
}
async function restoreLatestRoster(){
 if(cs.launching||cs.pendingLaunch)return;
 const chosen=new Map();
 for(const s of cs.submissions)if(s.provider!=='mock'&&s.available&&!chosen.has(s.team_id))chosen.set(s.team_id,s.id);
 const previous=cs.selected;
 cs.selected=new Set(chosen.values());
 try{await saveRosterSelection()}catch(err){cs.selected=previous;renderTeamSelection();throw err}
 renderTeamSelection();invalidate();
 notice('已恢复为每队最新可用版本，请核对选手名单。');
}
function renderTeamSelection(){
 const search=$('#team-filter').value.toLowerCase(),locked=cs.launching||cs.pendingLaunch;
 const teams=new Map();
 for(const s of cs.submissions){if(s.provider==='mock'||!s.available)continue;if(!teams.has(s.team_id))teams.set(s.team_id,[]);teams.get(s.team_id).push(s)}
 const rows=[...teams.values()].filter(vs=>(vs[0].team_name+' '+vs[0].team_id+' '+vs.map(v=>v.model_id).join(' ')).toLowerCase().includes(search)).map(vs=>{
   const chosen=vs.find(v=>cs.selected.has(v.id)),shown=chosen||vs[0],id=shown.team_id;
   const options='<option value="">不参赛</option>'+vs.map(v=>'<option value="'+e(v.id)+'"'+(chosen&&chosen.id===v.id?' selected':'')+'>'+e(v.submission_version)+(v.is_latest?'（最新）':'')+'</option>').join('');
   const select='<select data-team-version="'+e(id)+'" aria-label="'+e(shown.team_name)+' 参赛版本"'+(locked?' disabled':'')+'>'+options+'</select>';
   let state=['未参赛','gray'];
   if(chosen)state=!chosen.credential_provided?['缺凭据','red']:!chosen.is_latest?['非最新版本','yellow']:['就绪','green'];
   return [e(shown.team_name)+'<br><span class="muted">'+e(id)+'</span>',select,e(shown.model_id),shown.is_aggregator?'是':'否',
     shown.credential_provided?e(shown.credential_source||'已提供'):'<span class="status status-red">未提供</span>',
     '<span class="status status-'+state[1]+'">'+state[0]+'</span>'+(chosen&&!chosen.is_latest?'<br><span class="roster-outdated">有更新版本 '+e(chosen.latest_version||'')+'</span>':'')];
 });
 $('#submission-selection-status').textContent='已选择 '+cs.selected.size+' 支队伍（共导入 '+teams.size+' 支）';
 $('#batch-submissions').innerHTML=rows.length?table(['队伍','参赛版本','模型','聚合代理','API 凭据','状态'],rows):'<p class="empty-hint">'+(teams.size?'没有匹配的队伍。':'还没有导入参赛配置。请先在上方“导入选手配置”中导入选手 JSON。')+'</p>';
 renderLaunchSummary();
}
function batchPayload(){const f=$('#batch-form');const mode=f.elements.mode.value,calls=+f.elements.calls.value;if(mode==='single_pass'&&calls!==1)throw Error('单次推理固定 1 次调用；如需多次，请选择多轮修正或独立投票');if(!Number.isInteger(calls)||calls<1||calls>state.settings.competition.max_calls_per_question)throw Error('调用次数超出主办方上限');return {request_key:cs.requestKey,name:f.elements.name.value,submission_ids:[...cs.selected],mode,calls,early_stop:mode==='self_refine'&&f.elements.early_stop.checked,official:true,dimension:null,limit:null,question_ids:[],concurrency:concurrencyNow()}}
async function loadBatches(skipProgress=false){const rows=await api('/evaluation-batches');
 $('#batch-list').innerHTML=rows.length?table(['批次','队伍状态','QA 进度','请求 / 耗时','操作'],rows.map(b=>[e(b.name)+'<br><span class="tag">'+e(modeLabel(b.plan.official))+'</span><br>'+badge(b.status),
   b.finished+' 完成 / '+b.teams+' 队',b.completed+' / '+b.total+'<progress max="'+(b.total||1)+'" value="'+b.completed+'"></progress>',fmt(b.actual_calls)+' 次 / '+fmtDuration(b.elapsed_seconds),
   '<button data-batch-progress="'+e(b.batch_id)+'">监控</button><button data-batch-analysis="'+e(b.batch_id)+'">分析 / 导出</button>'+(b.plan.official&&!b.started&&b.status==='draft'?'<button data-start-batch="'+e(b.batch_id)+'">启动批次'+(b.plan.max_external_requests?'（产生 API 费用）':'')+'</button>':'')])):'<p class="empty-hint">还没有比赛批次。</p>';
 const old=$('#analytics-batch').value;options($('#analytics-batch'),rows.map(b=>({id:b.batch_id,label:b.name+' · '+statusLabel(b.status).text})),old);
 if(!cs.batchId){const current=rows.find(b=>b.plan.official&&b.started);if(current)cs.batchId=current.batch_id}
 if(cs.batchId&&!skipProgress)await showBatch(cs.batchId)}
const RED_STATES=['interrupted','infrastructure_error','suspended'];
function runRank(r){return RED_STATES.includes(r.status)?0:['running','stopping'].includes(r.status)?1:['queued','draft'].includes(r.status)?2:r.status==='stopped'?3:r.status==='completed'?4:5}
function errorText(r){const k=r.error_breakdown||{};const parts=[['api_error','接口'],['timeout','超时'],['invalid_output','无效'],['infrastructure_error','平台']].filter(([x])=>k[x]).map(([x,l])=>l+' '+k[x]);return String(r.error_count||0)+(parts.length?'（'+parts.join(' / ')+'）':'')}
function renderExceptions(b,runs){const panel=$('#exception-panel'),box=$('#exception-actions');if(!panel||!box)return;
 const rows=runs.map(r=>{const acts=[];
   if(['queued','running'].includes(r.status))acts.push('<button class="danger" data-flow-stop="'+e(r.run_id)+'">停止该队</button>');
   if(['interrupted','infrastructure_error'].includes(r.status)||r.infrastructure_errors>0)acts.push('<button class="danger" data-flow-rerun="'+e(r.run_id)+'">按冻结配置重跑</button>');
   if(!['queued','running','stopping'].includes(r.status))acts.push('<button class="secondary" data-run="'+e(r.run_id)+'">标记平台故障…</button>');
   return [e(r.name)+' / '+e(r.submission_version),badge(r.status),acts.join('')||'—']});
 box.innerHTML=(b.status==='running'?'<p><button class="danger" data-flow-stop-batch="'+e(b.batch_id)+'">停止整个批次</button></p>':'')+table(['队伍','状态','操作'],rows);
 panel.hidden=false;const red=runs.filter(r=>RED_STATES.includes(r.status)).length,tag=$('#exception-tag');if(tag){tag.textContent=red?red+' 项需处理':'';tag.className='tag'+(red?' tag-bad':'')}if(red)panel.open=true}
async function showBatch(id){cs.batchId=id;const rev=++cs.progressRevision;const b=await api('/evaluation-batches/'+id+'/progress');if(cs.batchId!==id||rev!==cs.progressRevision)return;
 const node=$('#batch-progress'),running=b.status==='running';cs.batchRunning=running;node.hidden=false;
 const pct=b.total?b.completed/b.total*100:0,planned=b.plan&&b.plan.max_requests;
 const runs=[...(b.runs||[])].sort((x,y)=>runRank(x)-runRank(y)||String(x.team_id).localeCompare(String(y.team_id)));
 const idx=teamIdx(runs),partial=running||runs.some(r=>r.completed<r.total);
 const answered=runs.reduce((n,r)=>n+r.completed,0),right=runs.reduce((n,r)=>n+Object.values(r.dimensions||{}).reduce((m,d)=>m+d.correct,0),0);
 const errors=runs.reduce((n,r)=>n+(r.error_count||0),0);
 const remain=b.completed>=20?fmtDuration((b.total-b.completed)*b.elapsed_seconds/b.completed):'计算中';
 const rows=runs.map(r=>{const c='tc'+(idx[String(r.team_id)]||0),dimsHtml=dims.map(k=>{const d=(r.dimensions||{})[k]||{completed:0,correct:0};
    const v=d.answered_accuracy===undefined?(d.completed?d.correct/d.completed:null):d.answered_accuracy;
    return '<span class="muted">'+DIM_NAMES[k]+'</span>'+bar(c+' mini',v||0,1)+'<span class="num">'+pct1(v)+' <span class="muted">('+fmt(d.completed)+' 题)</span></span>'}).join('');
   const done=Object.values(r.dimensions||{}).reduce((m,d)=>m+d.correct,0),acc=r.answered_accuracy===undefined?(r.completed?done/r.completed:null):r.answered_accuracy;
   return ['<b>'+e(r.name)+'</b> <span class="muted">/ '+e(r.submission_version)+'</span><br><span class="muted small">'+e(r.model.model_id)+'</span>',
     bar(c,r.completed,r.total)+'<span class="num muted small">'+fmt(r.completed)+' / '+fmt(r.total)+' · '+(r.total?Math.round(r.completed/r.total*100):0)+'%</span>',
     '<div class="big-acc">'+pct1(r.completed?acc:null)+'</div><span class="muted small">已答 '+fmt(r.completed)+' 题中答对 '+fmt(done)+'</span>',
     '<div class="mini-grid">'+dimsHtml+'</div>',e(errorText(r)),badge(r.status),'<button data-run="'+e(r.run_id)+'">逐题详情</button>']});
 node.innerHTML='<h2>'+e(b.name)+' '+badge(b.status)+'</h2><p class="tag">'+e(modeLabel(b.plan.official))+(b.plan.concurrency?' ｜ 每队 '+b.plan.concurrency+' 套并发':'')+'</p>'
  +'<div class="kpis">'+kpi('整体进度',pct.toFixed(1)+'%',fmt(b.completed)+' / '+fmt(b.total)+' 题',bar('overall-bar',b.completed,b.total))
  +(running?kpi('预计剩余',remain,b.completed>=20?'按本批次平均速度估算':'已完成 20 题后给出估算'):kpi('未完成题数',fmt(b.total-b.completed),'本批次尚未全部完成'))
  +kpi(partial?'已答题平均准确率':'平均准确率',pct1(answered?right/answered:null),partial?'仅统计已答题，非最终成绩':'全部题目')
  +kpi('请求 / 错误',fmt(b.actual_calls)+' / '+fmt(errors),(planned?'计划 '+fmt(planned)+' 次 · ':'')+'错误率 '+(b.actual_calls?(errors/b.actual_calls*100).toFixed(2):'0.00')+'%')
  +kpi('已用时间',fmtDuration(b.elapsed_seconds),running?'进行中':'已停止计时')+'</div>'
  +'<h3>'+(partial?'各队实时进度 <span class="tag tag-warn">暂估</span>':'各队成绩')+'</h3>'
  +(partial?'<p class="sub">准确率 ＝ 已答对 ÷ <b>已答题数</b>（不是全部题数）。每队答完全部题目之前仅供观察，最终成绩以全部题目为准。</p>':'')
  +table(['队伍 / 模型','进度','当前准确率（已答）','分维度（已答题数）','异常','状态','操作'],rows);
 renderExceptions(b,runs);
 const results=$('#results-view');if(results)results.hidden=running;
 const curve=$('#curve-panel');if(curve&&!partial)curve.hidden=true;
 if(partial)loadCurves(id,idx,runs[0]?runs[0].total:0).catch(()=>{})}
async function loadCurves(id,idx,total,force=false){const panel=$('#curve-panel');if(!panel)return;
 if(!force&&cs.curveId===id&&Date.now()-(cs.curveAt||0)<15000)return;cs.curveId=id;cs.curveAt=Date.now();
 const data=await api('/evaluation-batches/'+id+'/accuracy-curves');if(cs.batchId!==id)return;
 const series=(data||[]).filter(d=>d.curve&&d.curve.length);if(!series.length){panel.hidden=true;return}
 const color=d=>TEAM_COLORS[idx[String(d.team_id)]||0];
 panel.hidden=false;panel.innerHTML='<h2>准确率随已答题数变化</h2><p class="sub">曲线越平稳，当前准确率越可信；已答题数少时（左侧）波动大属正常。</p><div class="legend">'+series.map(d=>'<span><i class="sw" data-c="'+e(color(d))+'"></i>'+e(d.name)+'</span>').join('')+'</div>'+curveSvg(series,color,total);
 panel.querySelectorAll('.sw[data-c]').forEach(n=>{n.className='sw tc'+TEAM_COLORS.indexOf(n.dataset.c)})}
function curveSvg(series,color,total){const W=1000,H=250,L=46,R=12,T=10,B=30;const max=Math.max(total||0,...series.map(d=>d.curve[d.curve.length-1][0]),1);
 const vals=series.flatMap(d=>d.curve.filter(p=>p[0]>=20).map(p=>p[1]));const all=vals.length?vals:series.flatMap(d=>d.curve.map(p=>p[1]));
 let lo=Math.max(0,Math.floor((Math.min(...all)-.05)*20)/20),hi=Math.min(1,Math.ceil((Math.max(...all)+.05)*20)/20);if(hi-lo<.2){hi=Math.min(1,lo+.2);lo=Math.max(0,hi-.2)}
 const x=i=>L+(W-L-R)*i/max,y=v=>T+(H-T-B)*(1-(Math.min(hi,Math.max(lo,v))-lo)/(hi-lo));
 let g='';for(let k=0;k<=4;k++){const v=lo+(hi-lo)*k/4;g+='<line x1="'+L+'" x2="'+(W-R)+'" y1="'+y(v).toFixed(1)+'" y2="'+y(v).toFixed(1)+'" stroke="#e5ebf2"/><text x="'+(L-8)+'" y="'+(y(v)+4).toFixed(1)+'" font-size="11" fill="#7b8ca3" text-anchor="end">'+Math.round(v*100)+'%</text>'}
 for(let k=0;k<=4;k++){const v=Math.round(max*k/4);g+='<text x="'+x(v).toFixed(1)+'" y="'+(H-8)+'" font-size="11" fill="#7b8ca3" text-anchor="middle">'+fmt(v)+'</text>'}
 for(const d of series){const c=color(d),pts=d.curve.map((p,k)=>(k?'L':'M')+x(p[0]).toFixed(1)+','+y(p[1]).toFixed(1)).join(' '),last=d.curve[d.curve.length-1];
  g+='<path d="'+pts+'" fill="none" stroke="'+c+'" stroke-width="2" stroke-linejoin="round"/><circle cx="'+x(last[0]).toFixed(1)+'" cy="'+y(last[1]).toFixed(1)+'" r="4" fill="'+c+'"/>'}
 return '<svg class="curve-chart" viewBox="0 0 '+W+' '+H+'" role="img" aria-label="各队准确率随已答题数变化">'+g+'</svg>'}
function analysisQuery(){const p=new URLSearchParams({top:$('#analytics-top').value});document.querySelectorAll('[data-analysis-team]:checked').forEach(n=>p.append('team_id',n.dataset.analysisTeam));return p}
async function loadAnalytics(rebuild=false){const id=$('#analytics-batch').value,revision=++cs.analysisRevision;if(!id){$('#competition-groups').innerHTML='<p>请先创建测评批次。</p>';return}if(rebuild){const batch=await api('/evaluation-batches/'+id);if(revision!==cs.analysisRevision||id!==$('#analytics-batch').value)return;$('#analytics-teams').innerHTML='<p>勾选队伍比较；雷达图展示所选范围中排名前四队。</p>'+batch.runs.map(r=>`<label class="inline"><input type="checkbox" data-analysis-team="${e(r.team_id)}" checked>${e(r.name)}</label>`).join('')}
 const checked=[...document.querySelectorAll('[data-analysis-team]:checked')];if(!checked.length){$('#competition-groups').textContent='请至少选择一支队伍';$('#competition-exports').innerHTML='';return}
 const params=analysisQuery(),data=await api('/evaluation-batches/'+id+'/analytics?'+params);if(revision!==cs.analysisRevision||id!==$('#analytics-batch').value)return;
 const exportParams=new URLSearchParams(params);exportParams.set('audience',$('#analytics-audience').value);const qs=e(exportParams.toString());
 $('#competition-exports').innerHTML=[['html','导出综合 HTML 报告'],['xlsx','导出 Excel'],['csv','导出 CSV'],['json','导出 JSON']].map(([f,l])=>`<a class="btn o" download href="/api/evaluation-batches/${e(id)}/export?${qs}&amp;format=${f}">${l}</a>`).join('')+`<a class="btn o" target="_blank" rel="noopener" href="/api/evaluation-batches/${e(id)}/export?${qs}&amp;format=html&amp;download=false">打开报告预览</a>`;
 $('#competition-summary').innerHTML='<p class="result-note">'+e(modeLabel(data.official))+' · 完成队伍 '+data.finished+' / '+data.teams+' · 已完成 QA '+fmt(data.qa_completed)+' / '+fmt(data.qa_total)+' · 实际请求 '+fmt(data.actual_calls)+'。'+e(data.scope_note)+'</p>';
 $('#competition-groups').innerHTML=data.groups.map(g=>renderGroup(g,id,params)).join('')+(data.groups.length?'':'<div class="panel">暂无完整可排名成绩。未完成队伍不进入最终排名。</div>')+(data.excluded.length?'<div class="panel"><h3>未进入最终排名</h3>'+data.excluded.map(x=>'<p>'+e(x.name+' · '+x.status+' · '+x.reason)+'</p>').join('')+'</div>':'');}
function chartPanel(id,g,params,chart){const q=new URLSearchParams(params);q.set('group',g.id);const src='/api/evaluation-batches/'+id+'/charts/'+chart+'.svg?'+q,title=charts[chart];
 return '<div class="panel chart-card"><div class="chart-head"><h3>'+e(title)+'</h3><span class="row"><a download href="'+e(src)+'">导出 SVG</a><button class="secondary" data-png="'+e(src)+'" data-chart-name="'+e(chart)+'">导出 PNG</button></span></div><img class="competition-chart" src="'+e(src)+'" alt="'+e(title+' · '+g.kind)+'"></div>'}
function renderGroup(g,id,params){
 const cells=g.entries.flatMap(r=>dims.map(k=>r.dimensions[k].accuracy)).filter(v=>v!==null),lo=Math.min(...cells),hi=Math.max(...cells);
 const heat=v=>v===null?'heat-na':'heat-'+(hi>lo?Math.min(5,Math.floor((v-lo)/(hi-lo)*6)):3),medal=['🥇','🥈','🥉'];
 const podium=g.entries.slice(0,3).map((r,i)=>'<div class="podium pc'+i+'"><small>第 '+(i+1)+' 名</small><strong>'+e(r.name)+'</strong><span class="score">'+r.weighted_score.toFixed(2)+'</span><em>综合分 · '+e(r.model.model_id)+'</em></div>').join('');
 const rows=g.entries.map((r,i)=>[String(r.rank)+(medal[i]?' '+medal[i]:''),'<i class="sw tc'+(i%6)+'"></i><b>'+e(r.name)+'</b><br><span class="muted small">'+e(r.model.model_id)+'</span>',
   bar('tc'+(i%6),r.weighted_score,100)+'<b class="num">'+r.weighted_score.toFixed(2)+'</b>',
   ...dims.map(k=>'<div class="heat '+heat(r.dimensions[k].accuracy)+'">'+pct1(r.dimensions[k].accuracy)+'</div>'),
   r.mean_question_seconds===null?'—':r.mean_question_seconds.toFixed(1)+' 秒',r.mean_calls===null||r.mean_calls===undefined?'—':r.mean_calls.toFixed(1),`<button data-run="${e(r.run_id)}">查看</button>`]);
 return '<div class="podiums">'+podium+'</div>'
  +'<div class="panel"><h2>综合排名与四维准确率</h2><p class="sub">综合分 ＝ 100 × (0.35·L2主 ＋ 0.35·L3主 ＋ 0.15·L1子 ＋ 0.15·L2子)；颜色越深准确率越高。'+e(g.kind+' · '+g.mode)+' · 每题最多 '+g.calls+' 次 · 提前结束 '+g.early_stop+' · 比较组 '+e(g.id)+'</p>'
  +table(['排名','队伍 / 模型','综合分','L2 主','L3 主','L1 子','L2 子','平均耗时','平均调用','详情'],rows)+'</div>'
  +'<div class="charts">'+chartPanel(id,g,params,'radar')+chartPanel(id,g,params,'efficiency')+'</div>'
  +'<details class="fold"><summary><span class="chev" aria-hidden="true"></span><b>更多图表</b><span class="hint">综合分排行榜、四维准确率对比</span></summary><div class="fold-body">'+chartPanel(id,g,params,'ranking')+chartPanel(id,g,params,'dimensions')+'</div></details>'}
async function pngExport(url,name){const response=await fetch(url);if(!response.ok)throw Error('图表加载失败');const svg=await response.text(),blob=new Blob([svg],{type:'image/svg+xml;charset=utf-8'}),src=URL.createObjectURL(blob);try{const image=new Image();await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=()=>reject(Error('PNG 转换失败'));image.src=src});const canvas=document.createElement('canvas'),scale=Math.min(2,Math.sqrt(16000000/(image.naturalWidth*image.naturalHeight)),32000/image.naturalHeight);canvas.width=image.naturalWidth*scale;canvas.height=image.naturalHeight*scale;const ctx=canvas.getContext('2d');ctx.scale(scale,scale);ctx.drawImage(image,0,0);const png=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));if(!png)throw Error('PNG 生成失败');const link=document.createElement('a'),file=URL.createObjectURL(png);link.href=file;link.download=name+'.png';link.click();setTimeout(()=>URL.revokeObjectURL(file),1000)}finally{URL.revokeObjectURL(src)}}
$('#json-upload').onsubmit=action(async()=>{
 const formData=new FormData($('#json-upload'));
 $('#json-import-status').textContent='正在校验并保存选手配置…';
 const result=await api('/submissions/json/import','POST',formData);
 if(!result.ok){
   $('#json-import-status').textContent='导入未生效：'+result.errors+' 行格式错误。'+result.items.map(x=>' 第 '+x.row+' 行：'+x.errors.join('；')).join('');
   return;
 }
 $('#json-upload').reset();
 await loadSubmissions();
 $('#json-import-status').textContent='成功导入 '+result.imported+' 份配置，跳过重复 '+result.duplicates+' 份。未调用模型。';
 notice('参赛配置已导入。'+(selectionNotice(result.selection_updates)?selectionNotice(result.selection_updates)+'，请在“参赛名单”中核对。':''));
});
$('#submission-upload').onsubmit=action(async()=>{cs.preview=null;$('#confirm-import').disabled=true;const d=await api('/submissions/batch/preview','POST',new FormData($('#submission-upload')));cs.preview=d.preview_id;$('#submission-upload').reset();$('#import-preview').innerHTML='<p>总计 '+d.total+' · 可导入 '+d.ready+' · 错误 '+d.errors+' · 重复 '+d.duplicates+' · 模型请求 '+d.api_requests+'</p>'+table(['行号','队伍 / 版本','模型 / API','聚合','三段 Prompt','凭据','状态 / 错误'],d.items.map(i=>[i.row,e((i.team_name||'')+' / '+(i.submission_version||'')),e(i.model_id||'')+'<br>'+e(i.api_base_url||''),i.is_aggregator?'是':'否',i.prompts_complete?'齐全':'缺失 / 无效',i.credential_provided?'已提供':'未提供 / Mock',e(i.status)+'<br>'+e(i.errors.join('；'))]));$('#confirm-import').disabled=!!d.errors});
$('#confirm-import').onclick=action(async ev=>{ev.target.disabled=true;try{const r=await api('/submissions/batch/import','POST',{preview_id:cs.preview});notice('导入 '+r.imported+' 份，跳过重复 '+r.duplicates+' 份；未调用模型');await loadSubmissions();if(selectionNotice(r.selection_updates))notice(selectionNotice(r.selection_updates)+'，请在“参赛名单”中核对。');await config()}catch(err){ev.target.disabled=false;throw err}});
$('#team-filter').oninput=renderTeamSelection;
$('#reset-roster-latest').onclick=action(restoreLatestRoster);
$('#clear-teams').onclick=action(async()=>{
 if(cs.launching||cs.pendingLaunch)return;
 const previous=cs.selected;cs.selected=new Set();
 try{await saveRosterSelection()}catch(err){cs.selected=previous;renderTeamSelection();throw err}
 renderTeamSelection();invalidate();
});
$('#batch-form').oninput=invalidate;
$('#batch-form').elements.mode.onchange=()=>{updateCallsControl($('#batch-form'),'#batch-calls-help',true);invalidate()};
$('#batch-form').elements.calls.onchange=()=>{updateCallsControl($('#batch-form'),'#batch-calls-help');invalidate()};
$('#batch-form').onsubmit=action(startOfficial);
$('#analytics-batch').onchange=action(()=>loadAnalytics(true));$('#analytics-top').onchange=action(()=>loadAnalytics());$('#analytics-audience').onchange=action(()=>loadAnalytics());$('#refresh-analytics').onclick=action(()=>loadAnalytics());
document.addEventListener('change',action(async ev=>{if(ev.target.dataset.teamVersion!==undefined){if(cs.launching||cs.pendingLaunch)return;const before=new Set(cs.selected);
 for(const s of cs.submissions)if(s.team_id===ev.target.dataset.teamVersion)cs.selected.delete(s.id);
 if(ev.target.value)cs.selected.add(ev.target.value);
 try{await saveRosterSelection()}catch(err){cs.selected=before;renderTeamSelection();throw err}renderTeamSelection();invalidate()}if(ev.target.dataset.analysisTeam)await loadAnalytics()}));
document.body.addEventListener('click',action(async ev=>{const b=ev.target.closest('button');if(!b)return;const d=b.dataset;if(d.batchProgress)await showBatch(d.batchProgress);if(d.startBatch){b.disabled=true;try{const batch=await api('/evaluation-batches/'+d.startBatch);if(batch.plan.official&&!await confirmOfficialStart(batch))return;await api('/evaluation-batches/'+d.startBatch+'/start','POST',{confirm_official:batch.plan.official});notice('已启动批次，所有队伍自动进入队列');await loadBatches()}finally{b.disabled=false}}if(d.stopBatch){await api('/evaluation-batches/'+d.stopBatch+'/stop','POST');notice('已请求停止整个批次');await loadBatches()}if(d.batchAnalysis){await switchTab('analytics');$('#analytics-batch').value=d.batchAnalysis;await loadAnalytics(true)}if(d.flowStop&&await askConfirm('停止该队','当前请求完成或超时后停止，已完成的题保留，已用请求不退回。')){await api('/runs/'+d.flowStop+'/stop','POST');notice('已请求停止该队');await loadBatches()}
if(d.flowRerun&&await askConfirm('按冻结配置重跑','将全量重跑该队 1,279 题，旧结果保留但不进入排名，会重新产生 API 费用。')){await api('/runs/'+d.flowRerun+'/rerun','POST');notice('已创建重跑任务');await loadBatches()}
if(d.flowStopBatch&&await askConfirm('停止整个批次','所有队伍在当前请求完成后停止，不能恢复为同一批次。')){await api('/evaluation-batches/'+d.flowStopBatch+'/stop','POST');notice('已请求停止整个批次');await loadBatches()}
if(d.png){b.disabled=true;try{await pngExport(d.png,d.chartName)}finally{b.disabled=false}}}));
$('#probe-concurrency').onclick=action(async()=>{
 const ids=[...cs.selected];if(!ids.length)throw Error('请先在“参赛名单”选择参赛队伍');
 if(!await askConfirm('检测各队可用并发','将对 '+ids.length+' 支队伍各发送最多 31 次真实请求（按 1、2、4、8、16 路逐级测试，遇到限流即停止），会产生少量 API 费用，约需 1～3 分钟。'))return;
 const button=$('#probe-concurrency'),status=$('#probe-status');button.disabled=true;let done=0;status.textContent='正在检测 0 / '+ids.length+'…';
 try{const out=await Promise.all(ids.map(async id=>{let r;try{r=await api('/competition/probe-concurrency','POST',{submission_id:id})}catch(err){r={submission_id:id,error:err.message}}status.textContent='正在检测 '+(++done)+' / '+ids.length+'…';return r}));
  cs.probe=out;renderProbe();renderLaunchSummary();status.textContent='检测完成'}
 finally{button.disabled=cs.launching||!!cs.pendingLaunch}
});
document.addEventListener('click',ev=>{const b=ev.target.closest('#probe-apply');if(!b||!cs.probePick)return;const f=$('#batch-form');f.elements.concurrency.value=String(cs.probePick);invalidate();notice('已采用 '+cs.probePick+' 套并发')});
document.addEventListener('wargame:tab',action(async ev=>{if(ev.detail==='submissions')await loadSubmissions();if(ev.detail==='batches'){await loadSubmissions();populateCalls($('#batch-form'),cs.pendingLaunch?.payload.calls);updateCallsControl($('#batch-form'),'#batch-calls-help');setLaunchState(cs.launching,cs.pendingLaunch?'启动结果尚未确认，请沿用原批次重试。':'');renderLaunchSummary()}if(ev.detail==='analytics'){await loadBatches();await loadAnalytics(true)}}));
// Progress polling is scoped to the selected batch; historical batch list refreshes
// only every 15 seconds and never triggers a duplicate /progress request.
setInterval(async()=>{
 if(state.tab!=='analytics'||!cs.batchId||!cs.batchRunning||cs.progressPolling)return;
 cs.progressPolling=true;
 try{await showBatch(cs.batchId)}catch(err){notice(err.message,true)}
 finally{cs.progressPolling=false}
},3000);
setInterval(async()=>{
 if(state.tab!=='analytics'||cs.listPolling)return;
 cs.listPolling=true;
 try{await loadBatches(true)}catch(err){notice(err.message,true)}
 finally{cs.listPolling=false}
},15000);
})();
