"""Explicit synthetic fills and cash-dividend accounting; no database or live orders."""

import json
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256

from app.schemas.dividend_scenario import (
    DividendScenarioDay,
    DividendScenarioEventRead,
    DividendScenarioRead,
    DividendScenarioRequest,
)

CENT = Decimal("0.01")


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


class DividendScenarioService:
    def evaluate(self, payload: DividendScenarioRequest) -> DividendScenarioRead:
        dates = [row.trade_date for row in payload.prices]
        if dates != sorted(set(dates)):
            raise ValueError("价格日期须严格递增且不得重复。")
        if (dates[-1] - dates[0]).days > 365:
            raise ValueError("情景窗口最多 366 个自然日。")
        if any(row.trade_date not in dates for row in payload.fills):
            raise ValueError("每笔给定成交都必须有当日未复权合成价格。")
        events = {}
        duplicates = 0
        for event in payload.events:
            if event.event_id in events:
                if event != events[event.event_id]:
                    raise ValueError("同一事件 ID 存在冲突内容或修订，须先显式选择一个版本。")
                duplicates += 1
                continue
            if not dates[0] <= event.record_date < event.ex_date <= event.payment_date:
                raise ValueError("须满足起日 ≤ 登记日 < 除息日 ≤ 到账日，不推测缺失登记历史。")
            for day in (event.record_date, event.ex_date, event.payment_date):
                if dates[0] <= day <= dates[-1] and day not in dates:
                    raise ValueError("窗口内登记、除息及到账日必须有明确处理日与价格，不得跳过。")
            events[event.event_id] = event
        # A canonical request hash is independent of duplicate events and their input order.
        contract = payload.model_dump()
        contract["symbol"] = payload.symbol.upper()
        contract["events"] = [events[key].model_dump() for key in sorted(events)]
        contract["version"] = "synthetic_cash_dividend_v1"

        def encode(value):
            if isinstance(value, Decimal):
                return "0" if value == 0 else format(value.normalize(), "f")
            if isinstance(value, date):
                return value.isoformat()
            raise TypeError(type(value).__name__)

        digest = sha256(
            json.dumps(contract, sort_keys=True, separators=(",", ":"), default=encode).encode()
        ).hexdigest()
        fills_by_date = defaultdict(list)
        for fill in payload.fills:
            fills_by_date[fill.trade_date].append(fill)
        state = {
            key: DividendScenarioEventRead(
                event_id=key,
                revision=event.revision,
                eligible_quantity=0,
                gross=Decimal(0),
                assumed_tax=Decimal(0),
                net=Decimal(0),
                status="awaiting_record",
            )
            for key, event in events.items()
        }
        cash, quantity = payload.initial_cash, payload.initial_quantity
        receivable = gross_total = tax_total = fees = Decimal(0)
        initial = cash + quantity * payload.initial_price
        if initial <= 0:
            raise ValueError("期初权益须为正。")
        output = []
        for price in payload.prices:
            day = price.trade_date
            sellable = quantity  # initial holdings and previous-day buys are assumed settled
            # Same-day ex/payment must first create the receivable, then settle it.
            for key in sorted(events):
                event, item = events[key], state[key]
                if event.ex_date == day:
                    item.gross = cents(item.eligible_quantity * event.cash_per_share)
                    item.assumed_tax = cents(item.gross * event.assumed_tax_rate)
                    item.net = item.gross - item.assumed_tax
                    item.status = "receivable"
                    receivable += item.net
                    gross_total += item.gross
                    tax_total += item.assumed_tax
            for key in sorted(events):
                if events[key].payment_date == day:
                    item = state[key]
                    cash += item.net
                    receivable -= item.net
                    item.status = "paid"
            for fill in fills_by_date[day]:
                amount = cents(fill.quantity * fill.price)
                if fill.side == "buy":
                    if cash < amount + fill.fee:
                        raise ValueError(f"{day} 买入现金不足，应收股息不可提前使用。")
                    cash -= amount + fill.fee
                    quantity += fill.quantity
                    if quantity > 10_000_000:
                        raise ValueError("情景持股数量超过上限。")
                else:
                    if fill.quantity > sellable:
                        raise ValueError(f"{day} 超过可卖数量，不允许卖空或卖出当日新买股份。")
                    if cash + amount < fill.fee:
                        raise ValueError(f"{day} 卖出后现金不足以支付给定费用。")
                    cash += amount - fill.fee
                    quantity -= fill.quantity
                    sellable -= fill.quantity
                fees += fill.fee
            for key in sorted(events):
                if events[key].record_date == day:
                    state[key].eligible_quantity = quantity
                    state[key].status = "awaiting_ex"
            value = quantity * price.close
            output.append(
                DividendScenarioDay(
                    trade_date=day,
                    cash=cash,
                    quantity=quantity,
                    sellable_quantity=sellable,
                    close=price.close,
                    receivable=receivable,
                    market_value=value,
                    total_equity=cash + receivable + value,
                    cumulative_gross_dividend=gross_total,
                    cumulative_assumed_tax=tax_total,
                    cumulative_fees=fees,
                )
            )
        return DividendScenarioRead(
            symbol=payload.symbol.upper(),
            contract_sha256=digest,
            initial_equity=initial,
            final_equity=output[-1].total_equity,
            total_return=output[-1].total_equity / initial - 1,
            duplicate_events_ignored=duplicates,
            days=output,
            events=[state[key] for key in sorted(state)],
            declarations=[
                "合成情景只消费本次显式输入，不读取或写入真实行情、持仓与研究运行。",
                "输入价格为未复权合成价格；禁止将复权总收益价格与现金派息重复计算。",
                "固定假设税率在除息日计提净应收，不代表真实持有期税务或实际税后收益。",
                "顺序：除息确认应收→到账→按输入顺序逐笔成交→登记日收盘资格及估值。",
                "成交是给定情景假设，不认证涨跌停、流动性或真实成交；不产生订单。",
                "期初股份假定已结算，当日新买股份不可当日卖出；非连续日期是假定处理日。",
                "正式回测公司行为政策仍为 reject；本工具不授予历史时点或执行资格。",
            ],
        )
