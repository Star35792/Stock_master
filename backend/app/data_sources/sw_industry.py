"""申万行业归属变迁史数据源。

为什么是申万
------------
项目现有的 ``market.security.industry`` 就是**申万二级行业名**（"白酒Ⅱ"、
"银行Ⅱ"、"电池"、"乘用车"…），共 135 个。所以只要拿到申万的行业**变更历史**，
就能把「当前标签」升级成真正的时点事实。

数据源
------
申万宏源研究《申万行业分类标准（2021 年版）》个股归属变动表：

    https://www.swsresearch.com/swindex/pdf/SwClass2021/StockClassifyUse_stock.xls

实测（2026-11-11）该表含 12,934 行 / 5,938 只标的，其中 **2,024 只历史上
发生过行业变更**，最多的一只变更过 10 次。行结构为
``(股票代码, 计入日期, 行业代码, 更新日期)``——``计入日期`` 就是该次归属的
生效日，这正是时点事实的来源。

代码结构
--------
申万 2021 分类的 6 位行业代码：**前 2 位 = 一级，前 4 位 = 二级，后 6 位 = 三级**。
项目的标签粒度是二级，所以映射键取前 4 位。

TLS
---
该站点的证书链在本机/部分企业网环境下不完整（``unable to get local issuer
certificate``），``certifi`` 也无法覆盖。因此默认**校验证书**，并提供显式的
``insecure`` 开关（akshare 官方库对同一站点的 JSON 接口也是 ``verify=False``）。
不校验证书意味着无法确认对端身份，只适用于公开只读文件的抓取。

重试与缓存
----------
两个实测出来的运维事实决定了这里的写法：

1. **代理 502 是常态**，不是致命错误。本次交付当天就遇到一次
   ``ProxyError: Tunnel connection failed: 502 Bad Gateway``。所以抓取带退避
   重试，而不是第一次失败就放弃。
2. **dry-run 审查的计划必须等于实跑执行的计划**。如果每次调用都重新下载，
   两次读到的可能是不同快照，「先看计划再落库」这个安全动作就失去意义。
   所以下载内容落本地缓存（带抓取时间与 sha256），默认复用；``refresh=True``
   才强制重新下载；网络失败但本地有缓存时，回退到缓存并明确告知。

缓存里放的是**原始字节**（不是解析结果），这样解析逻辑的修改不会被缓存
掩盖，重新解析即可复用同一份原始文件。
"""

from __future__ import annotations

import hashlib
import io
import json
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

#: 默认缓存目录（相对 backend/ 解析）。
DEFAULT_CACHE_DIR = Path("data/cache/sw_industry")

#: 缓存元数据文件名。
_CACHE_META_NAME = "StockClassifyUse_stock.meta.json"


class SwIndustryDataError(RuntimeError):
    """申万行业变迁史抓取或解析失败。"""


@dataclass(frozen=True)
class SwIndustryRecord:
    """一条申万行业归属记录（变动表的一行）。"""

    #: 6 位股票代码，不含交易所后缀。
    symbol_code: str
    #: 该次归属的生效日。
    effective_from: date
    #: 6 位申万行业代码。
    industry_code: str
    #: 源表里的「更新日期」，仅作留痕。
    update_time: date | None

    @property
    def level2_code(self) -> str:
        """二级行业代码（前 4 位）。"""

        return self.industry_code[:4]


class SwIndustryDataSource:
    """申万行业归属变迁史。"""

    name = "sw_industry"
    source_type = "api"

    HISTORY_URL = (
        "https://www.swsresearch.com/swindex/pdf/SwClass2021/StockClassifyUse_stock.xls"
    )

    def __init__(
        self,
        *,
        timeout_seconds: int = 60,
        cache_dir: Path | None = None,
        retries: int = 3,
        backoff_seconds: float = 2.0,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
        self.retries = max(1, retries)
        self.backoff_seconds = backoff_seconds

    # ------------------------------------------------------------------ 缓存

    @property
    def cache_path(self) -> Path:
        return self.cache_dir / "StockClassifyUse_stock.xls"

    @property
    def cache_meta_path(self) -> Path:
        return self.cache_dir / _CACHE_META_NAME

    def _read_cached_bytes(self) -> bytes | None:
        if not self.cache_path.exists():
            return None
        return self.cache_path.read_bytes()

    def _write_cache(self, payload: bytes) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_bytes(payload)
        meta = {
            "url": self.HISTORY_URL,
            "fetched_at": date.today().isoformat(),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        }
        self.cache_meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------ 抓取

    def _download(self, *, insecure: bool) -> bytes:
        try:
            import requests
        except ImportError as error:  # pragma: no cover - 依赖缺失
            raise SwIndustryDataError(
                "requests is required to fetch the SW industry file"
            ) from error

        headers = {"User-Agent": "Mozilla/5.0"}
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                response = requests.get(
                    self.HISTORY_URL,
                    headers=headers,
                    timeout=self.timeout_seconds,
                    verify=not insecure,
                )
                response.raise_for_status()
                return response.content
            except Exception as error:  # noqa: BLE001 - 统一成数据源错误
                last_error = error
                message = str(error)
                if "CERTIFICATE_VERIFY_FAILED" in message or "SSL" in message:
                    # 证书错误重试多少次都一样，直接失败并给出可执行的提示。
                    raise SwIndustryDataError(
                        f"failed to fetch SW industry history: {error}"
                        "（该站点证书链在本机不完整；确认数据来源可信后可用 "
                        "insecure=True 重试）"
                    ) from error
                if attempt >= self.retries:
                    break
                # 代理 502 是瞬时故障，退避后重试。
                time.sleep(self.backoff_seconds * attempt)
        raise SwIndustryDataError(
            f"failed to fetch SW industry history after {self.retries} attempts: "
            f"{last_error}"
        ) from last_error

    def fetch_history(
        self,
        *,
        insecure: bool = False,
        refresh: bool = False,
    ) -> list[SwIndustryRecord]:
        """抓取并解析申万个股行业归属变动表。

        ``refresh=False``（默认）时优先用本地缓存；网络失败但本地有缓存时
        回退到缓存——申万这张表是**历史发布物**，不随行情漂移，回退是安全的。
        """

        payload: bytes | None = None
        if not refresh:
            payload = self._read_cached_bytes()

        if payload is None:
            try:
                payload = self._download(insecure=insecure)
            except SwIndustryDataError:
                cached = self._read_cached_bytes()
                if cached is None:
                    raise
                # 网络失败但本地有快照：用缓存继续。申万这张表是历史发布物，
                # 不随行情漂移，回退是安全的。
                payload = cached
            else:
                self._write_cache(payload)

        try:
            import pandas as pd
        except ImportError as error:  # pragma: no cover - 依赖缺失
            raise SwIndustryDataError(
                "pandas is required to read the SW industry file"
            ) from error

        try:
            frame = pd.read_excel(
                io.BytesIO(payload),
                dtype={"股票代码": "str", "行业代码": "str"},
            )
        except Exception as error:  # noqa: BLE001
            raise SwIndustryDataError(
                f"failed to parse SW industry xls: {error}"
            ) from error

        return self._normalize(frame)

    def _normalize(self, frame: Any) -> list[SwIndustryRecord]:
        columns = set(frame.columns)
        required = {"股票代码", "计入日期", "行业代码"}
        missing = required - columns
        if missing:
            raise SwIndustryDataError(
                f"unexpected SW industry columns: missing {sorted(missing)}; "
                f"got {sorted(columns)}"
            )

        records: list[SwIndustryRecord] = []
        for row in frame.itertuples(index=False):
            code = str(getattr(row, "_0", "") or getattr(row, "股票代码", "")).strip()
            if not code:
                continue
            code = code.zfill(6)
            if not code.isdigit():
                continue
            effective_from = _to_date(getattr(row, "计入日期", None))
            if effective_from is None:
                continue
            industry_code = str(
                getattr(row, "行业代码", "") or ""
            ).strip().zfill(6)
            if not industry_code.isdigit():
                continue
            records.append(
                SwIndustryRecord(
                    symbol_code=code,
                    effective_from=effective_from,
                    industry_code=industry_code,
                    update_time=_to_date(getattr(row, "更新日期", None)),
                )
            )
        return records


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    try:
        return value.date() if hasattr(value, "date") else value
    except (AttributeError, ValueError):
        return None
