"""六族 alternative 采集调度步骤的行为约束。

这些用例守住三条不变量：

1. **单源失败不拖垮其他源** —— 东财挂了不该让同花顺情绪族也白跑；
2. **空集不等于成功** —— 端点活着但归一化后没有数据，必须报 ``partial``；
3. **失败必须落到 job_log** —— 断供再次伪装成「成功」是这个步骤要根治的病。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.scheduler import alternative_data
from app.scheduler.alternative_data import (
    JOB_NAME,
    run_alternative_data_collection,
)
from app.services.eastmoney_collection import EastmoneyFlowCollectionResult
from app.services.hithink_alternative_collection import AlternativeCollectionResult


class FakeJobLog:
    _id_counter = 0

    def __init__(self) -> None:
        FakeJobLog._id_counter += 1
        self.id = FakeJobLog._id_counter
        self.status = "running"
        self.rows_read = 0
        self.rows_inserted = 0
        self.rows_updated = 0
        self.rows_failed = 0
        self.metadata_: dict | None = None
        self.error_message: str | None = None


class FakeJobLogRepository:
    def __init__(self) -> None:
        self.jobs: list[FakeJobLog] = []

    def create(
        self,
        db: object,
        *,
        job_name: str,
        job_type: str,
        batch_id: int | None = None,
        metadata: dict | None = None,
    ) -> FakeJobLog:
        job = FakeJobLog()
        job.metadata_ = metadata
        self.jobs.append(job)
        return job

    def finish(
        self, db: object, *, job_log: FakeJobLog, status: str, **kwargs: Any
    ) -> FakeJobLog:
        job_log.status = status
        if "error_message" in kwargs:
            job_log.error_message = kwargs["error_message"]
        for key in ("rows_read", "rows_inserted", "rows_updated", "rows_failed"):
            if key in kwargs:
                setattr(job_log, key, kwargs[key])
        if kwargs.get("metadata"):
            job_log.metadata_ = kwargs["metadata"]
        return job_log


class FakeSession:
    def __enter__(self) -> FakeSession:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None


@pytest.fixture
def job_repo() -> FakeJobLogRepository:
    return FakeJobLogRepository()


def _install_sources(
    monkeypatch: pytest.MonkeyPatch,
    *,
    hithink: Any = None,
    eastmoney: Any = None,
) -> None:
    """把两个采集服务替身装进调度模块的命名空间。"""

    class FakeHithink:
        def collect_current(self, db: object) -> AlternativeCollectionResult:
            if isinstance(hithink, Exception):
                raise hithink
            return hithink

    class FakeEastmoney:
        def collect_current(self, db: object) -> EastmoneyFlowCollectionResult:
            if isinstance(eastmoney, Exception):
                raise eastmoney
            return eastmoney

    monkeypatch.setattr(
        alternative_data, "HithinkAlternativeCollectionService", FakeHithink
    )
    monkeypatch.setattr(
        alternative_data, "EastmoneyFlowCollectionService", FakeEastmoney
    )


def test_collection_reports_success_when_every_source_returns_rows(
    monkeypatch: pytest.MonkeyPatch, job_repo: FakeJobLogRepository
) -> None:
    _install_sources(
        monkeypatch,
        hithink=AlternativeCollectionResult(
            batch_no="h1", raw_snapshots=10, normalized_rows=8, constituent_rows=2
        ),
        eastmoney=EastmoneyFlowCollectionResult(
            batch_no="e1", raw_snapshots=5, normalized_rows=5
        ),
    )

    result = run_alternative_data_collection(
        session_factory=FakeSession, job_log_repository=job_repo
    )

    assert result.status == "success"
    assert result.failed is False
    assert result.partial is False
    assert result.empty_datasets == ()
    assert result.failures == {}
    assert result.sources["hithink"]["normalized_rows"] == 8
    assert result.sources["eastmoney"]["normalized_rows"] == 5

    job = job_repo.jobs[-1]
    assert job.status == "success"
    assert job.rows_inserted == 13
    assert job.metadata_["status"] == "success"


def test_one_failing_source_does_not_stop_the_other(
    monkeypatch: pytest.MonkeyPatch, job_repo: FakeJobLogRepository
) -> None:
    _install_sources(
        monkeypatch,
        hithink=RuntimeError("hithink tunnel 502"),
        eastmoney=EastmoneyFlowCollectionResult(
            batch_no="e1", raw_snapshots=5, normalized_rows=5
        ),
    )

    result = run_alternative_data_collection(
        session_factory=FakeSession, job_log_repository=job_repo
    )

    assert result.status == "failed"
    assert result.failed is True
    assert "hithink" in result.failures
    assert "tunnel 502" in result.failures["hithink"]
    # 东财的结果必须保留 —— 一个源挂掉不是全部白跑
    assert result.sources["eastmoney"]["normalized_rows"] == 5

    job = job_repo.jobs[-1]
    assert job.status == "failed"
    assert job.metadata_["failures"]["hithink"].startswith("RuntimeError")


def test_empty_dataset_is_reported_as_partial_not_success(
    monkeypatch: pytest.MonkeyPatch, job_repo: FakeJobLogRepository
) -> None:
    _install_sources(
        monkeypatch,
        hithink=AlternativeCollectionResult(
            batch_no="h1",
            raw_snapshots=4,
            normalized_rows=0,
            constituent_rows=0,
            empty_datasets=("hot_stock",),
        ),
        eastmoney=EastmoneyFlowCollectionResult(
            batch_no="e1", raw_snapshots=5, normalized_rows=5
        ),
    )

    result = run_alternative_data_collection(
        session_factory=FakeSession, job_log_repository=job_repo
    )

    assert result.status == "partial"
    assert result.partial is True
    assert result.failed is False
    assert result.empty_datasets == ("hot_stock",)
    assert job_repo.jobs[-1].status == "partial"
    assert job_repo.jobs[-1].metadata_["empty_datasets"] == ["hot_stock"]


def test_unknown_source_is_reported_as_a_failure(
    monkeypatch: pytest.MonkeyPatch, job_repo: FakeJobLogRepository
) -> None:
    _install_sources(
        monkeypatch,
        hithink=AlternativeCollectionResult(
            batch_no="h1", raw_snapshots=1, normalized_rows=1, constituent_rows=0
        ),
    )

    result = run_alternative_data_collection(
        sources=("hithink", "nonexistent"),
        session_factory=FakeSession,
        job_log_repository=job_repo,
    )

    assert result.status == "failed"
    assert "nonexistent" in result.failures
    assert "unknown alternative data source" in result.failures["nonexistent"]
    assert result.sources["hithink"]["normalized_rows"] == 1


def test_job_name_is_stable_for_operational_lookup(
    job_repo: FakeJobLogRepository,
) -> None:
    """任务名写死在常量里，运维面板按名字查这条记录。"""
    assert JOB_NAME == "alternative_data_collection"
