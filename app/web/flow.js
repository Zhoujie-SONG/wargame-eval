'use strict';
// Sidebar readiness, per-page status line and button gating for the four official pages.
// All state comes from GET /api/competition/status; nothing here decides readiness.
(()=>{
const FLOW=[['dataset','数据集'],['submissions','参赛名单'],['batches','设置并启动'],['analytics','监控与结果']];
const ICON={done:'✓',warning:'!',blocked:'✗',todo:'○',running:'●'};
const STATE_TEXT={done:'已完成',warning:'需确认',blocked:'未就绪',todo:'未开始',running:'进行中'};
const CHIP={ok:'✓',info:'i',warn:'!',bad:'✗'};
const hadHash=!!location.hash;
let flow=null,tab='dataset',timer=null,jumped=false;
const isFlow=t=>FLOW.some(([k])=>k===t);
const step=key=>flow&&flow.steps.find(s=>s.key===key);

function tip(s){return STATE_TEXT[s.state]+'：'+s.message+(s.missing.length?'\n'+s.missing.join('\n'):'')}

function renderNav(){
  FLOW.forEach(([key])=>{
    const button=$('.flow-nav [data-tab="'+key+'"]');
    const s=step(key)||{state:'todo',message:'',missing:[]};
    button.className='state-'+s.state+(tab===key?' active':'');
    button.title=tip(s);
    button.querySelector('.dot').textContent=ICON[s.state];
    if(tab===key)button.setAttribute('aria-current','page');
    else button.removeAttribute('aria-current');
  });
}

function renderStatus(){
  const box=$('#flow-status'),s=step(tab);
  if(!s){box.innerHTML='';return}
  box.className='status-line state-'+s.state;
  // The headline already says the first open issue; list only the remaining checks as chips.
  const chips=s.checks.filter(c=>!(s.missing.length&&c.text===s.missing[0]));
  box.innerHTML='<span class="dot">'+ICON[s.state]+'</span><div class="body"><strong>'+e(s.message)+'</strong>'
    +(chips.length?'<div class="chips">'+chips.map(c=>'<span class="chip chip-'+c.level+'">'+CHIP[c.level]+' '+e(c.text)+'</span>').join('')+'</div>':'')+'</div>';
}

// The only gated control: "开始正式测评" (blocked, or frozen while a batch is running).
function renderGate(){
  const s=step('batches'),btn=$('#start-official-batch'),why=$('#start-gate-reason');
  if(!s||!btn)return;
  const gated=s.state==='blocked'||s.state==='running';
  if(gated){btn.disabled=true;btn.dataset.flowGated='1'}
  else if(btn.dataset.flowGated==='1'&&btn.textContent!=='正在处理…'){btn.disabled=false;delete btn.dataset.flowGated}
  if(why)why.textContent=gated?(s.state==='running'?'比赛进行中，设置已冻结':(s.missing[0]||'')):'';
}

function render(){
  const formal=isFlow(tab);
  $('#flow-status').hidden=!formal;
  renderNav();
  if(!formal||!flow)return;
  renderStatus();renderGate();
}

async function refresh(){
  try{flow=await api('/competition/status')}catch(_){return}
  // After a reload during a running batch, open the monitor (only when no page was asked for).
  if(!jumped){jumped=true;if(flow.locked&&!hadHash&&tab==='dataset'&&typeof switchTab==='function'){await switchTab('analytics');return}}
  render();
}

let pending=null;
function refreshSoon(){clearTimeout(pending);pending=setTimeout(()=>{if(isFlow(tab))refresh()},800)}

document.addEventListener('wargame:tab',ev=>{
  tab=ev.detail;
  render();
  if(isFlow(tab)){refresh();if(!timer)timer=setInterval(()=>{if(isFlow(tab))refresh()},10000)}
});
// Anything the organizer does may change a step; re-read the server state shortly after.
document.addEventListener('click',ev=>{
  if(ev.target.closest('button'))refreshSoon();
});
document.addEventListener('change',refreshSoon);
document.addEventListener('wargame:dataset-imported',refresh);
})();
