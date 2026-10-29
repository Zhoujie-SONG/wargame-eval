const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

const app=fs.readFileSync('app/web/app.js','utf8');
const html=fs.readFileSync('app/web/index.html','utf8');
const saved={qa_path:'G:\\题库\\qa_all.json',images_dir:'G:\\截图'};
const imported={total:1279,mapped_frames:240,unique_images:240,counts:{L2_main:179,L3_main:61,L1_sub:757,L2_sub:282},changed:true};
const event={preventDefault(){}};
function setup(){
  const nodes=new Map(),listeners=new Map(),events=[],refreshes=[],notices=[];
  const $=id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',innerHTML:'',disabled:false,attributes:{},setAttribute(k,v){this.attributes[k]=v},removeAttribute(k){delete this.attributes[k]}});return nodes.get(id)};
  const fields={qa_path:$('qa_path'),images_dir:$('images_dir')};
  const form=$('#local-import-form');form.elements=fields;form.querySelectorAll=()=>[...Object.values(fields),$('#local-import-submit')];
  const buttons=['workspace','manual','zip','local-path'].map(mode=>({dataset:{importMode:mode},selected:mode==='workspace',classList:{toggle(_,value){buttons.find(b=>b.dataset.importMode===mode).selected=value}},setAttribute(){}}));
  const panes=buttons.map(b=>({dataset:{importPane:b.dataset.importMode},hidden:b.dataset.importMode!=='workspace'}));
  const context={$,state:{offset:20},form:()=>Object.fromEntries(Object.entries(fields).map(([key,node])=>[key,node.value])),
    notice:(text,error)=>notices.push({text,error}),workspaceCheck:async()=>{},
    stats:async()=>refreshes.push('stats'),search:async()=>refreshes.push('search'),loadImages:async()=>refreshes.push('images'),
    document:{querySelectorAll:s=>s==='[data-import-mode]'?buttons:panes,addEventListener:(name,fn)=>listeners.set(name,fn),dispatchEvent:ev=>events.push(ev.type)},
    CustomEvent:class{constructor(type){this.type=type}},api:async()=>{throw Error('Unexpected API call')}};
  vm.createContext(context);
  vm.runInContext(app.slice(app.indexOf('function setImportMode('),app.indexOf('async function workspaceCheck(')),context);
  vm.runInContext(app.slice(app.indexOf('const localImportState='),app.indexOf("$('#import-manual').onsubmit=")),context);
  return {context,$,fields,buttons,panes,listeners,events,refreshes,notices};
}

test('four import modes coexist with the upstream concurrency and monitoring interface',()=>{
  assert.deepEqual([...html.matchAll(/data-import-mode="([^"]+)"/g)].map(m=>m[1]),['workspace','manual','zip','local-path']);
  for(const id of ['import-local','import-manual','import-form','local-import-form','probe-concurrency','probe-result','curve-panel','batch-progress'])assert.ok(html.includes(`id="${id}"`));
  assert.match(html,/name="concurrency"/);
  const workspace=app.slice(app.indexOf("$('#import-local').onclick="),app.indexOf('function importSummary('));
  assert.match(workspace,/api\('\/dataset\/import-local','POST'\)/);
});

test('first dataset visit loads saved paths once and subsequent visits preserve the selected mode',async()=>{
  const t=setup();let requests=0;t.context.api=async url=>{requests++;assert.equal(url,'/dataset/local-source');return saved};
  t.listeners.get('wargame:tab')({detail:'dataset'});await t.context.loadLocalSource();
  assert.equal(t.fields.qa_path.value,saved.qa_path);assert.equal(t.fields.images_dir.value,saved.images_dir);
  assert.equal(t.buttons.find(b=>b.selected).dataset.importMode,'local-path');
  t.context.setImportMode('manual');t.listeners.get('wargame:tab')({detail:'dataset'});await t.context.loadLocalSource();
  assert.equal(requests,1);assert.equal(t.buttons.find(b=>b.selected).dataset.importMode,'manual');
});

test('a late default-path response cannot overwrite typed paths or a chosen import mode',async()=>{
  const t=setup();let resolve;t.context.api=()=>new Promise(r=>resolve=r);
  const pending=t.context.loadLocalSource();t.fields.qa_path.value='D:\\custom.json';t.fields.images_dir.value='D:\\images';t.context.setImportMode('zip');
  resolve(saved);await pending;
  assert.equal(t.fields.qa_path.value,'D:\\custom.json');assert.equal(t.fields.images_dir.value,'D:\\images');
  assert.equal(t.buttons.find(b=>b.selected).dataset.importMode,'zip');
});

test('incomplete saved paths retain the workspace default and a source-read failure permits manual entry',async()=>{
  const t=setup();t.context.api=async()=>({qa_path:saved.qa_path,images_dir:''});await t.context.loadLocalSource();
  assert.equal(t.buttons.find(b=>b.selected).dataset.importMode,'workspace');
  const failed=setup();failed.context.api=async()=>{throw Error('source unavailable')};await failed.context.loadLocalSource();
  assert.match(failed.$('#local-source-status').textContent,/默认路径读取失败.*source unavailable/);
  assert.equal(failed.$('#local-import-submit').disabled,false);
});

test('path import sends both trimmed paths once, refreshes the dataset and emits readiness event',async()=>{
  const t=setup();t.fields.qa_path.value=' '+saved.qa_path+' ';t.fields.images_dir.value=' '+saved.images_dir+' ';
  let resolve,requests=0;t.context.api=async(url,method,body)=>{requests++;assert.equal(url,'/dataset/import-local');assert.equal(method,'POST');assert.deepEqual(JSON.parse(JSON.stringify(body)),saved);return new Promise(r=>resolve=r)};
  const pending=t.context.importLocalDataset(event);await t.context.importLocalDataset(event);
  assert.equal(requests,1);assert.equal(t.$('#local-import-submit').disabled,true);assert.equal(t.fields.qa_path.disabled,true);assert.equal(t.$('#local-import-form').attributes['aria-busy'],'true');
  resolve(imported);await pending;
  assert.equal(t.$('#local-import-submit').disabled,false);assert.equal(t.fields.qa_path.disabled,false);
  assert.equal(t.context.state.offset,0);assert.deepEqual(t.refreshes,['stats','search','images']);assert.deepEqual(t.events,['wargame:dataset-imported']);
  assert.match(t.$('#dataset-import-result').textContent,/1279 道 QA，240 个截图帧，240 张唯一图片/);
  assert.match(t.$('#dataset-import-result').textContent,/179 \/ 61 \/ 757 \/ 282/);
});

test('empty paths and rejected imports do not refresh or announce a dataset change',async()=>{
  const t=setup();await t.context.importLocalDataset(event);assert.match(t.$('#dataset-import-result').textContent,/请填写/);
  t.fields.qa_path.value=saved.qa_path;t.fields.images_dir.value=saved.images_dir;t.context.api=async()=>{throw Error('different dataset')};
  await t.context.importLocalDataset(event);assert.match(t.$('#dataset-import-result').textContent,/导入失败.*different dataset/);
  assert.equal(t.$('#local-import-submit').disabled,false);assert.deepEqual(t.events,[]);assert.deepEqual(t.refreshes,[]);
});

test('repeat import is reported as ready and a refresh failure does not misreport a failed import',async()=>{
  const t=setup();t.fields.qa_path.value=saved.qa_path;t.fields.images_dir.value=saved.images_dir;
  t.context.api=async()=>({...imported,already_imported:true,changed:false});await t.context.importLocalDataset(event);
  assert.match(t.$('#dataset-import-result').textContent,/相同数据已导入/);
  t.context.stats=async()=>{throw Error('stats unavailable')};await t.context.importLocalDataset(event);
  assert.match(t.$('#dataset-import-result').textContent,/数据已导入，但页面统计刷新失败.*stats unavailable/);
  assert.equal(t.$('#local-import-submit').disabled,false);assert.equal(t.events.length,2);
});
