'use strict';
(()=>{
Object.assign(labels,{submissions:'参赛名单',batches:'设置并启动',analytics:'监控与结果'});
const cs={submissions:[],preview:null,requestKey:crypto.randomUUID(),selected:new Set(),batchId:null,revision:0,analysisRevision:0,launching:false,pendingLaunch:null};
const dims=['L2_main','L3_main','L1_sub','L2_sub'];
const charts={ranking:'综合分排行榜',dimensions:'四维准确率对比',radar:'能力雷达图',efficiency:'推理效率分析'};
cs.progressRevision=0;cs.progressPolling=false;cs.listPolling=false;cs.batchRunning=false;
const fmt=n=>Number(n||0).toLocaleString('en-US');
function badge(status){const l=statusLabel(status);return '<span class="status status-'+l.cls+'">'+e(l.text)+'</span>'}
function fmtDuration(sec){const m=Math.round((sec||0)/60);if(m<1)return '不足 1 分钟';const h=Math.floor(m/60);return h?h+' 小时 '+(m%60)+' 分':m+' 分钟'}
const MODE_NAMES={single_pass:'Single-pass · 单次推理',self_refine:'Self-refine · 多轮检查修正',majority_vote:'Majority-vote · 独立采样投票'};
function renderLaunchSummary(){const f=$('#batch-form');if(!f||!f.elements)return;const mode=f.elements.mode.value,calls=mode==='single_pass'?1:(+f.elements.calls.value||1),teams=cs.selected.size;
 const rows=[['参赛队伍',teams+' 支（在“参赛名单”调整）'],['每队题数','1,279 道 QA'],['推理策略',MODE_NAMES[mode]||mode],['每题最大调用',calls+' 次'],['最大 API 请求数',teams?fmt(teams)+' × 1,279 × '+calls+' = '+fmt(teams*1279*calls):'—']];
 $('#launch-summary').innerHTML='<dl class="kv">'+rows.map(([k,v])=>'<div><dt>'+e(k)+'</dt><dd>'+e(v)+'</dd></div>').join('')+'</dl>'}
function askConfirm(title,text){const dialog=$('#confirm-dialog');$('#confirm-title').textContent=title;$('#confirm-text').textContent=text;
 return new Promise(resolve=>{dialog.addEventListener('close',()=>resolve(dialog.returnValue==='confirm'),{once:true});dialog.returnValue='cancel';dialog.showModal()})}
function selectionNotice(list){return (list||[]).map(v=>v.previous_version?'队伍 '+v.team_name+' 已从 '+v.previous_version+' 切换到 '+v.submission_version:'队伍 '+v.team_name+' 的 '+v.submission_version+' 已加入参赛名单').join('；')}
function invalidate(){renderLaunchSummary();if(cs.launching||cs.pendingLaunch)return;cs.revision++;cs.requestKey=crypto.randomUUID();$('#batch-launch-status').textContent=''}
function modeLabel(official){return official?'正式比赛｜全量 1,279 道 QA':'历史测试｜非正式成绩'}
function setLaunchState(busy,message=''){cs.launching=busy;const locked=busy||!!cs.pendingLaunch;for(const control of $('#batch-form').querySelectorAll('input,select'))control.disabled=locked;for(const control of document.querySelectorAll('[data-team-version],#reset-roster-latest,#clear-teams,#team-filter'))control.disabled=locked;$('#start-official-batch').disabled=busy;$('#start-official-batch').textContent=busy?'正在处理…':cs.pendingLaunch?'重试 / 查看本批次启动结果':'开始正式测评';$('#batch-launch-status').textContent=message;if(!locked)updateCallsControl($('#batch-form'),'#batch-calls-help')}
async function confirmOfficialStart(batch){
 const dialog=$('#official-start-dialog'),p=batch.plan;
 const items=p.items||[];
 const latest=new Map();
 for(const s of cs.submissions)if(s.is_latest||!latest.has(s.team_id))latest.set(s.team_id,s);
 const outdated=items.filter(item=>{const newer=latest.get(item.team_id);return newer&&newer.submission_version!==item.submission_version});
 const summary=table(['确认项目','本次正式比赛配置'],[
  ['比赛名称',e(batch.name)],['参赛队伍',p.teams+' 支'],['每队 QA',p.questions_per_team+' 道'],
  ['推理模式',e(p.mode)],['每题最大调用',p.calls+' 次'],
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
function batchPayload(){const f=$('#batch-form');const mode=f.elements.mode.value,calls=+f.elements.calls.value;if(mode==='single_pass'&&calls!==1)throw Error('单次推理固定 1 次调用；如需多次，请选择多轮修正或独立投票');if(!Number.isInteger(calls)||calls<1||calls>state.settings.competition.max_calls_per_question)throw Error('调用次数超出主办方上限');return {request_key:cs.requestKey,name:f.elements.name.value,submission_ids:[...cs.selected],mode,calls,early_stop:mode==='self_refine'&&f.elements.early_stop.checked,official:true,dimension:null,limit:null,question_ids:[]}}
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
 panel.hidden=false;if(runs.some(r=>RED_STATES.includes(r.status)))panel.open=true}
async function showBatch(id){cs.batchId=id;const rev=++cs.progressRevision;const b=await api('/evaluation-batches/'+id+'/progress');if(cs.batchId!==id||rev!==cs.progressRevision)return;
 const node=$('#batch-progress'),running=b.status==='running';cs.batchRunning=running;node.hidden=false;
 const pct=b.total?b.completed/b.total*100:0,planned=b.plan&&b.plan.max_requests;
 const remain=!running?'':b.completed>=20?'预计剩余 '+fmtDuration((b.total-b.completed)*b.elapsed_seconds/b.completed)+' · ':'预计剩余 计算中 · ';
 const runs=[...(b.runs||[])].sort((x,y)=>runRank(x)-runRank(y)||String(x.team_id).localeCompare(String(y.team_id)));
 node.innerHTML='<h2>'+e(b.name)+' '+badge(b.status)+'</h2><p class="tag">'+e(modeLabel(b.plan.official))+'</p>'
  +'<div class="overall"><progress max="'+(b.total||1)+'" value="'+(b.completed||0)+'"></progress><span>'+pct.toFixed(1)+'% · '+remain+'请求 '+fmt(b.actual_calls)+(planned?' / '+fmt(planned):'')+' · 已用 '+fmtDuration(b.elapsed_seconds)+'</span></div>'
  +table(['队伍 / 版本','模型','进度','状态','错误','操作'],runs.map(r=>[e(r.name)+' / '+e(r.submission_version),e(r.model.model_id),
     '<progress max="'+(r.total||1)+'" value="'+r.completed+'"></progress>'+fmt(r.completed)+' / '+fmt(r.total),badge(r.status),e(errorText(r)),
     '<button data-run="'+e(r.run_id)+'">逐题详情</button>']));
 renderExceptions(b,runs);
 const results=$('#results-view');if(results)results.hidden=running}
function analysisQuery(){const p=new URLSearchParams({top:$('#analytics-top').value});document.querySelectorAll('[data-analysis-team]:checked').forEach(n=>p.append('team_id',n.dataset.analysisTeam));return p}
async function loadAnalytics(rebuild=false){const id=$('#analytics-batch').value,revision=++cs.analysisRevision;if(!id){$('#competition-groups').innerHTML='<p>请先创建测评批次。</p>';return}if(rebuild){const batch=await api('/evaluation-batches/'+id);if(revision!==cs.analysisRevision||id!==$('#analytics-batch').value)return;$('#analytics-teams').innerHTML='<p>勾选队伍比较；雷达图展示所选范围中排名前两队。</p>'+batch.runs.map(r=>`<label class="inline"><input type="checkbox" data-analysis-team="${e(r.team_id)}" checked>${e(r.name)}</label>`).join('')}
 const checked=[...document.querySelectorAll('[data-analysis-team]:checked')];if(!checked.length){$('#competition-groups').textContent='请至少选择一支队伍';$('#competition-exports').innerHTML='';return}
 const params=analysisQuery(),data=await api('/evaluation-batches/'+id+'/analytics?'+params);if(revision!==cs.analysisRevision||id!==$('#analytics-batch').value)return;const exportParams=new URLSearchParams(params);exportParams.set('audience',$('#analytics-audience').value);$('#competition-exports').innerHTML=['html','csv','xlsx','json'].map(f=>`<a download href="/api/evaluation-batches/${e(id)}/export?${e(exportParams.toString())}&amp;format=${f}">导出 ${f==='html'?'综合 HTML 报告':f.toUpperCase()}</a>`).join('　')+`　<a target="_blank" rel="noopener" href="/api/evaluation-batches/${e(id)}/export?${e(exportParams.toString())}&amp;format=html&amp;download=false">打开报告预览</a>`;
 $('#competition-summary').innerHTML='<p class="tag">'+e(modeLabel(data.official))+'</p><div class="cards">'+[['整批队伍',data.teams],['完成队伍',data.finished],['已完成 QA',data.qa_completed+' / '+data.qa_total],['实际请求',data.actual_calls]].map(([l,v])=>`<div class="card"><span>${e(l)}</span><strong>${e(v)}</strong></div>`).join('')+'</div><p>'+e(data.scope_note)+'</p>';
 $('#competition-groups').innerHTML=data.groups.map(g=>'<div class="panel"><h2>'+e(g.kind+' · '+g.mode)+' <span class="tag">'+modeLabel(g.kind==='official')+'</span></h2><p>比较组 '+e(g.id)+' · 每题最多 '+g.calls+' 次 · 提前结束 '+g.early_stop+'</p>'+table(['排名','队伍','模型','L2主','L3主','L1子','L2子','综合分','平均秒/题','详情'],g.entries.map(r=>[r.rank,e(r.name),e(r.model.model_id),...dims.map(k=>percent(r.dimensions[k].accuracy)),r.weighted_score.toFixed(3),r.mean_question_seconds===null?'—':r.mean_question_seconds.toFixed(4),`<button data-run="${e(r.run_id)}">查看</button>`]))+Object.entries(charts).map(([chart,title])=>{const q=new URLSearchParams(params);q.set('group',g.id);const src='/api/evaluation-batches/'+id+'/charts/'+chart+'.svg?'+q;return `<div class="chart-panel"><h3>${e(title)}</h3><div class="row"><a download href="${e(src)}">导出 SVG</a><button data-png="${e(src)}" data-chart-name="${e(chart)}">导出 PNG</button></div><img class="competition-chart" src="${e(src)}" alt="${e(title+' · '+g.kind)}"></div>`}).join('')+'</div>').join('')+(data.groups.length?'':'<div class="panel">暂无完整可排名成绩。未完成队伍不进入最终排名。</div>')+(data.excluded.length?'<div class="panel"><h3>未进入最终排名</h3>'+data.excluded.map(x=>'<p>'+e(x.name+' · '+x.status+' · '+x.reason)+'</p>').join('')+'</div>':'');}
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
