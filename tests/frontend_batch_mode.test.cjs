const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const crypto=require('node:crypto');

function setup(){
  const nodes=new Map();
  function $(id){if(!nodes.has(id))nodes.set(id,{value:'',checked:false,disabled:false,innerHTML:'',textContent:'',replaceChildren(){this.innerHTML=''}});return nodes.get(id)}
  const fields={};for(const name of ['name','mode','calls','dimension','limit','question_ids','early_stop','batch_test'])fields[name]=$('field:'+name);
  Object.assign(fields.name,{value:'测试批次'});fields.mode.value='single_pass';fields.calls.value='1';fields.batch_test.checked=true;
  $('#batch-form').elements=fields;
  nodes.set('#batch-test-mode',fields.batch_test);
  const context={$,crypto,URLSearchParams,Set,Image:class{},labels:{},state:{settings:{competition:{max_calls_per_question:3}}},
    form:()=>Object.fromEntries(Object.entries(fields).filter(([,v])=>!v.disabled).map(([k,v])=>[k,v.value])),
    e:String,table:()=>'',action:fn=>fn,options:()=>{},notice:()=>{},setInterval:()=>{},
    document:{addEventListener:()=>{},body:{addEventListener:()=>{}},querySelectorAll:()=>[]},api:async()=>{throw Error('Unexpected API call')}};
  vm.createContext(context);
  const source=fs.readFileSync('app/web/competition.js','utf8').replace(/\}\)\(\);\s*$/,'globalThis.testAPI={batchPayload,invalidate,updateBatchMode,confirmOfficialStart,loadAnalytics,cs};})();');
  vm.runInContext(source,context);
  return {context,fields,$,a:context.testAPI};
}

test('sidebar retains nine routes in the required groups and test switch defaults on',()=>{
  const html=fs.readFileSync('app/web/index.html','utf8');
  const nav=html.match(/<nav[\s\S]*?<\/nav>/)[0];
  assert.deepEqual([...nav.matchAll(/data-tab="(.*?)"/g)].map(x=>x[1]),['dataset','submissions','batches','analytics','reports','models','prompts','create','monitor']);
  assert.match(nav,/正式比赛/);assert.match(nav,/开发者选项/);
  assert.match(html,/<input[^>]+id="batch-test-mode"[^>]+role="switch"[^>]+checked/);
});

test('test/official toggle clears scope and invalidates the saved preview without changing strategy',()=>{
  const {a,fields,$}=setup();
  fields.mode.value='majority_vote';fields.calls.value='3';fields.dimension.value='L2_main';fields.limit.value='5';fields.question_ids.value='Q1,Q2';
  assert.equal(a.batchPayload().official,false);
  a.cs.batchPreview='old preview';$('#batch-preview').innerHTML='old preview';$('#save-batch').disabled=false;
  const key=a.batchPayload().request_key;
  fields.batch_test.checked=false;a.updateBatchMode();
  const p=a.batchPayload();assert.equal(p.official,true);assert.equal(p.dimension,null);assert.equal(p.limit,null);assert.equal(p.question_ids.length,0);
  for(const name of ['dimension','limit','question_ids']){assert.equal(fields[name].disabled,true);assert.equal(fields[name].value,'')}
  assert.equal(p.mode,'majority_vote');assert.equal(p.calls,3);assert.notEqual(p.request_key,key);
  assert.equal(a.cs.batchPreview,null);assert.equal($('#batch-preview').innerHTML,'');assert.equal($('#save-batch').disabled,true);
  assert.match($('#batch-mode-label').textContent,/正式比赛/);
  fields.batch_test.checked=true;a.updateBatchMode();
  for(const name of ['dimension','limit','question_ids'])assert.equal(fields[name].disabled,false);
  assert.equal(a.batchPayload().official,false);
});

test('a pending preview cannot re-enable save after the mode changes',async()=>{
  const {context,fields,$,a}=setup();let resolve;
  context.api=()=>new Promise(r=>{resolve=r});
  const pending=$('#batch-form').onsubmit();
  fields.batch_test.checked=false;a.updateBatchMode();
  resolve({official:false});await pending;
  assert.equal(a.cs.batchPreview,null);assert.equal($('#save-batch').disabled,true);assert.equal($('#batch-preview').innerHTML,'');
  await assert.rejects($('#save-batch').onclick(),/重新预览/);
});

test('an unchanged preview enables freezing and the request remains nonofficial',async()=>{
  const {context,$,a}=setup();let payload;
  context.api=async(url,method,p)=>{payload=p;return {official:p.official,teams:1,questions_per_team:4,total_qa:4,max_requests:4,max_external_requests:0,mode:p.mode,calls:1,budget:{timeout_per_question_seconds:60}}};
  await $('#batch-form').onsubmit();
  assert.equal(payload.official,false);assert.equal($('#save-batch').disabled,false);
  assert.equal(a.cs.batchPreview,JSON.stringify(a.batchPayload()));assert.match($('#batch-preview').innerHTML,/批量测试｜非正式成绩/);
});

test('official confirmation renders frozen values and requires the confirm action',async()=>{
  const {a,$,context}=setup();const dialog=$('#official-start-dialog');let close;
  context.table=(headers,rows)=>JSON.stringify(rows);
  dialog.addEventListener=(event,fn)=>{close=fn};dialog.showModal=()=>{};
  const batch={name:'冻结正式批次',plan:{teams:2,questions_per_team:1279,max_requests:7674,mode:'self_refine',calls:3}};
  const canceled=a.confirmOfficialStart(batch);dialog.returnValue='cancel';close();assert.equal(await canceled,false);
  for(const value of ['冻结正式批次','1279','7674','self_refine','参赛队伍数量'])assert.ok($('#official-start-summary').innerHTML.includes(value));
  const confirmed=a.confirmOfficialStart(batch);dialog.returnValue='confirm';close();assert.equal(await confirmed,true);
});

test('analytics cannot display a delayed response from a previously selected batch',async()=>{
  const {context,$,a}=setup();let resolve;
  context.document.querySelectorAll=()=>[{dataset:{analysisTeam:'team-1'}}];
  $('#analytics-batch').value='old-batch';$('#analytics-top').value='10';
  context.api=()=>new Promise(r=>{resolve=r});
  const pending=a.loadAnalytics();
  $('#analytics-batch').value='new-batch';$('#competition-summary').innerHTML='new-batch results';
  resolve({official:true});await pending;
  assert.equal($('#competition-summary').innerHTML,'new-batch results');
  assert.equal($('#competition-exports').innerHTML,'');
});
