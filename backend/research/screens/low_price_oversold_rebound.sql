-- 低价超跌反弹选股模板
-- 数据基于 market.daily_bar（前复权）和 market.security
-- 运行前请确认 adjust_type 字段包含 'qfq'
--
-- 可调参数（由 run_screen.py 注入到 SQL 主体，命令行同名参数可覆盖）：
--   max_price          价格上限（默认 15 元）
--   min_ret20          20 日涨跌幅下限（默认 -0.35）
--   max_ret20          20 日涨跌幅上限（默认 -0.15）
--   min_ret5           5 日涨跌幅下限（默认 -0.05）
--   max_ret5           5 日涨跌幅上限（默认 0.15）
--   min_avg_amount     20 日均成交额下限（默认 10000000 元）
--   top_n              返回前 N 只（默认 20）
--   exclude_financials 是否排除金融/银行/证券/保险/期货/黄金/贵金属（1 排除，0 不排除）
--   order_expr         排序表达式（score_intent DESC / score_inverse DESC）
--
-- 评分：四项等权，均在通过硬阈值的候选池内做横截面 PERCENT_RANK 归一到 0~1。
-- PERCENT_RANK 对窗口首行取 0、末行取 1，因此排序方向即偏好方向。
--
--   score_intent（策略本意：低价超跌反弹）
--     rk_low_price   股价越低越好       -> ORDER BY p_latest DESC
--     rk_deep_drop   20 日跌幅越深越好  -> ORDER BY ret20 DESC
--     rk_ret5_up     5 日走势越强越好   -> ORDER BY ret5 ASC
--     rk_liquid      20 日均额越大越好  -> ORDER BY avg_amount_20 ASC
--
--   score_inverse（历史实现：低波回踩）
--     前两项取反（偏好 15 元以下的高价 + 浅跌），第三项偏好近 5 日偏弱，
--     流动性项与 intent 相同。该口径并非"错误打分"，而是另一种因子暴露，
--     样本内表现不同，故保留为可切换模式，由回测结果决定默认值。

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
        s.industry,
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
      AND p.p_latest <= :max_price
      AND (p.p_latest / p.p_d20 - 1) BETWEEN :min_ret20 AND :max_ret20
      AND (p.p_latest / p.p_d5 - 1) BETWEEN :min_ret5 AND :max_ret5
      AND st.avg_amount_20 >= :min_avg_amount
      AND (s.name NOT LIKE '%ST%')
      AND (db.special_treatment_status IS NULL OR db.special_treatment_status != 'ST')
      -- :exclude_financials = 0 时整体跳过下面的金融类排除
      AND (:exclude_financials = 0 OR (
          (s.industry IS NULL OR (
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
      ))
)
SELECT
    b.*,
    ROUND(PERCENT_RANK() OVER (ORDER BY b.p_latest DESC), 4) AS rk_low_price,
    ROUND(PERCENT_RANK() OVER (ORDER BY b.p_latest), 4) AS rk_high_price,
    ROUND(PERCENT_RANK() OVER (ORDER BY b.ret20 DESC), 4) AS rk_deep_drop,
    ROUND(PERCENT_RANK() OVER (ORDER BY b.ret20), 4) AS rk_shallow_drop,
    ROUND(PERCENT_RANK() OVER (ORDER BY b.ret5), 4) AS rk_ret5_up,
    ROUND(PERCENT_RANK() OVER (ORDER BY b.ret5 DESC), 4) AS rk_ret5_down,
    ROUND(PERCENT_RANK() OVER (ORDER BY b.avg_amount_20), 4) AS rk_liquid,
    ROUND((PERCENT_RANK() OVER (ORDER BY b.p_latest DESC)
         + PERCENT_RANK() OVER (ORDER BY b.ret20 DESC)
         + PERCENT_RANK() OVER (ORDER BY b.ret5)
         + PERCENT_RANK() OVER (ORDER BY b.avg_amount_20))::DOUBLE / 4, 4) AS score_intent,
    ROUND(((1 - PERCENT_RANK() OVER (ORDER BY b.p_latest DESC))
         + (1 - PERCENT_RANK() OVER (ORDER BY b.ret20 DESC))
         + (1 - PERCENT_RANK() OVER (ORDER BY b.ret5))
         + PERCENT_RANK() OVER (ORDER BY b.avg_amount_20))::DOUBLE / 4, 4) AS score_inverse
FROM base b
ORDER BY :order_expr
LIMIT :top_n;
