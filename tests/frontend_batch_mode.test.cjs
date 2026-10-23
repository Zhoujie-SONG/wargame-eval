const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const crypto=require('node:crypto');

function setup(storage=new Map()){
  const nodes=new Map(),listeners=new Map(),rosterStorage=new Map();
  const html=fs.readFileSync('app/web/index.html','utf8');
  const ids=new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(x=>x[1]));
  function $(id){if(/^#[\w-]+$/.test(id)&&!ids.has(id.slice(1)))return null;if(!nodes.has(id))nodes.set(id,{value:'',checked:false,disabled:false,innerHTML:'',textContent:'',querySelectorAll(){return []},scrollIntoView(){},setCustomValidity(v){this.validationMessage=v}});return nodes.get(id)}
  const fields={};for(const name of ['name','mode','calls','early_stop'])fields[name]=$('field:'+name);
  fields.name.value='正式比赛';fields.mode.value='self_refine';fields.calls.value='3';
  $('#batch-form').elements=fields;$('#batch-form').querySelectorAll=()=>Object.values(fields);
  const context={$,crypto,URLSearchParams,Set,Image:class{},labels:{},state:{settings:{competition:{max_calls_per_question:3}}},
    form:()=>Object.fromEntries(Object.entries(fields).filter(([,v])=>!v.disabled).map(([k,v])=>[k,v.value])),
    e:x=>String(x).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),table:(h,r)=>JSON.stringify(r),action:fn=>fn,options:()=>{},notice:()=>{},setInterval:()=>{},percent:String,
    sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    localStorage:{getItem:k=>rosterStorage.get(k)??null,setItem:(k,v)=>rosterStorage.set(k,v),removeItem:k=>rosterStorage.delete(k)},
    document:{addEventListener:(event,fn)=>listeners.set(event,fn),body:{addEventListener:()=>{}},querySelectorAll:()=>[]},api:async()=>{throw Error('Unexpected API call')}};
  vm.createContext(context);
  const app=fs.readFileSync('app/web/app.js','utf8');
  vm.runInContext(app.slice(app.indexOf('function populateCalls('),app.indexOf('function selectPrompt(')),context);
  const source=fs.readFileSync('app/web/competition.js','utf8').replace(/\}\)\(\);\s*$/,'globalThis.testAPI={batchPayload,invalidate,confirmOfficialStart,startOfficial,renderTeamSelection,loadSubmissions,restoreLatestRoster,loadAnalytics,cs};})();');
  vm.runInContext(source,context);
  return {context,fields,$,a:context.testAPI,storage,listeners};
}
const plan={fingerprint:'f'.repeat(64),teams:2,questions_per_team:1279,max_requests:7674,max_external_requests:7674,mode:'self_refine',calls:3,real_images_complete:true};
function answerDialog($,answer){const d=$('#official-start-dialog');let close;d.addEventListener=(event,fn)=>close=fn;d.showModal=()=>{d.returnValue=answer;close()}}

test('group numbering remains independent; formal form has one primary action and no test controls',()=>{
  const html=fs.readFileSync('app/web/index.html','utf8'),nav=html.match(/<nav[\s\S]*?<\/nav>/)[0],form=html.match(/<form id="batch-form"[\s\S]*?<\/form>/)[0];
  assert.deepEqual([...nav.matchAll(/data-tab="(.*?)"/g)].map(x=>x[1]),['dataset','submissions','batches','analytics','models','prompts','create','monitor']);
  assert.deepEqual([...nav.matchAll(/<button[^>]*>(\d{2})/g)].map(x=>x[1]),['01','02','03','04','01','02','03','04']);
  assert.match(html,/id="historical-report"/);
  assert.doesNotMatch(form,/batch_test|batch-test-mode|name="(?:dimension|limit|question_ids)"|save-batch|readonly/);
  assert.equal((form.match(/<button/g)||[]).length,1);assert.match(form,/开始正式测评/);assert.match(form,/value="self_refine" selected/);
});

test('V1.3 page initialization reaches dynamic calls setup with its real element IDs',async()=>{
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
