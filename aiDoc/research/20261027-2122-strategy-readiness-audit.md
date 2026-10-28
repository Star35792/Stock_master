# 单因子策略资格与后续验证前置审计（2026-10-27）

## 裁决

`evidence_gated` 保留为历史研究预设名称，当前不是已通过资格门槛的策略。此预设的回测暴露来自 `volume_stability_20@v1-qfq`，不能继承 `v2-qfq-2y` 的画像结论。当前主库分级结果 `core=0`；`v1-qfq` 为 frozen（119 日、定义 PIT unknown、证据 research_only），`v2-qfq-2y` 为 observation（483 日、PIT point_in_time、画像仍为 research_only）。可复现输出：`backend/outputs/factor_pool_audit_20261027.json`、`backend/outputs/evidence_gated_qualification_20261027.json`。

另一个历史时点缺口在组合层：启用行业权重约束的旧回测仍使用 `market.security.industry` 当前标签，不具备当日可得性的证明；因此不能把该约束表述为已验证的历史行业限制。

本轮将回测脚本增加精确版本资格报告及 `--qualification-only` 只读入口，后续回测产物显式标记 `strategy_validation_status=research_only`。因子池分级 JSON 同时导出证据资格及原因。历史 run #79/#80 不因这些说明变化而被改写；其收益继续仅作研究观察。

复现入口（在 `backend/` 执行）：

```powershell
.venv/Scripts/python.exe scripts/research/run_warm_factor_selection.py --spec-set evidence_gated --qualification-only --out outputs/evidence_gated_qualification_20261027.json
.venv/Scripts/python.exe scripts/research/audit_strategy_readiness.py --start-date 2026-05-06 --end-date 2026-10-19 --out outputs/strategy_readiness_20261027.json
```

## 独立窗口的可用性

只读预检 `backend/scripts/research/audit_strategy_readiness.py` 对 2026-05-06～2026-10-19 生成 `backend/outputs/strategy_readiness_20261027.json`：区间内 4,197 条公司行为（现金 3,806、混合 350、送股 40、配股 1）全部缺 `record_date`、`payment_date` 和 `source_event_id`。主库 `security_status_event=0`；5,579 条行业历史均来自 `current_label_backfill_20261024`，不能当历史时点标签；7,729 条证券的 `delist_date` 全为空。当前人工名单的 ST、行业、流动性、波动及上市时长规则仍未成为回测共用合同。因此该窗口维持 blocked，没有可信净收益数字。

现存公司行为库另有 50 条现金、送股、转增、配股和拆股数值全为零的无实质事件，误标为配股。本轮修正 HiThink 导入的批量与逐行发布校验：原始行继续留痕，新导入时不再把全零行发布到 `market`。既有 50 条仅审计挂账，未直接删除或改写。

因子画像来源还存在独立血缘缺口：`factor.factor_profile` 唯一键不含 factor release、价格输入快照或输入哈希；温层 IC 脚本按指定 release 读取因子值，却从可原地更新的 curated qfq 路径读取远期收益。即使重跑同名版本，旧画像可能与新输入混合。**不得通过简单重跑或更新资格字段恢复核心池**。项目现有 `evidence_eligibility=strategy_eligible` 只表示输入来源资格，不能替代退市样本、公司行为执行会计或样本外策略验证。

## 下一实施顺序和验收

1. 为画像绑定 factor release 与价格输入快照及日期全集；对固定版本做整窗重算、旧行残留和输入哈希核对，再按精确版本分级。v1 仍不得借 v2 资格晋级。
2. 建立名单和回测共用的版本化 `as_of` 准入合同，记录逐股拒绝原因与候选集哈希。可先覆盖真实历史上市日、可信行情、流动性和波动；ST 与行业在历史来源认证前保持阻断，不能把当前标签回填过去。
3. 通过 raw→market 补充公司行为公告、登记、除权、支付和来源 ID，确认金额单位及同日重复事件；补退市股票可信价格。事件会计逐类型实现并验证现金、应收、股数守恒，未知事件继续 fail-closed。
4. 冻结相同因子版本、名单合同、费用及事件政策后，才运行 2026-05 之后未用于选参的窗口。报告净收益、同日基准、最大回撤、换手、成本、覆盖与拒绝成交；若前置条件不满足，只输出阻断证据。

交易所公开的[上交所权益分派实施公告示例](https://static.sse.com.cn/disclosure/bond/announcement/company/c/new/2026-07-31/245696_20260731_CJU6.pdf)列出股权登记日、除权日和现金发放日；[深交所说明](https://www.szse.cn/www/investor/index/update/t20211222_590340.html)解释权益登记日与除权日的关系。它们提供候选原始证据路径，但不能把通用 R+1 规则替代每只证券的实施公告与来源审计。

## T2 评审与验证

触发：因子资格、回测解释、数据血缘和历史选股合同。架构师、产品经理、金融专家及规则编写者均有条件通过资格/可用性审计和无实质事件拦截，均阻断在缺失日期和历史时点数据下放开公司行为回测或宣布策略晋级。主代理采纳共同结论，未调整选股参数、事件会计或既有回测收益。针对性验证：资格只读入口及预检脚本实跑；预检重复运行 SHA256 一致；相关因子池、IC 与回测测试 33 项、HiThink 导入测试 5 项通过。回退：恢复导入校验及删除新增审计入口和报告字段；现有回测与数据库未改动。
