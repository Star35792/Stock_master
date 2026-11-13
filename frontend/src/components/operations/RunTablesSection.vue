<script setup lang="ts">
import { onMounted, ref } from "vue";
import { BarChart3, LineChart } from "@lucide/vue";

import type { OperationsOverview } from "../../types";
import { formatDate, formatDateTime, formatNumber, formatPercent, statusClass, statusLabel, strategyLabel } from "./formatters";

const props = defineProps<{
  factors: OperationsOverview["factors"];
  backtests: OperationsOverview["backtests"];
}>();

const runPage = ref<OperationsOverview["backtests"]["latest_runs"]>([]);
const runTotal = ref(0);
const runOffset = ref(0);
const runError = ref("");
const runLoading = ref(false);
const loaded = ref(false);

async function loadRuns(offset = 0) {
  runLoading.value = true;
  runError.value = "";
  try {
    const response = await fetch(`/api/v1/backtests/runs?limit=20&offset=${offset}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const page = await response.json() as {
      items: OperationsOverview["backtests"]["latest_runs"];
      total: number;
    };
    runPage.value = page.items;
    runTotal.value = page.total;
    runOffset.value = offset;
    loaded.value = true;
  } catch (cause) {
    runError.value = `历史运行读取失败：${cause instanceof Error ? cause.message : "未知错误"}；${loaded.value ? "保留上次读取的列表" : "下表显示工作台最近摘要"}。`;
  } finally { runLoading.value = false; }
}
onMounted(() => { void loadRuns(); });
</script>

<template>
  <section id="factors" class="dashboard-section">
    <div class="section-title">
      <BarChart3 :size="20" aria-hidden="true" />
      <h2>因子运行</h2>
    </div>

    <div class="table-panel full">
      <table>
        <thead>
          <tr>
            <th>因子</th>
            <th>版本</th>
            <th>状态</th>
            <th>输入区间</th>
            <th>写入行</th>
            <th>结束</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="run in factors.latest_runs" :key="run.id">
            <td>{{ run.factor_name }}</td>
            <td>{{ run.factor_version }}</td>
            <td><span :class="statusClass(run.status)">{{ statusLabel(run.status) }}</span></td>
            <td>{{ formatDate(run.input_start_date) }} - {{ formatDate(run.input_end_date) }}</td>
            <td>{{ formatNumber(run.rows_written) }}</td>
            <td>{{ formatDateTime(run.finished_at) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>

  <section id="backtests" class="dashboard-section">
    <div class="section-title">
      <LineChart :size="20" aria-hidden="true" />
      <h2>回测结果</h2>
    </div>
    <p v-if="runError" role="alert">{{ runError }}</p>
    <p v-if="runLoading" role="status">正在读取历史运行…</p>

    <div class="table-panel full">
      <table>
        <thead>
          <tr>
            <th>名称</th>
            <th>策略</th>
            <th>状态</th>
            <th>区间</th>
            <th>最终权益</th>
            <th>收益</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="run in loaded ? runPage : props.backtests.latest_runs" :key="run.id">
            <td><RouterLink v-if="loaded" :to="`/backtests/runs/${run.id}`">{{ run.name }}</RouterLink><span v-else>{{ run.name }}</span></td>
            <td>{{ strategyLabel(run.strategy_name) }}</td>
            <td><span :class="statusClass(run.status)">{{ statusLabel(run.status) }}</span></td>
            <td>{{ formatDate(run.start_date) }} - {{ formatDate(run.end_date) }}</td>
            <td>{{ formatNumber(run.final_equity) }}</td>
            <td>{{ formatPercent(run.total_return) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-if="loaded && runTotal === 0">暂无历史回测运行。</p>
    <p v-if="loaded && runTotal > 0">
      第 {{ runOffset + 1 }}–{{ Math.min(runOffset + 20, runTotal) }} / {{ runTotal }} 条
      <button type="button" :disabled="runOffset === 0 || runLoading" @click="loadRuns(runOffset - 20)">上一页</button>
      <button type="button" :disabled="runOffset + 20 >= runTotal || runLoading" @click="loadRuns(runOffset + 20)">下一页</button>
    </p>
  </section>
</template>
