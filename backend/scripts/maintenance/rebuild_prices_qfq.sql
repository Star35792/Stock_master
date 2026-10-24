ATTACH 'data/stock_master.duckdb' AS src (READ_ONLY);
COPY (
    SELECT
        d.symbol,
        CASE
            WHEN split_part(d.symbol, '.', 1)[1:2] IN ('15','16','18','51','56','58') THEN 'etf'
            WHEN split_part(d.symbol, '.', 1)[1:3] = '399'
                 OR (split_part(d.symbol, '.', 2) = 'SH' AND split_part(d.symbol, '.', 1)[1:3] = '000') THEN 'index'
            WHEN split_part(d.symbol, '.', 1)[1:2] IN ('00','30','60','68','83','87','92') THEN 'stock'
            ELSE 'unknown'
        END AS security_type,
        d.trade_date,
        d.open * f.factor AS open,
        d.high * f.factor AS high,
        d.low * f.factor AS low,
        d.close * f.factor AS close,
        d.volume, d.amount,
        d.turnover_rate,
        f.factor AS qfq_factor,
        year(d.trade_date) AS year, month(d.trade_date) AS month
    FROM src.market.daily_bar d
    JOIN LATERAL (
        SELECT af.factor
        FROM src.market.adjustment_factor af
        WHERE af.symbol = d.symbol
          AND af.trade_date = d.trade_date
          AND af.adjust_type = 'qfq'
          AND af.source_name = 'hithink_finance_dump'
          AND af.version = 'v1'
        ORDER BY af.updated_at DESC, af.id DESC
        LIMIT 1
    ) f ON true
    WHERE d.adjust_type = 'none'
      AND d.quality_status = 'trusted'
    ORDER BY d.symbol, d.trade_date
) TO 'data/warm/curated/prices_qfq' (
    FORMAT PARQUET, COMPRESSION ZSTD,
    PARTITION_BY (year, month), PER_THREAD_OUTPUT false,
    ROW_GROUP_SIZE 100000
);
