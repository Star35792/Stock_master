# libs · 模块规则

作用域：`libs/**`（SDK 副本与本地数据源适配层）。跨模块规则见根 [`AGENTS.md`](../AGENTS.md)。

## a_stock_data/（本地代码，被后端 import）

- 只有一个模块：`loader.py` 把未入库的 `a-stock-data-main/SKILL.md`（「结构化 Markdown + 内嵌 Python」，约 87 个端点）净化成可 import 的模块。
- 它是在用外部源的实现：`backend/app/data_sources/tencent.py:107` 与 `tdx_official.py`、`backend/scripts/ingest/reconcile_with_a_stock_data.py` 都把仓库根的 `libs/` 插进 `sys.path` 后 import 本包。
- 必须保持的不变式：AST 白名单只保留「可安全执行的定义」，导入时**不得**发起任何外网请求。上游代码块常在模块级直接取数并 `print`，不净化就会 import 即打几十个请求（触发供应商风控）、灌满 stdout、耗时数十秒。
- 改这里等于改数据源实现：按 T2 评审，并保持 `SKILL.md` 变更后 loader 的白名单仍然覆盖所需端点。

## hithink_sdk/（vendored 上游副本）

- 上游同花顺 MarketDB SDK 的整目录副本，发行包名 `marketdb`，`requires-python >=3.11`。**后端不 import 它**：HiThink 取数走 `backend/app/data_sources/hithink.py` 自己的 urllib 实现。
- 不在任何检查门的命令范围内（`ci_check.py` 只 lint `app tests scripts ../scripts/ci_check.py`，pytest `testpaths = ["tests"]`）。注意 `backend/pyproject.toml:54` 的 `extend-exclude = ["libs/hithink_sdk"]` 是相对 `backend/` 解析的，实际指向不存在的路径，不构成保护——不要因此把该目录加进 Ruff 目标。
- 它的 `README.md` 描述的是上游 monorepo 布局（例如 `python/tests/`），本仓库没有那些路径，不要照其指令执行；README 内容受 `libs/hithink_sdk/tests/test_public_docs_governance.py` 断言约束。
- 只做整体上游同步并注明来源版本，不做本地分叉、不做风格重构。
