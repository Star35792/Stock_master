# 因子画像输入身份闸门

## 结论

`factor.factor_profile` 的核心池资格不能只由 `factor_version` 与 `code_version` 标识。相同版本的画像可能来自不同 warm release、被原子替换的 `curated/prices_qfq` 或不同日期子集；旧唯一键 `(factor_name, factor_version, trade_date)` 还允许部分重算保留其他日期旧行。

本轮在不改变表、列、唯一键和 DuckDB/Parquet 存储布局的约束下，复用既有 `evidence_eligibility_reason VARCHAR(500)` 承载 `evidence_identity_v1`。旧自由文本保留供审计，但不能进入核心池。

## 身份生成

温层 IC 持久化前执行以下门禁：

1. 用 `WarmLayerStore.table_globs("factor", "factor_value", release_tag)` 解析实际 factor release lineage，对每个实际 Parquet 的相对路径、字节数和文件 SHA-256 生成规范化清单。
2. 对实际 `curated/prices_qfq` 做同样的逐文件清单；其 checksum 必须等于 `lake.release` 中 active、trusted 的 `prices_qfq` release checksum。
3. 计算前后各取一次完整内容快照；factor 或 QFQ 任一 digest 变化即中止，不写画像。
4. 预期日期来自固定 QFQ 快照在请求窗口内的完整日期集合。每个因子实际生成的画像日期必须与它完全相等，才写 `strategy_eligible`。
5. 信封绑定 warm release tag、factor+QFQ 来源总哈希、请求窗口、实际首末日/数量/日期 SHA-256、factor name/version 和 code version。完整逐文件清单写入每次运行的审计 JSON；信封只保存紧凑摘要并显式检查不超过 500 字符。

脚本默认代码版本使用 `app_code_version` 确定性源码指纹。整批 upsert 在同一事务中提交；不完整因子只写 `research_only`，不会伪造合法身份。

## 汇总复核

仓储汇总同时统计：

- `count(distinct evidence_eligibility)`；
- `count(distinct evidence_eligibility_reason)`；
- `count(distinct code_version)`。

服务层再按当前查询窗口从数据库读取有序 `trade_date`，计算真实日期 SHA-256，并与信封中的首日、末日、数量和 hash 比较；同时重算 factor name/version/code version 身份。只有 eligibility、identity、code version 都恰好一个，且所有复核相等，输入身份闸门才通过。因此缺中间日、额外旧日、部分重算、混合输入及截取原整窗的子窗口都会 fail-closed。

API 与 CLI 显式返回 identity distinct 数、有效性、release tag 与实际日期 hash，分级原因说明具体阻断。

## 能力边界

该门禁能证明画像没有静默混合不同 release、QFQ 内容、代码或日期全集；不能证明历史证券池无幸存者偏差，也不能补齐历史 ST/行业、退市价格、公司行为登记/支付与事件会计、费用滑点、停牌涨跌停、容量流动性或样本外稳定性。

因此 `core` 仍只表示因子画像证据层级，不是生产策略、可交易收益或投资建议。历史画像不重写、不删除；恢复资格必须在固定输入上整窗重算，并继续满足所有既有门槛。
