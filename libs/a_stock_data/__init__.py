"""a-stock-data 数据源包（旁置工具包，不参与后端运行时依赖）。

与 ``libs/hithink_sdk`` 同构：位于仓库 ``libs/`` 下，被 ``backend/pyproject.toml``
的 ``ruff.extend-exclude`` 排除，不进入后端依赖图。它提供的是**取数能力**，
真正的适配器要按 Stock Master 的 ``raw → market`` 规则写在
``backend/app/data_sources/`` 下。

当前模块：

- ``loader``：把 ``a-stock-data-main/SKILL.md`` 内嵌 Python 加载为可 import 模块。
"""

from __future__ import annotations

__all__ = ["loader"]
