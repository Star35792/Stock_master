---
doc_type: analysis
status: snapshot
as_of: 2026-11-18
note: 基于 2026-11-18 当前工作区的后端 OpenAPI 与前端挂载链路盘点；用于后续前端切片，不改变既有路线优先级。
---

# 前后端能力覆盖与差距分析

## 1. 结论

当前问题不是“前端整体无法调用后端”，而是**核心操作闭环覆盖较好，研究编排、数据治理和审计入口覆盖不足**。

- 后端当前暴露 81 个 HTTP 方法（按 `method + path` 计数，含 `/health`）。
- 从 `frontend/src/router.ts` 的已挂载路由出发，沿页面、组件、composable 到实际请求追踪，共有 49 个方法可被用户界面触达，占 60.5%。
- 另有 3 个方法已经写了前端请求封装，但没有任何挂载页面消费；它们不会因“存在 API 文件”而成为用户能力。
- 剩余 29 个方法没有前端调用。
- 49 个已接入方法覆盖了单次因子回测、价格价值研究、回测浏览与比较预览、组合草案、风险评估、人工确认、模拟执行、持仓账本和只读运维等主干；因此不能把现状概括为“前端不可用”。
- 用户仍会明显感到前端落后，主要因为页面按技术域分散，后端已经提供的研究聚合视图没有挂载，同时“质量”页只展示汇总、“回测”页只开放部分高级研究能力、“模拟盘”缺少审计读取入口。

最值得优先处理的不是为 32 个未接入方法逐一造页面，而是先把它们分为三类：

1. 必须进入日常用户流程的研究、质量与审计能力；
2. 适合在高级页面按需展开的证据与诊断能力；
3. 应继续保留为维护接口、无需出现在普通前端的注册和健康检查能力。

## 2. 盘点口径

本分析以 2026-11-18 当前未提交工作区为快照，代码优先于既有说明文档。

- 后端事实源：导入 `backend/app/main.py` 后生成的 FastAPI OpenAPI 路由表；统一前缀由 `backend/app/api/router.py` 注册。
- 前端事实源：`frontend/src/router.ts`、已挂载页面、其组件/composable，以及实际执行的 `fetch` 调用。
- “已接入”要求存在 `路由 -> 页面/组件 -> 调用函数 -> HTTP 请求` 的可达链路。仅有未使用的 API 封装不计为已接入。
- demo 数据不计为后端能力覆盖。运维接口全部不可用时，`operationsDashboard.ts` 会明确标记 `source=demo` 并提示当前显示演示数据。
- 本分析只判断能力是否进入用户界面，不判断每个后端方法都应有页面。
- 本轮没有启动后端服务或调度器，没有写业务库。

## 3. 端点覆盖总览

| 领域 | 后端方法数 | 已挂载页面可触达 | 仅有未使用客户端 | 无前端调用 | 判断 |
| --- | ---: | ---: | ---: | ---: | --- |
| 健康检查 | 1 | 0 | 0 | 1 | 可继续作为部署诊断，不必占普通导航 |
| 行情与参考数据 | 9 | 4 | 0 | 5 | 基础搜索和 K 线可用，生命周期、交易日历与公司行为证据缺失 |
| 数据质量与修复证据 | 4 | 0 | 0 | 4 | 当前“质量”页只有运维聚合摘要，没有质量处置流程 |
| 因子 | 8 | 2 | 0 | 6 | 能选定义、看画像；缺运行、数值、批量画像和池分类 |
| 研究与回测 | 29 | 16 | 2 | 11 | 单次研究较完整；聚合工作台、批量/滚动、严格验证未进入 UI |
| 组合、风控、模拟盘与持仓 | 25 | 22 | 1 | 2 | 覆盖最完整；主要缺审计报告和按 ID 恢复读取 |
| 运维 | 5 | 5 | 0 | 0 | 只读范围内完整，写侧重跑仍按路线明确暂缓 |
| **合计** | **81** | **49** | **3** | **29** | **覆盖率 60.5%，但各领域分布不均** |

这里的“行情与参考数据”包含证券、日线、生命周期、交易日历、公司行为和复权因子；“数据质量与修复证据”包含质量问题、跨源对账和人工修复记录。

## 4. 当前页面实际支持什么

| 页面 | 已支持的后端主能力 | 主要缺口 |
| --- | --- | --- |
| `/overview` | 运维概览、告警候选、失败摘要和详情 | 后端已有研究 workbench，但总览仍是运维总览；没有“最近回测 -> 草案 -> 持仓”的研究入口 |
| `/market` | 证券搜索、自选、本地可信日线摘要、K 线、成交量和明细 | 无证券生命周期、缺失日线、交易日历、公司行为和复权因子证据 |
| `/factors` | 因子定义、最近画像、显式方向、单次回测预检与运行、期末持仓 | 无因子运行/数值浏览、批量画像证据、池分类、批量回测和滚动研究 |
| `/price-value` | 最新初筛、冻结名单跟踪、收益状态、手工 FCFE 评估、价格价值假设回测 | 无已保存价格价值回测读取、策略目录、内置价值选股显式运行入口 |
| `/backtests` | 分页筛选、跨页选择、比较预览、同日净值/回撤曲线、链接恢复 | 无严格比较、合同漂移、验证报告和逐日持仓浏览 |
| `/backtests/runs/:runId` | 运行摘要、净值、成交、绩效计算/读取、基准、期末持仓 | 未使用后端研究 pipeline 聚合接口，页面仍并发拼接多个细粒度接口；无逐日 positions |
| `/portfolio` | 草案创建/读取、账本或回测预填、风险预算、就绪核对、净值快照、风险评估、人工确认、撤销和模拟执行 | 主闭环完整；直接按 execution ID 恢复的客户端存在但未使用 |
| `/positions` | 账本创建/编辑、流水记账/删除、持仓分析、草案发现、偏离检测 | 与真实持仓边界一致，未发现关键后端方法漏接 |
| `/paper` | 模拟账户、持仓、净值绩效及组合闭环 | 无 ledger audit 面板，无净值快照详情回读 |
| `/quality` | `operations/overview` 中的质量状态计数和最近记录 | 没有调用质量问题列表和状态更新接口，也没有跨源对账、人工修复、公司行为、复权证据 |
| `/operations` | 失败分类、单项详情、只读重跑计划 | 与 V6.1 只读边界一致；没有执行重跑是明确非目标，不是本轮缺陷 |

`/alerts` 和 `/failures` 只是重定向，不是独立页面。当前有 10 个顶层业务路由和 1 个回测详情路由，但没有研究首页路由。

## 5. 关键差距

### 5.1 研究主线缺少统一入口

后端已经新增：

- `GET /api/v1/research-workflow/workbench`：聚合最近回测、组合草案、持仓账本和告警；
- `GET /api/v1/research-workflow/pipeline`：按 `run_id` 聚合运行摘要、净值、绩效、基准和期末持仓。

前端也已有 `frontend/src/researchWorkflowApi.ts`，但 `fetchResearchWorkbench` 和 `fetchResearchPipeline` 在 `frontend/src` 中均没有调用者，路由和侧边栏也没有研究首页。结果是后端专门为减少前端拼装而提供的 BFF 能力完全没有形成用户价值。

这也是当前“后端较完善、前端跟不上”感受最强的来源：用户必须在因子、回测、组合、持仓页面之间自行寻找上下文；`useResearchStore` 只保存因子方向、最近 run ID 和基准，不能替代可恢复的服务端研究视图。

### 5.2 “质量”页名义与实际能力不一致

当前 `/quality` 只读取 `operations/overview` 内的计数和最近记录，没有调用：

- `GET /api/v1/data-quality/issues`；
- `PATCH /api/v1/data-quality/issues/{issue_id}/status`；
- `GET /api/v1/daily-bar-source-compares`；
- `GET /api/v1/manual-corrections`。

因此它是“质量概览”，不是“质量工作台”。用户无法筛选完整问题、更新处置状态，也无法在同一处查看跨源差异与人工修复审计。公司行为、复权因子和缺失日线同样没有前端证据入口。

### 5.3 V4 高级研究能力只停留在 API

前端因子页完成了单次预检和单次回测，回测页完成了列表、详情和探索性比较预览，但下列能力未进入页面：

- 批量因子选股回测；
- 滚动窗口生成与滚动回测；
- 严格回测比较、合同漂移和验证报告；
- 因子运行、因子值、批量画像摘要和因子池分类；
- 回测逐日持仓。

这会让研究者看到结果，却难以在前端完成“候选批量运行 -> 训练/测试窗口 -> 严格比较 -> 漂移诊断 -> 证据归档”的完整 V4 流程。

### 5.4 V5 主闭环完整，但审计可用性落后

组合、风控、确认、模拟执行、真实持仓账本和偏离检测是当前覆盖最好的领域。主要缺口集中在只读恢复与审计：

- `GET /api/v1/paper-accounts/{account_id}/ledger-audit` 没有页面入口；
- `GET /api/v1/paper-accounts/{account_id}/equity-snapshots/{snapshot_id}` 没有前端调用；
- `GET /api/v1/paper-executions/{execution_id}` 已有 `fetchPaperExecution` 封装，但没有页面调用。

当前可以按 approval ID 恢复执行结果，足以支撑主流程；但当用户需要解释现金差异、核对净值快照或只拿到 execution ID 时，仍要直接调用 API。

### 5.5 行情页缺少研究所需的时点和公司行为证据

行情页已经能消费可信日线，但没有展示证券上市/退市生命周期、开市日历、公司行为、复权因子和缺失日线。这不会阻断普通看图，却会削弱对回测时点、停牌、除权除息和缺口的解释能力。该类信息应作为按需展开的证据面板，不宜挤入默认行情主视图。

### 5.6 前端合同与文档存在漂移

- 前端请求/响应类型主要手工维护，没有从 OpenAPI 自动生成或做完整的契约覆盖检查；后端继续扩展时容易出现“接口已交付、客户端未发现”。
- `frontend/src/api.ts` 已经较大，同时 `MarketPage.vue`、`BacktestRunPage.vue`、`RunTablesSection.vue` 仍直接发请求；端点盘点必须跨多个位置，降低了缺口的可见性。
- 当前 `README.md` 的前端段落仍称 `WorkspaceRoutePage.vue` 承载 12 个工作区路由，但该文件已删除，当前路由实际指向独立页面。README 不能作为本次覆盖统计的事实源。

这些是可维护性问题，不代表当前请求已发生运行时契约错误。前端测试和类型构建当前均通过。

## 6. 建议实施顺序

以下顺序是本分析提出的候选切片，不自动改写 `roadmap.md` 的正式优先级。

### 切片 A：挂载研究工作台与单次研究 pipeline

目标：优先复用已完成的后端聚合接口和前端客户端，以最小后端改动形成连续研究入口。

- 新增“研究首页”路由和侧边栏入口，展示最近回测、草案、账本和告警；每个区块独立降级。
- 从最近回测进入 pipeline 视图，并提供回测详情、草案预填、持仓分析的明确跳转。
- 保留细粒度接口页面作为专业详情；聚合接口负责首屏和跨页上下文，不替代事实源。
- 不把 Pinia 临时状态当作可恢复事实，刷新后仍以服务端聚合结果为准。

验收：进入研究首页会实际调用 workbench；选择 run 会调用 pipeline；部分区块失败时其他区块仍可用；刷新和复制链接后仍能恢复 run 上下文。

### 切片 B：把质量概览升级为质量工作台

目标：先完成问题发现、筛选、证据查看和人工状态更新，不直接增加自动修复。

- 分页/筛选展示质量问题，明确严重度、状态、数据集、证券和交易日。
- 状态更新必须保留人工确认与后端审计语义；不得把 UI 操作包装成静默修复。
- 增加跨源对账和人工修复记录只读区；公司行为、复权因子、缺失日线作为证据抽屉或详情页。
- 运维重跑计划仍只读；在权限、审计、幂等和确认合同完成前不增加执行按钮。

验收：完整问题列表可分页/筛选；状态更新失败不会乐观伪成功；证据来源和时间可见；任何修复动作仍遵守 `raw -> market` 与审计规则。

### 切片 C：补齐 V4 高级研究流程

目标：把已有批量、滚动和验证接口组织成研究流程，而不是简单堆表单。

- 从单次因子回测请求派生批量候选，明确公共参数与候选差异。
- 提供滚动窗口预览，再执行滚动回测；训练窗、测试窗、选择策略和证据资格必须分区显示。
- 比较页区分探索性 `compare-preview` 与严格 `compare`，另设验证报告和漂移诊断。
- 因子证据页展示运行、值、批量画像身份和池分类，避免只看最近画像就误当 PIT 证据。

验收：页面不混淆预检、正式运行、探索比较和严格验证；失败项可定位；Decimal 请求值、版本、时间窗和风险声明原样保留。

### 切片 D：补齐 V5 审计与行情证据

目标：增强解释和恢复，不扩展到真实账户或自动交易。

- 模拟盘增加 ledger audit、净值快照详情和 execution ID 查询入口。
- 行情页按需展示生命周期、交易日历、公司行为、复权因子和缺失记录。
- 健康检查、`register-all` 等维护接口默认不进入普通导航；如确有需要，放入受控的开发/维护面板。

验收：审计只读、差异不自动修复；真实持仓与模拟盘明确分区；不存在券商连接或真实订单入口。

## 7. 未进入挂载页面的完整端点清单

### 7.1 已有客户端封装、但没有页面消费（3）

- `GET /api/v1/research-workflow/workbench`
- `GET /api/v1/research-workflow/pipeline`
- `GET /api/v1/paper-executions/{execution_id}`

### 7.2 没有前端调用（29）

基础诊断与行情证据：

- `GET /health`
- `GET /api/v1/securities/{symbol}/lifecycle`
- `GET /api/v1/daily-bars/missing`
- `GET /api/v1/trading-calendar`
- `GET /api/v1/corporate-actions`
- `GET /api/v1/adjustment-factors`

质量与修复审计：

- `GET /api/v1/data-quality/issues`
- `PATCH /api/v1/data-quality/issues/{issue_id}/status`
- `GET /api/v1/daily-bar-source-compares`
- `GET /api/v1/manual-corrections`

因子证据与维护：

- `POST /api/v1/factors/definitions/register-all`
- `POST /api/v1/factors/batch-profiles`
- `GET /api/v1/factors/batch-profiles/summary`
- `POST /api/v1/factors/pool-classifications`
- `GET /api/v1/factors/runs`
- `GET /api/v1/factors/values`

研究与回测：

- `POST /api/v1/backtests/runs/compare`
- `POST /api/v1/backtests/runs/drift`
- `POST /api/v1/backtests/runs/validation-report`
- `GET /api/v1/backtests/runs/{run_id}/positions`
- `GET /api/v1/strategy-research/definitions`
- `POST /api/v1/strategy-research/definitions/register-all`
- `POST /api/v1/strategy-research/value-investing/screen`
- `GET /api/v1/strategy-research/price-value/backtests/{run_id}`
- `POST /api/v1/strategy-research/rolling-windows`
- `POST /api/v1/strategy-research/factor-selection/backtests/batch`
- `POST /api/v1/strategy-research/rolling-backtests`

模拟盘审计：

- `GET /api/v1/paper-accounts/{account_id}/ledger-audit`
- `GET /api/v1/paper-accounts/{account_id}/equity-snapshots/{snapshot_id}`

## 8. 非目标与边界

- 不因前端补齐而引入券商连接、真实账户同步、真实订单或自动实盘交易。
- 不把所有维护接口都公开成普通用户按钮；`register-all`、健康检查等应继续受控。
- 不在本轮实现运维重跑写入口。V6.1 的只读重跑计划符合当前路线，不视为缺陷。
- 不用前端聚合结果替代后端不可变研究产物、组合草案、持仓账本或审计事实。
- 不因展示方便而把 Decimal 金融字段转成浮点后再提交。

## 9. 验证与已知限制

- OpenAPI 盘点：81 个 HTTP 方法；49 个由挂载页面实际触达，3 个仅有未使用客户端，29 个无前端调用。
- 前端：`bun test` 通过 52 项；`bun run build`（`vue-tsc --noEmit && vite build`）通过。
- 后端：`tests/test_api.py` 与 `tests/test_research_workflow_api.py` 共 68 项通过。
- 前端测试运行时出现 composable 被脱离组件直接执行导致的 `onBeforeUnmount` Vue 警告；未造成测试失败，但可作为测试夹具清理候选，不影响本分析结论。
- 后端测试有 Starlette/FastAPI 反弃用警告；与本轮前后端能力差距无直接关系。
- 本分析基于当前工作区快照；后续若路由、页面或客户端发生变化，应重新从 OpenAPI 和挂载链路生成覆盖表，不能把本数字当作长期常量。

## 10. 文档影响与评审结论

- 变更分级：T1。只新增现状分析与导航，不修改路线优先级、公共 API、schema、金融口径或用户工作流。
- 触发原因：用户要求核对前后端能力差距并形成文档。
- 受影响视角：产品经理（用户流程和切片优先级候选）、前端工程师（页面与调用链）、后端工程师（API 事实和聚合边界）；金融边界仅做不越界核对。
- P0/P1 阻断：无。本分析不授权实现候选切片，也不改变既有阶段门。
- 文档同步：新增本快照并加入 `aiDoc/README.md` Reference Map；不改 `roadmap.md`，因为没有正式改变阶段状态或优先级。
