from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from json import loads
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import duckdb

from app.config.settings import settings
from app.data_sources.hithink import HithinkFinanceError


class HithinkDumpKind(StrEnum):
    FULL_DAILY = "daily-k"
    RECENT_DAILY = "daily-k-10d"
    ADJUSTMENT_FACTORS = "adjustment-factors"


@dataclass(frozen=True)
class HithinkDump:
    kind: HithinkDumpKind
    path: Path
    release_tag: str


class HithinkDumpDataSource:
    name = "hithink_finance_dump"
    source_type = "vendor"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        cache_dir: str | Path | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        self.api_key = api_key or settings.hithink_finance_api_key
        self.base_url = (base_url or settings.hithink_finance_base_url).rstrip("/")
        self.cache_dir = Path(cache_dir or settings.hithink_dump_cache_dir)
        self.timeout_seconds = timeout_seconds or settings.hithink_finance_timeout_seconds

    def download(self, kind: HithinkDumpKind) -> HithinkDump:
        if not self.api_key:
            raise HithinkFinanceError("HITHINK_FINANCE_API_KEY is required")
        request = Request(
            f"{self.base_url}/dump/market-dumps/{kind.value}/download-url",
            headers={"X-api-key": self.api_key},
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                envelope = loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise HithinkFinanceError(f"HiThink dump HTTP error: {exc.code}") from exc
        except URLError as exc:
            raise HithinkFinanceError(f"HiThink dump network error: {exc.reason}") from exc
        if envelope.get("code") != 0:
            raise HithinkFinanceError(
                envelope.get("message") or "HiThink dump API returned an error",
                code=envelope.get("code"),
                request_id=envelope.get("request_id"),
            )
        presigned_url = (envelope.get("data") or {}).get("presigned_url")
        if not presigned_url:
            raise HithinkFinanceError("HiThink dump response has no presigned_url")

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self.cache_dir / f"{kind.value}.parquet"
        partial = target.with_suffix(".parquet.part")
        try:
            download_request = Request(presigned_url, method="GET")
            with urlopen(download_request, timeout=self.timeout_seconds) as response:
                with partial.open("wb") as output:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
        except (HTTPError, URLError) as exc:
            raise HithinkFinanceError(f"HiThink dump download failed: {exc}") from exc
        partial.replace(target)
        release_tag = presigned_url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        return HithinkDump(kind=kind, path=target, release_tag=release_tag)

    def iter_daily_rows(
        self, path: str | Path, *, chunk_size: int = 10_000
    ) -> Iterator[list[dict[str, Any]]]:
        connection = duckdb.connect()
        try:
            cursor = connection.execute(
                """
                SELECT thscode, date_ms, open_price, high_price, low_price,
                       close_price, volume, turnover
                FROM read_parquet(?)
                WHERE adjusted = 'none'
                ORDER BY date_ms, thscode
                """,
                [str(Path(path))],
            )
            columns = [item[0] for item in cursor.description]
            while rows := cursor.fetchmany(chunk_size):
                yield [dict(zip(columns, row, strict=True)) for row in rows]
        finally:
            connection.close()

    def daily_date_range(self, path: str | Path) -> tuple[date, date]:
        connection = duckdb.connect()
        try:
            row = connection.execute(
                "SELECT min(date_ms), max(date_ms) FROM read_parquet(?) WHERE adjusted = 'none'",
                [str(Path(path))],
            ).fetchone()
        finally:
            connection.close()
        if not row or row[0] is None or row[1] is None:
            raise HithinkFinanceError("HiThink daily dump is empty")
        return _ms_to_date(row[0]), _ms_to_date(row[1])

    def iter_adjustment_rows(
        self, path: str | Path, *, chunk_size: int = 10_000
    ) -> Iterator[list[dict[str, Any]]]:
        connection = duckdb.connect()
        try:
            cursor = connection.execute(
                """
                SELECT thscode, ticker, ex_date_ms, dividend_per_share,
                       per_share_bonus, allotment_ratio, allotment_price, currency
                FROM read_parquet(?)
                ORDER BY ex_date_ms, thscode
                """,
                [str(Path(path))],
            )
            columns = [item[0] for item in cursor.description]
            while rows := cursor.fetchmany(chunk_size):
                yield [dict(zip(columns, row, strict=True)) for row in rows]
        finally:
            connection.close()


def _ms_to_date(value: int) -> date:
    return datetime.fromtimestamp(value / 1000, tz=ZoneInfo("Asia/Shanghai")).date()
