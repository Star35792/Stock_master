"""通达信官网盘后包 —— 独立厂商的第二数据源。

为什么需要它
------------
项目里 ``raw.daily_bar`` 的三个来源（``hithink_finance`` / ``hithink_finance_dump``
/ ``hithink_finance_index``）**全部同属同花顺**，共享同一个上游。它们之间互相
"对账"证明不了任何东西：一个上游的系统性错误会被三个源同时复制。
通达信官网盘后包是另一家厂商的独立产物，是真正能交叉验证的对照源。

单位口径（实测 2026-11-10，52,605 行）
--------------------------------------
- **个股**：``volume`` 单位是「股」，与项目一致 —— 5,572 只个股成交量
  **100% 精确相等**。
- **指数**：``volume`` 是通达信原值（手），项目 ``market.daily_bar`` 存的是
  「股」—— 541 只指数呈现精确的 100 倍差、2 只 10000 倍差。**这是口径差，
  不是数据错**，比较前必须归一。
- **成交额**：两边都是「元」，6,118/6,125 精确相等 —— 这是最干净的对照字段。

代码映射
--------
通达信用 ``999999`` 表示上证指数，项目用 ``000001.SH``；``399xxx`` 与 ``000xxx``
在两边同码。适配器只输出**项目 symbol 命名空间内存在**的代码，其余（通达信
独有的 ``999xxx`` 系列、基金、债券等）不进入对账，避免造出"项目没有的标的"。

依赖
----
``a-stock-data`` 是一个「结构化 Markdown + 内嵌 Python」的 Skill 包，不是 pip
包。本模块通过 :mod:`a_stock_data.loader` 加载它 —— 该 loader 会用 AST 白名单
剥离模块级副作用（import 零网络请求）。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.data_sources.base import DailyBarRecord

#: 仓库根目录（本文件位于 ``backend/app/data_sources/``）
_REPO_ROOT = Path(__file__).resolve().parents[3]

#: ``libs/`` 下的 a-stock-data 加载器所在目录
_LIBS_DIR = _REPO_ROOT / "libs"

if str(_LIBS_DIR) not in sys.path:
    sys.path.insert(0, str(_LIBS_DIR))


class TdxOfficialDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class TdxPackage:
    """一份通达信盘后包的原始内容。"""

    trade_date: date
    rows: list[dict[str, Any]]


def _market_to_exchange(market: str) -> str:
    return {"sh": "SH", "sz": "SZ", "bj": "BJ"}.get(str(market).lower(), "")


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not number.is_finite():
        return None
    return number


class TdxOfficialDataSource:
    """通达信官网盘后包客户端。

    与其它 ``app/data_sources`` 适配器的区别：盘后包是**整市场单日快照**，
    不是按标的拉的日线。所以它提供 ``fetch_package(trade_date)`` 返回整包，
    由上层服务负责按 ``market.security`` 过滤出项目命名空间内的标的。
    """

    name = "tdx_official"
    source_type = "vendor"

    def __init__(self, *, skill_path: Path | None = None) -> None:
        self._skill_path = skill_path
        self._module: Any | None = None

    def _load(self) -> Any:
        if self._module is not None:
            return self._module
        try:
            from a_stock_data.loader import load
        except ImportError as exc:  # pragma: no cover - 环境缺依赖时的显式失败
            raise TdxOfficialDataError(
                f"无法加载 a_stock-data loader（{_LIBS_DIR}）: {exc}"
            ) from exc
        kwargs = {"path": self._skill_path} if self._skill_path is not None else {}
        module, _skipped = load(**kwargs)
        self._module = module
        return module

    def fetch_package(self, trade_date: date) -> TdxPackage:
        """下载并解析某一交易日的通达信官网盘后包。

        非交易日、当日包尚未发布（通常收盘后数小时）、或早于官网保留范围时，
        底层会抛 ``ValueError`` —— 这里原样抛出，让上层把"没有盘后包"记成
        一条可读的错误，而不是伪装成"抓到 0 行"。
        """
        module = self._load()
        ymd = trade_date.strftime("%Y%m%d")
        try:
            frame = module.tdx_daily_package(ymd)
        except (ValueError, RuntimeError) as exc:
            raise TdxOfficialDataError(f"{trade_date} 通达信盘后包不可用: {exc}") from exc

        if frame is None or len(frame) == 0:
            raise TdxOfficialDataError(f"{trade_date} 通达信盘后包为空")

        frame = frame.rename(columns={c: c.lower() for c in frame.columns})
        rows: list[dict[str, Any]] = []
        for record in frame.to_dict("records"):
            code = str(record.get("code", "")).zfill(6)
            exchange = _market_to_exchange(str(record.get("market", "")))
            if not code or not exchange:
                continue
            rows.append(
                {
                    "symbol": f"{code}.{exchange}",
                    "name": record.get("name"),
                    "open": _to_decimal(record.get("open")),
                    "high": _to_decimal(record.get("high")),
                    "low": _to_decimal(record.get("low")),
                    "close": _to_decimal(record.get("close")),
                    "prev_close": _to_decimal(record.get("prev_close")),
                    "volume": _to_decimal(record.get("volume")),
                    "amount": _to_decimal(record.get("amount")),
                    "source_url": record.get("source_url"),
                }
            )
        return TdxPackage(trade_date=trade_date, rows=rows)

    def to_daily_bar_records(
        self,
        package: TdxPackage,
        *,
        allowed_symbols: set[str] | None = None,
        security_types: dict[str, str] | None = None,
    ) -> list[DailyBarRecord]:
        """把盘后包转成项目口径的日线记录。

        ``allowed_symbols`` 为 ``None`` 时不过滤；给出时只保留集合内的标的，
        用于把通达信独有的 ``999xxx`` 指数代码与基金债券挡在外面。

        **成交量不做换算。** 通达信的指数成交量是「手」而项目存「股」，但
        换算倍数随指数而变（实测 541 只为 100 倍、2 只为 10000 倍），写死一个
        常数会把部分标的换错。原始值原样入库，口径知识集中在对账层处理：
        对账规则识别「精确整数倍」并归一，既不误判冲突，也不污染可信层。
        """
        records: list[DailyBarRecord] = []
        for row in package.rows:
            symbol = str(row["symbol"])
            if allowed_symbols is not None and symbol not in allowed_symbols:
                continue
            if row["open"] is None or row["close"] is None:
                continue
            # A bar with no traded price is not a bar.  The package lists newly
            # listed or suspended symbols with open=high=low=0 while still
            # carrying a close (the listing price), and the project would
            # reject those with PRICE_INVALID — an audit row that says
            # "the vendor sent garbage" when in fact the symbol simply had no
            # trade that day.  Such rows are dropped at the boundary instead.
            if row["open"] <= 0 or row["high"] <= 0 or row["low"] <= 0:
                continue
            records.append(
                DailyBarRecord(
                    symbol=symbol,
                    trade_date=package.trade_date,
                    adjust_type="none",
                    open=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=row["close"],
                    volume=row["volume"],
                    amount=row["amount"],
                    raw_payload={
                        "name": row["name"],
                        "prev_close": str(row["prev_close"])
                        if row["prev_close"] is not None
                        else None,
                        "source_url": row["source_url"],
                        "security_type": (security_types or {}).get(symbol),
                        "volume_unit": "tdx_raw",
                    },
                )
            )
        return records
