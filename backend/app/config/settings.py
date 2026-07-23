from functools import cached_property
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote

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

    redis_url_override: str | None = Field(default=None, alias="REDIS_URL", repr=False)
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_username: str | None = None
    redis_password: str | None = Field(default=None, repr=False)

    akshare_timeout_seconds: int = 30

    hithink_finance_api_key: str | None = Field(default=None, repr=False)
    hithink_finance_base_url: str = "https://fuyao.aicubes.cn"
    hithink_finance_timeout_seconds: int = 30
    hithink_dump_cache_dir: str = "data/hithink_dumps"
    data_lake_root: str = "data/warm"
    # When true, price-based factor services read forward-adjusted bars from the
    # materialized curated/prices_qfq Parquet instead of the ORM daily_bar table.
    # Only qfq is served by the curated reader; other adjust types still use the
    # ORM path regardless of this flag.
    factor_reads_curated: bool = False

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = Field(default="stock_master", repr=False)
    minio_secret_key: str = Field(default="stock_master_dev_secret", repr=False)
    minio_bucket: str = "stock-master"
    minio_secure: bool = False

    eastmoney_timeout_seconds: int = 15
    eastmoney_retries: int = 2
    xueqiu_cookie: str | None = Field(default=None, repr=False)
    xueqiu_timeout_seconds: int = 15

    @cached_property
    def database_url(self) -> str:
        return f"duckdb:///{self.metadata_db_path}"

    @cached_property
    def redis_url(self) -> str:
        if self.redis_url_override:
            return self.redis_url_override

        auth = ""
        if self.redis_password:
            username = quote(self.redis_username or "")
            password = quote(self.redis_password)
            auth = f"{username}:{password}@" if username else f":{password}@"

        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"


settings = Settings()
