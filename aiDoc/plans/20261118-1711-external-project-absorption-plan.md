---
doc_type: plan
status: partially_superseded
as_of: 2026-11-18
superseded_by: research/20261118-2036-training-ic-statistical-diagnostics.md
note: >-
  Hikyuu、New Tea Quant、TickFlow 两轮本地源码扫描的吸收方案与证据快照。A1、A2、B 首片及 C 训练窗统计诊断首片已交付；当前状态见取代文档及其引用。其余候选、准入条件和来源证据继续有效，不改变事实源或策略资格。真实公司行为仍 reject，候选内部顺序不替代 roadmap 的正式近期优先级。
---

# 三个本地参考项目的能力吸收方案

## 1. 目标与本次交付

把 `hikyuu-master/`、`new-tea-quant-master/`、`tickflow-stock-panel-main/` 中有价值的代码、研究方法、领域建模和交互机制，映射到 Stock Master 的既有能力与缺口，形成可以分批验收的实施清单。

本次交付是**方案和扫描证据归档**。所有切片状态均为“待实施”，不执行源码迁移、数据采集、数据库升级、策略重新评级或 Git 提交。当前阶段与正式近期顺序仍以 [roadmap](roadmap.md) 为准；本文在既有“可信基线 → V4 研究 → V5 持仓 → V6 运维”内提供候选。

用户已在本次会话声明对三个项目拥有所有权，要求不把许可证核查作为本次阻断项。迁移仍记录来源路径、文件摘要和改写理由，供代码与金融口径追溯。

采用“保留主项目底座、按模块吸收”的方案：与整体替换引擎或并行运行第二套研究平台相比，该方案可以复用现有研究合同、三库职责和 Vue 工作台，控制长期兼容成本。允许在隔离内存样本上对照外部算法；外部项目本身不成为生产数据源或运行时依赖。

## 2. 现状基线与证据边界

| 领域 | 本项目已具备 | 需要补齐或专项核验 |
| --- | --- | --- |
| 数据事实源 | 主库 `stock_master.duckdb`、研究库 `research.duckdb`、手工账本 `positions.db`、有效 Parquet release 链 | 不另建外部项目的行情库、研究 JSON 事实库或宽表副本 |
| 时点与精度 | [StrategyFactorFrame](../../backend/app/strategies/inputs.py) 拒绝未来/重复/非有限因子；Decimal 数学、输入切片摘要 | 观测日期正确不等于公告首次可得时间与修订版本已认证；尚缺通用信号前缀不变性审计 |
| 公司行为 | 可信事件模型、证据采集、回测事件拒绝政策 | [BacktestService](../../backend/app/services/backtest.py) 的政策为 `reject`，现金与持仓事件会计未交付 |
| 研究 | 单次/批量回测、比较、滚动执行、不可变训练产物、`training_ic_v1` | 参数敏感性、完整训练产物验证、晋级/守护闭环；`training_artifact_verified=false` 仍有效 |
| 因子处理 | [横截面服务](../../backend/app/services/factor_cross_section.py) 已有去极值、标准化、平均秩、行业/市值中性化 | 统计可信度、训练内相关去重、条件稳定性；不重复迁移已有变换 |
| 成本与组合 | [费用模型](../../backend/app/backtest/fees.py)、组合约束、风险预算、人工确认及 paper 幂等执行 | 分项费用已存在，股票/ETF 日期感知费用 v2 有适用范围，不能一概称“缺费用模型” |
| 用户流程 | 研究首页与 pipeline、回测列表/详情/比较、组合草案、持仓和运维工作台 | 跨页个股上下文、信号现场、研究实验记录与高级研究入口 |
| 任务 | [进程租约](../../backend/app/services/job_lease.py)、数据库写锁、故障状态与受控运维动作 | 内存预算、重任务有界排队、计算与结果的按需生成 |

证据来自 CodeGraph 的当前磁盘源码、必要的本地文档/依赖清单与第 6 节的两个隔离算术检查。扫描不是全量逐行审计；没有启动三个项目、安装其依赖、运行其完整测试或读取生产数据库。CodeGraph 发现测试关联不等于测试通过。本文不采用 README 中的速度、内存和测试数量宣传值作为本项目验收指标。

三个参考目录当前未纳入主仓库版本管理，不能作为永久规范。第 9 节固定关键文件 SHA-256；其他条目只保留定位线索，实施前须补齐对应文件摘要、调用依赖和测试。目录删除或源码变化后，摘要用于说明当时看过的版本，不能据此重建源文件。

## 3. 完整候选台账

方式：“适配抽取”表示可提取小范围代码和用例，**仍未取得直接复制即合格的结论**；“按合同重写”表示保留思想/场景，以本项目数据、精度和版本合同实现；“交互借鉴”保持 Vue 3；“延后”保留线索，未承诺建设。A–H 对应第 4 节切片。

| ID | 候选与来源定位 | 本项目增量价值与吸收方式 | 切片 |
| --- | --- | --- | --- |
| C01 | Hikyuu [updateWithWeight](../../hikyuu-master/hikyuu_cpp/hikyuu/trade_manage/TradeManager.cpp) | 分红/送转/缩股的事件模型、持仓现金重放用例；按合同重写，先完成日期与血缘证据 | A2 |
| C02 | TSP [audit_matrix / audit_frame](../../tickflow-stock-panel-main/backend/app/backtest/evidence.py) | 截断/扰动未来输入、重算过去信号，增加行为审计；适配抽取 | A1 |
| C03 | Hikyuu 数值空值契约、NTQ [HfqRoi](../../new-tea-quant-master/core/modules/strategy/core/engines/shared/services/hfq_roi/hfq_roi.py) 反例 | 统一缺失、合法零、非有限值、窗口未满的语义；复用现有 Decimal 防线，补边界样本 | A1 |
| D01 | NTQ [BaseTimeSeriesContract.until](../../new-tea-quant-master/core/modules/data_contract/core/base/base_time_series_contract.py) | 统一按可得时点取数据的接口；扩展已有不可变输入合同，不导入其 loader/ORM | G |
| D02 | TSP [capabilities](../../tickflow-stock-panel-main/backend/app/data_providers/capabilities.py) | 按数据集声明源能力、单位、可用状态与历史覆盖；不能凭接口可用授予可信资格 | G |
| D03 | NTQ [TagCalcWindowResolver](../../new-tea-quant-master/core/modules/tag/core/engines/shared/calc_window.py) | 带日期的类别标签与稀疏计算水位；“最后算到日期”独立于“最后产出日期” | E/G |
| R01 | TSP [stats_v2](../../tickflow-stock-panel-main/backend/app/backtest/stats_v2.py) | Newey–West、FDR、Deflated Sharpe 等研究诊断；核对假设后按精度合同适配 | C |
| R02 | TSP [compute_rank_correlation / prune_correlated_factors](../../tickflow-stock-panel-main/backend/app/backtest/mining.py) | 仅训练窗内按共同有效样本计算相关、确定性去重并解释代表关系 | C |
| R03 | TSP [generate_nested_folds](../../tickflow-stock-panel-main/backend/app/backtest/mining.py) | 内层选参、外层评价、purge/embargo 和逐折缺口；按交易日合同重写 | C |
| R04 | NTQ [sweeps](../../new-tea-quant-master/core/modules/strategy/core/engines/analyzer/steps/campaign/summarize/sweeps.py)、[joint_sweeps](../../new-tea-quant-master/core/modules/strategy/core/engines/analyzer/steps/campaign/summarize/joint_sweeps.py)，TSP [robust_optimizer](../../tickflow-stock-panel-main/backend/app/backtest/robust_optimizer.py) | 单轴敏感性、联合热力图、分段稳健分、冠军收敛与高原；适配抽取，缺段政策须重定 | B |
| R05 | TSP [guardian](../../tickflow-stock-panel-main/backend/app/backtest/guardian.py) | 冻结基线、新增数据复验、观察/退役记录；按本项目研究资格重写，不自动触发持仓操作 | D |
| R06 | TSP [strategy-iteration](../../tickflow-stock-panel-main/docs/strategy-iteration.md) | 假设、预注册标准、负知识、停止规则与留出集使用记录；先记录流程，专用 UI 属后续设计 | B/D |
| R07 | NTQ [四层研究流程](../../new-tea-quant-master/README.md) | 机会覆盖 → 单股路径 → 资金组合 → 人工回放，分别解释每层证据；复用已有研究流程 | E |
| R08 | TSP [regime_alignment](../../tickflow-stock-panel-main/backend/app/backtest/regime_alignment.py) | 按市场环境诊断因子/策略；环境只在可得后生效，历史标签随版本冻结 | C/G |
| P01 | NTQ [StrategyContext.capture](../../new-tea-quant-master/core/modules/strategy/core/hooks/hook_params/strategy_context.py) | 记录信号当时的少量特征、排名和理由，避免用今天的数据解释旧决策 | E |
| P02 | NTQ [GoalSettings](../../new-tea-quant-master/core/modules/strategy/core/engines/shared/services/strategy_settings/goal_settings.py) | 分批退出、保护/移动止损、到期退出；先建情景推演政策，单独评审金融语义 | F |
| P03 | NTQ [DecisionEngine](../../new-tea-quant-master/core/modules/strategy/core/engines/decision_maker/engine.py) | 逐日推进、选择/笔记、恢复会话及人工规则对照；重写交互与状态机 | F |
| P04 | Hikyuu [TradeSuggestion](../../hikyuu-master/hikyuu_cpp/hikyuu/trade_sys/system/TradeSuggestion.h)、[AllocateFundsBase](../../hikyuu-master/hikyuu_cpp/hikyuu/trade_sys/allocatefunds/AllocateFundsBase.h) | 多策略观点先汇总为组合目标，保留来源与冲突；单策略合同稳定后再实现 | F/H |
| P05 | Hikyuu [Performance](../../hikyuu-master/hikyuu_cpp/hikyuu/trade_manage/Performance.cpp) | 成交生命周期、持有时长、连续亏损、空仓时间和 R 倍数；先明确配对与风险分母 | F |
| P06 | Hikyuu [ICIRMultiFactor](../../hikyuu-master/hikyuu_cpp/hikyuu/trade_sys/multifactor/imp/ICIRMultiFactor.cpp) | IC/IR 驱动权重作为等权以外的候选；标签成熟后才可用于下一决策，不照搬滚动权重 | H |
| E01 | NTQ [EntityPlanner](../../new-tea-quant-master/core/modules/backtest_engine/core/schedule/entity_based/planner.py)/[SlicePlanner](../../new-tea-quant-master/core/modules/backtest_engine/core/schedule/slice_based/planner.py)，TSP [memory_budget](../../tickflow-stock-panel-main/backend/app/services/memory_budget.py)/[HeavyJobLimiter](../../tickflow-stock-panel-main/backend/app/services/heavy_job_limiter.py) | 探针估算、读算分离、有界队列、内存准入、取消与进程隔离；按本机运行形态适配 | G |
| E02 | TSP [ResolvedFeaturePlan / BacktestResultPolicy](../../tickflow-stock-panel-main/backend/app/backtest/strategy.py) | 只读所需列和预热帧，搜索阶段只生成必需指标，最终选定运行再完整产出 | B/G |
| U01 | TSP [StockPreviewHost](../../tickflow-stock-panel-main/frontend/src/components/StockPreviewHost.tsx) | 从名单、成交、持仓共享个股详情和来源导航；Vue 交互借鉴 | E |
| U02 | TSP [MiniCandlestick](../../tickflow-stock-panel-main/frontend/src/components/stock-table/MiniCandlestick.tsx)、虚拟列表 | 列表迷你图、可见行批量加载、稳定引用减少重绘；先测本项目实际列表压力 | E/H |
| U03 | TSP [Watchlist](../../tickflow-stock-panel-main/frontend/src/pages/Watchlist.tsx)、[分组统计](../../tickflow-stock-panel-main/frontend/src/lib/watchlistGroupStats.ts) | 本地自选升级服务端偏好、分组和可配置列；不把等权分组统计冒充持仓收益 | H |
| U04 | TSP [Dashboard](../../tickflow-stock-panel-main/frontend/src/pages/Dashboard.tsx) | 可配置布局、显隐和按需请求；按真实使用需求延后，不增加默认首页复杂度 | H |
| X01 | TSP [compile_formula](../../tickflow-stock-panel-main/backend/app/factors/dsl.py) | 有限公式、错误位置、依赖与预热推导；先定义受控语法和复杂度预算再接计算 | H |
| X02 | TSP [ExtensionBoundary](../../tickflow-stock-panel-main/frontend/src/extensions/ExtensionBoundary.tsx)、[扩展合同](../../tickflow-stock-panel-main/backend/app/extensions/contracts.py) | 页面故障隔离、核心拥有语义、外围只消费合同；先借模块边界，运行时插件延后 | H |
| X03 | TSP [开放平台设计](../../tickflow-stock-panel-main/docs/open-platform-plan.md)与 AI 工具足迹/确认卡 | 可追溯查询、API 契约检查和薄 MCP 桥的候选；单用户当前无需求时不引入网关/Token 系统 | H |
| X04 | Hikyuu [指标概览](../../hikyuu-master/docs/zh/indicator/overview.rst)与部件化系统 | 指标公式、预热/空值用例、一次替换一部件的实验思想；按已提出的研究假设逐项移植 | H |

## 4. 分批实施方案

以下顺序只约束本吸收方案内部。A2 的生产数据门未通过时，继续维护现有拒绝政策，A1/B/E 的隔离样本和只读工作可以独立推进；不以数据补齐为由无限阻塞所有改进。

### A1：信号行为审计与缺失语义（首个可编码切片）

- 输入：固定可信内存样本、版本化策略/因子函数、显式审计起止日和截止点；复用 `StrategyFactorFrame`，不另建 loader。
- 输出：有界 `AuditReport`，逐项记录截断/扰动/重复输入检查、比较数量、差异示例及 `passed/failed/insufficient/error`；初版内存返回，不新增数据库表或公共 API。
- 方法：先固定随机种子与状态；对未来 OHLCV、可得时间和证券池变化分别建样本，只比较截止点及以前已生效信号。回调须从被截断/扰动的可信输入重新执行受审范围内的预处理、因子与信号；仅裁剪预先算好的 frame 不能检查因子上游泄漏。报告标注审计起点和实际覆盖链路。重复同输入不一致时先报告非确定性，不能归因为未来函数。
- 验收：正常滞后策略通过；故意 `shift(-1)` 或全样本归一化策略失败；只读历史之外的数据扰动不改旧信号；空输入、零方差、缺价、未满窗口、NaN/Inf 均不转成合法零。抽样通过仅说明所测切片，不能授予全量 PIT 资格。
- 前置与回退：不依赖生产库写入；关闭诊断即可恢复原运行路径。完成代码与金融独立复核后，才讨论接入预检或研究详情。

### A2：公司行为合同与最小事件会计

- 第一交付先做事件证据矩阵与合成账本：事件 ID/修订、公告和首次可得时刻、登记日/除权日/到账日、数量与现金处理顺序、税费假设、可卖数量、取整、源版本。
- 首个运行能力限定为**现金分红**，仅对已验证持仓资格与完整日期证据的事件启用新政策版本；除息日应收与到账日可用现金分开，不能提前把应收用于买入。不使用 hfq 比例模拟现金入账。合同明确股息字段的税前/税后含义、税费计提/支付时点和期末未到账应收的权益处理；必要税务证据不足时只能给出版本化情景，不声称真实税后净收益。
- 送转/拆合股、配股、复杂税务和退市结算分别做后续合同，不用一个通用倍率隐式覆盖。未支持事件、冲突日期、关键证据缺失继续 fail-closed，旧 `reject` 合同与历史运行不改写。
- 验收：登记日前后买卖资格、除息与到账跨日、期间清仓仍有应收、同日买卖顺序、重复事件幂等、修订隔离、期初持仓来源和现金/应收/权益守恒；明确外部资金流与投资收益的区别。
- 落地限制：只扩展研究回测，第一片不改变 paper 与手工账本。真实数据应用须先通过可信事件与必要持仓历史核验，再做新旧版本对照；有代码不等于数据门已通过。

### B：参数敏感性、比较报告与实验记录

- 先消费既有 run 和已落地绩效，声明基线、扫描轴、候选集合和窗口；不因只读查询补算/回写绩效，不默默丢弃失败候选。
- 同一窗口内核对可信行情/日历/公司行为切片、因子来源与证券池、代码、费用/滑点/执行政策、全部非扫描参数；允许已声明轴不同。`data_version` 可能包含策略输入摘要，不能以整体哈希不同一概拒绝；缺组成摘要的旧运行只能展示探索性对照，不能宣称受控敏感性。
- 跨窗口按“候选 × fold”矩阵比较，窗口内数据一致、窗口间日期和数据摘要可不同；缺候选或缺 fold 单独列出。单轴曲线与多轴交互分开，缺网格单元不插值成实测收益。
- 输出敏感度、联合热力图、分段中位/最差/离散程度、冠军收敛和邻域高原。`0.6/0.4`、邻域容差等只作为待验证的版本化研究政策，不直接设为资格门槛。
- 缺失/失败 fold 默认使稳健排名“不完整/不可排名”，不能过滤缺失后把剩余高分当完整成绩；如未来引入覆盖惩罚，须另发政策版本。0 分是有效值，不用真值判断变成负无穷。
- 实验记录绑定 run IDs、代码/数据身份、假设、预注册接受/停止标准、扫描轴、结果、负知识及留出集访问历史；第一片只读对照与可导出摘要，不新建策略发布系统。
- 验收：同合同单轴、同时改多轴、不同股票池/费用、代码不同、混合因子来源、缺绩效/缺窗/0 分和边界网格；确定性输出与排序。展示接现有回测比较区，公共接口与持久化若需变化另做切片评审。

### C：统计可信度、训练内去重与嵌套验证

- 先在既有 `rank_ic`/训练产物上增加可选诊断；FDR 明确同一检验族与所有尝试，HAC 明确滞后数及缺失日期政策，Sharpe 校正明确采样频率、偏度/峰度约定和有效试验数假设。不能把近似诊断概率解释成“策略未来盈利概率”。
- 相关性只用当前训练窗与共同有效样本，记录配对样本数/日期数；不把未知相关当零。去重保留代表因子、门槛、剔除原因和确定性决胜规则。
- 内层学习方向、去重、选参数，外层测试一次评价；purge 覆盖收益标签跨界，embargo 是否足够取决于实际训练样本集合，不因记录了日期字段就自动成立。重叠外层测试不按独立样本累加或复利。
- 只要用外层成绩选冠军、调权重或看过后继续改方案，该数据即成为选择证据；最终资格必须保留另一个未参与选择的留出集或此后前向观察。TSP 的外层测试权重精调不能连同“最终独立 OOS”称谓照搬。
- 市场环境/标签只在真实可得后使用，不能用全样本事后划分行情后反推历史入场；先做条件诊断，再评审是否进入策略。
- 验收：标签跨界、日期缺口、极端缺失掩码、全同值、零方差、训练外值篡改、窗口重叠、预算耗尽、候选不足和多次查看留出集。以独立慢参考或已知解核对公式，所有政策/样本集合与尝试计数进入产物身份。

### D：基线、challenger 与持续复验

- 依赖 B 的身份与缺失政策、C 的验证协议；先建立人工可解释的研究状态，不把外部 `healthy/watch/retired` 直接映射为本项目 `core` 或可执行资格。
- 基线固定策略/参数/代码、数据截止点、证据资格和接受标准；尾随参考、参与挑选的样本、独立留出集、前向观察分别标记。
- 新数据不足显示等待，数据缺口显示证据不完整；策略退化与数据异常分流。复验不覆盖基线，不删除旧产物，不重用已消费的留出集反复选参。
- 20%/50% 退化、30 日新样本等外部默认值均需专项验证；不得因复制配置就成为默认门槛。状态只影响研究目录与人工复核队列。
- 验收：基线冻结、重复复验幂等、证据版本变化、负/零基线、缺段、调参后重新建档和可审计撤回。`training_artifact_verified=false` 不因本功能存在而自动转 true。

### E：研究解释与跨页面上下文

- 优先扩展已有研究首页、pipeline、回测详情与持仓页面：名单机会覆盖、单股价格路径、组合净收益和人工推演分别呈现证据边界，不强行增加四套引擎。
- 信号现场只保留白名单特征、日期/可得时刻、排名、规则版本和引用 ID；与运行不可变绑定。缺历史现场时显示未记录，不从当前因子回填。
- 统一 Vue 个股详情宿主，携带来源列表、选中项、研究 run 和观察日期；回测成交位置与当时信号可切换，跨页/关闭时取消过期请求。迷你图和虚拟列表只在实际容量测试表明需要时增加。
- 验收：名单→个股→成交→持仓往返上下文正确，历史日不会被最新数据替换；部分接口失败/空数据/过期响应不串标的；有键盘操作和可见错误状态。现场存储如需新增表，先提出容量与血缘设计。

### F：退出情景、人工回放与组合意图

- 顺序：定义版本化退出政策与单笔生命周期 → 受控合成回放 → 人工决策与笔记 → 多策略意图合并。先复用现有 PortfolioBuilder、费用、可交易性与人工确认。
- 政策明确触发价格、可得时间、下一可成交时点、当根多阈值冲突、缺价、涨跌停、T+1、初始/剩余仓位比例与手数取整；不以研究止损价保证必然成交。
- 回放仅显示当时已可得信息；跳日、恢复和重复确认保持幂等。是否采用已知完整历史产物构建回放须通过逐字段泄漏审计，不能暴露未来退出日期/收益。
- 生命周期先固定成交配对（如 FIFO/平均成本，最终选择须专项决策）、未平仓定义、公司行为、现金流和 R 倍数风险分母；未知初始风险不补零。
- 多策略先合并目标与冲突解释，再执行同源组合约束；保留 gross/net 观点与策略贡献，不引入多个竞争的权威账本。
- 验收：分批卖出、同日冲突、跨期公司行为、零/缺风险分母、重启恢复、同标的相反观点、预算不足；第一实现仍限本地研究/模拟，不扩大到真实账户。

### G：数据消费与计算效率

实施时拆成 G1 数据消费/源能力/水位与 G2 资源效率两个任务，分别验收，避免一次跨越所有边界。

- 分两步：先做字段/预热/结果依赖计划与内存观测，再按观测决定批量宽度、缓存和并发。不能用“完整矩阵常驻”换速度而忽略峰值复制成本。
- `as_of` 门面在现有 repository 与 `StrategyFactorFrame` 之上统一有效时间、公告/首次可得时间、修订版本和发布 ID；消费层不能自行绕过 `raw → market`。
- 数据源能力表分别记录声明支持、凭据可用、历史覆盖、单位/复权口径、质量资格；仅声明可用不能自动故障切换或发布可信数据。TSP 的源直连路径不迁入研究消费链。
- 标签区分可审计事实、人工当前判断和算法派生；人工当下判断不能回填成历史事实。增量水位在数据与结果提交成功后推进，空结果可以记录已计算区间，失败不能越过；refresh、recompute、dry-run 分别定义。
- 资源控制先验证 Windows 本机形态；Linux cgroup 探测按需启用。新进程只接受序列化配置与只读输入/小结果，不能继承或竞写 DuckDB 引擎；主库/研究库写入仍走对应写锁与现有租约。
- G2 编码前固定任务状态的权威主调度进程与研究结果的单写入者，子进程只计算；IPC 明确消息大小上限和超限处理。结果绑定任务/尝试标识，取消或失败后的晚到结果不能覆盖终态或写成成功产物。
- 有界队列、超时、取消、任务状态、缓存 key（release/代码/参数/日期）、失效与清理一并验收。写锁不等于内存配额，进程内 limiter 不声称跨进程互斥。
- 验收：有限合成数据量梯度、峰值 RSS/耗时/产物大小、预算不足、探针失败、取消/崩溃/重启、输入未变缓存命中与版本改变失效；性能改写前后语义与数值摘要一致。阈值由测量预注册，不照搬外部机器基准。

### H：明确延后的扩展项

保留 X01–X04、P06、U03/U04 及大规模策略组合的线索。启动条件是已存在具体用户流程或研究假设、前述证据门不再阻断、最小实现成本可说明。

- DSL 先做有限表达式、类型/窗口/依赖/复杂度约束与可重复计算；AST 检查不能当完整安全沙箱，不支持任意 Python 自动执行。
- 指标只按需求移植公式与边界测试；IC/ICIR 动态权重须等待 forward label 完整成熟，时点隔离后与固定等权做独立对照。
- 先模块和页面故障隔离，再考虑运行时插件；当前不迁入 C++ 构建链、React/Tailwind、第二套 ORM 或 PostgreSQL/Redis/MinIO。
- Open API/MCP、AI 助手、服务端自选和可配置看板均按用户需求另定切片；写操作复用现有预检/确认/执行/审计，不能建立绕过门禁的第二条动作通道。

## 5. 依赖、首片和事实存放

| 批次 | 交付与依赖 | 完成标准 |
| --- | --- | --- |
| 第一批 | A1；A2 先合同/样本，可独立推进 | 防未来函数反例可被检出；公司行为支持/拒绝矩阵与守恒样本清楚，不解除生产拒绝政策 |
| 第二批 | B；E 的个股上下文可并行 | 同窗扫描轴与固定条件可核验；失败候选可见；已有页面形成解释闭环 |
| 第三批 | C → D | 训练/验证/留出集合和尝试历史可追溯；研究状态不冒充已认证收益 |
| 按需批次 | F；G 在性能或数据消费问题实际出现时优先 | 金融合同与资源预算分别验收；H 保留为后续候选 |

推荐第一个实现任务为 **A1 内存审计器及合成反例**。其验证成本可控，能为后续移植算法提供检查工具；A2 数据证据盘点同时推进。本文不排日期与工期，不以未经测量的代码量估时。

事实存放沿用：来源证据先入主库 raw，发布后的可信行情/事件与因子按既有主库和温层职责消费；研究 run、训练/比较/诊断/观察的权威产物归研究库与已评审的温层研究产物；组合/风险/paper 控制事实归主库；人工真实持仓只归 `positions.db`。标签与自选偏好的最终表结构、公共 API 路由、研究分析持久化结构留给对应切片评审，本文的名称不是已存在的表或接口。

每片先记录父运行/发布、代码/政策/参数身份、输入摘要、时间范围、覆盖和缺失、输出状态及原因；不向旧 run 追加改变解释的诊断事实。新增持久诊断应是独立分析版本，旧运行无法还原输入时保留“不可核验”。首个 A1 不落盘；以后确需缓存/导出/测试产物时，先固定受控目录、数量/大小预算、引用和清理时点，失败路径同样清理自身无引用可再生产物。

## 6. 已验证的不兼容口径与重现方法

以下是外部函数的隔离检查，不是对三个项目整体正确性的判决，也不是已修复问题。

| 检查 | 当时实得 | 迁移要求 |
| --- | --- | --- |
| TSP `robust_score([1.0, 0.0])` | median=0.5，worst=0，score=0.3 | 合法零须保留 |
| TSP `robust_score([1.0, None])` / `[1.0, NaN]` | median=1，worst=1，score=1 | 注释称缺段负无穷，实际过滤缺段；新政策必须显式表达不完整性 |
| TSP `robust_score([None, None])` | score=-inf，其余 None | 不将无穷写入 DECIMAL 或标准 JSON |
| NTQ `HfqRoi.ratio(100, None/0/NaN)` | 均为 0.0 | 缺价与零收益分离，不能以“同股等价金额”代替实际现金事件账 |

检查在 `backend/` 工作目录以 `uv run --no-sync python -c ...` 执行：标准库 `ast.parse` 读取源文件，仅提取 `robust_score` 函数或 `HfqRoi` 类到临时命名空间后调用，未 import 外部 app、未创建数据库、未落测试文件。复核同样可以按此方法重现；源文件摘要见第 9 节。

还需专项防线：TSP 外层测试成绩用于权重精调后不能继续作为未参与选择的最终留出集；NTQ 按记录时间裁剪的 cursor 不能自动证明该字段就是首次可得时间；Hikyuu 事件算法不能替代本项目事件日期与来源证据；TSP 的财务最新快照合并不能替代本项目修订历史合同。

## 7. 验证、回退与文档同步

- 代码实施：每片依影响运行相应测试与本地共用检查门；金融算法增加独立已知解/慢参考、失败反例与确定性重跑。前端交互还须实走成功、空数据和错误流程。
- 数据实施：先只读审计/内存样本，再经显式幂等升级与受控发布；不自动全库备份，不改 active/父链或未理解的 WAL。需要 schema 变化时补齐 `schema-plan` 与 `module-index`，不能以启动初始化当任意升级授权。
- 回退：新算法、政策、接口与研究产物各自版本化；关闭新入口不删除已引用产物，不重新解释旧 run。生产数据的升级/回退必须在各自切片另定方案，本文件不授权执行。
- 本次文档仅新增方案、Reference Map 与计划导航、roadmap 的候选方案说明，并重生成状态索引；未改变现行架构、schema、API、源或金融公式，因此本轮不改 relations/data/schema/research 当前合同，也不改根产品 README 或 AI 规则。
- 后续按实际边界同步：研究/持仓语义回写 roadmap 与对应 research；架构/数据流回写 trusted-daily-data-center 与 system-map；数据发布与时点回写 data；API/schema 各遵循根规则。不得把本计划状态替代这些正式合同。

## 8. 分级、风险与评审范围

本设计按 **T2** 评审：覆盖公司行为、时点、统计/回测假设、组合与持仓工作流，以及跨模块迁移和长期兼容取舍。独立复核选择金融行业专家（A–D/F 的正确性）、架构/后端（事实源、分层、资源和回退）、产品/前端（用户价值、切片范围与交互）。主代理汇总冲突，评审过程保留当前任务，本文只保留稳定设计。

本次金融、架构/后端、产品/前端三个独立复核均通过方案落档，未留本次文档的 P0/P1；采纳了审计起点覆盖上游预处理、股息税费/应收口径、G1/G2 拆分与子进程晚到结果隔离建议。结论不授予任何待实施切片生产准入，后续仍按实际改动重新判级与复核。

| 风险 | 影响与处置 |
| --- | --- |
| 金融阻断 | 公司行为日期/持仓资格不足、未来数据依赖、缺失被补零、重复使用 OOS、金融精度不兼容；对应生产片未验证前不放行 |
| 合同与验收缺口 | 第 3 节是候选而非全量代码验收；每片必须补源摘要、测试、容量、存储/API/schema 决策和所需独立复核 |
| 维护成本 | 新引擎/原生依赖、双事实源、任意插件执行会扩大成本；按现有底座改写，小模块先行 |

金融硬检查在**方案层**已约束：可信血缘、公告/首次可得时间、精度、成本/不可成交、约束、版本复现与人工确认。它们在外部代码和真实数据上的运行正确性仍**未验证**，对应实施片的准入项不能以本次文档评审代替。行业中性化/费用等已存在能力只做差异与用例比较，不把未知补成通过。

本次文档验收要求：30 项台账与 A–H 对应完整，方案与已交付事实分离；引用存在且核心摘要可核对；frontmatter/状态索引检查通过；保留工作区既有运维改动。未要求也未执行提交。

## 9. 关键来源快照（SHA-256，2026-11-18）

路径相对仓库根；摘要固定本轮关键证据字节，不代表已保存外部项目的完整可恢复副本。

| 项目内相对路径 | SHA-256 |
| --- | --- |
| `hikyuu-master/hikyuu_cpp/hikyuu/trade_manage/TradeManager.cpp` | `ebdb0d96ad8f0a92a564b94c9865fb4386d691d3da8a617319c60215e0a0fc6b` |
| `hikyuu-master/hikyuu_cpp/hikyuu/trade_sys/system/TradeSuggestion.h` | `a2691b7703ae89b65e4b551344811c54b55ae7f95723f82774c6c787d0679204` |
| `new-tea-quant-master/core/modules/data_contract/core/base/base_time_series_contract.py` | `1655b94d47be72e67d5a79a7ab0f1aac8ee192703d562cafe888a802274563c9` |
| `new-tea-quant-master/core/modules/tag/core/engines/shared/calc_window.py` | `6b17e2c644e814aa32e52a5e69d1d099387c51dfe8e8ff7b3e96c80b4632f0d0` |
| `new-tea-quant-master/core/modules/strategy/core/engines/shared/services/hfq_roi/hfq_roi.py` | `997c84f4f7cbdf02d4f5cc46e8159a61847f525b02c7fdbd97f98527257619fa` |
| `new-tea-quant-master/core/modules/strategy/core/engines/analyzer/steps/campaign/summarize/sweeps.py` | `2c17e5e663f2073ecd0ef934bd2ec9a5d62acf3731a0a0eeffa07d9ebea950e6` |
| `tickflow-stock-panel-main/backend/app/backtest/evidence.py` | `bb9a3f719d240a9466db1afe199c060c7f5015075955010fbfc0ac8642a43890` |
| `tickflow-stock-panel-main/backend/app/backtest/stats_v2.py` | `7b39d257f4dd0718320a6bf413d476db2f04cf0471667e01ffb81cfa6691a871` |
| `tickflow-stock-panel-main/backend/app/backtest/mining.py` | `85b4dc995941c353570e2f1c3af7c380be195421865d1ca78927dc6be6ea1531` |
| `tickflow-stock-panel-main/backend/app/backtest/robust_optimizer.py` | `e255b3c25e407180c62d91ebbf273584fb440bd39cfe9fa148de442751b42d91` |
| `tickflow-stock-panel-main/backend/app/services/heavy_job_limiter.py` | `f0596ebef64e87cabfd1601b3eb8a2eecae2cb5ef5399b93890e620f6c0bf6a6` |
| `tickflow-stock-panel-main/docs/strategy-iteration.md` | `d20544ad963c12b4542575f7113540621a08b7972a7adf4385e451fa2d57948b` |
