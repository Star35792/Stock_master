# 策略优化切片：补扣非净利润估值因子

> 日期：2026-10-20
> 触发：用户指令「调研一下策略并进行优化」
> 范围：`backend/app/services/valuation_factors.py`、对应测试、因子定义；不改变回测、组合、风控或数据契约。

---

## 1. 调研结论

项目策略文档 `aiDoc/strategy/20260922-1314-price-value-margin-strategy.md` 强调：

> 「不能用非经常性损益粉饰的利润判断质量。」

但 `backend/app/services/valuation_factors.py` 的估值因子仅有：

- `earnings_yield`（净利润/市值）
- `book_to_price`
- `sales_to_price`
- `fcf_yield`
- `ev_to_ebitda`
- `industry_valuation_gap`

缺少**扣除非经常性损益后的净利润**入口。`aiDoc/research/20261013-2021-strategy-optimization-review.md` 第 2.3 节已将该缺口标记为「实质漏洞」。

本次优化目标：在估值层补上扣非口径，并同时给出「利润质量」度量，使策略可以同时筛选「便宜」和「利润扎实」的标的。

---

## 2. 实现

### 2.1 新增因子

| 因子名 | 中文名 | 计算口径 | 方向 |
| --- | --- | --- | --- |
| `deducted_earnings_yield` | 扣非盈利收益率 | 扣非净利润 / 最新总市值 | higher_is_better |
| `deducted_earnings_quality` | 扣非利润质量 | 扣非净利润 / 净利润 | higher_is_better |

`deducted_earnings_quality` 越接近 1，利润受非经常性损益影响越小；>1 表示存在非经常性亏损（同样代表质量高）。

### 2.2 数据源别名

新增 `_DEDUCTED_NET_PROFIT` 别名集合，覆盖常见字段名：

- `deducted_net_profit`
- `net_profit_after_deduction`
- `recurring_net_profit`
- `net_profit_excluding_non_recurring`
- `扣非净利润`
- `归属于母公司股东的扣除非经常性损益的净利润`

### 2.3 时点与 fail-closed 行为

- 复用 `ValuationFactorService` 已有的 `availability_policy=valuation_trade_date_and_report_date_before_trade_date`，即财报公告日（`report_date`）必须早于交易日，避免未来函数。
- 当扣非净利润字段缺失或净利润为 0 时，两个因子均返回 `None`，不写 `factor_value`，fail-closed。

### 2.4 分类

两个因子均归属 `FactorCategory.VALUATION`（估值与安全边际），与 `earnings_yield` 保持一致；`deducted_earnings_quality` 虽然带有质量含义，但其输入是纯估值/利润口径，不引入新的财务结构指标，因此不拆分到 `FUNDAMENTAL_QUALITY`。

---

## 3. 代码变更

- `backend/app/services/valuation_factors.py`
  - 新增 `_DEDUCTED_NET_PROFIT` 别名
  - 新增两条 `ValuationFactorSpec`
  - 在 `_calculate_value` 中增加分支
  - 新增 `_deducted_earnings_quality` 辅助函数

- `backend/tests/test_valuation_factors.py`
  - 更新全量运行期望：因子数 7 → 9
  - 新增 `test_deducted_earnings_factors`：正常计算验证
  - 新增 `test_deducted_earnings_yield_falls_back_to_none_when_missing`：缺失扣非字段时 fail-closed

---

## 4. 验证

```bash
cd backend
python -m ruff check app/services/valuation_factors.py tests/test_valuation_factors.py
python -m pytest tests/test_valuation_factors.py tests/test_factor_batch_profiler.py tests/test_factor_pool_classifier.py tests/test_api.py -q
```

结果：

- `ruff`：通过
- `test_valuation_factors.py`：4 passed
- `test_factor_batch_profiler.py` + `test_factor_pool_classifier.py` + `test_api.py`：74 passed，22 warnings（既有 Starlette 弃用警告）

---

## 5. 后续建议

1. 待 `factor_value` 有扣非因子历史数据后，运行 `scripts/factors/run_batch_profiles.py` 生成 IC 证据，纳入 `FactorPoolClassifierService` 的核心池/观察池/冻结池分级。
2. 在 `price_value` 或 `multi_factor` 策略模板中，将 `deducted_earnings_quality` 作为过滤条件（例如 < 0.7 剔除），避免非经常性损益粉饰的价值陷阱。
3. 当 HiThink/Tushare 等数据源提供扣非净利润时，验证字段名是否能被 `_DEDUCTED_NET_PROFIT` 别名命中，必要时扩展别名。

---

## 6. 关联文档

- `aiDoc/research/20261013-2021-strategy-optimization-review.md`：策略层优化评审，已将扣非缺口标记为已关闭
- `aiDoc/strategy/20260922-1314-price-value-margin-strategy.md`：价格与价值匹配策略，扣非口径为策略理念所需
- `aiDoc/research/decision-factor-framework.md`：因子域设计
