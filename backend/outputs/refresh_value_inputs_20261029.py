"""One-off use of existing ingestion services, preserving their quality gates."""

import argparse
from dataclasses import asdict
from datetime import date, datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.utils.duckdb_write_lock import duckdb_write_lock


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['sync', 'adjustments', 'valuation', 'catchup', 'calendar', 'screen'])
    parser.add_argument('--trade-date', type=date.fromisoformat)
    args = parser.parse_args()
    stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d-%H%M%S')
    output = Path('outputs') / f'value_input_refresh_{args.stage}_{stamp}.json'
    print(json.dumps({'stage': args.stage, 'status': 'running'}, ensure_ascii=False), flush=True)
    result = {'stage': args.stage, 'started_at': datetime.now().astimezone().isoformat()}
    try:
        with duckdb_write_lock(timeout_seconds=10):
            engine = create_engine(f'duckdb:///{settings.metadata_db_path}')
            try:
                with Session(engine) as db:
                    if args.stage == 'sync':
                        from app.services.auto_data_sync import AutoDataSyncService
                        result['result'] = asdict(AutoDataSyncService().sync(db))
                    elif args.stage == 'adjustments':
                        from app.services.hithink_dump_collection import HithinkDumpCollectionService
                        from app.services.adjustment_build import AdjustmentBuildService
                        result['collection'] = asdict(HithinkDumpCollectionService().collect_adjustments(db))
                        result['build'] = asdict(AdjustmentBuildService().rebuild_qfq(db))
                    elif args.stage == 'valuation':
                        if args.trade_date is None:
                            raise ValueError('--trade-date required')
                        from app.services.price_value_historical_collection import PriceValueHistoricalCollectionService
                        result['result'] = PriceValueHistoricalCollectionService().collect(db, trade_date=args.trade_date)
                    elif args.stage == 'calendar':
                        from app.services.calendar_completion import CalendarCompletionService
                        result['result'] = CalendarCompletionService().run(
                            db, start=date(2026, 9, 1), end=date(2026, 10, 29),
                        )
                    elif args.stage == 'catchup':
                        from app.services.price_value_input_prep import PriceValueInputPrepService
                        result['result'] = PriceValueInputPrepService().run(
                            db, observation_mode='current_research_catchup',
                            price_date=args.trade_date,
                        )
                    else:
                        from app.services.price_value_screen import PriceValueScreenService
                        service = PriceValueScreenService()
                        report = service.run(db)
                        result['result'] = {k: v for k, v in report.items() if k != 'rows'}
            finally:
                engine.dispose()
        result['status'] = 'success'
    except Exception as exc:
        result['status'] = 'failed'
        result['error_type'] = type(exc).__name__
        # Error text can contain presigned download URLs; do not echo those.
        if 'http' not in str(exc).lower():
            result['error'] = str(exc)[:500]
    result['finished_at'] = datetime.now().astimezone().isoformat()
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True, default=str), flush=True)
    print(str(output.resolve()), flush=True)
    return 0 if result['status'] == 'success' else 1


if __name__ == '__main__':
    raise SystemExit(main())
