# scripts · 模块规则

作用域：`scripts/**`（仓库级共享脚本）。跨模块规则见根 [`AGENTS.md`](../AGENTS.md)。

## ci_check.py 是本地与 CI 共用的唯一检查门

- 步骤：backend = Ruff + 全量 pytest；frontend = `bun test` + `bun run build`。按步执行，失败即停并透传子进程退出码；可执行文件缺失返回 127。
- `ci-check.sh` 只是兼容入口，内部 `uv run --project backend --extra dev` 调本目录 Python，命令真源始终在 `ci_check.py`。

## 改动约束

- 只用 Python 标准库，Python 3.13；必须从任意工作目录可运行（仓库根由 `Path(__file__).resolve().parents[1]` 推导，不要改成相对 cwd）。
- `--list` 必须能在未安装 uv / bun 的环境跑通：列出命令时不得查找或执行可执行文件。
- 步骤的命令、顺序、工作目录、`--basetemp` 都是被契约测试锁定的公共接口：改这里必须同步 `backend/tests/test_ci_check_runner.py`（它逐条断言命令列表、失败即停、退出码、以及「bun 不回退到 npm/pnpm」）。
- `.github/workflows/ci.yml` 只按 `--scope backend` / `--scope frontend` 调本门，门内的步骤变化自动生效、不需要改 CI；只有新增**scope 或 job**时才同步 ci.yml，且不得另建第二套 CI 入口。
- 一次性维护、迁移、研究脚本不放这里，放 `backend/scripts/`（它们受域写锁与 `backend/AGENTS.md` 约束）。

## doc_status_index.py

- 从 `aiDoc/` 各时间戳文档的 YAML frontmatter 生成[文档状态索引](../aiDoc/20261115-1649-doc-status-index.md)，`--check` 只比不写；索引是生成物，不手工编辑。
- 契约由 `backend/tests/test_doc_status_index.py` 锁定（缺 frontmatter、悬空 `superseded_by`、索引未再生成都会失败），该测试在 backend 全量 pytest 内，因此已随 CI 生效。
- `superseded_by` 路径相对 `aiDoc/`，`../` 开头指向仓库根方向；词表改动要同步根 `AGENTS.md` 的「文档维护」小节。
