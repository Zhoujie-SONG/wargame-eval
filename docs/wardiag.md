# WarDiag 题目与 Demo

从侧栏“项目展示 → WarDiag 兵棋决策”进入，或打开 `/#wardiag`。页面沿用站内卡片、表格和蓝灰配色，将原数据概览与题库阅读精简合并，底部接入智能体 Demo。

页面包含实际题目数量、难点筛选、母局势切换、同母五题阅读，以及 Demo 答题流程、官方模板和智能体测评区。题目数量从当前阅读材料计算；没有材料时显示空状态。

## 接入已有材料

将现有 WarDiag 阅读页导出的 HTML 放在 `data/wardiag/reader.html`，将已经生成的官方 Demo ZIP 放在 `data/wardiag/demo.zip`，然后按主 README 启动网站。

也可通过环境变量指定已有文件，无须复制原始题包：

| 环境变量 | 用途 | 默认值 |
| --- | --- | --- |
| `WARGAME_WARDIAG_READER` | 已导出的阅读 HTML 或对应 JSON | `data/wardiag/reader.html` |
| `WARGAME_WARDIAG_DEMO` | 已生成的官方 Demo ZIP | `data/wardiag/demo.zip` |
| `WARGAME_WARDIAG_SERVICE_URL` | 本机已有的智能体测评服务 | `http://127.0.0.1:8766` |

阅读适配只提取允许展示的题文和元信息，默认不返回答案、解析、来源定位或私有参考。题目文件与 ZIP 保存在本机，`data/wardiag/` 已加入 Git 忽略，Docker 构建也排除 `data/`。

## 智能体测评

Demo 展示模型读题、调用工具并提交答案的过程。上传、接口自检、模型选择和任务执行复用已有 WarDiag 测评服务，由当前站点提供同源接口。执行环境和服务状态按实际响应显示；模型请求仅在用户启动测评后执行。

测评服务尚未运行时，题目阅读与本地模板下载仍可使用。此页不自行生成题目、裁决局势或改写原有评分参考。
