-- 低价超跌反弹选股模板
-- 数据基于 market.daily_bar（前复权）和 market.security
-- 运行前请确认 adjust_type 字段包含 'qfq'
--
-- 可调参数：
--   :max_price        价格上限（默认 15 元）
--   :min_ret20        20 日跌幅下限（默认 -35%）
--   :max_ret20        20 日跌幅上限（默认 -15%）
--   :min_ret5         5 日涨跌幅下限（默认 -5%）
--   :max_ret5         5 日涨跌幅上限（默认 +15%）
--   :min_avg_amount   20 日均成交额下限（默认 1000 万元）
--   :top_n            返回前 N 只（默认 20）

WITH dates AS (
    SELECT DISTINCT trade_date
    FROM market.daily_bar
    ORDER BY trade_date DESC
    LIMIT 25
),
latest_date AS (SELECT trade_date AS latest FROM dates LIMIT 1),
d5_date AS (SELECT trade_date AS d5 FROM dates ORDER BY trade_date DESC LIMIT 1 OFFSET 4),
d20_date AS (SELECT trade_date AS d20 FROM dates ORDER BY trade_date DESC LIMIT 1 OFFSET 19),
prices AS (
    SELECT
        symbol,
        MAX(CASE WHEN trade_date = (SELECT latest FROM latest_date) THEN close END) AS p_latest,
        MAX(CASE WHEN trade_date = (SELECT d5 FROM d5_date) THEN close END) AS p_d5,
        MAX(CASE WHEN trade_date = (SELECT d20 FROM d20_date) THEN close END) AS p_d20
    FROM market.daily_bar
    WHERE adjust_type = 'qfq'
      AND trade_date IN ((SELECT latest FROM latest_date), (SELECT d5 FROM d5_date), (SELECT d20 FROM d20_date))
    GROUP BY symbol
),
stats AS (
    SELECT
        symbol,
        AVG(amount) AS avg_amount_20,
        AVG(volume) AS avg_volume_20,
        STDDEV(close) AS std_close_20
    FROM market.daily_bar
    WHERE adjust_type = 'qfq'
      AND trade_date >= (SELECT d20 FROM d20_date)
      AND trade_date <= (SELECT latest FROM latest_date)
    GROUP BY symbol
),
base AS (
    SELECT
        s.symbol,
        s.name,
        s.exchange,
        p.p_latest,
        p.p_d5,
        p.p_d20,
        ROUND((p.p_latest / p.p_d5 - 1) * 100, 2) AS ret5,
        ROUND((p.p_latest / p.p_d20 - 1) * 100, 2) AS ret20,
        st.avg_amount_20,
        st.avg_volume_20
    FROM prices p
    JOIN market.security s ON s.symbol = p.symbol
    JOIN stats st ON st.symbol = p.symbol
    JOIN market.daily_bar db ON db.symbol = p.symbol
        AND db.trade_date = (SELECT latest FROM latest_date)
        AND db.adjust_type = 'qfq'
    WHERE s.security_type = 'stock'
      AND s.is_active
      AND p.p_latest IS NOT NULL
      AND p.p_d5 IS NOT NULL
      AND p.p_d20 IS NOT NULL
      AND p.p_latest <= 15
      AND (p.p_latest / p.p_d20 - 1) BETWEEN -0.35 AND -0.15
      AND (p.p_latest / p.p_d5 - 1) BETWEEN -0.05 AND 0.15
      AND st.avg_amount_20 >= 10000000
      AND (s.industry IS NULL OR (
          s.industry NOT LIKE '%金融%'
          AND s.industry NOT LIKE '%银行%'
          AND s.industry NOT LIKE '%证券%'
          AND s.industry NOT LIKE '%保险%'
          AND s.industry NOT LIKE '%期货%'
          AND s.industry NOT LIKE '%黄金%'
          AND s.industry NOT LIKE '%贵金属%'
      ))
      AND s.name NOT LIKE '%银行%'
      AND s.name NOT LIKE '%保险%'
      AND s.name NOT LIKE '%证券%'
      AND s.name NOT LIKE '%信托%'
      AND s.name NOT LIKE '%期货%'
      AND s.name NOT LIKE '%黄金%'
      AND (s.name NOT LIKE '%ST%')
      AND (db.special_treatment_status IS NULL OR db.special_treatment_status != 'ST')
)
SELECT
    b.*,
    ROUND(PERCENT_RANK() OVER (ORDER BY b.p_latest), 4) AS price_rank,
    ROUND(PERCENT_RANK() OVER (ORDER BY b.ret20), 4) AS ret20_rank,
    ROUND(PERCENT_RANK() OVER (ORDER BY b.ret5 DESC), 4) AS ret5_rank,
    ROUND(PERCENT_RANK() OVER (ORDER BY b.avg_amount_20), 4) AS amount_rank,
    ROUND((PERCENT_RANK() OVER (ORDER BY b.p_latest)
         + PERCENT_RANK() OVER (ORDER BY b.ret20)
         + PERCENT_RANK() OVER (ORDER BY b.ret5 DESC)
         + PERCENT_RANK() OVER (ORDER BY b.avg_amount_20))::DOUBLE / 4, 4) AS score
FROM base b
ORDER BY score DESC
LIMIT 20;
