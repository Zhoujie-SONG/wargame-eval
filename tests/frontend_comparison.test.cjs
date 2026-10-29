const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

const storageKey='wargame.runComparison.selected.v1';
function row(n,extra={}){
  return {run_id:'run-'+String(n).padStart(6,'0'),name:'同名结果',label:'同名结果 · 版本 '+n+' · run-'+String(n).padStart(6,'0'),
    created_at:`2026-10-${String(n+1).padStart(2,'0')}T02:00:00+00:00`,finished_at:`2026-10-${String(n+1).padStart(2,'0')}T03:00:00+00:00`,
    batch_name:n%2?'原始批次':'',submission_version:n%2?'v'+n:'',prompt_version:'prompt-v'+n,model_id:'model-'+n,
    kind:'official',mode:'self_refine',calls_per_question:3,early_stop:false,total:1279,weighted_score:60+n,
    dimensions:Object.fromEntries(['L2_main','L3_main','L1_sub','L2_sub'].map(key=>[key,{total:100,correct:60,accuracy:.6}])),
    selectable:true,reason:'',...extra};
}
function setup(storage=new Map()){
  const nodes=new Map(),listeners=new Map();
  const $=id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',innerHTML:'',hidden:false,disabled:false,querySelectorAll:()=>[]});return nodes.get(id)};
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const context={$,e:escape,state:{tab:'dataset'},URLSearchParams,Intl,Date,
    table:(heads,rows)=>'<table><thead>'+heads.map(h=>'<th>'+escape(h)+'</th>').join('')+'</thead><tbody>'+rows.map(cells=>'<tr>'+cells.map(cell=>'<td>'+cell+'</td>').join('')+'</tr>').join('')+'</tbody></table>',
    document:{addEventListener:(name,fn)=>listeners.set(name,fn)},
    localStorage:{getItem:key=>storage.has(key)?storage.get(key):null,setItem:(key,value)=>storage.set(key,value)},
    api:async()=>{throw Error('Unexpected API call')}};
  vm.createContext(context);
  const source=fs.readFileSync('app/web/comparison.js','utf8').replace(/\}\)\(\);\s*$/,'globalThis.testAPI={cmp,loadCandidates,generateComparison,toggleSelection,selectVisible,visibleRows,dateText,palette,colorClass};})();');
  vm.runInContext(source,context);
  return {context,$,a:context.testAPI,storage,listeners};
}
async function load(t,rows){t.context.api=async url=>{assert.equal(url,'/run-comparisons/candidates');return {candidates:rows}};await t.a.loadCandidates()}
function result(rows){return {entries:rows.map((r,i)=>({...r,rank:i+1,color:['#2f6fa8','#d98224','#248267','#8661b2'][i%4]})),warnings:[],selection_count:rows.length,scope_note:'仅所选记录对比'}}

test('comparison section is independent of existing batch results and has two chart exports',()=>{
  const html=fs.readFileSync('app/web/index.html','utf8');
  assert.match(html,/<script src="\/static\/comparison.js" defer>/);
  assert.ok(html.indexOf('id="run-comparison"')>html.indexOf('id="analytics"'));
  assert.ok(html.indexOf('id="run-comparison"')<html.indexOf('id="results-view"'));
  for(const id of ['batch-progress','curve-panel','analytics-batch','competition-groups','history-panel'])assert.ok(html.includes(`id="${id}"`));
  assert.doesNotMatch(fs.readFileSync('app/web/comparison.js','utf8'),/思维准确率/);
});

test('first load selects the latest three selectable runs across batches and standalone runs',async()=>{
  const t=setup();await load(t,[row(1),row(5,{selectable:false,reason:'未完成'}),row(3),row(2),row(4)]);
  assert.deepEqual([...t.a.cmp.selected],[row(4).run_id,row(3).run_id,row(2).run_id]);
  assert.deepEqual(JSON.parse(t.storage.get(storageKey)),[row(4).run_id,row(3).run_id,row(2).run_id]);
  const table=t.$('#comparison-candidates').innerHTML;
  for(const value of ['model-2','Prompt prompt-v2','参赛版本 v3','run-000002','2026/10/03 11:00:00','62.000','未完成','独立测评'])assert.ok(table.includes(value),value);
  assert.equal(t.$('#comparison-generate').disabled,false);
});

test('saved selections are restored, stale entries removed, and new results never added automatically',async()=>{
  const storage=new Map([[storageKey,JSON.stringify([row(1).run_id,row(2).run_id,'deleted'])]]);
  const t=setup(storage);await load(t,[row(5),row(1),row(2,{selectable:false,reason:'基础设施故障'})]);
  assert.deepEqual([...t.a.cmp.selected],[row(1).run_id]);assert.match(t.$('#comparison-status').textContent,/清理 2 条失效/);
  await load(t,[row(6),row(5),row(1),row(2)]);assert.deepEqual([...t.a.cmp.selected],[row(1).run_id]);
  t.$('#comparison-clear').onclick();assert.deepEqual(JSON.parse(storage.get(storageKey)),[]);
  const reload=setup(storage);await load(reload,[row(1),row(2),row(3)]);assert.deepEqual([...reload.a.cmp.selected],[]);
});

test('search and select-visible preserve hidden choices and reject more than eight without partial selection',async()=>{
  const t=setup(new Map([[storageKey,'[]']]));await load(t,Array.from({length:10},(_,i)=>row(i+1)));
  t.a.selectVisible();assert.deepEqual([...t.a.cmp.selected],[]);assert.match(t.$('#comparison-status').textContent,/超过 8 项上限/);
  t.a.toggleSelection(row(1).run_id,true);t.$('#comparison-search').value='model-2';t.$('#comparison-search').oninput();
  assert.equal(t.a.visibleRows().length,1);t.a.selectVisible();assert.deepEqual([...t.a.cmp.selected],[row(1).run_id,row(2).run_id]);
  t.$('#comparison-search').value='no match';t.$('#comparison-search').oninput();assert.match(t.$('#comparison-candidates').innerHTML,/已选记录仍然保留/);
  t.$('#comparison-search').value='';for(let i=3;i<=8;i++)t.a.toggleSelection(row(i).run_id,true);
  t.a.toggleSelection(row(9).run_id,true);assert.equal(t.a.cmp.selected.length,8);assert.match(t.$('#comparison-status').textContent,/最多选择 8/);
});

test('no results, one result and candidate fetch failure remain actionable without enabling comparison',async()=>{
  for(const rows of [[],[row(1)]]){const t=setup();await load(t,rows);assert.equal(t.$('#comparison-generate').disabled,true);assert.match(t.$('#comparison-status').textContent,/至少需要 2 条/)}
  const t=setup(new Map([[storageKey,JSON.stringify([row(1).run_id,row(2).run_id])]]));t.context.api=async()=>{throw Error('offline')};
  await t.a.loadCandidates();assert.match(t.$('#comparison-status').textContent,/加载失败.*offline/);assert.equal(t.$('#comparison-refresh').disabled,false);
  assert.equal(t.$('#comparison-generate').disabled,true);assert.equal(t.a.cmp.initialized,false);
  await load(t,[row(1),row(2)]);assert.deepEqual([...t.a.cmp.selected],[row(1).run_id,row(2).run_id]);
});

test('only the newest candidate response is rendered after overlapping refreshes',async()=>{
  const t=setup(),resolvers=[];t.context.api=()=>new Promise(resolve=>resolvers.push(resolve));
  const first=t.a.loadCandidates(),second=t.a.loadCandidates();resolvers[1]({candidates:[row(3),row(4)]});await second;
  resolvers[0]({candidates:[row(1),row(2)]});await first;
  assert.deepEqual([...t.a.cmp.rows].map(r=>r.run_id),[row(4).run_id,row(3).run_id]);assert.equal(t.a.cmp.loading,false);
});

test('comparison includes all selected IDs in both chart URLs, backend colors, warnings and score details',async()=>{
  const t=setup();await load(t,[row(1),row(2),row(3)]);let requested;
  t.context.api=async url=>{requested=url;return {...result([row(3),row(2),row(1)]),warnings:['规则不同 <script>']}};
  await t.a.generateComparison();const html=t.$('#comparison-results').innerHTML;
  for(const r of [row(1),row(2),row(3)])assert.equal(new URLSearchParams(requested.split('?')[1]).getAll('run_id').includes(r.run_id),true);
  for(const value of ['radar.svg?','ranking.svg?','data-png=','导出 SVG','导出 PNG','固定 0–100%','comparison-color-0','comparison-color-1','63.000','60.00%','规则不同 &lt;script&gt;'])assert.ok(html.includes(value),value);
  assert.doesNotMatch(html,/\sstyle=/i);
  assert.equal(t.$('#comparison-results').hidden,false);assert.equal(t.$('#comparison-generate').disabled,false);
});

test('changing selection clears stale charts and late comparison responses cannot overwrite a new selection',async()=>{
  const t=setup();await load(t,[row(1),row(2),row(3)]);const pending=[];t.context.api=()=>new Promise(resolve=>pending.push(resolve));
  const first=t.a.generateComparison();await t.a.generateComparison();assert.equal(pending.length,1);
  t.a.toggleSelection(row(1).run_id,false);assert.equal(t.$('#comparison-results').hidden,true);assert.equal(t.$('#comparison-results').innerHTML,'');assert.match(t.$('#comparison-result-status').textContent,/选择已变更/);
  const second=t.a.generateComparison();pending[1](result([row(3),row(2)]));await second;
  const current=t.$('#comparison-results').innerHTML;pending[0](result([row(3),row(2),row(1)]));await first;
  assert.equal(t.$('#comparison-results').innerHTML,current);assert.equal(t.a.cmp.generating,false);
  t.a.toggleSelection(row(1).run_id,true);assert.equal(t.$('#comparison-results').innerHTML,'');
});

test('refresh invalidates in-flight comparisons and failed generations leave no stale exports',async()=>{
  const t=setup();await load(t,[row(1),row(2)]);let resolve;
  t.context.api=()=>new Promise(r=>resolve=r);const pending=t.a.generateComparison();
  await load(t,[row(1),row(2),row(3)]);resolve(result([row(2),row(1)]));await pending;
  assert.equal(t.$('#comparison-results').innerHTML,'');assert.equal(t.$('#comparison-results').hidden,true);
  t.context.api=async()=>{throw Error('结果暂不可用')};await t.a.generateComparison();
  assert.match(t.$('#comparison-result-status').textContent,/生成失败.*结果暂不可用/);assert.equal(t.$('#comparison-generate').disabled,false);
  t.context.api=async()=>result([row(9),row(10)]);await t.a.generateComparison();assert.match(t.$('#comparison-result-status').textContent,/返回记录与当前选择不一致/);
});

test('unsafe labels and colors are escaped and unavailable runs cannot be selected',async()=>{
  const t=setup(),unsafe=row(1,{name:'<img onerror=alert(1)>',label:'<svg onload=alert(1)>',selectable:false,reason:'<script>'});
  await load(t,[unsafe,row(2),row(3)]);t.a.toggleSelection(unsafe.run_id,true);assert.equal(t.a.cmp.selected.includes(unsafe.run_id),false);
  assert.match(t.$('#comparison-candidates').innerHTML,/&lt;img onerror/);assert.doesNotMatch(t.$('#comparison-candidates').innerHTML,/<img onerror/);
  t.context.api=async()=>{const data=result([row(2,{label:'<script>'}),row(3,{label:'<b>same</b>'})]);data.entries[0].color='red;background:url(unsafe)';return data};await t.a.generateComparison();
  assert.match(t.$('#comparison-results').innerHTML,/&lt;script&gt;/);assert.doesNotMatch(t.$('#comparison-results').innerHTML,/<script>/);
  assert.match(t.$('#comparison-results').innerHTML,/comparison-color-fallback/);assert.doesNotMatch(t.$('#comparison-results').innerHTML,/background:url/);
});

test('all eight backend colors map to external CSS classes without inline styles',()=>{
  const t=setup(),backend=fs.readFileSync('app/services/run_comparison.py','utf8'),css=fs.readFileSync('app/web/style.css','utf8');
  const backendPalette=backend.match(/COLORS = \([\s\S]*?\)/)[0].match(/#[0-9a-f]{6}/g);
  assert.deepEqual([...t.a.palette],backendPalette);
  backendPalette.forEach((color,i)=>{assert.equal(t.a.colorClass({color}),`comparison-color-${i}`);assert.ok(css.includes(`.comparison-color-${i}{--bar:${color}}`))});
  assert.equal(t.a.colorClass({color:'url(unsafe)'}),'comparison-color-fallback');
  assert.doesNotMatch(fs.readFileSync('app/web/comparison.js','utf8'),/\sstyle=/i);
});

test('storage errors do not prevent comparison, and returning to analytics preserves rendered results',async()=>{
  const t=setup();t.context.localStorage.getItem=()=>{throw Error('denied')};t.context.localStorage.setItem=()=>{throw Error('denied')};
  await load(t,[row(1),row(2)]);assert.equal(t.$('#comparison-storage-warning').hidden,false);assert.equal(t.a.cmp.selected.length,2);
  t.context.api=async()=>result([row(2),row(1)]);await t.a.generateComparison();const html=t.$('#comparison-results').innerHTML;
  t.context.api=async()=>{throw Error('Should not fetch')};t.listeners.get('wargame:tab')({detail:'analytics'});
  assert.equal(t.$('#comparison-results').innerHTML,html);
});
