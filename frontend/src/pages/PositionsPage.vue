<script setup lang="ts">
import { ArrowLeftRight, BookOpen, CircleAlert, Pencil, Plus, RefreshCw, Trash2, Wallet } from "@lucide/vue";
import { onMounted } from "vue";

import { createPositionBookWorkspace } from "../composables/positionBook";
import { formatNumber, formatPercent } from "../components/operations/formatters";

const {
  books,
  selectedBookId,
  selectedBook,
  holdings,
  trades,
  loading,
  actionError,
  notice,
  newBookName,
  newBookCash,
  creatingBook,
  editingBook,
  editForm,
  savingBook,
  sortKey,
  sortAsc,
  industryFilter,
  sortedFilteredHoldings,
  industryOptions,
  tradeForm,
  recordingTrade,
  driftPlanId,
  driftThreshold,
  planOptions,
  planTotal,
  planOffset,
  planOptionsLoading,
  planOptionsLoaded,
  planOptionsError,
  selectedPlan,
  drift,
  driftLoading,
  driftOutsidePlanCount,
  driftEarlierQuoteCount,
  quoteCoverage,
  directionLabels,
  toggleSort,
  startEditBook,
  saveBookEdit,
  loadPlanOptions,
  loadBooks,
  refreshBookData,
  handleCreateBook,
  handleRecordTrade,
  handleDeleteTrade,
  handleFetchDrift,
  pnlTone,
  maxExposureWeight,
  start,
} = createPositionBookWorkspace();

onMounted(start);
</script>

<template>
  <section class="positions-page">
    <header class="section-title">
      <Wallet :size="18" />
      <div>
        <h2>持仓分析</h2>
        <p class="section-note">
          手工记录外部终端的真实买卖流水，按可信行情估值；仅用于持仓管理，不连接券商、不下单。
        </p>
      </div>
    </header>

    <p v-if="actionError" class="inline-error"><CircleAlert :size="14" />{{ actionError }}</p>
    <p v-if="notice" class="inline-notice">{{ notice }}</p>
    <p v-if="holdings?.sequence_anomaly" class="inline-error">
      <CircleAlert :size="14" />
      账本存在乱序流水（卖出早于其覆盖的买入被补录）。重放会把该笔卖出所得全额计入已实现盈亏，请核对流水日期后修正。
    </p>

    <div class="lookup-row book-bar">
      <div class="book-select">
        <label>持仓账本</label>
        <select v-model="selectedBookId" :disabled="books.length === 0 || savingBook || recordingTrade" @change="refreshBookData">
          <option v-if="books.length === 0" :value="null">暂无账本</option>
          <option v-for="book in books" :key="book.id" :value="book.id">
            #{{ book.id }} {{ book.name }}（现金 {{ formatNumber(book.cash) }}）
          </option>
        </select>
      </div>
      <button class="secondary-action" type="button" :disabled="loading || selectedBookId === null" @click="refreshBookData">
        <RefreshCw :size="16" />{{ loading ? "刷新中…" : "刷新" }}
      </button>
      <button class="secondary-action" type="button" :disabled="selectedBookId === null" @click="startEditBook">
        <Pencil :size="15" />编辑
      </button>
      <div class="new-book">
        <input v-model="newBookName" placeholder="新账本名称" />
        <input v-model="newBookCash" inputmode="decimal" placeholder="初始现金" />
        <button type="button" :disabled="creatingBook" @click="handleCreateBook">
          <Plus :size="16" />{{ creatingBook ? "创建中…" : "创建账本" }}
        </button>
      </div>
    </div>

    <div v-if="editingBook" class="exposure-panel book-edit-panel">
      <h3><Pencil :size="15" />编辑账本「{{ selectedBook?.name }}」</h3>
      <div class="trade-form">
        <label>名称<input v-model="editForm.name" maxlength="100" /></label>
        <label>现金<input v-model="editForm.cash" inputmode="decimal" /></label>
        <label>备注<input v-model="editForm.note" maxlength="200" placeholder="可选" /></label>
        <button type="button" :disabled="savingBook" @click="saveBookEdit">
          {{ savingBook ? "保存中…" : "保存" }}
        </button>
        <button class="secondary-action" type="button" @click="editingBook = false">取消</button>
      </div>
    </div>

    <div v-if="holdings" class="summary-grid">
      <div class="metric-card"><span>总权益</span><strong>{{ formatNumber(holdings.total_equity) }}</strong><small>现金 {{ formatNumber(holdings.cash) }} + 持仓 {{ formatNumber(holdings.positions_market_value) }}</small><small v-if="quoteCoverage.missing">含 {{ quoteCoverage.missing }} 只成本替代估值，非完整市价权益</small></div>
      <div class="metric-card"><span>已实现盈亏</span><strong :class="pnlTone(holdings.realized_pnl)">{{ formatNumber(holdings.realized_pnl) }}</strong><small>流水重放推导</small></div>
      <div class="metric-card"><span>集中度 HHI</span><strong>{{ formatPercent(holdings.concentration.hhi) }}</strong><small>Top5 {{ formatPercent(holdings.concentration.top5_weight) }} · 最大单票 {{ formatPercent(holdings.concentration.max_symbol_weight) }}</small></div>
      <div class="metric-card" :class="{ warning: holdings.price_stale }">
        <span>最新报价日</span>
        <strong>{{ holdings.price_as_of ?? "无可信价" }}</strong>
        <small>可信未复权收盘；仅是持仓中最晚日期</small>
      </div>
      <div class="metric-card" :class="{ warning: quoteCoverage.missing || quoteCoverage.earlier }"><span>逐仓报价覆盖</span><strong>{{ quoteCoverage.priced }} / {{ quoteCoverage.total }}</strong><small>缺可信价 {{ quoteCoverage.missing }} 只 · 报价早于最晚日 {{ quoteCoverage.earlier }} 只</small></div>
    </div>

    <div v-if="holdings && holdings.holdings.length > 0" class="table-panel full">
      <div class="table-toolbar">
        <label class="industry-filter">行业
          <select v-model="industryFilter">
            <option value="">全部（{{ holdings.holdings.length }} 只）</option>
            <option v-for="industry in industryOptions" :key="industry" :value="industry">
              {{ industry }}（{{ holdings!.holdings.filter((row) => (row.industry ?? "-") === industry).length }}）
            </option>
          </select>
        </label>
      </div>
      <table>
        <thead>
          <tr>
            <th>代码</th><th>名称</th><th>行业</th><th>数量</th><th>成本</th>
            <th>现价 / 报价日</th>
            <th class="sortable" @click="toggleSort('market_value')">市值 {{ sortKey === "market_value" ? (sortAsc ? "↑" : "↓") : "" }}</th>
            <th>浮盈</th>
            <th class="sortable" @click="toggleSort('unrealized_pnl_pct')">收益率 {{ sortKey === "unrealized_pnl_pct" ? (sortAsc ? "↑" : "↓") : "" }}</th>
            <th class="sortable" @click="toggleSort('weight')">权重 {{ sortKey === "weight" ? (sortAsc ? "↑" : "↓") : "" }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in sortedFilteredHoldings" :key="row.symbol">
            <td>{{ row.symbol }}</td>
            <td>{{ row.name ?? "-" }}</td>
            <td>{{ row.industry ?? "-" }}</td>
            <td>{{ formatNumber(row.quantity) }}</td>
            <td>{{ formatNumber(row.average_cost) }}</td>
            <td>
              {{ row.price ? formatNumber(row.price) : "-" }}
              <small v-if="row.price_stale" class="stale-tag">缺价</small>
              <small v-else-if="row.price_as_of">{{ row.price_as_of }}<span v-if="holdings.price_as_of && row.price_as_of < holdings.price_as_of"> · 早于最晚报价</span></small>
              <small v-else class="stale-tag">报价日未记录</small>
              <small v-if="row.sequence_anomaly" class="stale-tag">乱序</small>
            </td>
            <td>{{ formatNumber(row.market_value) }}<small v-if="row.price_stale" class="stale-tag">成本替代，非市价</small></td>
            <td :class="pnlTone(row.unrealized_pnl)">{{ row.unrealized_pnl === null ? "-" : formatNumber(row.unrealized_pnl) }}</td>
            <td :class="pnlTone(row.unrealized_pnl_pct)">{{ row.unrealized_pnl_pct === null ? "-" : formatPercent(row.unrealized_pnl_pct) }}</td>
            <td>{{ formatPercent(row.weight) }}<small v-if="row.price_stale" class="stale-tag">估算</small></td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else-if="holdings && !loading" class="empty-hint">当前账本暂无持仓，先在下方记一笔买入流水。</p>

    <div v-if="holdings && holdings.industry_exposure.length > 0" class="exposure-panel">
      <h3><BookOpen :size="15" />行业暴露</h3>
      <p v-if="quoteCoverage.missing" class="section-note">行业权重含缺价持仓的成本替代估值，仅供定位敞口。</p>
      <div v-for="row in holdings.industry_exposure" :key="row.industry" class="exposure-row">
        <span class="exposure-label">{{ row.industry }}（{{ row.symbol_count }} 只）</span>
        <div class="exposure-bar">
          <div
            class="exposure-fill"
            :style="{ width: `${maxExposureWeight() > 0 ? (Number(row.weight) / maxExposureWeight()) * 100 : 0}%` }"
          />
        </div>
        <span class="exposure-value">{{ formatPercent(row.weight) }}</span>
      </div>
    </div>

    <div class="trade-panel">
      <h3><ArrowLeftRight :size="15" />记一笔交易</h3>
      <div class="trade-form">
        <select v-model="tradeForm.side">
          <option value="buy">买入</option>
          <option value="sell">卖出</option>
        </select>
        <input v-model="tradeForm.symbol" placeholder="证券代码，如 600000.SH" />
        <input v-model="tradeForm.quantity" inputmode="numeric" placeholder="数量" />
        <input v-model="tradeForm.price" inputmode="decimal" placeholder="成交价" />
        <input v-model="tradeForm.fee" inputmode="decimal" placeholder="费用" />
        <input v-model="tradeForm.trade_date" type="date" />
        <button
          type="button"
          :disabled="recordingTrade || selectedBookId === null"
          @click="handleRecordTrade"
        >{{ recordingTrade ? "记录中…" : "记录流水" }}</button>
      </div>
      <p class="section-note">卖出前会校验净持仓；删除流水会重新推导持仓与已实现盈亏。</p>
    </div>

    <div v-if="trades.length > 0" class="table-panel full">
      <table>
        <thead>
          <tr><th>日期</th><th>方向</th><th>代码</th><th>数量</th><th>价格</th><th>费用</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="trade in trades" :key="trade.id">
            <td>{{ trade.trade_date }}</td>
            <td :class="trade.side === 'buy' ? 'positive' : 'negative'">{{ trade.side === "buy" ? "买入" : "卖出" }}</td>
            <td>{{ trade.symbol }}</td>
            <td>{{ formatNumber(trade.quantity) }}</td>
            <td>{{ formatNumber(trade.price) }}</td>
            <td>{{ formatNumber(trade.fee) }}</td>
            <td>
              <button class="icon-action" type="button" :title="'删除流水 #' + trade.id" @click="handleDeleteTrade(trade.id)">
                <Trash2 :size="14" />
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="drift-panel">
      <h3><ArrowLeftRight :size="15" />组合草案偏离检测</h3>
      <div class="lookup-row drift-lookup">
        <select v-model="driftPlanId" aria-label="选择组合草案" :disabled="planOptionsLoading && planOptions.length === 0">
          <option value="">选择组合草案</option>
          <option v-for="item in planOptions" :key="item.plan_id" :value="String(item.plan_id)">#{{ item.plan_id }} · {{ item.name }} · {{ item.as_of_date }}</option>
        </select>
        <input v-model="driftPlanId" inputmode="numeric" aria-label="组合草案 ID" placeholder="或输入草案 ID" />
        <input v-model="driftThreshold" inputmode="decimal" placeholder="阈值，默认 0.02" />
        <button class="secondary-action" type="button" :disabled="driftLoading" @click="handleFetchDrift">
          {{ driftLoading ? "检测中…" : "检测偏离" }}
        </button>
      </div>
      <div class="plan-picker-actions">
        <button class="secondary-action" type="button" :disabled="planOptionsLoading" @click="loadPlanOptions(true)">刷新草案</button>
        <button v-if="planOffset < planTotal" class="secondary-action" type="button" :disabled="planOptionsLoading" @click="loadPlanOptions()">加载更多（{{ planOffset }}/{{ planTotal }}）</button>
      </div>
      <p v-if="planOptionsError" class="section-note">{{ planOptionsError }}；仍可手动输入草案 ID。</p>
      <p v-else-if="planOptionsLoaded && !planOptionsLoading && planTotal === 0" class="section-note">尚无组合草案。可先到 <RouterLink to="/portfolio">组合页</RouterLink> 创建，再返回此处刷新草案。</p>
      <p v-if="selectedPlan" class="section-note">已选「{{ selectedPlan.name }}」：草案截至 {{ selectedPlan.as_of_date }}，草案预算 {{ formatNumber(selectedPlan.total_equity) }} 元；请核对与当前账本的时点和资金规模。</p>
      <p class="section-note">把草案最终权重与当前账本实仓权重对比，只读建议，不生成订单。</p>
      <p v-if="quoteCoverage.earlier" class="section-note">{{ quoteCoverage.earlier }} 只持仓报价早于账本最晚报价日；日期较早可能由停牌等原因造成。偏离计算会阻断缺可信价持仓，但较早的可信价仍会参与，请核对逐仓日期后解释结果。</p>
      <div v-if="drift" class="drift-result">
        <p class="section-note">草案「{{ drift.plan_name }}」截至 {{ drift.plan_as_of_date }}，草案预算 {{ formatNumber(drift.plan_total_equity) }} 元；当前账本权益 {{ formatNumber(drift.total_equity) }} 元。请按当前持仓和行情重新判断该草案是否仍适用。</p>
        <p v-if="drift.price_as_of && drift.plan_as_of_date !== drift.price_as_of" class="section-note">草案截至日与持仓最晚报价日不同；草案目标可能已过期，偏离结果不等于当日可执行方案。</p>
        <div class="plan-metrics">
          <div><span>偏离超阈值</span><strong>{{ drift.summary.off_threshold_count }} / {{ drift.rows.length }}</strong></div>
          <div><span>最大偏离</span><strong>{{ formatPercent(drift.summary.max_abs_drift) }}</strong></div>
          <div><span>现金权重</span><strong>{{ formatPercent(drift.summary.cash_weight) }} / 目标 {{ formatPercent(drift.summary.cash_target_weight) }}</strong></div>
          <div><span>最晚报价日</span><strong>{{ drift.price_as_of ?? "缺价" }}</strong></div>
        </div>
        <p v-if="driftOutsidePlanCount" class="section-note">{{ driftOutsidePlanCount }} 只草案外持仓已按目标权重和目标数量 0 纳入偏离结果；方向仍按所设阈值判断，阈值内显示“持有”表示暂不提示调整。</p>
        <p v-if="driftEarlierQuoteCount" class="section-note">{{ driftEarlierQuoteCount }} 只持仓报价早于最晚报价日，目标数量按各行对应的可信价格估算，请核对日期。</p>
        <div class="table-panel full">
          <table>
            <thead>
              <tr><th>代码</th><th>草案</th><th>报价日</th><th>目标权重</th><th>当前权重</th><th>偏离</th><th>方向</th><th>当前/草案目标数量</th><th>原因</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in drift.rows" :key="row.symbol">
                <td>{{ row.symbol }}</td>
                <td>{{ row.in_plan ? "已纳入" : "草案外" }}</td>
                <td>{{ row.price_as_of ?? "无报价" }}<span v-if="row.price_as_of && drift.price_as_of && row.price_as_of < drift.price_as_of"> · 较早</span></td>
                <td>{{ formatPercent(row.target_weight) }}</td>
                <td>{{ formatPercent(row.current_weight) }}</td>
                <td :class="pnlTone(row.drift)">{{ formatPercent(row.drift) }}</td>
                <td :class="row.direction === 'buy' ? 'positive' : row.direction === 'sell' ? 'negative' : ''">
                  {{ directionLabels[row.direction] }}<small v-if="!row.in_plan && row.direction === 'hold'"> · 阈值内暂不调整</small>
                </td>
                <td>{{ formatNumber(row.current_quantity) }} / {{ row.target_quantity === null ? "-" : formatNumber(row.target_quantity) }}</td>
                <td>{{ row.reasons.join("、") }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.positions-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.inline-notice {
  margin: 0;
  padding: 8px 12px;
  border-radius: 8px;
  background: #ecfdf5;
  color: #047857;
  font-size: 13px;
}

.book-bar {
  flex-wrap: wrap;
  gap: 12px;
  align-items: flex-end;
}

.book-select {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.book-select label {
  font-size: 12px;
  color: var(--text-muted, #64748b);
}

.new-book {
  display: flex;
  gap: 8px;
  margin-left: auto;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
}

.positive {
  color: #dc2626;
}

.negative {
  color: #16a34a;
}

.stale-tag {
  margin-left: 4px;
  padding: 1px 6px;
  border-radius: 8px;
  background: #fef3c7;
  color: #92400e;
  font-size: 11px;
}

.empty-hint {
  color: var(--text-muted, #64748b);
  font-size: 13px;
}

.exposure-panel,
.trade-panel,
.drift-panel {
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 10px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  background: var(--panel-bg, #fff);
}

.exposure-panel h3,
.trade-panel h3,
.drift-panel h3 {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  margin: 0;
}

.drift-lookup {
  flex-wrap: wrap;
  justify-content: flex-start;
}

.drift-lookup select {
  min-width: min(100%, 320px);
  max-width: 100%;
  min-height: 36px;
}

.plan-picker-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.exposure-row {
  display: grid;
  grid-template-columns: 140px 1fr 70px;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}

.exposure-bar {
  height: 10px;
  border-radius: 5px;
  background: #eef2f7;
  overflow: hidden;
}

.exposure-fill {
  height: 100%;
  background: #2563eb;
}

.trade-form {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.drift-result {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.icon-action {
  border: none;
  background: transparent;
  color: #dc2626;
  cursor: pointer;
  padding: 4px;
}
</style>
<style scoped>
.book-edit-panel label {
  display: grid;
  gap: 4px;
  font-size: 12px;
  color: #64748b;
}

.table-toolbar {
  display: flex;
  justify-content: flex-end;
  padding: 8px 10px 0;
}

.industry-filter {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #64748b;
}

.industry-filter select {
  min-height: 32px;
}

.sortable {
  cursor: pointer;
  user-select: none;
}

.sortable:hover {
  color: #2563eb;
}
</style>
