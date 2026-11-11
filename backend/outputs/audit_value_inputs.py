import json
from datetime import date
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from app.config.settings import settings
from app.repositories.strategy.price_value_screen import PriceValueScreenRepository

engine = create_engine(f'duckdb:///{settings.metadata_db_path}')
with Session(engine) as db:
    for query in (
        "SELECT exchange,count(*),min(trade_date),max(trade_date) FROM market.trading_calendar WHERE trade_date BETWEEN '2026-09-01' AND '2026-10-29' GROUP BY 1",
        "SELECT exchange,trade_date,is_open FROM market.trading_calendar WHERE trade_date BETWEEN '2026-09-01' AND '2026-10-28' ORDER BY trade_date,exchange",
        "SELECT source_name,count(*),min(period_end),max(period_end) FROM fundamental.financial_statement GROUP BY 1",
        "SELECT metrics FROM fundamental.financial_statement WHERE symbol='000002.SZ' AND statement_type='income' AND period_end='2025-12-31' LIMIT 1",
    ):
        print(json.dumps([list(row) for row in db.execute(text(query))], default=str))
    inputs = PriceValueScreenRepository().diagnostic_inputs(db, trade_date=date(2026,10,28), financial_as_of=date(2026,10,29))
    print(json.dumps({'sessions': inputs['sessions'], 'financial_sample': inputs['financial_rows'][:1]},default=str))
engine.dispose()
