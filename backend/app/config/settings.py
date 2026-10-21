from functools import cached_property
from hashlib import sha256
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def source_code_version() -> str:
    app_root = Path(__file__).resolve().parents[1]
    digest = sha256()
    for path in sorted(app_root.rglob("*.py")):
        digest.update(path.relative_to(app_root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return f"source-{digest.hexdigest()[:20]}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "stock-master"
    app_env: str = "dev"
    app_debug: bool = True
    app_code_version: str = Field(default_factory=source_code_version)

    metadata_db_path: str = "data/stock_master.duckdb"

    akshare_timeout_seconds: int = 30

    hithink_finance_api_key: str | None = Field(default=None, repr=False)
    hithink_finance_base_url: str = "https://fuyao.aicubes.cn"
    hithink_finance_timeout_seconds: int = 30
    hithink_dump_cache_dir: str = "data/hithink_dumps"
    auto_data_sync_enabled: bool = True
    auto_data_sync_interval_seconds: int = Field(default=3600, ge=60)
    data_lake_root: str = "data/warm"
    raw_landing_root: str = "data/raw_landing"
    price_value_screen_enabled: bool = True
    price_value_screen_dir: str = "data/price_value_screens"
    price_value_input_prep_enabled: bool = True
    # When true, price-based factor services read forward-adjusted bars from the
    # materialized curated/prices_qfq Parquet instead of the ORM daily_bar table.
    # Only qfq is served by the curated reader; other adjust types still use the
    # ORM path regardless of this flag.
    factor_reads_curated: bool = True

    # Root directory for exported warm-layer Parquet files.  The split keeps the
    # control-plane DuckDB small while large analytical tables live here.
    warm_layer_root: str = "data/warm"

    eastmoney_timeout_seconds: int = 15
    eastmoney_retries: int = 2
    xueqiu_cookie: str | None = Field(default=None, repr=False)
    xueqiu_timeout_seconds: int = 15

    @cached_property
    def database_url(self) -> str:
        return f"duckdb:///{self.metadata_db_path}"


settings = Settings()
