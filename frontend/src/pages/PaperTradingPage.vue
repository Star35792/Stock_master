<script setup lang="ts">
import {
  ArrowRight,
  Check,
  CircleAlert,
  CircleHelp,
  Layers3,
  LockKeyhole,
  ShieldCheck,
} from "@lucide/vue";
import { computed, onMounted, ref } from "vue";

import { fetchPaperAccounts } from "../api";
import PortfolioPlanSection from "../components/operations/PortfolioPlanSection.vue";
import type { PaperAccount } from "../types";

type WorkflowStep = {
  id: number;
  label: string;
  detail: string;
};

const steps: WorkflowStep[] = [
  { id: 1, label: "组合草案", detail: "输入目标与约束，生成不可执行草案" },
  { id: 2, label: "事实与风控", detail: "核对账户、行情、净值和容量数据" },
  { id: 3, label: "人工确认", detail: "确认订单摘要与五分钟有效期" },
  { id: 4, label: "模拟执行", detail: "卖先买后，整批写入本地模拟账户" },
];

const activeStep = ref(1);
const environment = ref<"paper" | "live">("paper");
const accountId = ref("");
const accounts = ref<PaperAccount[]>([]);
const accountsLoading = ref(false);
const backendAvailable = ref(false);
const tradingBusy = ref(false);
const pageError = ref<string | null>(null);

const canWrite = computed(() => backendAvailable.value && environment.value === "paper" && Boolean(accountId.value));
const activeStepInfo = computed(() => steps[activeStep.value - 1]);

function chooseStep(step: number) {
  activeStep.value = step;
  pageError.value = null;
}

function validateEnvironment() {
  if (environment.value !== "paper") {
    pageError.value = "当前工作台只允许 paper-only 环境，真实交易入口未开放。";
    environment.value = "paper";
    return;
  }
  if (!accountId.value) {
    pageError.value = "请选择一个模拟账户后再开始写入操作。";
    return;
  }
  pageError.value = null;
}

async function loadAccounts() {
  accountsLoading.value = true;
  try {
    accounts.value = await fetchPaperAccounts();
    backendAvailable.value = true;
    accountId.value = accounts.value[0] ? String(accounts.value[0].id) : "";
  } catch (error) {
    backendAvailable.value = false;
    pageError.value = error instanceof Error ? error.message : "模拟账户读取失败";
  } finally {
    accountsLoading.value = false;
  }
}

onMounted(() => void loadAccounts());
</script>

<template>
  <main class="paper-page">
    <header class="paper-hero">
      <div>
        <div class="eyebrow"><Layers3 :size="15" />操作工作台 / PAPER</div>
        <h1>模拟盘执行</h1>
        <p>从组合草案到本地成交，所有步骤都绑定可回放的事实和人工确认。</p>
      </div>
      <div class="safety-badge"><LockKeyhole :size="16" />不会发送真实订单</div>
    </header>

    <section class="environment-panel" aria-label="运行环境">
      <div class="panel-heading">
        <div>
          <span class="section-kicker">运行边界</span>
          <h2>先选择操作上下文</h2>
        </div>
        <span class="state-pill state-pill--ok"><ShieldCheck :size="15" />paper-only</span>
      </div>
      <div class="context-grid">
        <label>
          <span>环境</span>
          <select v-model="environment">
            <option value="paper">Paper · 本地模拟</option>
            <option value="live" disabled>Live · 未开放</option>
          </select>
        </label>
        <label>
          <span>模拟账户</span>
          <select v-model="accountId" :disabled="accountsLoading || tradingBusy" @change="validateEnvironment">
            <option value="">{{ accountsLoading ? "账户加载中…" : "请选择账户" }}</option>
            <option v-for="account in accounts" :key="account.id" :value="String(account.id)">
              {{ account.name }} · 可用现金 {{ account.cash_balance }}
            </option>
          </select>
        </label>
        <div class="context-note">
          <CircleHelp :size="17" />
          <span>账户来自当前 quant_db。真实账户、券商连接和自动交易均不在此页面。</span>
        </div>
      </div>
      <div v-if="pageError" class="page-alert page-alert--error" role="alert">
        <CircleAlert :size="17" />{{ pageError }}
      </div>
    </section>

    <nav class="workflow" aria-label="模拟盘流程">
      <button
        v-for="step in steps"
        :key="step.id"
        type="button"
        :class="['workflow-step', { 'workflow-step--active': activeStep === step.id } ]"
        @click="chooseStep(step.id)"
      >
        <span class="workflow-index">
          <Check v-if="step.id < activeStep" :size="15" />
          <span v-else>{{ step.id }}</span>
        </span>
        <span class="workflow-copy">
          <strong>{{ step.label }}</strong>
          <small>{{ step.detail }}</small>
        </span>
        <ArrowRight v-if="step.id < steps.length" class="workflow-arrow" :size="16" />
      </button>
    </nav>

    <section class="stage-banner">
      <div>
        <span class="section-kicker">当前阶段 {{ activeStep }}/{{ steps.length }}</span>
        <h2>{{ activeStepInfo.label }}</h2>
        <p>{{ activeStepInfo.detail }}</p>
      </div>
      <div class="stage-status">
        <span class="state-dot" />
        <span>{{ canWrite ? "已选择模拟账户，可进行本地写入" : backendAvailable ? "等待选择模拟账户" : "模拟交易后端不可用" }}</span>
      </div>
    </section>

    <section v-if="!accountId" class="empty-state">
      <CircleHelp :size="22" />
      <div>
        <strong>还没有模拟账户上下文</strong>
        <p>选择模拟账户后，组合草案、风控、确认和执行控件才会启用。</p>
      </div>
    </section>

    <section id="paper-operation" class="operation-surface">
      <PortfolioPlanSection
        :account-id="accountId ? Number(accountId) : undefined"
        :write-enabled="canWrite"
        @busy-change="tradingBusy = $event"
      />
    </section>
  </main>
</template>

<style scoped>
.paper-page {
  --paper-ink: #172033;
  --paper-muted: #64748b;
  --paper-border: #dbe2ea;
  --paper-surface: #ffffff;
  --paper-accent: #0f766e;
  max-width: 1180px;
  margin: 0 auto;
  padding: 32px 28px 72px;
  color: var(--paper-ink);
}

.paper-hero,
.panel-heading,
.stage-banner,
.context-grid,
.stage-status,
.eyebrow,
.safety-badge,
.state-pill,
.page-alert,
.context-note,
.empty-state {
  display: flex;
  align-items: center;
}

.paper-hero,
.panel-heading,
.stage-banner {
  justify-content: space-between;
  gap: 24px;
}

.paper-hero { margin-bottom: 24px; }
.eyebrow,
.section-kicker {
  color: var(--paper-accent);
  font-size: 12px;
  font-weight: 750;
  letter-spacing: .08em;
  text-transform: uppercase;
}
.eyebrow { gap: 7px; }
h1, h2, p { margin: 0; }
h1 { margin-top: 8px; font-size: clamp(28px, 4vw, 42px); letter-spacing: 0; }
h2 { margin-top: 5px; font-size: 20px; letter-spacing: 0; }
.paper-hero p { margin-top: 8px; color: var(--paper-muted); font-size: 14px; }
.safety-badge,
.state-pill { gap: 7px; border: 1px solid #bfe4d5; border-radius: 999px; padding: 8px 12px; color: #17624f; background: #effbf6; font-size: 12px; font-weight: 700; white-space: nowrap; }

.environment-panel,
.stage-banner,
.operation-surface,
.empty-state { border: 1px solid var(--paper-border); border-radius: 8px; background: var(--paper-surface); }
.environment-panel { padding: 20px; }
.context-grid { align-items: end; gap: 16px; margin-top: 20px; }
label { display: grid; gap: 7px; min-width: 190px; color: var(--paper-muted); font-size: 12px; font-weight: 700; }
select { width: 100%; min-height: 40px; border: 1px solid #cbd5e1; border-radius: 6px; padding: 0 10px; color: var(--paper-ink); background: #fff; font: inherit; }
.context-note { flex: 1; align-items: flex-start; gap: 8px; padding: 10px 0; color: var(--paper-muted); font-size: 12px; line-height: 1.5; }
.page-alert { gap: 8px; margin-top: 14px; padding: 10px 12px; border-radius: 6px; color: #9f1239; background: #fff1f2; font-size: 13px; }

.workflow { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 20px 0; }
.workflow-step { position: relative; display: flex; align-items: center; gap: 10px; min-height: 72px; border: 1px solid var(--paper-border); border-radius: 8px; padding: 12px; text-align: left; color: var(--paper-muted); background: #f8fafc; cursor: pointer; }
.workflow-step--active { border-color: #74b9ae; color: var(--paper-ink); background: #f0faf8; box-shadow: inset 0 0 0 1px #74b9ae; }
.workflow-index { display: grid; place-items: center; width: 28px; height: 28px; flex: 0 0 28px; border: 1px solid #cbd5e1; border-radius: 50%; font-size: 12px; font-weight: 800; }
.workflow-step--active .workflow-index { border-color: var(--paper-accent); color: #fff; background: var(--paper-accent); }
.workflow-copy { display: grid; gap: 3px; min-width: 0; }
.workflow-copy strong { font-size: 13px; }
.workflow-copy small { color: var(--paper-muted); font-size: 11px; line-height: 1.35; }
.workflow-arrow { margin-left: auto; color: #94a3b8; }

.stage-banner { margin-bottom: 16px; padding: 18px 20px; background: #f8fafc; }
.stage-banner p { margin-top: 7px; color: var(--paper-muted); font-size: 13px; }
.stage-status { gap: 8px; color: var(--paper-muted); font-size: 12px; white-space: nowrap; }
.state-dot { width: 8px; height: 8px; border-radius: 50%; background: #f59e0b; }
.empty-state { gap: 14px; margin-bottom: 16px; padding: 18px 20px; color: var(--paper-muted); }
.empty-state strong { color: var(--paper-ink); font-size: 14px; }
.empty-state p { margin-top: 4px; font-size: 12px; }
.operation-surface { overflow: hidden; }

@media (max-width: 860px) {
  .paper-page { padding: 24px 16px 48px; }
  .context-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .context-note { grid-column: 1 / -1; }
  .workflow { grid-template-columns: repeat(2, 1fr); }
}

@media (max-width: 560px) {
  .paper-hero, .panel-heading, .stage-banner { align-items: flex-start; flex-direction: column; }
  .context-grid, .workflow { grid-template-columns: 1fr; }
  .workflow-step { min-height: 60px; }
  .workflow-arrow { display: none; }
  .stage-status { white-space: normal; }
}
</style>
