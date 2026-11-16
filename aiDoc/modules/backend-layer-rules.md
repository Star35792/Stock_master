# 后端分层 Reference

跨模块分层、可信数据和金融精度规则见根 [`AGENTS.md`](../../AGENTS.md)；后端命令与实现入口见 [`backend/AGENTS.md`](../../backend/AGENTS.md)。本页只记录分层规则之外的后端实现约束。

## 研究读取与发布

- 温层只读仓库（`repositories/research_read.py`）与 ORM 仓库是**孪生实现**：同一份查询语义必须有两条路径，`research_read_source=warm` 走温层、`control` 走 ORM。新增回测/研究读取能力时两条都要改，不能只改一条；两条路径返回的列名、排序和过滤语义必须一致（例如 `list_factor_values` 都按 `trade_date, symbol` 排序）。
- 研究读取路径不得改写输入 Parquet，研究产物经 ORM 写入研究库。curated 发布仅由既定发布管道执行：`daily_loop` 暂存构建并绑定因子输入，质量闸门通过后发布、激活 release；不得绕过该管道直接覆盖可信输入。

文档同步映射见根 `AGENTS.md` 的「文档同步门槛」，按实际受影响边界执行。
