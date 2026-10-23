const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const crypto=require('node:crypto');

function setup(storage=new Map()){
  const nodes=new Map(),listeners=new Map(),rosterStorage=new Map(),timers=[];
  const html=fs.readFileSync('app/web/index.html','utf8');
  const ids=new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(x=>x[1]));
  function $(id){if(/^#[\w-]+$/.test(id)&&!ids.has(id.slice(1)))return null;if(!nodes.has(id))nodes.set(id,{value:'',checked:false,disabled:false,innerHTML:'',textContent:'',querySelectorAll(){return []},scrollIntoView(){},setCustomValidity(v){this.validationMessage=v}});return nodes.get(id)}
  const fields={};for(const name of ['name','mode','calls','early_stop'])fields[name]=$('field:'+name);
  fields.name.value='正式比赛';fields.mode.value='self_refine';fields.calls.value='3';
  $('#batch-form').elements=fields;$('#batch-form').querySelectorAll=()=>Object.values(fields);
  const context={$,crypto,URLSearchParams,Set,Image:class{},labels:{},state:{settings:{competition:{max_calls_per_question:3}}},
    form:()=>Object.fromEntries(Object.entries(fields).filter(([,v])=>!v.disabled).map(([k,v])=>[k,v.value])),
    e:x=>String(x).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),table:(h,r)=>JSON.stringify(r),action:fn=>fn,options:()=>{},notice:()=>{},setInterval:(fn,ms)=>timers.push({fn,ms}),percent:String,
    sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    localStorage:{getItem:k=>rosterStorage.get(k)??null,setItem:(k,v)=>rosterStorage.set(k,v),removeItem:k=>rosterStorage.delete(k)},
    document:{addEventListener:(event,fn)=>listeners.set(event,fn),body:{addEventListener:()=>{}},querySelectorAll:()=>[]},api:async()=>{throw Error('Unexpected API call')}};
  vm.createContext(context);
  const app=fs.readFileSync('app/web/app.js','utf8');
  vm.runInContext(app.slice(app.indexOf('function populateCalls('),app.indexOf('function selectPrompt(')),context);
  const source=fs.readFileSync('app/web/competition.js','utf8').replace(/\}\)\(\);\s*$/,'globalThis.testAPI={batchPayload,invalidate,confirmOfficialStart,startOfficial,renderTeamSelection,loadSubmissions,restoreLatestRoster,loadAnalytics,cs,showBatch,renderLaunchSummary,selectionNotice,fmtDuration};})();');
  vm.runInContext(source,context);
  return {context,fields,$,a:context.testAPI,storage,listeners,timers};
}
const plan={fingerprint:'f'.repeat(64),teams:2,questions_per_team:1279,max_requests:7674,max_external_requests:7674,mode:'self_refine',calls:3,real_images_complete:true};
function answerDialog($,answer){const d=$('#official-start-dialog');let close;d.addEventListener=(event,fn)=>close=fn;d.showModal=()=>{d.returnValue=answer;close()}}

test('group numbering remains independent; formal form has one primary action and no test controls',()=>{
  const html=fs.readFileSync('app/web/index.html','utf8'),nav=html.match(/<nav[\s\S]*?<\/nav>/)[0],form=html.match(/<form id="batch-form"[\s\S]*?<\/form>/)[0];
  assert.deepEqual([...nav.matchAll(/data-tab="(.*?)"/g)].map(x=>x[1]),['dataset','submissions','batches','analytics','models','prompts','create','monitor']);
  assert.deepEqual([...nav.matchAll(/<button[^>]*>(\d{2})/g)].map(x=>x[1]),['01','02','03','04','01','02','03','04']);
  assert.match(nav,/class="flow-nav"/);
  for(const key of ['dataset','submissions','batches','analytics'])assert.match(html,new RegExp('<section id="'+key+'" class="page"'));
  assert.doesNotMatch(html,/id="flow-stepper"|id="flow-home"/);assert.match(html,/id="flow-status"/);assert.match(html,/src="\/static\/flow\.js"/);
  assert.doesNotMatch(html,/下一步/);assert.doesNotMatch(html,/id="go-(?:batches|submissions)"/);
  assert.match(html,/id="historical-report"/);
  assert.doesNotMatch(form,/batch_test|batch-test-mode|name="(?:dimension|limit|question_ids)"|save-batch|readonly/);
  assert.equal((form.match(/<button/g)||[]).length,1);assert.match(form,/开始正式测评/);assert.match(form,/value="self_refine" selected/);
});

test('V1.4 page initialization reaches dynamic calls setup with its real element IDs',async()=>{
  const {context,fields,$,listeners}=setup();
  assert.equal($('#refresh-submissions'),null);
  context.state.settings.competition.max_calls_per_question=5;
  fields.calls.value='';
  context.api=async url=>{assert.ok(['/submissions','/submissions/official-roster','/evaluation-batches'].includes(url));return url==='/submissions/official-roster'?{submission_ids:[]} : []};
  await listeners.get('wargame:tab')({detail:'batches'});
  assert.equal((fields.calls.innerHTML.match(/<option/g)||[]).length,5);
  assert.equal(fields.calls.value,'5');assert.equal(fields.calls.disabled,false);
  fields.calls.value='2';fields.calls.onchange();
  assert.equal(fields.calls.value,'2');assert.equal(fields.calls.validationMessage,'');
});

test('calls selector follows organizer cap and Single-pass refuses multiple calls explicitly',()=>{
  const {context,fields,$,a}=setup();context.state.settings.competition.max_calls_per_question=5;
  context.populateCalls($('#batch-form'));assert.equal((fields.calls.innerHTML.match(/<option/g)||[]).length,5);
  fields.mode.value='single_pass';context.updateCallsControl($('#batch-form'),'#batch-calls-help',true);
  assert.equal(fields.calls.value,'1');assert.equal(a.batchPayload().calls,1);
  fields.calls.value='5';context.updateCallsControl($('#batch-form'),'#batch-calls-help');
  assert.match(fields.calls.validationMessage,/固定 1 次/);assert.throws(()=>a.batchPayload(),/固定 1 次/);
  for(const mode of ['self_refine','majority_vote']){fields.mode.value=mode;context.updateCallsControl($('#batch-form'),'#batch-calls-help');assert.equal(fields.calls.validationMessage,'');assert.equal(a.batchPayload().calls,5)}
  const p=a.batchPayload();assert.equal(p.official,true);assert.equal(p.limit,null);assert.equal(p.dimension,null);assert.equal(p.question_ids.length,0);
});

test('Mock submissions never appear in official candidates',()=>{
  const {a,$}=setup();a.cs.submissions=[{id:'mock',provider:'mock',available:true,team_name:'Mock team',team_id:'m',model_id:'mock'},{id:'real',provider:'openai',available:true,team_name:'Real team',team_id:'r',model_id:'real',credential_provided:true}];
  a.renderTeamSelection();assert.doesNotMatch($('#batch-submissions').innerHTML,/Mock team/);assert.match($('#batch-submissions').innerHTML,/Real team/);
});

test('canceling confirmation makes only a read-only preview, without creation or launch',async()=>{
  const {context,$,a}=setup();const requests=[];context.api=async(url)=>{requests.push(url);return plan};answerDialog($,'cancel');
  await a.startOfficial();assert.deepEqual(requests,['/evaluation-batches/preview']);assert.equal(a.cs.pendingLaunch,null);assert.equal($('#start-official-batch').disabled,false);
  for(const value of ['1279','7674','全部真实模型','完整','3 次'])assert.ok($('#official-start-summary').innerHTML.includes(value));
});

test('a double click submits only one confirmation and one combined request; lost response retries the same key',async()=>{
  const {context,$,a,storage}=setup();let resolvePreview;const requests=[];
  context.api=async(url,method,payload)=>{requests.push({url,payload});if(url.endsWith('/preview'))return new Promise(r=>resolvePreview=r);throw Error('network response lost')};answerDialog($,'confirm');
  const first=a.startOfficial();await a.startOfficial();resolvePreview(plan);await assert.rejects(first,/network response lost/);
  assert.equal(requests.length,2);assert.ok(a.cs.pendingLaunch);assert.ok(storage.has('wargame.pendingOfficialLaunch'));assert.equal($('#start-official-batch').disabled,false);
  const key=requests[1].payload.request_key;
  await assert.rejects(a.startOfficial(),/network response lost/);assert.equal(requests.length,3);assert.equal(requests[2].payload.request_key,key);
  assert.equal(requests[2].url,'/evaluation-batches/start-new');
  const restored=setup(storage);assert.equal(restored.a.cs.pendingLaunch.payload.request_key,key);assert.equal(restored.fields.name.disabled,true);
});

test('successful combined start automatically opens batch monitoring and clears pending state',async()=>{
  const {context,$,a,storage}=setup();answerDialog($,'confirm');
  context.api=async(url)=>{if(url.endsWith('/preview'))return plan;if(url.endsWith('/start-new'))return {batch_id:'formal-batch',started:true};if(url==='/evaluation-batches')return [];return {batch_id:'formal-batch',name:'正式比赛',status:'running',plan:{official:true},elapsed_seconds:0,runs:[],teams:2}};
  await a.startOfficial();assert.equal(a.cs.batchId,'formal-batch');assert.equal(a.cs.pendingLaunch,null);assert.equal(storage.size,0);assert.equal($('#batch-progress').hidden,false);assert.equal($('#start-official-batch').disabled,false);
});

test('a monitoring refresh failure cannot turn a successful launch into a new chargeable request',async()=>{
  const {context,$,a}=setup();answerDialog($,'confirm');const messages=[];context.notice=message=>messages.push(message);
  const key=a.cs.requestKey;
  context.api=async(url)=>{if(url.endsWith('/preview'))return plan;if(url.endsWith('/start-new'))return {batch_id:'started-batch',started:true};throw Error('monitor unavailable')};
  await a.startOfficial();assert.equal(a.cs.batchId,'started-batch');assert.equal(a.cs.requestKey,key);
  assert.ok(messages.some(message=>message.includes('已启动；监控暂时加载失败')));
});

test('analytics discards a delayed response from a previously selected batch',async()=>{
  const {context,$,a}=setup();let resolve;
  context.document.querySelectorAll=()=>[{dataset:{analysisTeam:'team-1'}}];
  $('#analytics-batch').value='old-batch';$('#analytics-top').value='10';context.api=()=>new Promise(r=>resolve=r);
  const pending=a.loadAnalytics();$('#analytics-batch').value='new-batch';$('#competition-summary').innerHTML='new-batch results';resolve({official:true});await pending;
  assert.equal($('#competition-summary').innerHTML,'new-batch results');assert.equal($('#competition-exports').innerHTML,'');
});


test('confirmation lists exact frozen versions and highlights newer unselected versions with XSS escaping',async()=>{
 const {a,$}=setup();answerDialog($,'cancel');
 a.cs.submissions=[{id:'s1',team_id:'t1',submission_version:'v1',is_latest:false},
   {id:'s2',team_id:'t1',submission_version:'v2',is_latest:true}];
 const items=[{team_id:'t1',team_name:'<img src=x onerror=alert(1)>',
   submission_version:'v1',model_id:'<script>oops</script>',
   is_aggregator:true,prompt_version:'pr-v3',credential_source:'环境变量'}];
 await a.confirmOfficialStart({name:'official',plan:{...plan,items}});
 const html=$('#official-start-summary').innerHTML;
 assert.match(html,/v1/);assert.match(html,/pr-v3/);assert.match(html,/有更新版本 v2/);
 assert.match(html,/roster-warning/);assert.match(html,/环境变量/);
 assert.ok(html.includes('&lt;img'));assert.ok(!html.includes('<script>'));
});

test('roster is persisted by server and restore-latest explicitly replaces stale selections',async()=>{
 const {context,a}=setup();const selected=[];
 const submissions=[
   {id:'new',team_id:'t',team_name:'T',model_id:'M',submission_version:'v2',is_latest:true,provider:'openai',available:true},
   {id:'old',team_id:'t',team_name:'T',model_id:'M',submission_version:'v1',is_latest:false,latest_version:'v2',provider:'openai',available:true}
 ];
 context.api=async(url,method,payload)=>{
   if(url==='/submissions')return submissions;
   if(url==='/submissions/official-roster'&&method==='PUT'){selected.push(payload.submission_ids);return {configured:true,submission_ids:payload.submission_ids}};
   if(url==='/submissions/official-roster')return {configured:true,submission_ids:['old']};
   throw Error('unexpected '+url);
 };
 await a.loadSubmissions();assert.ok(a.cs.selected.has('old'));assert.ok(!a.cs.selected.has('new'));
 await a.restoreLatestRoster();assert.ok(a.cs.selected.has('new'));assert.ok(!a.cs.selected.has('old'));
 assert.equal(selected.length,1);assert.deepEqual([...selected[0]],['new']);
});


test('polling checks selected batch every 3 seconds and historical batch list every 15 seconds',async()=>{
  const {context,a,timers}=setup();
  context.state.tab='analytics';a.cs.batchId='active-batch';a.cs.batchRunning=true;
  const requests=[];
  context.api=async(url)=>{
    requests.push(url);
    if(url==='/evaluation-batches')return [];
    if(url==='/evaluation-batches/active-batch/progress')return {
      batch_id:'active-batch',name:'active',status:'running',plan:{official:true},
      teams:1,queued:0,running:1,finished:0,failed_or_interrupted:0,
      actual_calls:1,elapsed_seconds:3,completed:1,total:1279,error_count:0,runs:[]
    };
    throw Error('unexpected '+url);
  };
  assert.deepEqual(timers.map(t=>t.ms),[3000,15000]);
  await timers[0].fn();
  assert.deepEqual(requests,['/evaluation-batches/active-batch/progress']);
  await timers[1].fn();
  assert.deepEqual(requests,['/evaluation-batches/active-batch/progress','/evaluation-batches']);
});


test('status labels are Chinese and unknown values fall back to the raw text',()=>{
  const {context}=setup();
  const label=v=>context.statusLabel(v);
  assert.deepEqual({...label('running')},{text:'运行中',cls:'green'});
  assert.deepEqual({...label('infrastructure_error')},{text:'平台故障',cls:'red'});
  assert.deepEqual({...label('interrupted')},{text:'已中断',cls:'red'});
  assert.deepEqual({...label('queued')},{text:'等待中',cls:'yellow'});
  assert.deepEqual({...label('weird')},{text:'weird',cls:'gray'});
});

test('roster shows one row per team with a version list; mock and unavailable entries are hidden',()=>{
  const {a,$}=setup();
  a.cs.selected=new Set(['a1']);
  a.cs.submissions=[
    {id:'a2',team_id:'A',team_name:'<b>Alpha</b>',submission_version:'v2',is_latest:true,latest_version:'v2',provider:'openai',available:true,model_id:'M',credential_provided:true,credential_source:'API Key'},
    {id:'a1',team_id:'A',team_name:'<b>Alpha</b>',submission_version:'v1',is_latest:false,latest_version:'v2',provider:'openai',available:true,model_id:'M',credential_provided:true,credential_source:'API Key'},
    {id:'b1',team_id:'B',team_name:'Beta',submission_version:'v1',is_latest:true,latest_version:'v1',provider:'openai',available:true,model_id:'N',credential_provided:false,credential_source:'未提供'},
    {id:'m1',team_id:'M',team_name:'MockTeam',submission_version:'v1',is_latest:true,provider:'mock',available:true,model_id:'mock'},
    {id:'x1',team_id:'X',team_name:'Broken',submission_version:'v1',is_latest:true,provider:'openai',available:false,model_id:'Z'}];
  a.renderTeamSelection();
  const rows=JSON.parse($('#batch-submissions').innerHTML);
  assert.equal(rows.length,2);                                   // Alpha (2 versions → 1 row) and Beta
  const [alpha,beta]=rows;
  assert.match(alpha[1],/data-team-version="A"/);assert.match(alpha[1],/<option value="a1" selected>v1<\/option>/);
  assert.match(alpha[1],/<option value="a2">v2（最新）<\/option>/);assert.match(alpha[1],/<option value="">不参赛<\/option>/);
  assert.match(alpha[5],/非最新版本/);assert.match(alpha[5],/有更新版本 v2/);
  assert.match(beta[1],/data-team-version="B"/);assert.match(beta[5],/未参赛/);assert.match(beta[4],/未提供/);
  const html=$('#batch-submissions').innerHTML;
  assert.ok(!html.includes('MockTeam')&&!html.includes('Broken')&&!html.includes('<b>'));   // escaped / filtered
  assert.equal($('#submission-selection-status').textContent,'已选择 1 支队伍（共导入 2 支）');
});

test('choosing a version saves the roster, replaces the same team and "不参赛" removes it; failures roll back',async()=>{
  const {context,a,listeners}=setup();const saved=[];
  a.cs.submissions=[{id:'a1',team_id:'A',provider:'openai',available:true,submission_version:'v1',team_name:'A'},
                    {id:'a2',team_id:'A',provider:'openai',available:true,submission_version:'v2',team_name:'A',is_latest:true}];
  a.cs.selected=new Set(['a1']);
  context.api=async(url,method,payload)=>{if(method==='PUT'){saved.push([...payload.submission_ids]);return {}}throw Error('unexpected '+url)};
  const change=listeners.get('change');
  await change({target:{dataset:{teamVersion:'A'},value:'a2'}});
  assert.deepEqual(saved,[['a2']]);assert.deepEqual([...a.cs.selected],['a2']);
  await change({target:{dataset:{teamVersion:'A'},value:''}});
  assert.deepEqual(saved[1],[]);assert.equal(a.cs.selected.size,0);
  context.api=async()=>{throw Error('offline')};a.cs.selected=new Set(['a1']);
  await assert.rejects(change({target:{dataset:{teamVersion:'A'},value:'a2'}}),/offline/);
  assert.deepEqual([...a.cs.selected],['a1']);                       // rolled back
});

test('launch summary follows team count and strategy',()=>{
  const {a,fields,$}=setup();
  a.cs.selected=new Set(['x','y']);fields.mode.value='self_refine';fields.calls.value='3';a.renderLaunchSummary();
  assert.ok($('#launch-summary').innerHTML.includes('2 × 1,279 × 3 = 7,674'));
  fields.mode.value='single_pass';fields.calls.value='3';a.renderLaunchSummary();   // Single-pass always counts one call
  assert.ok($('#launch-summary').innerHTML.includes('2 × 1,279 × 1 = 2,558'));
  a.cs.selected=new Set();a.renderLaunchSummary();assert.ok($('#launch-summary').innerHTML.includes('—'));
});

test('import notices tell a switched team apart from a newly added one',()=>{
  const {a}=setup();
  assert.equal(a.selectionNotice([{team_name:'A',submission_version:'v2',previous_version:'v1'},{team_name:'B',submission_version:'v1',previous_version:null}]),
    '队伍 A 已从 v1 切换到 v2；队伍 B 的 v1 已加入参赛名单');
  assert.equal(a.selectionNotice([]),'');
});

test('monitor page: red teams first, error breakdown, remaining time, results hidden only while running',async()=>{
  const {context,a,$}=setup();
  const run=(name,status,done,extra={})=>({run_id:name,team_id:name,name,submission_version:'v1',model:{model_id:'M'},status,completed:done,total:1279,error_count:0,infrastructure_errors:0,...extra});
  const batch=(status,runs,extra={})=>({batch_id:'b',name:'正式',status,plan:{official:true,max_requests:7674},teams:runs.length,completed:600,total:2558,actual_calls:1500,elapsed_seconds:7200,error_count:0,runs,...extra});
  context.api=async()=>batch('running',[run('Done','completed',1279),run('Run','running',500,{error_count:3,error_breakdown:{api_error:2,timeout:1,invalid_output:0,infrastructure_error:0}}),
                                          run('Wait','queued',0),run('Bad','interrupted',200)]);
  await a.showBatch('b');
  const html=$('#batch-progress').innerHTML;
  const at=n=>html.indexOf('"'+n+' / v1');
  assert.ok(at('Bad')<at('Run')&&at('Run')<at('Wait')&&at('Wait')<at('Done'));          // red → running → queued → completed
  assert.match(html,/3（接口 2 \/ 超时 1）/);assert.match(html,/预计剩余 6 小时 32 分/);        // (2558-600) × 7200 ÷ 600 = 23,496 s
  assert.equal($('#results-view').hidden,true);assert.equal(a.cs.batchRunning,true);
  assert.match($('#exception-actions').innerHTML,/data-flow-stop-batch/);assert.match($('#exception-actions').innerHTML,/data-flow-rerun=\\?"Bad/);
  assert.match($('#exception-actions').innerHTML,/data-flow-stop=\\?"Run/);assert.equal($('#exception-panel').open,true);
  context.api=async()=>batch('running',[run('Run','running',3)],{completed:3,total:2558,elapsed_seconds:60});
  await a.showBatch('b');assert.match($('#batch-progress').innerHTML,/预计剩余 计算中/);   // fewer than 20 done
  context.api=async()=>batch('completed',[run('Done','completed',1279)],{completed:2558});
  await a.showBatch('b');
  assert.equal($('#results-view').hidden,false);assert.equal(a.cs.batchRunning,false);assert.doesNotMatch($('#batch-progress').innerHTML,/预计剩余/);
});

test('durations are shown in hours and minutes',()=>{
  const {a}=setup();
  assert.equal(a.fmtDuration(20),'不足 1 分钟');assert.equal(a.fmtDuration(600),'10 分钟');assert.equal(a.fmtDuration(12000),'3 小时 20 分');
});
