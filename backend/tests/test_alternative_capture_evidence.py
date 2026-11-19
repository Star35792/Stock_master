from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from app.models.market import AlternativeDataSnapshot, IndexConstituentSnapshot
from app.models.raw import ImportBatch, RawVendorSnapshot
from app.services import eastmoney_collection, hithink_alternative_collection
from app.services.alternative_data_supply import minimum_rows_for

HithinkCollection = hithink_alternative_collection.HithinkAlternativeCollectionService


@pytest.fixture
def database():
    engine = create_engine("duckdb:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA raw"))
        connection.execute(text("CREATE SCHEMA market"))
    for model in (
        ImportBatch, RawVendorSnapshot, AlternativeDataSnapshot, IndexConstituentSnapshot,
    ):
        model.__table__.create(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        yield db
    engine.dispose()


class HithinkSource:
    name = "hithink_finance"
    source_type = "api"
    fail_at = ""
    hot_stock_heat = "1.25"

    def response(self, dataset):
        if dataset == self.fail_at:
            raise RuntimeError(f"vendor failed at {dataset}")
        return {
            "trade_date": "2026-11-19", "item": [
                {"thscode": f"{600000 + i}.SH", "heat": "1"}
                for i in range(minimum_rows_for(dataset))
            ],
        }

    def fetch_limit_up_pool(self):
        return self.response("limit_up_pool")

    def fetch_limit_break_pool(self):
        return self.response("failed_limit_up")

    def fetch_limit_down_pool(self):
        return self.response("limit_down_pool")

    def fetch_limit_up_statistics(self):
        return {**self.response("limit_up_statistics"), "seal_rate": "0.85"}

    def fetch_limit_up_ladder(self):
        self.response("limit_up_ladder")
        return {
            "trade_date": "2026-11-19",
            "item": [{"date": "20261119", "boards": {"two_board": [
                {"thscode": f"{600000 + i}.SH"}
                for i in range(minimum_rows_for("limit_up_ladder"))
            ]}}],
        }

    def fetch_anomaly_list(self):
        return self.response("anomaly")

    def fetch_hot_stock_list(self):
        payload = self.response("hot_stock")
        payload["item"][0]["heat"] = self.hot_stock_heat
        return payload

    def fetch_skyrocket_list(self):
        return self.response("skyrocket")

    def fetch_dragon_tiger_list(self, board_type="all"):
        if board_type == "hot_money":
            return {
                "trade_date": "2026-11-19", "vendor_evidence": "retain me",
                "hot_money_items": [
                    {"name": "seat", "rows": [{"thscode": "600000.SH", "net_value": "12"}]},
                ],
            }
        return {"trade_date": "2026-11-19", "stock_items": [{"thscode": "600000.SH"}]}

    def fetch_ths_indices(self, *, tag):
        return [
            {"thscode": f"{880000 + i}.TI", "name": tag}
            for i in range(minimum_rows_for(f"index_catalog_{tag}"))
        ]

    def fetch_index_constituents(self, *, symbol):
        return [{"thscode": "600000.SH", "weight": "0.1234567890"}]


class EastmoneySource:
    name = "eastmoney"
    source_type = "api"
    fail_at = ""
    inflow = "12.3456万"

    def fetch_main_fund_rank(self, *, limit, market):
        if self.fail_at == "main":
            raise RuntimeError("vendor main failed")
        return [
            {"code": str(600000 + i), "main_net_inflow": self.inflow}
            for i in range(minimum_rows_for("eastmoney_main_fund_rank"))
        ]

    def fetch_sector_fund_flow(self, *, kind, limit):
        if self.fail_at == kind:
            raise RuntimeError(f"vendor {kind} failed")
        return [
            {"code": f"BK{428 + i:04d}", "name": kind, "main_net_inflow": "1.2亿"}
            for i in range(minimum_rows_for(f"eastmoney_{kind}_fund_flow"))
        ]


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


@pytest.mark.parametrize("source_kind", ["hithink", "eastmoney"])
def test_fetch_failure_retains_batch_and_already_captured_raw(database, source_kind):
    if source_kind == "hithink":
        source = HithinkSource()
        source.fail_at = "failed_limit_up"
        service = HithinkCollection(data_source=source)
    else:
        source = EastmoneySource()
        source.fail_at = "industry"
        service = eastmoney_collection.EastmoneyFlowCollectionService(data_source=source)
    with pytest.raises(RuntimeError, match="vendor"):
        service.collect_current(database)
    batch = database.scalar(select(ImportBatch))
    assert batch.status == "failed"
    assert batch.source_name == source.name
    assert batch.row_count == batch.accepted_count == 1
    assert count(database, RawVendorSnapshot) == 1
    assert count(database, AlternativeDataSnapshot) == 0
    captured = database.scalar(select(RawVendorSnapshot))
    assert batch.request_params["raw_snapshot_refs"][0]["raw_snapshot_id"] == captured.id
    assert "vendor" in batch.error_message


@pytest.mark.parametrize("source_kind", ["hithink", "eastmoney"])
def test_first_fetch_failure_still_has_terminal_source_audit(database, source_kind):
    if source_kind == "hithink":
        source = HithinkSource()
        source.fail_at = "limit_up_pool"
        service = HithinkCollection(data_source=source)
    else:
        source = EastmoneySource()
        source.fail_at = "main"
        service = eastmoney_collection.EastmoneyFlowCollectionService(data_source=source)
    with pytest.raises(RuntimeError, match="vendor"):
        service.collect_current(database)
    batch = database.scalar(select(ImportBatch))
    assert batch.status == "failed"
    assert batch.row_count == batch.accepted_count == 0
    assert batch.finished_at is not None
    assert count(database, RawVendorSnapshot) == 0


@pytest.mark.parametrize("source_kind,expected_raw", [("hithink", 14), ("eastmoney", 3)])
def test_market_failure_rolls_back_publication_without_losing_raw(
    database, monkeypatch, source_kind, expected_raw,
):
    module = hithink_alternative_collection if source_kind == "hithink" else eastmoney_collection
    service = (
        module.HithinkAlternativeCollectionService(data_source=HithinkSource())
        if source_kind == "hithink"
        else module.EastmoneyFlowCollectionService(data_source=EastmoneySource())
    )
    original = module._upsert_alternative_rows
    calls = 0

    def fail_second(db, rows):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("normalization/market publication failed")
        return original(db, rows)

    monkeypatch.setattr(module, "_upsert_alternative_rows", fail_second)
    with pytest.raises(ValueError, match="publication failed"):
        service.collect_current(database)
    assert count(database, RawVendorSnapshot) == expected_raw
    assert count(database, AlternativeDataSnapshot) == 0
    batch = database.scalar(select(ImportBatch))
    assert batch.status == "failed"
    assert len(batch.request_params["raw_snapshot_refs"]) == expected_raw
    assert batch.accepted_count == expected_raw


@pytest.mark.parametrize("source_kind,expected_raw", [("hithink", 14), ("eastmoney", 3)])
def test_repeat_payload_reuses_raw_and_references_original_batch(
    database, source_kind, expected_raw,
):
    service = (
        HithinkCollection(data_source=HithinkSource())
        if source_kind == "hithink"
        else eastmoney_collection.EastmoneyFlowCollectionService(data_source=EastmoneySource())
    )
    first = service.collect_current(database)
    published_rows = count(database, AlternativeDataSnapshot)
    second = service.collect_current(database)
    assert first.new_raw_snapshots == expected_raw
    assert second.new_raw_snapshots == 0
    assert second.reused_raw_snapshots == expected_raw
    assert count(database, RawVendorSnapshot) == expected_raw
    assert count(database, AlternativeDataSnapshot) == published_rows
    batches = list(database.scalars(select(ImportBatch).order_by(ImportBatch.id)))
    assert batches[1].accepted_count == 0
    assert batches[1].row_count == expected_raw
    assert all(ref["reused"] for ref in batches[1].request_params["raw_snapshot_refs"])
    assert {
        ref["origin_batch_id"] for ref in batches[1].request_params["raw_snapshot_refs"]
    } == {batches[0].id}


@pytest.mark.parametrize("source_kind,expected_raw", [("hithink", 14), ("eastmoney", 3)])
def test_changed_payload_retains_immutable_raw_versions(database, source_kind, expected_raw):
    if source_kind == "hithink":
        source = HithinkSource()
        service = HithinkCollection(data_source=source)
    else:
        source = EastmoneySource()
        service = eastmoney_collection.EastmoneyFlowCollectionService(data_source=source)
    service.collect_current(database)
    old_payloads = deepcopy(list(database.execute(
        select(RawVendorSnapshot.id, RawVendorSnapshot.raw_payload).order_by(RawVendorSnapshot.id)
    )))
    if source_kind == "hithink":
        source.hot_stock_heat = "9.8765"
    else:
        source.inflow = "98.7654万"
    result = service.collect_current(database)
    assert result.new_raw_snapshots == 1
    assert count(database, RawVendorSnapshot) == expected_raw + 1
    for snapshot_id, payload in old_payloads:
        assert database.get(RawVendorSnapshot, snapshot_id).raw_payload == payload
    if source_kind == "eastmoney":
        value = database.scalar(select(AlternativeDataSnapshot.value).where(
            AlternativeDataSnapshot.dataset == "eastmoney_main_fund_rank",
        ))
        assert value == Decimal("987654")


def test_hot_money_original_response_and_constituents_have_raw_lineage(database):
    service = hithink_alternative_collection.HithinkAlternativeCollectionService(
        data_source=HithinkSource(),
    )
    result = service.collect_current(database, index_symbols=["000300.SH"])
    assert result.raw_snapshots == result.new_raw_snapshots == 15
    snapshot = database.scalar(select(RawVendorSnapshot).where(
        RawVendorSnapshot.dataset == "dragon_tiger_hot_money",
    ))
    assert snapshot.raw_payload["vendor_evidence"] == "retain me"
    assert "hot_money_items" in snapshot.raw_payload
    row = database.scalar(select(AlternativeDataSnapshot).where(
        AlternativeDataSnapshot.dataset == "dragon_tiger_hot_money",
    ))
    assert row.item_key == "seat|600000.SH"
    assert database.scalar(select(RawVendorSnapshot).where(
        RawVendorSnapshot.dataset == "index_constituents",
    )) is not None
    assert count(database, IndexConstituentSnapshot) == 1


def test_malformed_source_date_keeps_raw_evidence_without_market_publication(database):
    class MalformedDateSource(HithinkSource):
        def fetch_limit_up_pool(self):
            return {"trade_date": "2026-99-42", "item": [{"thscode": "600000.SH"}]}

    service = HithinkCollection(data_source=MalformedDateSource())
    capture_date = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    with pytest.raises(ValueError, match="month"):
        service.collect_current(database)
    snapshot = database.scalar(select(RawVendorSnapshot))
    assert snapshot.snapshot_date == capture_date
    assert snapshot.raw_payload == {
        "trade_date": "2026-99-42", "item": [{"thscode": "600000.SH"}],
    }
    assert count(database, AlternativeDataSnapshot) == 0
    batch = database.scalar(select(ImportBatch))
    assert batch.status == "failed"
    assert batch.row_count == batch.accepted_count == 1
    reference = batch.request_params["raw_snapshot_refs"][0]
    assert reference["date_role"] == "capture_date_fallback"
    assert reference["market_published"] is False
    assert reference["date_parse_error"].startswith("ValueError:")
    assert "ValueError:" in batch.error_message
