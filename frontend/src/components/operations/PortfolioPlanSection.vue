<script setup lang="ts">
import { ClipboardList, Play, Plus, Search, ShieldCheck, Trash2, XCircle } from "@lucide/vue";
import { onMounted, onUnmounted } from "vue";

import { createPortfolioPlanWorkflow } from "../../composables/portfolioPlan";
import { useResearchStore } from "../../stores/research";
import { historyStatusLabel, statusLabel } from "./formatters";
import WeightDistribution from "./WeightDistribution.vue";

const props = defineProps<{ writeEnabled?: boolean; accountId?: number }>();
const emit = defineEmits<{ busyChange: [value: boolean] }>();
const researchStore = useResearchStore();

const {
  form,
  newAsset,
  riskBudgetEnabled,
  styleBudgetEnabled,
  trackingErrorBudgetEnabled,
  volatilityBudgetEnabled,
  riskBudgetDraft,
  isPrefilling,
  prefillBooks,
  prefillBookId,
  prefillRunId,
  isPrefillingFromRun,
  prefillNote,
  prefillFromBacktestRun,
  prefillFromPositionBook,
  plan,
  lookupId,
  approvalLookupId,
  isSubmitting,
  isLoading,
  isLoadingApproval,
  isCheckingReadiness,
  errorMessage,
  paperAccountId,
  readiness,
  equitySnapshot,
  riskEvaluation,
  approval,
  execution,
  confirmationPhrase,
  isCapturingEquity,
  isEvaluatingRisk,
  isApproving,
  isRevokingApproval,
  isExecuting,
  writeEnabled,
  expectedConfirmationPhrase,
  displayedApprovalStatus,
  estimatedCashAfter,
  submitPlan,
  loadPlan,
  captureEquity,
  evaluateRisk,
  confirmPaperExecution,
  executeApprovedPaperBatch,
  loadApproval,
  revokeApproval,
  checkReadiness,
  hashFingerprint,
  formatDateTime,
  formatDecimal,
  formatPercent,
  reasonLabels,
  styleLabels,
  industrySlices,
  symbolSlices,
  start,
  stop,
} = createPortfolioPlanWorkflow(undefined, {
  writeEnabled: () => props.writeEnabled !== false,
  accountId: () => props.accountId,
  onBusyChange: (value) => emit("busyChange", value),
  getLastRunId: () => researchStore.lastRunId,
});

onMounted(start);
onUnmounted(stop);
</script>

<template>
  <section id="portfolio" class="dashboard-section">
    <div class="section-title">
      <ClipboardList :size="20" aria-hidden="true" />
      <h2>组合草案</h2>
      <span class="readonly-pill">手工模拟快照 · 未风控 · 不可执行</span>
    </div>

    <div class="portfolio-layout">
      <form class="portfolio-form" @submit.prevent="submitPlan">
        <div class="form-grid">
          <label>草案名称<input v-model.trim="form.name" required maxlength="100" /></label>
          <label>快照日期<input v-model="form.as_of_date" required type="date" /></label>
          <label>现金<input v-model="form.cash" required inputmode="decimal" /></label>
          <label>现金留存率<input v-model="form.constraints.cash_reserve_rate" required inputmode="decimal" /></label>
          <label>单标的上限<input v-model="form.constraints.max_symbol_weight" required inputmode="decimal" /></label>
          <label>行业上限<input v-model="form.constraints.max_industry_weight" required inputmode="decimal" /></label>
        </div>

        <div class="readiness-panel">
          <label><input v-model="riskBudgetEnabled" type="checkbox" /> 启用组合风险预算</label>
          <p class="section-note">草案预算约束目标权重；不等于真实持仓的当前风险或正式风控。</p>
          <template v-if="riskBudgetEnabled">
            <label>行业风险上限（比例）
              <input v-model="riskBudgetDraft.max_industry_weight" required type="number" min="0" max="1" step="any" />
            </label>
            <p class="section-note">与上方行业上限同时生效，取更严格者；0.40 表示 40%。</p>
            <label><input v-model="styleBudgetEnabled" type="checkbox" /> 限制三因子风格代理暴露</label>
            <label v-if="styleBudgetEnabled">代理暴露绝对值上限（标准化因子分数）
              <input v-model="riskBudgetDraft.max_style_exposure" required type="number" min="0" step="any" placeholder="如 0.50" />
            </label>
            <p v-if="styleBudgetEnabled" class="section-note">仅评估草案日期的动量、波动率、流动性三个因子；规模与估值未纳入。缺任一正权重标的的当日因子时拒绝创建。</p>
            <label><input v-model="trackingErrorBudgetEnabled" type="checkbox" /> 限制年化跟踪误差</label>
            <label v-if="trackingErrorBudgetEnabled">基准代码
              <input v-model.trim="riskBudgetDraft.benchmark" required maxlength="100" placeholder="如 000300.SH" />
            </label>
            <label v-if="trackingErrorBudgetEnabled">跟踪误差上限（比例）
              <input v-model="riskBudgetDraft.max_tracking_error" required type="number" min="0" max="1" step="any" placeholder="如 0.08" />
            </label>
            <label><input v-model="volatilityBudgetEnabled" type="checkbox" /> 限制年化波动率</label>
            <label v-if="volatilityBudgetEnabled">波动率上限（比例）
              <input v-model="riskBudgetDraft.max_portfolio_volatility" required type="number" min="0" max="1" step="any" placeholder="如 0.20" />
            </label>
            <p v-if="trackingErrorBudgetEnabled || volatilityBudgetEnabled" class="section-note">采用截至草案日期的可信前复权价格与至少 60 条同日收益，按恒定目标权重估计历史年化风险；缺日或缺基准时拒绝创建。</p>
          </template>
        </div>

        <div class="asset-editor">
          <div class="asset-editor-header">
            <strong>手工标的快照</strong>
            <div class="asset-editor-actions">
              <select
                v-if="prefillBooks.length > 1"
                v-model.number="prefillBookId"
                class="prefill-book-select"
                aria-label="预填来源账本"
              >
                <option v-for="book in prefillBooks" :key="book.id" :value="book.id">
                  {{ book.name }}
                </option>
              </select>
              <input
                v-model="prefillRunId"
                inputmode="numeric"
                placeholder="回测 run ID"
                class="prefill-run-input"
              />
              <button type="button" class="secondary-action" :disabled="isPrefilling || isPrefillingFromRun" @click="prefillFromPositionBook">
                {{ isPrefilling ? "预填中…" : "从持仓账本预填" }}
              </button>
              <button type="button" class="secondary-action" :disabled="isPrefilling || isPrefillingFromRun" @click="prefillFromBacktestRun">
                {{ isPrefillingFromRun ? "预填中…" : "复制回测目标权重" }}
              </button>
              <button type="button" class="secondary-action" @click="form.assets.push(newAsset())">
                <Plus :size="16" />增加标的
              </button>
            </div>
          </div>
          <p v-if="prefillNote" class="section-note">{{ prefillNote }}</p>
          <div v-for="(asset, index) in form.assets" :key="index" class="asset-row">
            <label>代码<input v-model.trim="asset.symbol" required maxlength="20" /></label>
            <label>类型
              <select v-model="asset.security_type">
                <option value="stock">股票</option><option value="etf">ETF</option><option value="index">指数</option>
              </select>
            </label>
            <label>行业<input v-model.trim="asset.industry" required maxlength="100" /></label>
            <label>价格<input v-model="asset.price" required inputmode="decimal" /></label>
            <label>当前数量<input v-model.number="asset.current_quantity" required min="0" type="number" /></label>
            <label>目标权重<input v-model="asset.requested_weight" required inputmode="decimal" /></label>
            <label>交易单位<input v-model.number="asset.lot_size" required min="1" type="number" /></label>
            <button
              type="button"
              class="remove-action"
              :disabled="form.assets.length === 1"
              aria-label="删除标的"
              @click="form.assets.splice(index, 1)"
            ><Trash2 :size="16" /></button>
          </div>
        </div>

        <p class="form-warning">请核对价格、持仓、行业、现金与草案日期；预填仅复制所示来源快照，草案不代表实时行情或真实账户指令。</p>
        <p v-if="!writeEnabled" class="inline-error">请连接后端并选择模拟账户后再操作。</p>
        <p v-if="errorMessage" class="inline-error">{{ errorMessage }}</p>
        <button class="primary-action" type="submit" :disabled="!writeEnabled || isSubmitting">
          {{ isSubmitting ? "保存中…" : "保存不可执行草案" }}
        </button>
      </form>

      <div class="portfolio-result">
        <div class="lookup-row">
          <input v-model="lookupId" inputmode="numeric" placeholder="草案 ID" />
          <button class="secondary-action" type="button" :disabled="isLoading" @click="loadPlan">
            <Search :size="16" />{{ isLoading ? "读取中…" : "读取" }}
          </button>
        </div>

        <div v-if="plan" class="plan-summary">
          <header>
            <div><span>草案 #{{ plan.plan_id }}</span><strong>{{ plan.name }}</strong></div>
            <span class="readonly-pill">不可执行</span>
          </header>
          <div class="plan-metrics">
            <div><span>总权益</span><strong>{{ formatDecimal(plan.total_equity) }}</strong></div>
            <div><span>目标现金</span><strong>{{ formatDecimal(plan.target_cash) }}</strong></div>
            <div><span>预计现金</span><strong>{{ formatDecimal(plan.cash_after) }}</strong></div>
          </div>
          <div v-if="industrySlices.length || symbolSlices.length" class="plan-distribution">
            <div class="distribution-block">
              <strong>行业权重分布（约束后）</strong>
              <WeightDistribution :slices="industrySlices" label="行业" />
            </div>
            <div class="distribution-block">
              <strong>单标的权重分布（约束后）</strong>
              <WeightDistribution :slices="symbolSlices" label="标的" />
            </div>
          </div>
          <div v-if="plan.risk_budget" class="readiness-panel">
            <strong>草案风险预算 · 截至 {{ plan.as_of_date }}</strong>
            <p class="section-note">行业上限 {{ formatPercent(plan.risk_budget.max_industry_weight) }}；与基础行业上限取更严格者。以下是目标组合的历史代理估计，不是当前真实持仓风险或未来保证。</p>
            <template v-if="plan.risk_budget.max_style_exposure !== null">
              <p class="section-note">三因子宽截面代理绝对暴露上限 {{ formatDecimal(plan.risk_budget.max_style_exposure) }} 个标准化因子分数；规模与估值未评估。</p>
              <p v-if="plan.style_risk?.factor_as_of_date" class="section-note">因子日期 {{ plan.style_risk.factor_as_of_date }} · {{ Object.entries(plan.style_risk.factor_versions).map(([name, version]) => `${styleLabels[name] ?? name} ${version}`).join("、") }}</p>
              <p v-for="[name, evidence] in Object.entries(plan.style_risk?.factor_evidence ?? {})" :key="name" class="section-note">{{ styleLabels[name] ?? name }} 截面 {{ evidence.observed_count }}/{{ evidence.expected_count }} · 因子运行 #{{ evidence.run_id }}</p>
              <ul v-if="plan.style_risk?.style_exposures.length">
                <li v-for="exposure in plan.style_risk.style_exposures" :key="exposure.style_name">
                  {{ styleLabels[exposure.style_name] ?? exposure.style_name }}：{{ formatDecimal(exposure.portfolio_exposure) }} 个标准化因子分数
                </li>
              </ul>
              <p v-if="plan.style_risk?.missing_styles.length" class="inline-error">缺少因子：{{ plan.style_risk.missing_styles.join("、") }}</p>
            </template>
            <p v-if="plan.risk_budget.max_tracking_error !== null" class="section-note">年化跟踪误差：{{ plan.style_risk?.tracking_error === null || !plan.style_risk ? "未计算" : formatPercent(plan.style_risk.tracking_error) }} / 上限 {{ formatPercent(plan.risk_budget.max_tracking_error) }} · 基准 {{ plan.risk_budget.benchmark ?? "未指定" }}</p>
            <p v-if="plan.risk_budget.max_portfolio_volatility !== null" class="section-note">年化波动率：{{ plan.style_risk?.portfolio_volatility === null || !plan.style_risk ? "未计算" : formatPercent(plan.style_risk.portfolio_volatility) }} / 上限 {{ formatPercent(plan.risk_budget.max_portfolio_volatility) }}</p>
            <p v-if="plan.style_risk && plan.style_risk.return_observation_count > 0" class="section-note">历史收益样本 {{ plan.style_risk.sample_start_date }} 至 {{ plan.style_risk.sample_end_date }} · {{ plan.style_risk.return_observation_count }} 条同日收益 · 恒定目标权重估计</p>
          </div>
          <div class="table-panel full">
            <table>
              <thead><tr><th>标的</th><th>原始</th><th>约束后</th><th>最终</th><th>当前/目标/差额</th><th>原因</th></tr></thead>
              <tbody>
                <tr v-for="target in plan.targets" :key="target.symbol">
                  <td>{{ target.symbol }}</td>
                  <td>{{ formatPercent(target.requested_weight) }}</td>
                  <td>{{ formatPercent(target.constrained_weight) }}</td>
                  <td>{{ formatPercent(target.final_weight) }}</td>
                  <td>{{ target.current_quantity }} / {{ target.target_quantity }} / {{ target.order_quantity }}</td>
                  <td>{{ target.reasons.map((reason) => reasonLabels[reason] ?? reason).join("、") || "-" }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="readiness-panel">
            <div class="lookup-row">
              <div>
                <strong>风险前数据核对</strong>
                <span>候选模拟账户，仅作本次只读诊断</span>
              </div>
              <input v-if="accountId === undefined" v-model="paperAccountId" inputmode="numeric" placeholder="模拟账户 ID" />
              <span v-else class="readonly-pill">账户 #{{ accountId }}</span>
              <button
                class="secondary-action"
                type="button"
                :disabled="!writeEnabled || isCheckingReadiness"
                @click="checkReadiness"
              >{{ isCheckingReadiness ? "核对中…" : "重新核对" }}</button>
            </div>
            <div class="lookup-row approval-lookup">
              <div>
                <strong>恢复确认或执行结果</strong>
                <span>响应丢失或页面刷新后，可按确认 ID 回读</span>
              </div>
              <input v-model="approvalLookupId" inputmode="numeric" placeholder="确认 ID" />
              <button class="secondary-action" type="button" :disabled="isLoadingApproval" @click="loadApproval">
                <Search :size="16" />{{ isLoadingApproval ? "读取中…" : "读取确认" }}
              </button>
            </div>
            <div v-if="readiness" class="readiness-result">
              <strong :class="readiness.ready_for_risk_check ? 'readiness-ok' : 'readiness-blocked'">
                {{ readiness.ready_for_risk_check ? "前置数据一致，可进入后续正式风控" : `存在 ${readiness.blockers.length} 个阻断项` }}
              </strong>
              <span>仍未执行风控，且不可创建订单或成交。</span>
              <ul v-if="readiness.blockers.length">
                <li v-for="blocker in readiness.blockers" :key="`${blocker.code}-${blocker.symbol ?? 'all'}`">
                  <code>{{ blocker.code }}</code>
                  <span>{{ blocker.symbol ? `${blocker.symbol} · ` : "" }}{{ blocker.message }}</span>
                </li>
              </ul>
              <div v-if="readiness.ready_for_risk_check" class="risk-actions">
                <button class="secondary-action" type="button" :disabled="isCapturingEquity" @click="captureEquity">
                  {{ isCapturingEquity ? "采集中…" : "采集账户净值快照" }}
                </button>
                <button class="secondary-action" type="button" :disabled="!equitySnapshot || isEvaluatingRisk" @click="evaluateRisk">
                  {{ isEvaluatingRisk ? "评估中…" : "运行正式风控" }}
                </button>
              </div>
              <div v-if="equitySnapshot" class="risk-fact-row">
                <span>净值快照 #{{ equitySnapshot.snapshot_id }}</span>
                <span>观测期 {{ equitySnapshot.observation_count }} 日 · {{ historyStatusLabel(equitySnapshot.history_status) }}</span>
                <span>回撤 {{ formatPercent(equitySnapshot.drawdown_since_monitoring_start) }}</span>
              </div>
              <div v-if="riskEvaluation" class="risk-evaluation-result">
                <strong :class="riskEvaluation.risk_passed ? 'readiness-ok' : 'readiness-blocked'">
                  {{ riskEvaluation.risk_passed ? "风险评估通过" : "风险评估阻断" }}
                </strong>
                <span>检查 #{{ riskEvaluation.check_id }} · 不会创建订单或成交</span>
                <ul>
                  <li v-for="order in riskEvaluation.orders" :key="order.symbol">
                    <code>{{ order.symbol }}</code>
                    <span>{{ order.passed ? "通过" : order.rejection_reasons.join("、") }}</span>
                  </li>
                </ul>
                <div v-if="riskEvaluation.risk_passed" class="approval-panel">
                  <template v-if="!approval">
                    <div class="approval-heading">
                      <ShieldCheck :size="18" aria-hidden="true" />
                      <strong>确认模拟执行意图</strong>
                    </div>
                    <code>{{ expectedConfirmationPhrase }}</code>
                    <input
                      v-model="confirmationPhrase"
                      autocomplete="off"
                      :placeholder="expectedConfirmationPhrase"
                    />
                    <button
                      class="primary-action"
                      type="button"
                      :disabled="!writeEnabled || confirmationPhrase !== expectedConfirmationPhrase || isApproving"
                      @click="confirmPaperExecution"
                    >
                      <ShieldCheck :size="16" />{{ isApproving ? "确认中…" : "记录模拟执行确认" }}
                    </button>
                  </template>
                  <template v-else>
                    <div class="approval-heading">
                      <ShieldCheck :size="18" aria-hidden="true" />
                      <strong>确认 #{{ approval.approval_id }}</strong>
                      <span :class="['status', `status-${displayedApprovalStatus}`]">{{ statusLabel(displayedApprovalStatus ?? "pending") }}</span>
                    </div>
                    <div class="approval-facts">
                      <span>评估 {{ hashFingerprint(approval.evaluation_hash) }}</span>
                      <span>批次 {{ hashFingerprint(approval.order_batch_hash) }}</span>
                      <span>到期 {{ formatDateTime(approval.expires_at) }}</span>
                    </div>
                    <ul class="approval-orders">
                      <li v-for="order in approval.order_summary" :key="order.execution_index">
                        <code>{{ order.execution_index + 1 }} · {{ order.symbol }}</code>
                        <span>
                          {{ order.side === "sell" ? "卖出" : "买入" }}
                          {{ Math.abs(order.signed_quantity) }} · 预计 {{ formatDecimal(order.estimated_fill_price) }}
                          · 费用 {{ formatDecimal(order.estimated_commission) }}
                        </span>
                      </li>
                    </ul>
                    <span v-if="estimatedCashAfter" class="approval-cash">
                      预计执行后现金 {{ formatDecimal(estimatedCashAfter) }}
                    </span>
                    <p class="form-warning">此记录不是执行许可，尚未创建订单或成交。</p>
                    <div v-if="displayedApprovalStatus === 'pending'" class="risk-actions">
                      <button
                        class="primary-action"
                        type="button"
                        :disabled="!writeEnabled || isExecuting"
                        @click="executeApprovedPaperBatch"
                      >
                        <Play :size="16" />{{ isExecuting ? "模拟执行中…" : "执行模拟交易" }}
                      </button>
                      <button
                        class="secondary-action"
                        type="button"
                        :disabled="isRevokingApproval || isExecuting"
                        @click="revokeApproval"
                      >
                        <XCircle :size="16" />{{ isRevokingApproval ? "撤销中…" : "撤销确认" }}
                      </button>
                    </div>
                  </template>
                </div>
                <div v-if="execution" class="execution-result">
                  <strong :class="execution.status === 'completed' ? 'readiness-ok' : 'readiness-blocked'">
                    {{ execution.status === "completed" ? "本地模拟执行完成" : "本地模拟执行已拒绝" }}
                  </strong>
                  <span>批次 #{{ execution.execution_id }} · {{ execution.fill_count }}/{{ execution.order_count }} 笔成交 · 未发送真实订单</span>
                  <span v-if="execution.cash_before !== null && execution.cash_after !== null">
                    现金 {{ formatDecimal(execution.cash_before) }} → {{ formatDecimal(execution.cash_after) }} · 费用 {{ formatDecimal(execution.total_commission) }}
                  </span>
                  <p v-if="execution.failure_message" class="inline-error">{{ execution.failure_code }} · {{ execution.failure_message }}</p>
                  <ul v-if="execution.orders.length">
                    <li v-for="order in execution.orders" :key="order.order_id">
                      <code>{{ order.execution_index + 1 }} · {{ order.symbol }}</code>
                      <span>{{ order.side === "sell" ? "卖出" : "买入" }} {{ order.quantity }} @ {{ formatDecimal(order.fill_price) }}</span>
                    </li>
                  </ul>
                  <p v-if="execution.status === 'completed'" class="form-warning">
                    账户状态已变化，下一步需重新采集净值快照后再进行风控。
                  </p>
                  <p v-else class="form-warning">
                    整批在预演阶段被拒绝，账户、持仓和现金未变化。请修正阻断原因后重新核对、风控并确认。
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>
        <div v-else class="empty-panel">保存新草案或输入 ID 回读。这里不会触发风控、订单或成交。</div>
      </div>
    </div>
  </section>
</template>
