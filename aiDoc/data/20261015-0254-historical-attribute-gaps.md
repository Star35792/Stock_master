# A 股历史时点属性缺口分析

**日期**：2026-10-15
**分析标的**：沪深北 A 股全市场相对估值初筛（price_value_screen）
**数据来源**：`data/stock_master.duckdb` 只读快照
**分析人**：AI 研究团队 · 研究总监

---

## TL;DR

当前 `price_value_screen` 在 2026-09-24 全市场初筛中 **0 只通过**，这不是估值普遍偏贵，而是历史时点属性缺失导致的系统性阻断。三大核心缺口按影响面排序：

1. **行业分类缺失** — 5,582 只 active `stock` 无行业标签，全部触发 `industry_missing`。
2. **ST/特殊处理状态缺失** — 11,114 只已有可信 bar 的股票 `special_treatment_status IS NULL`，全部触发 `treatment_unknown_or_restricted`。
3. **上市日期缺失/未验证** — 725 只股票 `list_date IS NULL`，触发 `listing_unverified`。

在修复上述三项前，全市场相对估值初筛无法产生可复核名单。

---

## 当前数据快照

| 指标 | 数值 | 备注 |
|------|------|------|
| 活跃股票总数（沪深北） | 6,868 | `is_active=TRUE`, `exchange IN ('SH','SZ','BJ')`, `security_type='stock'` |
| 2026-09-24 有可信 bar 的股票 | 5,557 | `quality_status IN ('trusted','manual_fixed')`, 非 suspect |
| 2026-09-24 估值初筛通过数 | 0 | 全部被阻断 |
| 初筛状态 | `blocked` | `no_reviewable_candidates` |

---

## 缺口一：行业分类缺失（最高优先级）

### 现状

```sql
SELECT security_type, COUNT(*)
FROM market.security
WHERE is_active = TRUE AND (industry IS NULL OR industry = '')
GROUP BY security_type;
```

结果：

| security_type | 缺失数 |
|---------------|--------|
| index | 2,144 |
| stock | 5,582 |

**5,582 只活跃 A 股没有行业标签**，占活跃股票总数的 **81.3%**。`price_value_screen` 中每一只缺失行业的股票都会被标记 `industry_missing`。

### 影响面

- 全市场初筛通过率为 0。
- 组合构建中的行业权重约束、风险预算中的行业上限无法正确计算。
- 行业中性化因子无法落地（`market.security_industry_history` 表尚不存在）。
- 金融行业 fail-closed 规则无法触发，因为根本读不到行业。

### 可行数据源

| 来源 | 覆盖 | 质量 | 接入成本 | 备注 |
|------|------|------|----------|------|
| 同花顺 iFinD / HiThink 证券资料 | 高 | 中 | 低 | 已有 `hithink_finance` 接入，可扩展行业字段 |
| 东方财富个股资料 | 高 | 中 | 低 | 已有 Eastmoney 采集链路，可复用 |
| 中证/申万行业分类（官方） | 高 | 高 | 中 | 需要授权或历史成分表 |
| CSMAR/Wind | 高 | 高 | 高 | 商业授权，历史时点最完整 |

### 建议动作

1. **短期**：在 `market.security` 补录当前行业分类（至少申万/东财口径）。
2. **中期**：新建 `market.security_industry_history(symbol, trade_date, industry_code, industry_name, source_name, version)`，支持历史时点回测。
3. **长期**：接入官方行业成分变更事件，避免用当前行业回填历史。

---

## 缺口二：ST/特殊处理状态缺失（最高优先级）

### 现状

```sql
SELECT special_treatment_status, COUNT(*)
FROM market.daily_bar
WHERE trade_date >= '2026-09-18'
GROUP BY special_treatment_status
ORDER BY COUNT(*) DESC LIMIT 10;
```

结果：所有 bar 的 `special_treatment_status` 都是 `NULL`。

进一步统计 2026-09-24：

| 条件 | 数量 |
|------|------|
| 有 bar 但 ST 状态缺失 | 11,114 |
| ST 状态已知且非 normal/none | 0 |

**100% 的日线没有 ST 状态**。`price_value_screen` 将 `special_treatment_status IS NULL` 视为 `treatment_unknown_or_restricted`。

### 影响面

- 全市场初筛全部被拒。
- 回测和模拟盘中无法识别 ST/*ST 股票的涨跌停限制、交易规则差异。
- 风控无法针对 ST 股票设置特殊处理规则。

### 可行数据源

| 来源 | 覆盖 | 质量 | 接入成本 | 备注 |
|------|------|------|----------|------|
| 交易所公告（SSE/SZSE/BJSE） | 高 | 高 | 中 | 权威但需解析公告文本 |
| 同花顺/东财日线接口 | 高 | 中 | 低 | 通常包含 `st_status` 或 `special_treatment` 字段 |
| Tushare/AKShare `stock_company` / `namechange` | 中 | 中 | 低 | 可提供历史 ST 变更记录 |
| 手动维护 ST 事件表 | 低 | 高 | 高 | 不适合全量覆盖 |

### 建议动作

1. **短期**：在日线同步时解析并写入 `market.daily_bar.special_treatment_status`（normal / ST / *ST / SST 等）。
2. **中期**：建立 `market.special_treatment_event(symbol, effective_date, st_status, reason, source_name)` 事件表，支持历史时点查询。
3. **长期**：与交易所公告系统对接，实现 ST 戴帽/摘帽事件自动识别。

---

## 缺口三：上市日期缺失/未验证（中优先级）

### 现状

```sql
SELECT COUNT(*)
FROM market.security
WHERE is_active = TRUE AND security_type = 'stock'
  AND (list_date IS NULL OR list_date > '2026-09-24');
```

结果：**725 只**活跃股票上市日期缺失或晚于目标日期。

### 影响面

- 触发 `listing_unverified`，但这些股票数量相对较少（约 10.6%）。
- 影响新股纳入回测的时间点判断，可能引入未来函数或幸存者偏差。
- `is_active` 为 TRUE 但 `list_date` 缺失，说明证券主数据同步有漏洞。

### 可行数据源

| 来源 | 覆盖 | 质量 | 接入成本 | 备注 |
|------|------|------|----------|------|
| 交易所上市公告 | 高 | 高 | 中 | 权威但需解析 |
| 同花顺/东财证券资料 | 高 | 中 | 低 | 已有接入可扩展 |
| CSRC 上市公司名录 | 高 | 高 | 中 | 公开免费 |

### 建议动作

1. **短期**：在证券主数据同步任务中补抓 `list_date`，并标记缺失为数据质量问题。
2. **中期**：建立证券生命周期事件表 `market.security_lifecycle_event`。
3. **长期**：与 IPO/退市/恢复上市公告联动。

---

## 其他发现

### 1. `market.security_industry_history` 表不存在

roadmap 中提到行业因子按 `market.security_industry_history` 时点标签生效，但当前 schema 中无此表。代码中实际使用 `market.security.industry` 当前值，并在元数据中记录回退。这是已知的技术债。

### 2. `market.daily_bar` 已有 `special_treatment_status` 字段但全部为空

schema 已经预留该字段，说明设计阶段已识别需求，但数据源解析未落地。

### 3. `valuation_snapshot` 中无 ROE 报告期字段

`market.valuation_snapshot` 当前没有 `report_date` 列，ROE 只保存在 `raw_payload` 中。这不影响初筛，但影响历史可比性。

### 4. 公司行为事件已有 `market.corporate_action`

除权除息、送转股等事件已落地，且质量较好。这是三大缺口中唯一相对完整的部分。

---

## 修复优先级建议

| 优先级 | 缺口 | 预计修复后通过数 | 工作量 |
|--------|------|------------------|--------|
| P0 | 行业分类补录 | 从 0 → ~5,500+ | 小（复用现有数据源） |
| P0 | ST 状态解析 | 从 ~5,500 → ~5,500+ | 小（日线同步字段扩展） |
| P1 | 上市日期补录 | 减少 725 只阻断 | 小 |
| P2 | 行业历史时点表 | 支撑回测/因子 | 中 |
| P2 | ST 事件表 | 支撑历史回测 | 中 |
| P3 | 官方公告级数据源 | 长期质量保障 | 大 |

---

## 推荐下一步实施顺序

1. **本周**：扩展 HiThink/东财证券资料同步，把 `industry` 写入 `market.security`；扩展日线同步，把 `special_treatment_status` 写入 `market.daily_bar`。
2. **下周**：跑一次全量 price_value_screen，确认通过名单非零，并人工抽查 20 只。
3. **2 周内**：补 `list_date`，建立 `market.security_industry_history` 和 `market.special_treatment_event` 两张时点表。
4. **1 个月内**：把 price_value_screen 的行业/ST 检查改为优先读取时点表，当前标签仅作为 fallback。

---

## 验证查询

```sql
-- 行业缺失
SELECT COUNT(*) FROM market.security
WHERE is_active = TRUE AND security_type = 'stock' AND (industry IS NULL OR industry = '');

-- ST 状态缺失
SELECT COUNT(*) FROM market.daily_bar
WHERE trade_date = '2026-09-24' AND special_treatment_status IS NULL;

-- 上市日期缺失
SELECT COUNT(*) FROM market.security
WHERE is_active = TRUE AND security_type = 'stock' AND list_date IS NULL;

-- 综合：若三项都补齐，预计可通过的候选数
SELECT COUNT(*) FROM market.security s
JOIN market.daily_bar b ON s.symbol = b.symbol AND b.trade_date = '2026-09-24'
WHERE s.is_active = TRUE AND s.security_type = 'stock'
  AND s.exchange IN ('SH', 'SZ', 'BJ')
  AND s.industry IS NOT NULL AND s.industry != ''
  AND b.special_treatment_status IS NOT NULL
  AND s.list_date IS NOT NULL AND s.list_date <= '2026-09-24';
```

---

## 风险提示

- 即使行业/ST/上市日期补齐，`price_value_screen` 仍只是**人工复核工具**，不代表可自动交易。
- 东方财富 PE/PB 的周期口径未核验，不能等同于 PE(TTM)。
- ROE 为供应商财报加权平均，跨市场比较需使用同一主报告期。
- 历史回测仍需等待 `pit_verified` 数据可得时间和修订血缘落地。
