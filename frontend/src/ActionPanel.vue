<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { ApiError, type Api } from "./api";
import type { Ticket } from "./types";

interface Job {
  job_id: string;
  status: string;
  proposal_sha256: string;
  proposal: {
    pre_action_state?: unknown;
    effect: string | null;
    choice: {
      action: string;
      timeout_ms: number | null;
      reason: string;
      evidence_ids: string[];
    } | null;
    retest: string;
    current_evidence: { evidence_id: string; text: string }[];
  };
  checkpoint: {
    error?: string;
    retest_passed?: boolean;
    action_receipt?: unknown;
    retest_receipt?: unknown;
    usage?: unknown;
  };
  approval: { decision: string; user_id: string; created_at: string } | null;
  expires_at: string;
  limitation: string;
}
interface Investigation {
  investigation_id: string;
  status: string;
  created_at: string;
}
type Event = { sequence: number; event: string; [key: string]: unknown };
const props = defineProps<{ api: Api; ticket: Ticket }>();
const investigations = ref<Investigation[]>([]);
const selected = ref("");
const jobs = ref<Job[]>([]);
const result = ref<Job | null>(null);
const events = ref<Event[]>([]);
const busy = ref(false);
const streaming = ref(false);
const error = ref("");
let active = true;
let controller: AbortController | undefined;
let requestKey: string | undefined;
const labels: Record<string, string> = {
  proposing: "建议生成中",
  pending: "等待人工批准",
  approved: "已批准，尚未执行",
  rejected: "已拒绝",
  action_running: "动作执行 / 恢复中",
  action_done: "动作完成，等待复测",
  retest_running: "复测执行 / 恢复中",
  completed: "闭环完成",
  failed: "已失败",
  cancelled: "已取消",
  cancel_requested: "取消已请求，已发出动作仍需核对",
  uncertain: "动作结果未知，需要人工核对",
};
async function load() {
  const [history, inv] = await Promise.all([
    props.api.request<{ items: Job[] }>(
      `/api/tickets/${props.ticket.ticket_id}/actions`,
    ),
    props.api.request<{ items: Investigation[] }>(
      `/api/tickets/${props.ticket.ticket_id}/hypothesis-investigations?limit=30`,
    ),
  ]);
  if (!active) return;
  jobs.value = history.items;
  const current = history.items.find(
    (job) => job.job_id === result.value?.job_id,
  );
  if (current) result.value = current;
  investigations.value = inv.items.filter((i) => i.status === "completed");
  if (!selected.value)
    selected.value = investigations.value[0]?.investigation_id ?? "";
}
async function run(operation: () => Promise<void>) {
  busy.value = true;
  error.value = "";
  try {
    await operation();
  } catch (e) {
    if (active)
      error.value =
        e instanceof ApiError
          ? `${e.code}：${e.message}`
          : "请求失败，请读取保存记录。";
  } finally {
    if (active) busy.value = false;
  }
}
async function inspect(job: Job) {
  controller?.abort();
  const body = await props.api.request<Job>(`/api/actions/${job.job_id}`);
  if (!active) return;
  result.value = body;
  events.value = [];
}
async function propose() {
  requestKey ??= crypto.randomUUID();
  const body = await props.api.request<Job>(
    `/api/investigations/${selected.value}/action-proposals`,
    {
      method: "POST",
      body: JSON.stringify({ request_id: requestKey }),
    },
  );
  if (!active) return;
  requestKey = undefined;
  result.value = body;
  events.value = [];
  await load();
}
async function change(operation: string, decision?: string) {
  const old = result.value;
  if (!old) return;
  const body = await props.api.request<Job>(
    `/api/actions/${old.job_id}/${operation}`,
    {
      method: "POST",
      ...(decision
        ? {
            body: JSON.stringify({
              decision,
              proposal_sha256: old.proposal_sha256,
            }),
          }
        : {}),
    },
  );
  if (!active || result.value?.job_id !== old.job_id) return;
  result.value = body;
  await load();
}
async function connect() {
  const job = result.value;
  if (!job) return;
  controller?.abort();
  const stream = new AbortController();
  controller = stream;
  streaming.value = true;
  error.value = "";
  try {
    await props.api.actionEvents(
      `/api/actions/${job.job_id}/events`,
      events.value.at(-1)?.sequence ?? 0,
      stream.signal,
      (value) => {
        if (
          active &&
          result.value?.job_id === job.job_id &&
          !stream.signal.aborted &&
          !events.value.some((e) => e.sequence === value.sequence)
        )
          events.value.push(value);
      },
    );
  } catch (e) {
    if (active && !stream.signal.aborted)
      error.value =
        e instanceof ApiError ? e.message : "事件连接中断，可手动按游标重连。";
  } finally {
    if (active && controller === stream) streaming.value = false;
  }
}
onMounted(() => run(load));
onUnmounted(() => {
  active = false;
  controller?.abort();
});
</script>

<template>
  <section class="card action-panel" aria-label="批准与实验动作">
    <div class="section-heading">
      <h2>批准与实验动作</h2>
      <button :disabled="busy" @click="run(load)">刷新动作记录</button>
    </div>
    <p class="notice">
      根据完成的本次假设调查建议一个实验动作。人工批准包括该动作及一次真实投递复测；批准后点击执行，每次推进一个步骤。
    </p>
    <label
      >动作依据调查<select
        v-model="selected"
        :disabled="busy"
        @change="requestKey = undefined"
      >
        <option value="">请选择完成的调查</option>
        <option
          v-for="item in investigations"
          :key="item.investigation_id"
          :value="item.investigation_id"
        >
          {{ item.investigation_id.slice(0, 8) }} ·
          {{ new Date(item.created_at).toLocaleString() }}
        </option>
      </select></label
    >
    <button class="primary" :disabled="busy || !selected" @click="run(propose)">
      生成待批准动作建议
    </button>
    <p>建议最多调用一次模型。超时与输出错误留档；执行和恢复不调用模型。</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <div v-if="result" class="action-result">
      <h3>{{ labels[result.status] }} · {{ result.status }}</h3>
      <p>{{ result.job_id }}</p>
      <p>{{ result.limitation }}</p>
      <template v-if="result.proposal.choice">
        <h3>{{ result.proposal.effect }}</h3>
        <p>{{ result.proposal.choice.reason }}</p>
        <p>
          操作：{{ result.proposal.choice.action }}；超时参数：{{
            result.proposal.choice.timeout_ms ?? "无"
          }}；有效期：{{ new Date(result.expires_at).toLocaleString() }}
        </p>
        <p>批准同时包括：{{ result.proposal.retest }}</p>
        <p>批准摘要：{{ result.proposal_sha256 }}</p>
        <details>
          <summary>批准绑定的运行状态</summary>
          <pre>{{
            JSON.stringify(result.proposal.pre_action_state, null, 2)
          }}</pre>
        </details>
        <div class="action-buttons">
          <button
            v-if="result.status === 'pending'"
            class="primary"
            :disabled="busy"
            @click="run(() => change('decision', 'approve'))"
          >
            批准此动作与一次复测
          </button>
          <button
            v-if="result.status === 'pending'"
            :disabled="busy"
            @click="run(() => change('decision', 'reject'))"
          >
            拒绝此建议
          </button>
          <button
            v-if="
              [
                'approved',
                'action_done',
                'action_running',
                'retest_running',
                'cancel_requested',
              ].includes(result.status)
            "
            class="primary"
            :disabled="busy"
            @click="run(() => change('advance'))"
          >
            执行下一步 / 恢复
          </button>
          <button
            v-if="
              ![
                'completed',
                'failed',
                'rejected',
                'cancelled',
                'uncertain',
              ].includes(result.status)
            "
            @click="
              change('cancel').catch(() => {
                error = '取消请求失败，请刷新记录。';
              })
            "
          >
            取消后续步骤
          </button>
          <button :disabled="streaming" @click="connect">
            {{ streaming ? "正在接收事件" : "接收 / 重放事件" }}
          </button>
        </div>
      </template>
      <p v-if="result.approval">
        人工决定：{{ result.approval.decision }} · 操作者
        {{ result.approval.user_id }}
      </p>
      <p v-if="result.checkpoint.error">
        停止 / 恢复原因：{{ result.checkpoint.error }}
      </p>
      <p v-if="result.checkpoint.retest_passed !== undefined">
        真实投递复测：{{
          result.checkpoint.retest_passed ? "通过" : "未通过"
        }}；原因语义尚未人工复核。
      </p>
      <details>
        <summary>动作依据原文</summary>
        <div
          v-for="item in result.proposal.current_evidence"
          :key="item.evidence_id"
        >
          <p>{{ item.evidence_id }}</p>
          <pre>{{ item.text }}</pre>
        </div>
      </details>
      <details :open="Boolean(result.checkpoint.retest_receipt)">
        <summary>执行与复测回执</summary>
        <pre>{{ JSON.stringify(result.checkpoint, null, 2) }}</pre>
      </details>
      <ol>
        <li v-for="item in events" :key="item.sequence">
          {{ item.sequence }} · {{ item.event }}
          <pre>{{ JSON.stringify(item) }}</pre>
        </li>
      </ol>
    </div>
    <details>
      <summary>动作历史（最近 30 条）</summary>
      <div v-for="item in jobs" :key="item.job_id">
        <button :disabled="busy" @click="run(() => inspect(item))">
          查看动作 {{ item.job_id.slice(0, 8) }} · {{ labels[item.status] }}
        </button>
      </div>
    </details>
  </section>
</template>

<style scoped>
.action-panel {
  margin-top: 24px;
}
label {
  display: grid;
  gap: 8px;
  margin: 16px 0;
}
select {
  width: 100%;
  min-width: 0;
}
.action-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin: 16px 0;
}
.action-result {
  overflow-wrap: anywhere;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 13px;
  padding: 12px;
  background: var(--color-surface-soft);
}
details {
  margin: 16px 0;
}
</style>
