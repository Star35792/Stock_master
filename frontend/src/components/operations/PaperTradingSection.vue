<script setup lang="ts">
import { PlayCircle } from "@lucide/vue";

import type { OperationsOverview } from "../../types";
import { formatDate, formatNumber, sideLabel, statusClass } from "./formatters";

defineProps<{
  paperTrading: OperationsOverview["paper_trading"];
}>();
</script>

<template>
  <section id="paper" class="dashboard-section">
    <div class="section-title">
      <PlayCircle :size="20" aria-hidden="true" />
      <h2>模拟盘</h2>
    </div>

    <div class="paper-grid">
      <article class="mini-stat">
        <span>账户</span>
        <strong>{{ paperTrading.account_count }}</strong>
      </article>
      <article class="mini-stat">
        <span>成交</span>
        <strong>{{ paperTrading.fill_count }}</strong>
      </article>
      <article class="mini-stat">
        <span>持仓</span>
        <strong>{{ paperTrading.position_count }}</strong>
      </article>
    </div>

    <div class="table-panel full">
      <table>
        <thead>
          <tr>
            <th>标的</th>
            <th>方向</th>
            <th>数量</th>
            <th>状态</th>
            <th>交易日</th>
            <th>原因</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="order in paperTrading.latest_orders" :key="order.id">
            <td>{{ order.symbol }}</td>
            <td>{{ sideLabel(order.side) }}</td>
            <td>{{ formatNumber(order.order_quantity) }}</td>
            <td><span :class="statusClass(order.status)">{{ order.status }}</span></td>
            <td>{{ formatDate(order.trade_date) }}</td>
            <td>{{ order.rejection_reason ?? "-" }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
