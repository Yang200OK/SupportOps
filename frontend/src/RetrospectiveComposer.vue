<script setup lang="ts">
import { onUnmounted, ref, watch } from "vue";
import { ApiError, type Api } from "./api";
import type { Retrospective } from "./memory-types";
const props = defineProps<{
  api: Api;
  investigationId: string;
  ticketId: string;
}>();
const open = ref(false);
const actions = ref<
  { job_id: string; investigation_id: string; status: string }[]
>([]);
const actionId = ref("");
const days = ref(7);
const result = ref<Retrospective | null>(null);
const busy = ref(false);
const error = ref("");
let requestId = crypto.randomUUID();
let generation = 0;
let mounted = true;
watch([actionId, days], () => {
  requestId = crypto.randomUUID();
  result.value = null;
  generation++;
});
watch(
  () => props.investigationId,
  () => {
    generation++;
    open.value = false;
    result.value = null;
    actions.value = [];
    actionId.value = "";
    error.value = "";
    requestId = crypto.randomUUID();
  },
);
onUnmounted(() => {
  mounted = false;
  generation++;
});
async function perform(action: (epoch: number) => Promise<void>) {
  if (busy.value) return;
  const epoch = generation;
  busy.value = true;
  error.value = "";
  try {
    await action(epoch);
  } catch (exc) {
    if (
      mounted &&
      generation === epoch &&
      !(exc instanceof ApiError && exc.code === "STALE_SESSION")
    )
      error.value = exc instanceof Error ? exc.message : "复盘失败。";
  } finally {
    if (mounted) busy.value = false;
  }
}
async function prepare(epoch: number) {
  const response = await props.api.request<{ items: typeof actions.value }>(
    `/api/tickets/${props.ticketId}/actions`,
  );
  if (!mounted || epoch !== generation) return;
  actions.value = response.items.filter(
    (a) =>
      a.investigation_id === props.investigationId &&
      ["completed", "failed", "rejected", "cancelled", "uncertain"].includes(
        a.status,
      ),
  );
  open.value = true;
}
async function create(epoch: number) {
  const body = await props.api.request<Retrospective>(
    `/api/investigations/${props.investigationId}/retrospectives`,
    {
      method: "POST",
      body: JSON.stringify({
        request_id: requestId,
        action_id: actionId.value || null,
        expires_in_days: days.value,
      }),
    },
  );
  if (mounted && epoch === generation) result.value = body;
}
</script>
<template>
  <section class="memory-composer" aria-label="调查复盘">
    <button :disabled="busy" @click="perform(prepare)">准备事件复盘</button>
    <p v-if="error" role="alert" class="notice danger">{{ error }}</p>
    <div v-if="open">
      <p class="muted">
        摘录已保存的调查事件，不新增模型调用。候选未经人工语义复核，不能证明原因。
      </p>
      <label
        >复盘绑定动作<select v-model="actionId" :disabled="busy">
          <option value="">只复盘调查</option>
          <option v-for="a in actions" :key="a.job_id" :value="a.job_id">
            {{ a.status }} · {{ a.job_id.slice(0, 8) }}
          </option>
        </select></label
      >
      <label
        >候选有效天数<input
          type="number"
          v-model="days"
          min="1"
          max="30"
          :disabled="busy"
      /></label>
      <button
        :disabled="
          busy || !!result || days < 1 || days > 30 || !Number.isInteger(days)
        "
        @click="perform(create)"
      >
        生成摘录摘要与候选
      </button>
    </div>
    <div v-if="result" class="notice">
      <h4>复盘已保存</h4>
      <p>
        {{ result.summary.event_count }} 个调查事件 · 新增模型调用
        {{ result.model_calls }}
      </p>
      <p>
        {{
          result.candidate
            ? "已生成待审核经验候选，可在经验与 Skill 查看与治理。"
            : "本记录只保存摘要，未生成可召回候选。"
        }}
      </p>
      <p class="fingerprint">来源 SHA-256：{{ result.source_sha256 }}</p>
      <p v-if="result.summary.action">
        动作 {{ result.summary.action.status }} · 复测
        {{
          result.summary.action.retest_passed === null
            ? "未完成"
            : result.summary.action.retest_passed
              ? "通过"
              : "未通过"
        }}；不证明唯一根因。
      </p>
    </div>
  </section>
</template>
<style scoped>
.memory-composer {
  border-top: 1px solid var(--color-border);
  margin-top: 24px;
  padding-top: 18px;
}
.memory-composer label {
  display: block;
  margin: 12px 0;
}
.fingerprint {
  overflow-wrap: anywhere;
}
</style>
