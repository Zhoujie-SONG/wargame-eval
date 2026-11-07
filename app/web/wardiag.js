(() => {
  'use strict';
  const mount = document.getElementById('wardiag-content');
  if (!mount) return;
  const API = '/api/wardiag';
  const taskNames = {
    comprehensive_decision: '综合策略生成', situation_understanding: '可行动作分析',
    interaction_prediction: '行动结果推演', value_estimation: '局面价值评估',
    equilibrium_selection: '博弈策略计算'
  };
  const state = {catalog: null, groups: [], group: null, task: '', loaded: false,
    loading: null, request: 0, controller: null, cache: new Map(), sections: []};
  const array = value => Array.isArray(value) ? value : [];
  const count = value => Number.isInteger(value) && value >= 0 ? value.toLocaleString('zh-CN') : '—';
  const node = (tag, cls = '', value) => {
    const item = document.createElement(tag);
    if (cls) item.className = cls;
    if (value !== undefined) item.textContent = String(value);
    return item;
  };

  mount.innerHTML = `
    <div class="panel wardiag-intro">
      <div class="wardiag-heading-row"><span class="tag">WarDiag · 诊断式评估</span><button type="button" id="wardiag-refresh" class="secondary">刷新题库</button></div>
      <h2>读一道局势，看五项决策能力</h2>
      <p>同一兵棋母局势下，分别评价综合策略生成、可行动作分析、行动结果推演、局面价值评估与博弈策略计算。</p>
      <div class="wardiag-counts" aria-label="当前题库规模"><div><strong id="wardiag-mother-count">—</strong><span>母局势</span></div><div><strong id="wardiag-question-count">—</strong><span>题目</span></div><p id="wardiag-updated">正在读取当前题库</p></div>
      <details class="wardiag-distribution"><summary>查看题型与难点分布</summary><div id="wardiag-distribution" class="table-scroll"></div><p class="small">多难点题分别计入对应难点；全库数量按题目去重，不相加各难点的计数。</p></details>
    </div>
    <div class="panel wardiag-library" aria-labelledby="wardiag-library-title">
      <div class="wardiag-heading-row"><h2 id="wardiag-library-title">题库阅读</h2><span class="tag" id="wardiag-filter-count">读取中</span></div>
      <div class="wardiag-filters"><label>决策难点<select id="wardiag-challenge"><option value="">全部难点</option></select></label><label>查找题目<input id="wardiag-search" type="search" placeholder="搜索题号、题型或母局势" autocomplete="off"></label><button type="button" id="wardiag-reset" class="secondary">清除筛选</button></div>
      <div id="wardiag-library-status" class="wardiag-empty" role="status" aria-live="polite"><strong>正在读取题库</strong></div>
      <div id="wardiag-picker" hidden><div class="wardiag-pagination"><button type="button" id="wardiag-previous" class="secondary">上一局势</button><label>母局势<select id="wardiag-group" aria-label="选择母局势"></select></label><button type="button" id="wardiag-next" class="secondary">下一局势</button><span id="wardiag-position" class="small muted"></span></div><div id="wardiag-task-selector" class="seg wardiag-task-selector" role="group" aria-label="选择当前母局势的题型"></div></div>
    </div>
    <div id="wardiag-reading" class="panel wardiag-reading" hidden aria-labelledby="wardiag-question-title">
      <div class="wardiag-heading-row"><div><span class="wardiag-kicker" id="wardiag-question-number"></span><h2 id="wardiag-question-title">题目</h2></div><div class="row wardiag-section-controls"><button type="button" class="secondary" id="wardiag-expand">展开全部</button><button type="button" class="secondary" id="wardiag-collapse">收起全部</button></div></div>
      <div id="wardiag-question-challenges" class="chips"></div><p id="wardiag-task-intro" class="sub"></p><div id="wardiag-question-status" role="status" aria-live="polite"></div><div id="wardiag-question-content"></div><p class="small wardiag-reading-note">各题独立作答，页面展示题文，不将其他题的模型答案传入当前题。</p>
    </div>
    <div class="panel wardiag-demo" aria-labelledby="wardiag-demo-title">
      <span class="wardiag-kicker">DEMO AGENT</span><h2 id="wardiag-demo-title">用智能体完成同一套题目</h2><p>模型阅读题目，按需要调用工具辅助计算，再提交最终答案。</p>
      <div class="wardiag-demo-flow" aria-label="题目进入 LLM Agent，按需调用工具后提交答案"><div><strong>题目</strong><span>局势 · 规则 · 冻结候选</span></div><span class="wardiag-flow-arrow" aria-hidden="true">→</span><div class="wardiag-demo-agent"><strong>LLM Agent</strong><span>阅读 · 工具调用 · 作答</span></div><span class="wardiag-flow-arrow" aria-hidden="true">→</span><div><strong>答案</strong><span>策略概率或分项结论</span></div></div>
      <div class="wardiag-toolbox" aria-label="Demo 计算工具箱"><span class="chip">题内检索</span><span class="chip">规则检索</span><span class="chip">规则阅读</span><span class="chip">算术计算</span><span class="chip">矩阵求解</span></div>
      <div class="wardiag-agent-actions"><div><h3>官方智能体模板</h3><p class="small">按统一题目与答案协议组织智能体。上传后先做包检查，再由你启动接口自检和测评。</p><a id="wardiag-template" class="wardiag-link-button" aria-disabled="true">下载官方模板</a><p id="wardiag-template-status" class="small">正在读取模板状态</p></div><div><h3>测评服务状态</h3><p id="wardiag-evaluation-status" role="status" aria-live="polite">进入本页后读取服务状态。</p><p id="wardiag-evaluation-detail" class="small"></p></div></div>
      <div id="wardiag-evaluation-controls"></div><div id="wardiag-comparison"></div>
    </div>`;
  const $ = id => document.getElementById('wardiag-' + id);

  async function request(path, options = {}) {
    let response;
    try { response = await fetch(API + path, {credentials: 'same-origin', cache: 'no-store',
      redirect: 'error', ...options, headers: {Accept: 'application/json', ...(options.headers || {})}}); }
    catch (error) {
      if (error.name === 'AbortError') throw error;
      throw new Error('暂时无法连接服务，请稍后重试。');
    }
    let data;
    try { data = await response.json(); }
    catch { throw new Error('服务返回了无法读取的内容，请刷新后重试。'); }
    if (!response.ok) {
      const messages = {
        PACKAGE_ARCHIVE_TOO_LARGE: '压缩包超过上传限额，请精简后重新提交。',
        PACKAGE_SANDBOX_UNAVAILABLE: '执行环境尚未就绪，当前不能运行智能体。',
        LOCAL_SESSION_OR_ORIGIN_REQUIRED: '提交会话已更新，请刷新测评状态后重试。',
        PACKAGE_CHANGED: '智能体内容已改变，请重新上传。',
        MODEL_PROFILE_CHANGED_CREATE_NEW_RUN: '模型配置已改变，请创建新的测评任务。'
      };
      throw new Error(messages[data.error_code || data.code || data.error] ||
        (response.status === 404 ? '找不到所选内容，请刷新后重新选择。' : '当前请求未完成，请稍后重试。'));
    }
    return data;
  }
  function status(title, message = '', error = false) {
    const root = $('library-status'); root.replaceChildren(node('strong', '', title));
    if (message) root.append(node('p', '', message));
    root.classList.toggle('wardiag-error', error); root.hidden = false;
  }
  function taskMeta(id) { return array(state.catalog?.tasks).find(item => item.id === id) || {}; }
  function taskName(task) { return taskMeta(task.id).label || taskNames[task.id] || task.title || '题目'; }
  function groupName(group) { return group.label || `母局势 ${Number(group.index) + 1}`; }
  function challengeName(id) { return array(state.catalog?.challenges).find(item => item.id === id)?.label || id; }
  function drawDistribution() {
    const root = $('distribution'); root.replaceChildren();
    const tasks = array(state.catalog?.tasks);
    if (!state.catalog?.available || !tasks.length) { root.append(node('p', 'small', '接入当前题库后显示实际分布。')); return; }
    const table = node('table'), head = node('thead'), row = node('tr');
    ['难点', ...tasks.map(item => item.label || taskNames[item.id] || item.id), '合计'].forEach(value => row.append(node('th', '', value)));
    head.append(row); table.append(head); const body = node('tbody');
    [...array(state.catalog.challenges), {id: '', label: '全库（去重）'}].forEach(axis => {
      const groups = array(state.catalog.groups).filter(group => !axis.id || array(group.challenges).includes(axis.id));
      const tr = node('tr'); tr.append(node('th', '', axis.label)); const total = new Set();
      tasks.forEach(task => {
        const ids = new Set();
        groups.forEach(group => array(group.tasks).filter(item => item.id === task.id).forEach(item => {
          const id = item.question_id || `${group.index}:${item.id}`; ids.add(id); total.add(id);
        })); tr.append(node('td', '', count(ids.size)));
      }); tr.append(node('td', '', count(total.size))); body.append(tr);
    }); table.append(body); root.append(table);
  }
  function drawCatalog() {
    const catalog = state.catalog;
    $('mother-count').textContent = catalog.available ? count(catalog.counts?.mothers) : '—';
    $('question-count').textContent = catalog.available ? count(catalog.counts?.questions) : '—';
    const updated = new Date(catalog.updated_at || '');
    $('updated').textContent = catalog.available ? (Number.isNaN(updated.getTime()) ? '显示当前接入题库' :
      `更新于 ${new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short'}).format(updated)}`) : '当前尚未接入题库';
    const challenge = $('challenge'), previous = challenge.value; challenge.replaceChildren();
    const all = node('option', '', '全部难点'); all.value = ''; challenge.append(all);
    array(catalog.challenges).forEach(item => { const option = node('option', '', item.label); option.value = item.id; challenge.append(option); });
    if (array(catalog.challenges).some(item => item.id === previous)) challenge.value = previous;
    const demo = catalog.demo || {}, link = $('template');
    const templateURL = typeof demo.template_url === 'string' && /^\/api\/wardiag\/[a-z0-9_/-]+$/i.test(demo.template_url) ? demo.template_url : '/api/wardiag/demo/template';
    if (demo.template_available === true || evaluation.connected) {
      link.href = templateURL; link.download = ''; link.setAttribute('aria-disabled', 'false'); link.removeAttribute('tabindex');
      $('template-status').textContent = '模板可下载。';
    } else { link.removeAttribute('href'); link.setAttribute('aria-disabled', 'true'); link.tabIndex = -1; $('template-status').textContent = '当前服务尚未提供可下载模板。'; }
    drawDistribution(); filterGroups(); drawComparison(demo.comparison);
  }
  function filterGroups() {
    const query = $('search').value.trim().toLocaleLowerCase(), challenge = $('challenge').value;
    state.groups = array(state.catalog?.groups).filter(group => {
      if (challenge && !array(group.challenges).includes(challenge)) return false;
      const search = [groupName(group), ...array(group.challenges).map(challengeName),
        ...array(group.tasks).flatMap(task => [task.title, task.display_id, task.question_id, taskName(task)])].join(' ').toLocaleLowerCase();
      return !query || search.includes(query);
    });
    $('filter-count').textContent = `${count(state.groups.length)} 个母局势`; $('picker').hidden = !state.groups.length;
    if (!state.catalog?.available) { status('当前尚未接入题库', '接入后将在这里显示实际题目与题型分布。'); $('reading').hidden = true; updateEvalButtons(); return; }
    if (!state.groups.length) { status('没有符合条件的题目', '调整难点或搜索词，也可以清除筛选查看全部题目。'); $('reading').hidden = true; updateEvalButtons(); return; }
    $('library-status').hidden = true;
    const current = state.groups.find(group => String(group.index) === String(state.group?.index)) || state.groups[0];
    $('group').replaceChildren();
    state.groups.forEach(group => { const option = node('option', '', groupName(group)); option.value = String(group.index); $('group').append(option); });
    chooseGroup(current);
  }
  function chooseGroup(group) {
    state.group = group; $('group').value = String(group.index); const index = state.groups.indexOf(group);
    $('position').textContent = `${index + 1} / ${state.groups.length}`;
    $('previous').disabled = index <= 0; $('next').disabled = index >= state.groups.length - 1;
    const tasks = array(group.tasks); if (!tasks.some(task => task.id === state.task)) state.task = tasks[0]?.id || '';
    drawTaskButtons(); updateEvalButtons();
    if (state.task) loadQuestion(); else { $('reading').hidden = true; status('该母局势暂无可读题目', '请切换其他母局势。'); }
  }
  function drawTaskButtons() {
    $('task-selector').replaceChildren();
    array(state.group?.tasks).forEach(task => {
      const button = node('button', task.id === state.task ? 'on' : '', taskName(task));
      button.type = 'button'; button.setAttribute('aria-controls', 'wardiag-reading'); button.setAttribute('aria-pressed', String(task.id === state.task));
      button.addEventListener('click', () => { if (state.task === task.id) return; state.task = task.id; drawTaskButtons(); updateEvalButtons(); loadQuestion(); });
      $('task-selector').append(button);
    });
  }
  async function loadQuestion() {
    const group = state.group, task = array(group?.tasks).find(item => item.id === state.task); if (!task) return;
    const requestId = ++state.request; state.controller?.abort(); state.controller = new AbortController();
    const key = `${group.index}:${task.id}`, reading = $('reading'); reading.hidden = false; reading.setAttribute('aria-busy', 'true');
    $('question-title').textContent = taskName(task); $('question-number').textContent = [groupName(group), task.display_id || task.title].filter(Boolean).join(' · ');
    $('task-intro').textContent = taskMeta(task.id).intro || '';
    $('question-challenges').replaceChildren(...array(group.challenges).map(id => node('span', 'chip', challengeName(id))));
    $('question-content').replaceChildren(); state.sections = []; $('question-status').textContent = '正在读取题目…';
    $('question-status').classList.remove('wardiag-error'); $('expand').disabled = true; $('collapse').disabled = true;
    try {
      const question = state.cache.get(key) || await request(`/questions/${encodeURIComponent(group.index)}/${encodeURIComponent(task.id)}`, {signal: state.controller.signal});
      if (requestId !== state.request) return;
      if (question.question_id !== task.question_id || question.question_sha256 !== task.question_sha256)
        throw new Error('题库已更新，请刷新后重新选择。');
      state.cache.set(key, question);
      const sections = array(question.sections);
      if (!sections.length) { $('question-status').textContent = '这道题暂无可显示的题文，请切换其他题目。'; return; }
      sections.forEach((section, index) => {
        const detail = node('details', 'wardiag-question-section'), title = typeof section.title === 'string' ? section.title : `题目材料 ${index + 1}`;
        detail.open = index === 0 || /作答|要求|提问|问题/.test(title); detail.append(node('summary', '', title));
        const body = node('article', 'wardiag-question-body');
        // Only the server's strict question-content sanitizer may supply this HTML.
        body.innerHTML = typeof section.html === 'string' ? section.html : '';
        detail.append(body); $('question-content').append(detail); state.sections.push(detail);
      });
      $('question-status').textContent = ''; $('expand').disabled = false; $('collapse').disabled = false;
    } catch (error) {
      if (requestId !== state.request || error.name === 'AbortError') return;
      $('question-status').textContent = error.message; $('question-status').classList.add('wardiag-error');
    } finally { if (requestId === state.request) reading.setAttribute('aria-busy', 'false'); }
  }

  const evaluation = {csrf: '', capabilities: {}, packages: [], providers: [], runs: [], run: null,
    question: '', busy: false, loading: null, ready: false, connected: false, timer: null, pending: null};
  const packageId = item => item?.package_id || item?.id || '';
  const packageInfo = item => item?.manifest || item || {};
  const packagePassed = item => { const test = item?.self_test || item?.last_self_test; return test?.ok === true || test?.status === 'passed'; };
  const statusLabels = {queued: '排队中', pending: '尚未开始', running: '运行中', paused: '已暂停',
    completed: '已完成', stopped: '已停止', interrupted: '已中断', failed: '未完成', scoring: '评分中'};
  const runActive = run => ['queued', 'pending', 'running', 'scoring'].includes(run?.status);
  $('evaluation-controls').innerHTML = `
    <details class="wardiag-eval-panel" open><summary>上传智能体与启动测评</summary>
      <div class="wardiag-heading-row wardiag-eval-heading"><p class="small">上传检查、接口自检和正式答题分别执行，由你点击启动。</p><button type="button" id="wardiag-eval-refresh" class="secondary">刷新服务与记录</button></div>
      <div id="wardiag-eval-notice" class="wardiag-eval-notice small" role="status" aria-live="polite">正在读取测评状态</div>
      <div class="wardiag-eval-grid">
        <div class="wardiag-eval-step"><h3>1　提交与检查</h3><label>智能体 ZIP<input type="file" id="wardiag-agent-file" accept=".zip,application/zip"></label><div class="row"><button type="button" id="wardiag-agent-upload" disabled>上传并检查包</button><button type="button" id="wardiag-agent-test" class="secondary" disabled>接口自检</button></div><label>作答程序<select id="wardiag-agent-package"><option value="">内置作答链</option></select></label><p id="wardiag-agent-info" class="small"></p></div>
        <div class="wardiag-eval-step"><h3>2　选择模型与题目</h3><div class="grid2"><label>模型服务<select id="wardiag-eval-provider"><option value="">暂无可用服务</option></select></label><label>模型<select id="wardiag-eval-model"><option value="">暂无可用模型</option></select></label></div><label>题目范围<select id="wardiag-eval-scope"><option value="current">当前阅读题目</option><option value="group">当前母局势全部题目</option><option value="filtered">当前筛选的全部母局势</option><option value="all">当前题库全部题目</option><option value="random">从当前题库随机选题</option></select></label><label id="wardiag-random-label" hidden>随机题数<input id="wardiag-eval-random-count" type="number" min="1" max="10000" value="5"></label><p id="wardiag-eval-selection" class="small"></p><button type="button" id="wardiag-eval-start" disabled>开始测评</button><p class="small">开始后会调用所选模型，可能产生 API 费用。每题独立作答并评分。</p></div>
      </div>
      <div class="wardiag-history"><label>历史测评<select id="wardiag-eval-history"><option value="">选择历史任务</option></select></label><button type="button" id="wardiag-eval-open" class="secondary" disabled>查看记录</button><button type="button" id="wardiag-eval-sync" class="secondary" disabled>同步当前任务</button></div>
      <div id="wardiag-run" class="wardiag-run" hidden><div class="wardiag-heading-row"><h3 id="wardiag-run-heading">测评任务</h3><span id="wardiag-run-status" class="tag"></span></div><p id="wardiag-run-meta" class="small"></p><progress id="wardiag-run-progress" max="100" value="0" aria-label="测评完成进度"></progress><p id="wardiag-run-counts" class="small"></p><div class="row"><button type="button" id="wardiag-run-pause" class="secondary" disabled>暂停</button><button type="button" id="wardiag-run-continue" class="secondary" disabled>继续</button><button type="button" id="wardiag-run-stop" class="danger" disabled>停止</button><button type="button" id="wardiag-run-retry" class="secondary" disabled>重试失败题</button></div><label>查看题目原答<select id="wardiag-run-question"></select></label><p id="wardiag-run-answer-status" class="small"></p><pre id="wardiag-run-answer"></pre><div id="wardiag-run-score" class="wardiag-run-score"></div></div>
    </details>`;

  function evalNotice(message, error = false) {
    $('eval-notice').textContent = message; $('eval-notice').classList.toggle('wardiag-error', error);
  }
  async function evalAPI(path, payload) {
    const options = {};
    if (payload !== undefined) {
      if (!evaluation.csrf) throw new Error('提交会话尚未就绪，请先刷新测评状态。');
      options.method = 'POST'; options.headers = {'Content-Type': 'application/json', 'X-WarDiag-CSRF': evaluation.csrf};
      options.body = JSON.stringify(payload);
    }
    const data = await request('/evaluation' + path, options);
    if (typeof data.csrf_token === 'string') evaluation.csrf = data.csrf_token;
    return data;
  }
  function selectedQuestions() {
    const scope = $('eval-scope').value;
    let groups = scope === 'all' || scope === 'random' ? array(state.catalog?.groups) : scope === 'filtered' ? state.groups :
      (state.group && state.groups.includes(state.group) ? [state.group] : []);
    const questions = new Map();
    groups.forEach(group => array(group.tasks).forEach(task => {
      if (scope === 'current' && task.id !== state.task) return;
      if (typeof task.question_id === 'string' && /^[a-f0-9]{64}$/i.test(task.question_sha256 || ''))
        questions.set(task.question_id, {question_id: task.question_id, question_sha256: task.question_sha256});
    }));
    return [...questions.values()];
  }
  function updateEvalButtons() {
    if (!$('eval-start')) return;
    const busy = evaluation.busy || Boolean(evaluation.loading), selected = evaluation.packages.find(item => packageId(item) === $('agent-package').value);
    const packageReady = !$('agent-package').value || (evaluation.capabilities.available === true && packagePassed(selected));
    const questions = selectedQuestions(), random = $('eval-scope').value === 'random', requested = Number($('eval-random-count').value);
    const selectionReady = state.catalog?.available === true && questions.length > 0 && (!random || (Number.isInteger(requested) && requested > 0 && requested <= questions.length));
    $('random-label').hidden = !random;
    $('eval-selection').textContent = random ? `${count(questions.length)} 道可选题；本次随机选择 ${count(requested)} 道。` : `本次选择 ${count(questions.length)} 道题。`;
    $('agent-upload').disabled = busy || !evaluation.csrf || !$('agent-file').files?.length;
    $('agent-file').disabled = busy || !evaluation.csrf;
    $('agent-test').disabled = busy || !evaluation.csrf || !selected || evaluation.capabilities.available !== true;
    $('agent-package').disabled = busy;
    $('eval-provider').disabled = busy || !evaluation.providers.length;
    $('eval-model').disabled = busy || !$('eval-provider').value;
    $('eval-start').disabled = busy || !evaluation.ready || !$('eval-provider').value || !$('eval-model').value || !selectionReady || !packageReady;
    $('eval-refresh').disabled = busy;
    $('eval-open').disabled = busy || !$('eval-history').value;
    $('eval-sync').disabled = busy || !evaluation.run;
    const run = evaluation.run, canControl = !busy && Boolean(evaluation.csrf && run);
    $('run-pause').disabled = !canControl || !['queued', 'pending', 'running'].includes(run?.status);
    $('run-continue').disabled = !canControl || !['paused', 'interrupted'].includes(run?.status);
    $('run-stop').disabled = !canControl || !['queued', 'pending', 'running', 'paused', 'scoring'].includes(run?.status);
    $('run-retry').disabled = !canControl || runActive(run) || !array(run?.questions).some(item => ['failed', 'interrupted'].includes(item.status));
  }
  function drawPackages() {
    const previous = $('agent-package').value; $('agent-package').replaceChildren();
    const builtin = node('option', '', '内置作答链'); builtin.value = ''; $('agent-package').append(builtin);
    evaluation.packages.forEach(item => {
      const info = packageInfo(item), option = node('option', '', `${info.name || '已上传智能体'}${info.version ? ' · ' + info.version : ''}${packagePassed(item) ? ' · 自检通过' : ' · 尚未通过自检'}`);
      option.value = packageId(item); $('agent-package').append(option);
    });
    if (evaluation.packages.some(item => packageId(item) === previous)) $('agent-package').value = previous;
    drawPackageInfo();
  }
  function drawPackageInfo() {
    const item = evaluation.packages.find(value => packageId(value) === $('agent-package').value);
    $('agent-info').textContent = item ? (packagePassed(item) ? '接口自检通过；答题质量在测评后独立评分。' : '包已保存。请点击接口自检，通过后可启动该智能体测评。') : '使用服务提供的内置作答链，每道题独立运行。';
    if (item && evaluation.capabilities.available !== true) $('agent-info').textContent += ' 当前智能体执行环境未就绪。';
    updateEvalButtons();
  }
  function drawModels() {
    const previous = $('eval-model').value;
    const models = array(evaluation.providers.find(provider => provider.id === $('eval-provider').value)?.models);
    $('eval-model').replaceChildren();
    if (!models.length) { const option = node('option', '', '暂无可用模型'); option.value = ''; $('eval-model').append(option); }
    models.forEach(model => { const option = node('option', '', model.label || model.id); option.value = model.id; $('eval-model').append(option); });
    if (models.some(model => model.id === previous)) $('eval-model').value = previous;
    updateEvalButtons();
  }
  function drawProviders() {
    const previous = $('eval-provider').value; $('eval-provider').replaceChildren();
    if (!evaluation.providers.length) { const option = node('option', '', '暂无可用服务'); option.value = ''; $('eval-provider').append(option); }
    evaluation.providers.forEach(provider => { const option = node('option', '', provider.label || provider.id); option.value = provider.id; $('eval-provider').append(option); });
    if (evaluation.providers.some(provider => provider.id === previous)) $('eval-provider').value = previous;
    drawModels();
  }
  function drawHistory() {
    const previous = $('eval-history').value; $('eval-history').replaceChildren();
    const empty = node('option', '', evaluation.runs.length ? '选择历史任务' : '暂无历史任务'); empty.value = ''; $('eval-history').append(empty);
    evaluation.runs.forEach((run, index) => {
      const id = run.run_id || run.id; if (!id) return;
      const option = node('option', '', `${run.name || run.config?.model_id || '测评任务 ' + (index + 1)} · ${statusLabels[run.status] || '已有记录'}${run.created_at ? ' · ' + String(run.created_at).slice(0, 19).replace('T', ' ') : ''}`);
      option.value = id; $('eval-history').append(option);
    });
    if (evaluation.runs.some(run => (run.run_id || run.id) === previous)) $('eval-history').value = previous;
    updateEvalButtons();
  }
  async function loadEvaluation() {
    if (evaluation.loading) return evaluation.loading;
    evaluation.loading = (async () => {
      evaluation.ready = false; evaluation.connected = false; evaluation.csrf = '';
      $('evaluation-status').textContent = '正在读取测评服务状态…'; evalNotice('正在读取可用程序、模型与历史记录。');
      let packagesReady = false;
      try { const data = await evalAPI('/packages'); evaluation.packages = array(data.packages).filter(packageId); evaluation.capabilities = data.capabilities || {}; packagesReady = true; evaluation.connected = true; }
      catch { evaluation.packages = []; evaluation.capabilities = {}; }
      try { const data = await evalAPI('/bootstrap'); evaluation.providers = array(data.providers); evaluation.runs = array(data.runs); evaluation.ready = Boolean(evaluation.csrf); evaluation.connected = true; }
      catch { evaluation.providers = []; evaluation.runs = []; }
      drawPackages(); drawProviders(); drawHistory();
      $('evaluation-status').textContent = evaluation.ready ? '测评服务已连接' : packagesReady ? '智能体提交服务已连接' : '测评服务暂不可用';
      $('evaluation-detail').textContent = evaluation.connected ? (evaluation.capabilities.available === true ? '智能体执行环境已就绪。' : '上传与包检查按服务能力开放；智能体接口自检与运行尚未就绪。') : '当前可以阅读题库和下载可用模板，上传、自检与模型调用尚未开放。';
      if (evaluation.connected && $('template').getAttribute('aria-disabled') === 'true') {
        $('template').href = '/api/wardiag/demo/template'; $('template').download = '';
        $('template').setAttribute('aria-disabled', 'false'); $('template').removeAttribute('tabindex');
        $('template-status').textContent = '模板下载服务已连接。';
      }
      evalNotice(!evaluation.connected ? '测评服务尚未连接。服务恢复后点击“刷新服务与记录”。' : !evaluation.providers.length ? '尚无可用模型。可先查看模板或提交智能体；模型就绪后刷新。' : '选择模型、程序与题目，点击开始测评。');
    })().finally(() => { evaluation.loading = null; updateEvalButtons(); });
    updateEvalButtons(); return evaluation.loading;
  }
  async function evalAction(callback) {
    if (evaluation.busy) return;
    evaluation.busy = true; updateEvalButtons();
    try { await callback(); }
    catch (error) { evalNotice(error.message || '操作未完成，请重试。', true); }
    finally { evaluation.busy = false; updateEvalButtons(); }
  }
  function upsertPackage(item) {
    const id = packageId(item); if (!id) return;
    const index = evaluation.packages.findIndex(value => packageId(value) === id);
    if (index < 0) evaluation.packages.push(item); else evaluation.packages[index] = item;
    drawPackages(); $('agent-package').value = id; drawPackageInfo();
  }
  async function uploadPackage() {
    await evalAction(async () => {
      const file = $('agent-file').files?.[0]; if (!file) return;
      const maximum = evaluation.capabilities.limits?.archive_bytes || evaluation.capabilities.max_archive_bytes || 20 * 1024 * 1024;
      if (!/\.zip$/i.test(file.name) || !Number.isSafeInteger(file.size) || file.size <= 0 || file.size > maximum)
        throw new Error('请选择非空 ZIP 压缩包，并确认文件未超过服务上传限额。');
      evalNotice('正在上传并检查包结构…'); const bytes = new Uint8Array(await file.arrayBuffer());
      const chunks = []; for (let at = 0; at < bytes.length; at += 32768) chunks.push(String.fromCharCode(...bytes.subarray(at, at + 32768)));
      const result = await evalAPI('/packages', {archive_base64: btoa(chunks.join(''))});
      upsertPackage(result.package || result); $('agent-file').value = '';
      evalNotice('上传与包检查已完成。接口自检由你另行点击启动。');
    });
  }
  async function selfTestPackage() {
    await evalAction(async () => {
      const item = evaluation.packages.find(value => packageId(value) === $('agent-package').value);
      if (!item || evaluation.capabilities.available !== true) throw new Error('请先选择智能体，并确认执行环境已就绪。');
      evalNotice('正在运行接口自检…');
      const result = await evalAPI(`/packages/${encodeURIComponent(packageId(item))}/self-test`, {});
      const updated = result.package || {...item, self_test: result.self_test || result}; upsertPackage(updated);
      evalNotice(packagePassed(updated) ? '接口自检通过。可选择模型和题目启动测评。' : '接口自检未通过，请调整智能体后重新提交。', !packagePassed(updated));
    });
  }
  async function createRun() {
    await evalAction(async () => {
      if (!evaluation.ready || !$('eval-provider').value || !$('eval-model').value) throw new Error('请先选择可用模型。');
      const item = evaluation.packages.find(value => packageId(value) === $('agent-package').value);
      if ($('agent-package').value && (!item || !packagePassed(item) || evaluation.capabilities.available !== true)) throw new Error('所选智能体尚未具备运行条件。');
      const questions = selectedQuestions(); if (!questions.length) throw new Error('请先选择具有完整绑定信息的题目。');
      const types = array(state.catalog?.tasks).map(task => task.id), random = $('eval-scope').value === 'random';
      const amount = Number($('eval-random-count').value);
      if (random && (!Number.isInteger(amount) || amount < 1 || amount > questions.length)) throw new Error('随机题数应在可选题目数量范围内。');
      const selection = random ? {method: 'random', types, count: amount} : {method: 'explicit', types, questions};
      const payload = {provider_id: $('eval-provider').value, model_id: $('eval-model').value, selection};
      if (item) payload.package_id = packageId(item);
      const signature = JSON.stringify(payload);
      if (evaluation.pending?.signature !== signature) evaluation.pending = {signature,
        key: globalThis.crypto?.randomUUID?.() || Date.now().toString(36) + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2)};
      evalNotice('正在创建测评任务…');
      const data = await evalAPI('/runs', {...payload, idempotency_key: evaluation.pending.key});
      const snapshot = data.snapshot || data, id = snapshot.run_id || data.run_id;
      if (!id) throw new Error('任务已提交，尚未获得任务编号。请刷新历史记录确认结果。');
      evaluation.pending = null;
      if (Array.isArray(snapshot.questions)) acceptRun(snapshot); else await readRun(id);
      evalNotice('测评已启动。原答、进度与评分将在同一任务中显示。');
      await refreshHistory();
    });
  }
  async function refreshHistory() {
    const data = await evalAPI('/runs'); evaluation.runs = array(data.runs || data); drawHistory();
  }
  function acceptRun(snapshot) {
    if (!snapshot || !snapshot.run_id || !Array.isArray(snapshot.questions)) throw new Error('任务记录不完整，请刷新后重试。');
    evaluation.run = snapshot;
    if (!snapshot.questions.some(question => question.question_id === evaluation.question))
      evaluation.question = (snapshot.questions.find(question => question.status === 'running') || snapshot.questions[0])?.question_id || '';
    drawRun(); scheduleRunSync();
  }
  async function readRun(id) {
    const data = await evalAPI('/runs/' + encodeURIComponent(id)); acceptRun(data.snapshot || data);
  }
  function scheduleRunSync() {
    clearTimeout(evaluation.timer); evaluation.timer = null;
    if (!runActive(evaluation.run) || document.getElementById('wardiag')?.hidden) return;
    evaluation.timer = setTimeout(async () => {
      if (!evaluation.run || evaluation.busy || document.getElementById('wardiag')?.hidden) { scheduleRunSync(); return; }
      try { await readRun(evaluation.run.run_id); }
      catch { evalNotice('自动同步暂未完成，可点击“同步当前任务”重试。', true); }
    }, 5000);
  }
  function drawRun() {
    const run = evaluation.run; $('run').hidden = !run; if (!run) return;
    $('run-heading').textContent = '当前测评任务'; $('run-status').textContent = statusLabels[run.status] || '已有记录';
    $('run-meta').textContent = [run.config?.package_name || '内置作答链', run.config?.model_id].filter(Boolean).join(' · ');
    const questions = array(run.questions), completed = run.counts?.completed ?? questions.filter(item => item.status === 'completed').length;
    const failed = run.counts?.failed ?? questions.filter(item => ['failed', 'interrupted'].includes(item.status)).length;
    const total = run.counts?.total ?? run.total ?? questions.length, scored = run.counts?.scored ?? questions.filter(item => item.score != null).length;
    $('run-progress').value = total > 0 ? Math.min(100, (completed + failed) * 100 / total) : 0;
    $('run-counts').textContent = `共 ${count(total)} 题 · 完成 ${count(completed)} · 未完成 ${count(failed)} · 已评分 ${count(scored)}`;
    $('run-question').replaceChildren(); questions.forEach((question, index) => {
      const option = node('option', '', `${question.number || question.display_id || index + 1} · ${question.label || '题目'} · ${statusLabels[question.status] || '已有记录'}`);
      option.value = question.question_id; $('run-question').append(option);
    }); $('run-question').value = evaluation.question; drawRunAnswer(); updateEvalButtons();
  }
  function drawRunAnswer() {
    const question = array(evaluation.run?.questions).find(item => item.question_id === evaluation.question);
    $('run-answer').textContent = question?.text || (question?.status === 'running' ? '当前题正在作答，原答返回后显示。' : '本题暂无返回的原答。');
    const usage = question?.usage?.total_tokens;
    $('run-answer-status').textContent = `${statusLabels[question?.status] || '尚未开始'}${Number.isFinite(usage) ? ' · ' + count(usage) + ' tokens' : ''}`;
    const root = $('run-score'); root.replaceChildren();
    if (question?.score == null) { root.append(node('p', 'small', '本题尚无评分结果。')); return; }
    const labels = {precision: '精确率', recall: '召回率', f1: 'F1', accuracy: '准确率',
      mae: '平均绝对误差', absolute_error: '绝对误差', strategy_loss: '策略损失', regret: '策略损失',
      comprehensive_pass: '综合通过', feasibility_pass: '可行性通过', equilibrium_value_gap: '均衡收益差',
      illegal_probability_mass: '非法候选概率'};
    const metrics = question.score.metrics || question.score;
    const values = Object.entries(labels).filter(([key]) => Number.isFinite(metrics?.[key]));
    if (!values.length) { root.append(node('p', 'small', '评分结果已保存，当前记录未提供可展示的汇总指标。')); return; }
    const list = node('dl', 'wardiag-score-metrics'); values.forEach(([key, label]) => {
      const row = node('div'); row.append(node('dt', '', label), node('dd', '', Number(metrics[key].toPrecision(6)))); list.append(row);
    }); root.append(list);
  }
  async function controlRun(action) {
    await evalAction(async () => {
      if (!evaluation.run) return;
      const data = await evalAPI(`/runs/${encodeURIComponent(evaluation.run.run_id)}/control`, {action});
      acceptRun(data.snapshot || data); evalNotice('任务状态已更新。'); await refreshHistory();
    });
  }
  function drawComparison(comparison) {
    const root = $('comparison'); root.replaceChildren(); root.append(node('h3', '', '已有 Demo 对比记录'));
    if (!comparison?.available || !array(comparison.rows).length) { root.append(node('p', 'small', '当前没有可展示的 Demo 对比结果。')); return; }
    const scope = [comparison.date ? '记录日期：' + comparison.date : '',
      Number.isInteger(comparison.question_count) ? count(comparison.question_count) + ' 道实验题目' : '',
      Number.isInteger(comparison.mother_count) ? count(comparison.mother_count) + ' 个实验母局势' : ''].filter(Boolean);
    root.append(node('p', 'small', `${scope.join(' · ')}。这是已有实验记录，当前阅读题库的规模不代表这些题目已运行 Demo。`));
    const scroll = node('div', 'table-scroll'), table = node('table'), thead = node('thead'), tr = node('tr');
    ['作答方式', '综合均值', '样本数 n', '完成', '截断', '总 tokens', '调用预算'].forEach(label => tr.append(node('th', '', label)));
    thead.append(tr); table.append(thead); const tbody = node('tbody');
    comparison.rows.forEach(item => {
      const budget = item.budget || {}, limits = [
        Number.isFinite(budget.max_rounds) ? '最多 ' + count(budget.max_rounds) + ' 轮' : '',
        Number.isFinite(budget.max_output_tokens_per_round) ? '每轮输出 ≤ ' + count(budget.max_output_tokens_per_round) + ' tokens' : '',
        Number.isFinite(budget.max_output_tokens_per_question_upper_bound) ? '每题输出上限 ' + count(budget.max_output_tokens_per_question_upper_bound) + ' tokens' : '',
        Number.isFinite(budget.max_tool_calls_per_question) ? '每题工具 ≤ ' + count(budget.max_tool_calls_per_question) + ' 次' : '',
        typeof budget.thinking_enabled === 'boolean' ? (budget.thinking_enabled ? '启用思考' : '未启用思考') : '',
        typeof budget.tools_enabled === 'boolean' ? (budget.tools_enabled ? '启用工具' : '未启用工具') : ''
      ].filter(Boolean).join('；') || '原记录未提供';
      const row = node('tr'); [item.label || '已有记录', Number.isFinite(item.mean) ? Number(item.mean.toPrecision(6)) : '—',
        count(item.n), count(item.completed), count(item.truncated), count(item.total_tokens), limits].forEach(value => row.append(node('td', '', value))); tbody.append(row);
    }); table.append(tbody); scroll.append(table); root.append(scroll);
    root.append(node('p', 'small', comparison.different_budget ? '各方式的工具与调用预算不同，结果属于观察性对照。' : '结果按原记录的题目与预算条件解释。'));
    if (!comparison.controlled_correction) root.append(node('p', 'small', '这份记录不代表受控纠错实验已完成。'));
    if (!comparison.formal_gold) root.append(node('p', 'small', '当前汇总不认证正式金标准。'));
    if (comparison.note) root.append(node('p', 'small', comparison.note));
  }
  $('agent-file').addEventListener('change', updateEvalButtons);
  $('agent-package').addEventListener('change', drawPackageInfo);
  $('agent-upload').addEventListener('click', uploadPackage); $('agent-test').addEventListener('click', selfTestPackage);
  $('eval-refresh').addEventListener('click', loadEvaluation);
  $('eval-provider').addEventListener('change', drawModels); $('eval-model').addEventListener('change', updateEvalButtons);
  $('eval-scope').addEventListener('change', updateEvalButtons); $('eval-random-count').addEventListener('input', updateEvalButtons);
  $('eval-start').addEventListener('click', createRun); $('eval-history').addEventListener('change', updateEvalButtons);
  $('eval-open').addEventListener('click', () => evalAction(() => readRun($('eval-history').value)));
  $('eval-sync').addEventListener('click', () => evalAction(() => readRun(evaluation.run.run_id)));
  $('run-question').addEventListener('change', () => { evaluation.question = $('run-question').value; drawRunAnswer(); });
  ['pause', 'continue', 'stop', 'retry'].forEach(action => $('run-' + action).addEventListener('click', () => controlRun(action)));
  document.addEventListener('wargame:tab', event => { if (event.detail !== 'wardiag') { clearTimeout(evaluation.timer); evaluation.timer = null; } else scheduleRunSync(); });
  updateEvalButtons();

  async function loadCatalog() {
    if (state.loading) return state.loading;
    state.loading = (async () => {
      $('refresh').disabled = true; status('正在读取当前题库');
      try { state.catalog = await request('/catalog'); state.cache.clear(); state.loaded = true; drawCatalog(); }
      catch (error) {
        state.catalog = null; state.groups = []; state.group = null; state.loaded = false;
        $('mother-count').textContent = '—'; $('question-count').textContent = '—'; $('filter-count').textContent = '读取未完成';
        $('updated').textContent = '当前题库状态未能读取';
        status('题库读取未完成', error.message, true); $('picker').hidden = true; $('reading').hidden = true; updateEvalButtons();
      }
      finally { $('refresh').disabled = false; state.loading = null; }
    })(); return state.loading;
  }
  function enter() { if (!state.loaded) { loadCatalog(); loadEvaluation(); } }
  $('refresh').addEventListener('click', () => { loadCatalog(); loadEvaluation(); });
  $('challenge').addEventListener('change', filterGroups); $('search').addEventListener('input', filterGroups);
  $('reset').addEventListener('click', () => { $('challenge').value = ''; $('search').value = ''; filterGroups(); });
  $('group').addEventListener('change', () => { const group = state.groups.find(item => String(item.index) === $('group').value); if (group) chooseGroup(group); });
  $('previous').addEventListener('click', () => { const index = state.groups.indexOf(state.group); if (index > 0) chooseGroup(state.groups[index - 1]); });
  $('next').addEventListener('click', () => { const index = state.groups.indexOf(state.group); if (index + 1 < state.groups.length) chooseGroup(state.groups[index + 1]); });
  $('expand').addEventListener('click', () => state.sections.forEach(section => { section.open = true; }));
  $('collapse').addEventListener('click', () => state.sections.forEach(section => { section.open = false; }));
  document.addEventListener('wargame:tab', event => { if (event.detail === 'wardiag') enter(); });
  if (location.hash === '#wardiag' || !document.getElementById('wardiag')?.hidden) enter();
})();
