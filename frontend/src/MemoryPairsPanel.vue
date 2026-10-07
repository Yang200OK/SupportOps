<script setup lang="ts">
import DetailDialog from "./DetailDialog.vue";
import { onMounted, onUnmounted, ref } from "vue";
import { ApiError, type Api } from "./api";
type Group = {
  attempted: number;
  completed: number;
  failed_or_stopped: number;
  missing_responses: number;
  known_input_tokens: number;
  known_output_tokens: number;
  unknown_model_calls: number;
  duration_ms_sum: number;
};
type PairReport = {
  available: boolean;
  complete?: boolean;
  status?: string;
  model?: string;
  groups?: Record<string, Group>;
  pairs?: unknown[];
  investigations?: {
    family: string;
    use_memory: boolean;
    investigation_id: string | null;
  }[];
  limitation?: string;
};
const props = defineProps<{ api: Api }>();
const report = ref<PairReport | null>(null);
const detail = ref<unknown>(null);
const detailOpen = ref(false);
const pairsOpen = ref(false);
const error = ref("");
const busy = ref(false);
const cohort = ref("current");
let generation = 0;
let mounted = true;
onUnmounted(() => {
  mounted = false;
  generation++;
});
async function perform(
  action: () => Promise<unknown>,
  target: "report" | "detail",
) {
  if (busy.value) return;
  const epoch = ++generation;
  busy.value = true;
  error.value = "";
  detail.value = null;
  if (target === "report") report.value = null;
  try {
    const body = await action();
    if (mounted && generation === epoch) {
      if (target === "report") report.value = body as PairReport;
      else {
        detail.value = body;
        detailOpen.value = true;
      }
    }
  } catch (exc) {
    if (
      mounted &&
      generation === epoch &&
      !(exc instanceof ApiError && exc.code === "STALE_SESSION")
    )
      error.value = exc instanceof Error ? exc.message : "成对结果读取失败。";
  } finally {
    if (mounted) busy.value = false;
  }
}
const load = () =>
  perform(
    () =>
      props.api.request(
        `/api/memory-pairs${cohort.value === "current" ? "" : `?cohort=${cohort.value}`}`,
      ),
    "report",
  );
onMounted(load);
</script>

<template>
  <section class="card memory-pairs-panel" aria-label="记忆成对实验">
    <div class="card-heading">
      <h2>记忆成对实验</h2>
      <button :disabled="busy" @click="load">刷新成对结果</button>
    </div>
    <p class="notice subtle">
      相同任务、模型、初始状态和预算；失败保留在分母中。模型语义评审不等于人工准确率，token、时延与完成数分开报告。
    </p>
    <label class="cohort-label"
      >实验批次
      <select v-model="cohort" :disabled="busy" @change="load">
        <option value="current">qwen-plus-2025-07-28 历史评测批次</option>
        <option value="max">qwen-max 验证批次（类型错误）</option>
        <option value="legacy">qwen3.7-plus 原批次（保留失败）</option>
      </select>
    </label>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <p v-if="report && !report.available" class="empty">
      尚无可展示的成对运行，状态：{{ report.status }}
    </p>
    <template v-if="report?.available">
      <p>
        {{ report.model }} ·
        {{ report.complete ? "已结束" : "运行中，尚未收齐" }} ·
        {{ report.limitation }}
      </p>
      <div class="pair-groups">
        <article v-for="(g, label) in report.groups" :key="label">
          <h3>{{ label === "on" ? "有记忆" : "无记忆" }}</h3>
          <p>
            完成 {{ g.completed }} / {{ g.attempted }} · 失败或停止
            {{ g.failed_or_stopped }} · 无响应 {{ g.missing_responses }}
          </p>
          <p>
            已知输入 {{ g.known_input_tokens }} / 输出
            {{ g.known_output_tokens }} token · 未知调用
            {{ g.unknown_model_calls }}
          </p>
          <p>调查累计时延 {{ g.duration_ms_sum }} ms；费用未对账</p>
        </article>
      </div>
      <button @click="pairsOpen = true" :disabled="busy">查看配对明细</button>
      <DetailDialog
        v-model:open="pairsOpen"
        title="配对任务与结果"
        :busy="busy"
      >
        <details>
          <summary>配对条件与分层结果</summary>
          <pre>{{ JSON.stringify(report.pairs, null, 2) }}</pre>
        </details>
        <div
          v-for="(r, index) in report.investigations"
          :key="index"
          class="pair-row"
        >
          <span
            >{{ r.family }} · {{ r.use_memory ? "有记忆" : "无记忆" }} ·
            {{ r.investigation_id?.slice(0, 12) ?? "无可读响应" }}</span
          ><button
            :disabled="busy || !r.investigation_id"
            @click="
              perform(
                () =>
                  props.api.request(
                    `/api/investigations/${r.investigation_id}`,
                  ),
                'detail',
              )
            "
          >
            查看成对调查
          </button>
        </div>
      </DetailDialog>
      <DetailDialog
        v-if="detail"
        v-model:open="detailOpen"
        title="成对调查详情"
        :busy="busy"
      >
        <pre class="pair-investigation">{{
          JSON.stringify(detail, null, 2)
        }}</pre>
      </DetailDialog>
    </template>
  </section>
</template>

<style scoped>
.memory-pairs-panel {
  min-width: 0;
}
.cohort-label {
  display: grid;
  gap: 8px;
  margin: 12px 0;
  min-width: 0;
}
.cohort-label select {
  max-width: 100%;
  min-width: 0;
}
.pair-groups {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 260px), 1fr));
  gap: 14px;
}
.pair-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid #ddd7cf;
}
.pair-row span {
  min-width: 0;
  overflow-wrap: anywhere;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 420px;
  overflow: auto;
  padding: 12px;
  background: #f5f3ee;
  font-size: 12px;
}
</style>
