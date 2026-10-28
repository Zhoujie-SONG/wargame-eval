'use strict';
const $ = s => document.querySelector(s);
const e = v => String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={dataset:'数据集',models:'模型配置',prompts:'Prompt 与推理配置',create:'创建测评任务',monitor:'任务监控',reports:'测评报告与排行榜'};
let state={models:[],prompts:[],tab:'dataset',offset:0,total:0,selectedRun:null};
function notice(text,error=false){const n=$('#notice');n.hidden=false;n.className=error?'error':'';n.textContent=text;clearTimeout(notice.timer);notice.timer=setTimeout(()=>n.hidden=true,12000)}
async function api(url,method='GET',body){const options={method};if(body instanceof FormData)options.body=body;else if(body!==undefined){options.headers={'Content-Type':'application/json'};options.body=JSON.stringify(body)}const r=await fetch('/api'+url,options);const data=await r.json();if(!r.ok){const err=Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail||data));err.status=r.status;err.data=data;throw err}return data}
function action(fn){return async ev=>{if(ev?.type==='submit')ev.preventDefault();const b=ev?.submitter;try{if(b)b.disabled=true;await fn(ev)}catch(err){notice(err.message,true)}finally{if(b)b.disabled=false}}}
function form(id){return Object.fromEntries(new FormData($(id)))}
function percent(n){return n===null?'—':(n*100).toFixed(2)+'%'}
function table(headers,rows){return '<table><thead><tr>'+headers.map(h=>'<th>'+e(h)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+r.map(c=>'<td>'+c+'</td>').join('')+'</tr>').join('')+'</tbody></table>'}
function options(select,items,selected){select.innerHTML=items.map(x=>`<option value="${e(x.id)}">${e(x.label||x.name)}</option>`).join('');if(items.some(x=>x.id===selected))select.value=selected}
async function config(){const [models,prompts,settings]=await Promise.all([api('/models'),api('/prompts'),api('/settings')]);state.models=models;state.prompts=prompts;state.settings=settings;
 options($('#run-model'),models,$('#run-model').value);options($('#run-prompt'),prompts.map(p=>({...p,label:p.name+' · '+p.version})),$('#run-prompt').value);
 options($('#prompt-edit'),[{id:'',name:'新建'},...prompts.map(p=>({...p,label:p.name+' · '+p.version}))],$('#prompt-edit').value);
 Object.entries(settings.competition).forEach(([k,v])=>$('#limits-form').elements[k].value=v);
 populateCalls($('#run-form'));updateCallsControl($('#run-form'),'#run-calls-help');const promptCalls=$('#prompt-form').elements.default_calls;promptCalls.max=settings.competition.max_calls_per_question;if(+promptCalls.value>+promptCalls.max)promptCalls.value=promptCalls.max;
 $('#health').textContent='服务在线 · 上限 '+settings.competition.max_calls_per_question+' 次 / 题';
 $('#model-list').innerHTML=table(['名称 / 模型','类型','聚合代理','凭据','操作'],models.map(m=>[e(m.name)+'<br><span class="muted">'+e(m.model_id)+'</span>',e(m.provider),m.is_aggregator?'是':'否',m.has_key?'已安全配置':'Mock 无需凭据',`<button data-edit-model="${e(m.id)}">编辑</button><button data-test-model="${e(m.id)}">连接测试</button><button class="danger" data-delete-model="${e(m.id)}">删除</button>`]));
 $('#prompt-list').innerHTML=table(['名称','版本','推理默认值'],prompts.map(p=>[e(p.name),e(p.version),e(p.default_mode||'single_pass')+' / '+e(p.default_calls||3)+' 次']));
}
async function loadImages(){const items=await api('/images');options($('#image-select'),items.map(i=>({id:i.id,label:(i.real?'真实':'Mock')+' · '+i.id.slice(0,18)})),$('#image-select').value)}
async function stats(){const s=await api('/dataset/stats');$('#stats').innerHTML=[['独立 QA',s.total],['真实截图已关联 QA',s.real_image_questions],['待补图 QA',s.missing_image_questions],['唯一截图帧',s.unique_frames]].map(([label,n])=>`<div class="card"><span>${e(label)}</span><strong>${n}</strong></div>`).join('')+'<div class="card"><span>四维数量 · L2主 / L3主 / L1子 / L2子</span><strong>'+['L2_main','L3_main','L1_sub','L2_sub'].map(k=>s.counts[k]).join(' / ')+'</strong></div>';}
async function search(){const p=new URLSearchParams(form('#search-form'));p.set('offset',state.offset);const d=await api('/questions?'+p);state.total=d.total;$('#page-label').textContent=`${state.offset+1}–${Math.min(state.offset+20,d.total)} / ${d.total}`;$('#question-table').innerHTML=table(['题目 ID','维度','复盘 / step','图片','操作'],d.items.map(q=>[e(q.question_id),e(q.level+'_'+q.role),e(q.replay_id)+'<br>'+q.image_step,q.image?(q.image.real?'已关联真实':'Mock'):'缺图',`<button data-question="${e(q.question_id)}">查看 / 关联</button>`]));}
async function showQuestion(id){const q=await api('/questions/'+encodeURIComponent(id));const n=$('#question-detail');n.innerHTML='<h2>'+e(q.question_id)+'</h2><p>父题：'+e(q.parent_id||'无')+' · '+e(q.question_type)+'</p><pre>'+e(q.input_text)+'</pre><p>管理员 GT：'+e(q.gt_answer.join(','))+'</p>'+(q.image?`<img class="preview" src="/api/images/${e(q.image.id)}" alt="关联截图">`:'<p class="warn">尚未关联截图</p>')+'<details><summary>参考标注（仅管理员）</summary><pre>'+e(q.reference_text)+'</pre></details>';$('#map-form').elements.replay_id.value=q.replay_id;$('#map-form').elements.image_step.value=q.image_step;n.scrollIntoView({block:'nearest'});}
function populateCalls(f,preferred){const cap=state.settings.competition.max_calls_per_question,old=preferred??+f.elements.calls.value;f.elements.calls.innerHTML=Array.from({length:cap},(_,i)=>`<option value="${i+1}">${i+1} 次</option>`).join('');f.elements.calls.value=String(old>=1?Math.min(old,cap):f.elements.mode.value==='single_pass'?1:cap)}
function updateCallsControl(f,help,resetSingle=false){const single=f.elements.mode.value==='single_pass';if(single&&resetSingle)f.elements.calls.value='1';const invalid=single&&+f.elements.calls.value!==1;const message='单次推理固定 1 次调用；如需多次，请选择多轮修正或独立投票';f.elements.calls.setCustomValidity(invalid?message:'');$(help).textContent=single?message:'所选次数为每题调用上限，包含首轮作答；提前结束时实际调用可能更少。';f.elements.early_stop.disabled=f.elements.mode.value!=='self_refine';if(f.elements.early_stop.disabled)f.elements.early_stop.checked=false;if(invalid)notice(message,true)}
const STATUS_LABELS={draft:['草稿','gray'],queued:['等待中','yellow'],running:['运行中','green'],stopping:['停止中','yellow'],stopped:['已停止','yellow'],completed:['已完成','green'],suspended:['已暂停','red'],interrupted:['已中断','red'],infrastructure_error:['平台故障','red'],superseded:['已被替代','gray'],stopped_or_interrupted:['已停止或中断','yellow']};
function statusLabel(status){const hit=STATUS_LABELS[status];return hit?{text:hit[0],cls:hit[1]}:{text:String(status??''),cls:'gray'}}
function selectPrompt(){const p=state.prompts.find(p=>p.id===$('#run-prompt').value);if(p){const f=$('#run-form');f.elements.mode.value=p.default_mode||'single_pass';populateCalls(f,f.elements.mode.value==='single_pass'?1:p.default_calls);f.elements.early_stop.checked=!!p.early_stop;updateCallsControl(f,'#run-calls-help')}}
async function loadRuns(){const items=await api('/runs');$('#run-list').innerHTML=table(['任务 / 类型','状态与进度','调用 / 计划','错误 / 答错','耗时','操作'],items.map(r=>[e(r.name)+'<br><span class="tag">'+e(r.kind)+'</span><br>'+e(r.run_id.slice(0,10)),e(r.status)+`<progress value="${r.completed}" max="${r.total}"></progress>${r.completed} / ${r.total}`,r.actual_calls+' / '+r.planned_calls+(r.kind==='mock'?'<br>模拟请求':'<br>外部 API 请求'),r.error_count+' / '+r.incorrect_count,r.elapsed_seconds.toFixed(1)+'s',`<button data-run="${e(r.run_id)}">详情</button>`+(['running','queued','stopping'].includes(r.status)?`<button class="danger" data-stop="${e(r.run_id)}">安全停止</button>`:'')+(['interrupted','infrastructure_error'].includes(r.status)||r.infrastructure_errors?`<button data-rerun="${e(r.run_id)}">按冻结配置重跑</button>`:'')]));}
async function board(){const items=await api('/leaderboard?kind='+$('#board-kind').value);$('#leaderboard').innerHTML=items.length?table(['组内排名 / 比较组','队伍 / 模型','题数','L2主','L3主','L1子','L2子','综合分','模式 / 调用','报告'],items.map(r=>[r.rank+'<br>'+e(r.ranking_group||r.cohort),e(r.name)+'<br>'+e(r.model.model_id)+(r.model.is_aggregator?'<br>聚合代理':''),r.total,...['L2_main','L3_main','L1_sub','L2_sub'].map(k=>percent(r.dimensions[k].accuracy)),r.weighted_score.toFixed(3),e(r.mode)+' / '+r.actual_calls,`<button data-run="${e(r.run_id)}">查看</button>`])):'<p>此类别暂无已完成成绩。Mock 成绩请切换至“Mock 联调”。</p>';}
async function showRun(id){state.selectedRun=id;const r=await api('/runs/'+id);const node=$('#run-detail');node.hidden=false;node.innerHTML=`<div class="row"><h2>${e(r.name)} · ${e(r.status)}</h2><span class="tag">${e(r.kind)} ${r.eligible?'正式可排名':'非正式成绩 / 尚未完成'}</span><button id="close-detail" class="secondary">关闭</button></div>`+
 `<p>run_id: ${e(id)} · Prompt ${e(r.prompt_version)} · ${e(r.mode)} · 规则组 ${e(r.cohort)}</p><div class="cards"><div class="card"><span>综合分</span><strong>${r.weighted_score.toFixed(3)}</strong></div><div class="card"><span>总体准确率</span><strong>${percent(r.overall_accuracy)}</strong></div><div class="card"><span>调用 / 计划</span><strong>${r.actual_calls} / ${r.planned_calls}</strong></div><div class="card"><span>进度</span><strong>${r.completed} / ${r.total}</strong></div></div>`+
 table(['维度','正确 / 总数','准确率'],Object.entries(r.dimensions).map(([k,d])=>[e(k),d.correct+' / '+d.total,percent(d.accuracy)]))+
 `<p>耗时 ${r.elapsed_seconds.toFixed(2)}s · 外部 API ${r.external_api_calls} 次 · Mock ${r.mock_requests} 次 · Token Usage ${e(JSON.stringify(r.token_usage))}</p>`+(r.error?'<p class="warn">'+e(r.error)+'</p>':'')+
 `<div class="row">${['json','csv','html'].map(f=>`<a href="/api/runs/${e(id)}/export/${f}" download>导出 ${f.toUpperCase()}</a>`).join('　')}<label class="inline"><input id="errors-only" type="checkbox">只看答错题</label></div><div id="result-table"></div><div id="call-detail"></div>`+
 `<details><summary>主办方基础设施故障处理</summary><p>仅用于已证实的主办方故障；普通超时和模型错误计错。标记后原成绩失去正式资格，可对受影响配置按冻结快照统一重跑。</p><form id="infra-form"><label>受影响题目 ID（逗号分隔）<input name="question_ids" required></label><label>故障原因<input name="reason" minlength="5" required></label><button>登记基础设施故障</button></form></details>`;
 function results(){const rows=r.results.filter(x=>!$('#errors-only').checked||!x.correct);$('#result-table').innerHTML=table(['题目','维度','最终答案','正确','状态','调用','逐轮输出'],rows.map(x=>[e(x.question_id),e(x.dimension),e(x.answer?.join(',')||'—'),x.correct?'✓':'✗',e(x.status),x.calls,`<button data-calls="${e(x.question_id)}">推理记录</button>`]));}
 results();$('#errors-only').onchange=results;$('#close-detail').onclick=()=>{node.hidden=true;state.selectedRun=null};node.querySelectorAll('[data-calls]').forEach(()=>{});
 node.onclick=action(async ev=>{const b=ev.target.closest('[data-calls]');if(b){const qid=b.dataset.calls;$('#call-detail').innerHTML='<h2>'+e(qid)+' · 逐轮记录</h2><pre>'+e(JSON.stringify(r.calls.filter(c=>c.question_id===qid),null,2))+'</pre>';}});
 $('#infra-form').onsubmit=action(async()=>{const d=form('#infra-form');await api('/runs/'+id+'/infrastructure','POST',{question_ids:d.question_ids.split(/[\s,，]+/).filter(Boolean),reason:d.reason});notice('故障已登记。可在任务监控中按冻结配置重跑。');await showRun(id);await loadRuns()});
 node.scrollIntoView({block:'start'});
}
async function switchTab(tab){
 if(tab==='reports')tab='analytics';
 state.tab=tab;
 document.querySelectorAll('.page').forEach(n=>n.hidden=n.id!==tab);
 document.querySelectorAll('nav button').forEach(n=>n.classList.toggle('active',n.dataset.tab===tab));
 $('#title').textContent=labels[tab];$('#run-detail').hidden=true;state.selectedRun=null;
 // Keep the URL without the browser scrolling to the section's matching ID.
 if(location.hash!=='#'+tab)history.pushState(null,'','#'+tab);
 window.scrollTo({top:0,left:0,behavior:'instant'});
 if(tab==='monitor')await loadRuns();
 if(tab==='analytics')await board();
 if(tab==='dataset'){await stats();await search();await loadImages()}
 document.dispatchEvent(new CustomEvent('wargame:tab',{detail:tab}));
}
document.querySelectorAll('nav button').forEach(b=>b.onclick=action(()=>switchTab(b.dataset.tab)));
$('#bundled').onclick=action(async()=>{notice('正在导入…');const d=await api('/dataset/import-bundled','POST');notice('导入完成：'+d.total+' 题');await stats();await search()});
$('#import-local').onclick=action(async()=>{
 const button=$('#import-local'),result=$('#dataset-import-result');
 button.disabled=true;result.textContent='正在校验题库、截图与关联，请稍候…';
 try{
  const d=await api('/dataset/import-local','POST');
  result.textContent=`${d.changed?'导入完成':'数据一致，无需重复导入'}：${d.total} 道 QA，${d.mapped_frames} 个截图帧，全部题目已关联真实截图。`+(d.backup?` 更新前备份：data/raw/${d.backup}；${d.changed_inputs} 道题的输入文字有变化（含换行差异），历史任务快照保留。`:'');
  state.offset=0;$('#question-detail').innerHTML='';
  await stats();await search();await loadImages();
  document.dispatchEvent(new CustomEvent('wargame:dataset-imported'));
 }catch(err){result.textContent='导入失败：'+err.message;throw err}
 finally{button.disabled=false}
});
$('#import-form').onsubmit=action(async()=>{const d=await api('/dataset/import','POST',new FormData($('#import-form')));notice('导入完成：'+d.total+' 题');await stats();await search()});
$('#image-form').onsubmit=action(async()=>{const f=new FormData($('#image-form'));f.set('real',$('#image-form').elements.real.checked?'true':'false');const d=await api('/images','POST',f);await loadImages();$('#image-select').value=d.id;notice('上传完成，资源 ID：'+d.id+'。请填写复盘与 step 保存关联。')});
$('#map-form').onsubmit=action(async()=>{const d=form('#map-form');d.image_step=+d.image_step;await api('/images/map','POST',[d]);notice('已关联，同帧所有 QA 复用此图片');await stats();await search()});
$('#batch-map').onclick=action(async()=>{await api('/images/map','POST',JSON.parse($('#mapping-json').value));notice('批量映射已保存');await stats();await search()});
$('#search-form').onsubmit=action(async()=>{state.offset=0;await search()});$('#prev').onclick=action(async()=>{state.offset=Math.max(0,state.offset-20);await search()});$('#next').onclick=action(async()=>{if(state.offset+20<state.total){state.offset+=20;await search()}});
$('#model-reset').onclick=()=>{$('#model-form').reset();$('#model-form').elements.id.value=''};
$('#model-form').onsubmit=action(async()=>{const d=form('#model-form');const id=d.id;delete d.id;d.temperature=+d.temperature;d.max_tokens=+d.max_tokens;d.is_aggregator=d.is_aggregator==='true';await api('/models'+(id?'/'+id:''),id?'PUT':'POST',d);$('#model-form').elements.api_key.value='';notice('模型配置已安全保存');await config()});
$('#prompt-edit').onchange=()=>{const p=state.prompts.find(x=>x.id===$('#prompt-edit').value);if(p)for(const k of ['name','system','initial','review','default_mode','default_calls','early_stop']){const el=$('#prompt-form').elements[k];if(el.type==='checkbox')el.checked=!!p[k];else el.value=p[k]??(k==='default_calls'?3:k==='default_mode'?'single_pass':'')}else $('#prompt-form').reset()};
$('#prompt-form').onsubmit=action(async()=>{const d=form('#prompt-form');d.default_calls=+d.default_calls;d.early_stop=$('#prompt-form').elements.early_stop.checked;const r=await api('/prompts','POST',d);notice('新版本已保存：'+r.version);await config();$('#run-prompt').value=r.id;selectPrompt()});
$('#limits-form').onsubmit=action(async()=>{const d=form('#limits-form');Object.keys(d).forEach(k=>d[k]=+d[k]);await api('/settings','PUT',d);notice('主办方规则已保存；新任务使用新上限');await config()});
$('#run-prompt').onchange=selectPrompt;
$('#run-form').elements.mode.onchange=()=>updateCallsControl($('#run-form'),'#run-calls-help',true);
$('#run-form').elements.calls.onchange=()=>updateCallsControl($('#run-form'),'#run-calls-help');
$('#run-form').onsubmit=action(async()=>{const d=form('#run-form');d.calls=+d.calls;d.limit=d.limit?+d.limit:null;d.dimension=d.dimension||null;d.question_ids=d.question_ids.split(/[\s,，]+/).filter(Boolean);d.early_stop=$('#run-form').elements.early_stop.checked;d.official=false;const r=await api('/runs','POST',d);notice('任务已进入后台队列：'+r.run_id);await switchTab('monitor')});
$('#board-kind').onchange=action(board);$('#refresh-board').onclick=action(board);
document.body.addEventListener('click',action(async ev=>{const b=ev.target.closest('button');if(!b)return;const d=b.dataset;if(d.question)await showQuestion(d.question);if(d.run)await showRun(d.run);if(d.stop){await api('/runs/'+d.stop+'/stop','POST');notice('已请求安全停止');await loadRuns()}if(d.rerun){const r=await api('/runs/'+d.rerun+'/rerun','POST');notice('冻结配置重跑已创建：'+r.run_id);await loadRuns()}if(d.editModel){const m=state.models.find(x=>x.id===d.editModel);const f=$('#model-form');for(const k of ['id','name','provider','base_url','model_id','api_key_env','temperature','max_tokens'])f.elements[k].value=m[k]??'';f.elements.is_aggregator.value=String(m.is_aggregator);f.elements.api_key.value='';f.scrollIntoView()}if(d.testModel){b.disabled=true;try{const r=await api('/models/'+d.testModel+'/test','POST');notice('连接成功 · '+r.requests+' 次'+(r.mock?'模拟请求':' API 请求'))}finally{b.disabled=false}}if(d.deleteModel){await api('/models/'+d.deleteModel,'DELETE');notice('配置已删除，历史任务快照保留');await config()}}));
setInterval(()=>{if(state.tab==='monitor')loadRuns().catch(err=>notice(err.message,true))},2000);
action(async()=>{await config();await switchTab(labels[location.hash.slice(1)]?location.hash.slice(1):'dataset')})();
