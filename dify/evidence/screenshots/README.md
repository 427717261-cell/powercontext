# 界面截图

本目录是「深夜电台 · 怪谈档案」的界面截图佐证，对应 `VERIFICATION.md` 的两组验证样例。
截图全部来自**实际运行的 Dify 1.4.2 实例**（`http://<host>:<EXPOSE_NGINX_PORT>/chat/<access_code>` 的 WebApp
对话界面，以及 `http://<host>:<EXPOSE_NGINX_PORT>/apps` 的 Dify 控制台），没有后期拼图。

> 截图里控制台地址是 `:8080`，那是我们验证机上 `EXPOSE_NGINX_PORT` 的取值；
> `make_dify_env.py` 生成的默认值是 **80**，评审按自己的 `.env` 访问即可。

| 文件 | 内容 | 对应验证点 |
| --- | --- | --- |
| `01_开场表单_听众代号.jpg` | WebApp 的「New chat setup」开场表单：**听众代号**输入框与「Start Chat」按钮 | Chatflow 的会话开场变量（`listener_id`）就是听众代号，填什么就以什么身份建 Scope |
| `02_开场白与推荐问题.jpg` | 电台开播的开场白与三个推荐问题（想听末班地铁的怪谈 / 讲讲凌晨三点的便利店 / 我今天特别累） | 玩法入口：听众可以直接点推荐问题开始 |
| `03_首轮播报_卷宗卡片.jpg` | 一轮完整播报：主播口播 + 结尾「📻 今晚翻出的卷宗」卡片，标注**命中通道:fts** | seekdb 检索结果进入对话，并标明走的是哪条通道 |
| `04_WorkflowProcess面板.jpg` | 对话下方可展开的 **Workflow Process** 面板 | 站点开启了 `show_workflow_steps`，Dify 的执行过程对终端用户可见 |
| `05_移动端对话界面.png` | 窄屏（移动端宽度）下的同一界面 | WebApp 响应式可用 |
| `06_跨会话记忆_第二次到访_混合检索.png` | **同一听众代号「123」的新会话**：用户问「我回来了，你还记得我吗？」，主播回答「嘶——记得，123。你第二次来了。上次城南那家凌晨三点还亮着灯的便利店，我还记着：穿校服的店员，以及那串永远热着的关东煮。」；结尾卷宗卡片同时出现 `命中通道:fts` 与 `命中通道:vector` | **跨会话记忆**：本轮 `http_recall`（召回听众记忆）取回了上一场会话写入的记忆，跨越了两条独立的 conversation；同一张图里也证明混合检索两条通道同现 |
| `07_WorkflowProcess_11节点全绿.png` | 展开后的 **Workflow Process** 面板，11 个节点全部成功：开始 → 组装请求体 → 建立听众档案（POWERCONTEXT）→ 解析 SCOPE_ID → **召回听众记忆（POWERCONTEXT）** → 整理记忆文本 → **检索怪谈档案（SEEKDB）** → 整理本轮素材 → 记住这一轮（POWERCONTEXT）→ 深夜主播·阿雾 → 播出 | 整条编排链路在真实对话里逐节点成功，PowerContext 召回/写回与 seekdb 检索各占一个独立节点 |
| `08_控制台_登录页.jpg` | Dify 控制台登录页（`http://<host>:8080/signin`） | 佐证控制台是**独立部署的真实 Dify 1.4.2**，不是模拟页面 |
| `09_控制台_应用列表.jpg` | 工作室的应用列表：应用「深夜电台 · 怪谈档案」，说明栏「Dify 编排 + seekdb 怪谈档案检索 + PowerContext 听众记忆」，类型 CHATFLOW | 作品确实以 Chatflow 形式存在于控制台 |
| `11_控制台_运行日志.jpg` | 控制台「日志与标注」页：逐条会话记录，带 **SUCCESS / FAILURE** 状态、消息数、用户与创建时间 | 真实运行历史；其中 FAILURE 的两条是额度触顶时的 LLM 节点失败（见下） |
| `13_控制台_会话变量_conversation_var.png` | 控制台编排页右侧的**会话变量**抽屉：`conversation_var`（String），**WRITE** 来源于「变量赋值」节点、**READ** 进入「LLM」节点 | 记忆与本轮素材是怎么被拼进提示词交给主播的——会话变量把「召回的听众记忆」这类上下文显式喂给 LLM |

## 两组对照关系

- `03` 与 `06`：**同一听众代号「123」的第一次 / 第二次到访**。
  `03` 里主播说「欢迎第一次来到」；`06` 里主播说「你第二次来了」并复述了上次便利店那档怪谈。
  两次在 Dify 里是两条**不同的 conversation**，记忆由 PowerContext 按 Scope 承接——
  这正是「记忆存在 PowerContext 里，而不在 Dify 会话上下文里」的直观证明。
- `04` 与 `07`：同一块 Workflow Process 面板的折叠态与展开态，展开后能逐节点看到
  PowerContext 召回、seekdb 检索各占一个节点，且全部成功。

## 已知的两点说明

1. 卷宗卡片里出现 `命中通道:fts` 与 `命中通道:vector`，说明同一次检索同时走了
   seekdb 的**全文**与**向量**两条通道；两条通道的原始执行计划见
   `../07_seekdb_explain.txt`（`TEXT RETRIEVAL SCAN` 与 `VECTOR INDEX ADAPTIVE SCAN` 同现）。
2. `11_控制台_运行日志.jpg` 里出现的两条 **FAILURE** 是 MiniMax Token Plan 额度触顶时
   LLM 节点失败留下的记录，属于**真实运行痕迹**、不是伪造的成功日志；额度恢复后同样的
   输入即可成功（见 `06`、`07`）。

> 界面截图只是佐证材料，**可运行的应用与完整复现步骤**以
> [`../README.md`](../../README.md) 和 [`../VERIFICATION.md`](../../VERIFICATION.md) 为准；
> 每个节点的输入/输出原文在 `../01_dify_node_executions.json`（22 条记录）。
