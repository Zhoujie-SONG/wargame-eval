(() => {
  'use strict';

  const mount = document.getElementById('wardiag-content');
  if (!mount) return;

  const tasks = [
    {
      name: '综合策略生成',
      short: '综合策略',
      purpose: '把可行性、行动后果、局面价值与对手选择放在一起，评估整体决策质量。',
      input: '共同母局面的局势、允许观察、规则，以及事先冻结的双方完整候选方案。',
      output: '本方在全部给定候选上的概率分布；非法候选的概率为 0。',
      scoring: '依据同一有限候选博弈的核验参考报告策略损失，允许存在多个最优策略。',
      exampleInput: '合成情境：红方可选 R1、R2、R3，蓝方可选 B1、B2。规则说明 R3 不可执行。模型依据题给局势与规则，为红方分配选择概率。',
      exampleOutput: 'R1：0.5\nR2：0.5\nR3：0\n\n这里只说明概率分布格式，不表示该分配已被评为最优。'
    },
    {
      name: '可行动作分析',
      short: '可行动作',
      purpose: '判断完整方案在相关时刻与声明分支中能否执行，单独测量可行性分析。',
      input: '与综合题共享的完整候选、允许观察、规则、时序及声明分支。',
      output: '红蓝双方全部合法完整方案的编号集合。',
      scoring: '合法集合的精确率、召回率、F1 与整集合正确率；不以策略收益判断是否合法。',
      exampleInput: '合成规则：方案中每一步的成本不得超过当前资源。红方资源为 2，R1 成本为 1、R2 成本为 2、R3 成本为 3；蓝方资源为 1，B1 与 B2 的成本均为 1。全部方案仅含一步，其他执行条件均满足。',
      exampleOutput: '红方合法方案：R1、R2\n蓝方合法方案：B1、B2'
    },
    {
      name: '行动结果推演',
      short: '结果推演',
      purpose: '在双方行动已经指定的条件下，推断检查点或终点的状态。',
      input: '当前局势、规则、双方事先指定的行动序列及顺序，以及检查点或终点。',
      output: '按题目列出的空位逐行填写指定状态；随机题按题给合同回答可能结果与概率。',
      scoring: '确定字段准确率与整状态正确率；随机分支概率另行评分。',
      exampleInput: '合成规则：相邻两格之间移动一次需要 10 秒，行动期间没有其他状态变化。红1 初始位于 A 格，B 格与 A 格相邻且为空；红1 在 0 秒开始由 A 移动至 B，蓝方保持不动。回答 10 秒时红1 所在格。',
      exampleOutput: '红1所在格：B'
    },
    {
      name: '局面价值评估',
      short: '价值评估',
      purpose: '依据允许的信息，估计当前局面对明确作答方的价值。',
      input: '明确的作答方、核验局势、允许信息、规则、剩余时域与题目声明的参考口径。',
      output: '本方一个 0 至 1 的折算胜率数值，平局按 0.5 折算。',
      scoring: '普通题报告五档同档正确率与绝对误差；使用代理参考时，结果衡量与该参考的一致性。',
      exampleInput: '合成计算示例：红方获胜、平局、失利的概率分别为 0.4、0.2、0.4。题目要求将平局按 0.5 折算，输出红方的折算胜率。',
      exampleOutput: '0.5\n\n计算：0.4 + 0.5 × 0.2 = 0.5。实际价值题需要依据题给局势估计，不能把本例当作价值模型验证。'
    },
    {
      name: '博弈策略计算',
      short: '博弈策略',
      purpose: '在双方有限候选与收益已经明确时，计算本方应采用的策略。',
      input: '原始局势、规则、双方有限候选、完整收益矩阵与明确的作答方。',
      output: '本方在给定候选上的选择概率分布；概率为 1 的单一候选也是有效的纯策略形式。',
      scoring: '判断该单方策略能否与某个对手策略构成近似均衡；结论限于给定有限候选博弈。',
      exampleInput: '合成零和博弈：行是红方 R1、R2，列是蓝方 B1、B2。红方收益矩阵为：\n\n          B1    B2\nR1         2    -1\nR2        -1     2\n\n蓝方收益为红方收益的相反数。求红方的均衡策略。',
      exampleOutput: 'R1：0.5\nR2：0.5\n\n该策略使蓝方选择任一列时，红方期望收益均为 0.5。'
    }
  ];

  mount.innerHTML = `
    <div class="panel wardiag-intro">
      <div class="wardiag-heading-row"><span class="tag">诊断式评估</span><span class="wardiag-kicker">WARDIAG</span></div>
      <h2>面向大模型兵棋策略生成的诊断式评估数据集</h2>
      <p class="wardiag-lead">在同一兵棋局面上，用一道综合策略题和四项独立任务，连接整体决策质量与分析、推演、估值、博弈求解能力。</p>
      <p class="wardiag-intro-note">每项任务直接获得经核验的上游输入，独立作答、分别评分；上一题的模型答案不会作为下一题的输入。</p>
    </div>

    <div class="cards wardiag-summary" aria-label="评估结构">
      <div class="card"><strong>1</strong><span>共同母局面</span><em>共享局势与允许的信息</em></div>
      <div class="card"><strong>5</strong><span>同母任务</span><em>整体决策与分项测量</em></div>
      <div class="card"><strong>4</strong><span>独立诊断分项</span><em>直接使用核验上游输入</em></div>
      <div class="card"><strong>5</strong><span>决策难点类别</span><em>按题目条件描述与核验</em></div>
    </div>

    <div class="panel">
      <h2>同一局面，分别测量五类任务</h2>
      <p class="sub">选择任务，查看它获得什么、回答什么，以及怎样解释结果。</p>
      <div id="wardiag-task-selector" class="seg wardiag-task-selector" role="group" aria-label="选择 WarDiag 任务"></div>
      <div id="wardiag-task-detail" class="wardiag-task-detail" role="region" aria-labelledby="wardiag-task-heading" aria-live="polite">
        <h3 id="wardiag-task-heading"></h3>
        <p id="wardiag-task-purpose" class="wardiag-task-purpose"></p>
        <dl class="wardiag-task-facts">
          <div><dt>题目输入</dt><dd id="wardiag-task-input"></dd></div>
          <div><dt>作答输出</dt><dd id="wardiag-task-output"></dd></div>
          <div><dt>评分依据</dt><dd id="wardiag-task-scoring"></dd></div>
        </dl>
        <details class="wardiag-example">
          <summary>查看合成结构示例</summary>
          <p class="small">以下内容由简化规则构造，用于说明任务结构，不是实际题库、平台原生裁决或测评成绩。</p>
          <div class="wardiag-example-columns">
            <div><h4>示例输入</h4><pre id="wardiag-example-input"></pre></div>
            <div><h4>示例输出</h4><pre id="wardiag-example-output"></pre></div>
          </div>
        </details>
      </div>
    </div>

    <div class="split wardiag-bottom">
      <div class="panel pane">
        <h2>题目怎样构建</h2>
        <ol class="wardiag-process">
          <li><strong>局势与规则</strong><span>确定共同母局面、观察权限与题给条件。</span></li>
          <li><strong>候选冻结</strong><span>在未来结果与价值计算之前固定完整候选。</span></li>
          <li><strong>执行与参考</strong><span>绑定行动结果、局面价值及有限博弈参考。</span></li>
          <li><strong>同母五题</strong><span>按每项任务的输入与输出要求组装题目。</span></li>
          <li><strong>独立评分</strong><span>分别记录综合策略质量和四项分项表现。</span></li>
        </ol>
      </div>
      <div class="panel pane">
        <h2>覆盖哪些决策难点</h2>
        <p>局面可以具有多个难点标签；具体机制仍需根据题目条件逐项核验。</p>
        <div class="chips wardiag-difficulties" aria-label="五类决策难点">
          <span class="chip">部分可观测</span><span class="chip">动态变化</span><span class="chip">大联合动作空间</span><span class="chip">长时域规划</span><span class="chip">随机效应</span>
        </div>
        <h3>如何读结果</h3>
        <p>综合题与分项分别报告，四项分数不直接相加。分项结果帮助观察能力差异，不能唯一确定模型内部错误的原因。</p>
        <p class="small wardiag-reference-note">当前提供五题生成、私有参考绑定与离线评分。策略结论限于题给候选和收益条件；代理价值参考的一致性不等于真实胜率已经得到验证。</p>
      </div>
    </div>`;

  const selector = document.getElementById('wardiag-task-selector');
  const fields = {
    heading: document.getElementById('wardiag-task-heading'),
    purpose: document.getElementById('wardiag-task-purpose'),
    input: document.getElementById('wardiag-task-input'),
    output: document.getElementById('wardiag-task-output'),
    scoring: document.getElementById('wardiag-task-scoring'),
    exampleInput: document.getElementById('wardiag-example-input'),
    exampleOutput: document.getElementById('wardiag-example-output')
  };
  const buttons = tasks.map((task, index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = task.short;
    button.setAttribute('aria-label', task.name);
    button.setAttribute('aria-controls', 'wardiag-task-detail');
    button.addEventListener('click', () => selectTask(index));
    selector.appendChild(button);
    return button;
  });

  function selectTask(index) {
    const task = tasks[index];
    fields.heading.textContent = task.name;
    for (const key of ['purpose', 'input', 'output', 'scoring', 'exampleInput', 'exampleOutput']) {
      fields[key].textContent = task[key];
    }
    buttons.forEach((button, buttonIndex) => {
      const selected = buttonIndex === index;
      button.classList.toggle('on', selected);
      button.setAttribute('aria-pressed', String(selected));
    });
  }

  selectTask(0);
})();
