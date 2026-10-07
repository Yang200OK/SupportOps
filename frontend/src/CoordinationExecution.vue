<script setup lang="ts">
import { onUnmounted, ref, watch } from "vue";
import { ApiError, type Api } from "./api";
import DetailDialog from "./DetailDialog.vue";

interface Claim {
  claim_id: string;
  text: string;
  support?: { verdict: string };
  citations: { evidence_id: string; quote: string }[];
}
interface Run {
  execution_id: string;
  status: string;
  revision: number;
  error: string | null;
  usage: { model_calls: number; tool_calls: number; context_chars: number };
  model_accounting: {
    known_calls: number;
    unknown_calls: number;
    input_tokens: number;
    output_tokens: number;
  };
  tasks: Record<
    string,
    { status: string; phase: string; error?: string; report: unknown }
  >;
  result: {
    claims: Claim[];
    conflicts: unknown[];
    missing_information: string[];
  } | null;
  events: unknown[];
}
const props = defineProps<{ api: Api; boardId: string; planned: boolean }>();
const run = ref<Run | null>(null);
const history = ref<Run[]>([]);
const busy = ref(false);
const cancelling = ref(false);
const error = ref("");
const detailOpen = ref(false);
const reportOpen = ref(false);
const detailTitle = ref("");
const detail = ref<unknown>(null);
const labels: Record<string, string> = {
  pending: "待推进",
  running: "执行中",
  completed: "已完成",
  failed: "已停止",
  uncertain: "回执未知",
  cancelled: "已取消",
};
const names: Record<string, string> = {
  documents: "文档取证",
  runtime: "现场取证",
  synthesis: "证据归并",
};
let active = true;
let epoch = 0;
let requestId: string | null = null;
watch(
  () => props.boardId,
  () => {
    epoch++;
    run.value = null;
    history.value = [];
    error.value = "";
    detailOpen.value = false;
    requestId = null;
  },
);
onUnmounted(() => {
  active = false;
  epoch++;
});
const current = (n: number) => active && n === epoch;
async function perform(action: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    await action();
  } catch (exc) {
    if (active && !(exc instanceof ApiError && exc.code === "STALE_SESSION"))
      error.value = exc instanceof Error ? exc.message : "执行请求失败";
  } finally {
    if (active) busy.value = false;
  }
}
async function create() {
  const n = epoch;
  requestId ??= crypto.randomUUID();
  const body = await props.api.request<Run>(
    `/api/coordination-boards/${props.boardId}/executions`,
    { method: "POST", body: JSON.stringify({ request_id: requestId }) },
  );
  if (current(n)) {
    run.value = body;
    requestId = null;
  }
}
async function read(id: string) {
  const n = epoch;
  const body = await props.api.request<Run>(
    `/api/coordination-executions/${id}`,
  );
  if (current(n)) run.value = body;
}
async function advance() {
  if (!run.value) return;
  const n = epoch;
  const body = await props.api.request<Run>(
    `/api/coordination-executions/${run.value.execution_id}/advance`,
    { method: "POST" },
  );
  if (current(n)) run.value = body;
}
async function cancel() {
  if (!run.value || cancelling.value) return;
  const n = epoch;
  cancelling.value = true;
  try {
    const body = await props.api.request<Run>(
      `/api/coordination-executions/${run.value.execution_id}/cancel`,
      { method: "POST" },
    );
    if (current(n)) run.value = body;
  } catch (exc) {
    if (current(n))
      error.value = exc instanceof Error ? exc.message : "取消失败";
  } finally {
    if (active) cancelling.value = false;
  }
}
async function listing() {
  const n = epoch;
  const body = await props.api.request<{ items: Run[] }>(
    `/api/coordination-boards/${props.boardId}/executions`,
  );
  if (current(n)) history.value = body.items;
}
function inspect(title: string, value: unknown) {
  reportOpen.value = false;
  detailTitle.value = title;
  detail.value = value;
  detailOpen.value = true;
}
async function evidence(id: string) {
  if (!run.value) return;
  const n = epoch;
  const body = await props.api.request(
    `/api/coordination-executions/${run.value.execution_id}/evidence/${encodeURIComponent(id)}`,
  );
  if (current(n)) inspect("协作证据原文", body);
}
</script>

<template>
  <section class="execution-panel">
    <div class="panel-heading">
      <h4>协作执行</h4>
      <div class="execution-buttons">
        <button :disabled="busy || !planned" @click="perform(create)">
          创建协作执行
        </button>
        <button :disabled="busy" @click="perform(listing)">读取执行历史</button>
      </div>
    </div>
    <p class="muted">
      先并行完成文档和现场调查，再推进证据归并。恢复沿用截止时间；回执未知时停止，不重复调用。
    </p>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <div v-if="run" class="execution-summary">
      <strong>执行状态：{{ labels[run.status] }}</strong>
      <p v-if="run.error" class="error">{{ run.error }}</p>
      <p>
        实际 {{ run.usage.model_calls }} 次模型 /
        {{ run.usage.tool_calls }} 次工具 / {{ run.usage.context_chars }} 字符
      </p>
      <p class="muted">
        模型用量已知 {{ run.model_accounting.known_calls }} 次，未知
        {{ run.model_accounting.unknown_calls }} 次；输入
        {{ run.model_accounting.input_tokens }} / 输出
        {{ run.model_accounting.output_tokens }} token，费用未对账。
      </p>
      <div class="execution-buttons">
        <button
          :disabled="
            busy || !planned || !['pending', 'running'].includes(run.status)
          "
          @click="perform(advance)"
        >
          {{ busy ? "正在执行取证波" : "推进就绪任务" }}
        </button>
        <button
          :disabled="busy"
          @click="perform(() => read(run!.execution_id))"
        >
          刷新执行
        </button>
        <button
          :disabled="cancelling || !['pending', 'running'].includes(run.status)"
          @click="cancel"
        >
          取消协作执行
        </button>
        <button @click="inspect('协作执行审计', run.events)">执行审计</button>
      </div>
      <article v-for="(task, id) in run.tasks" :key="id" class="execution-task">
        <strong>{{ names[id] }} · {{ labels[task.status] }}</strong>
        <span v-if="task.error" class="error"> {{ task.error }}</span>
        <button v-if="task.report" @click="inspect('子任务报告', task.report)">
          查看子任务报告
        </button>
      </article>
      <div v-if="run.result">
        <p>
          归并结论 {{ run.result.claims.length }} 条，未决冲突
          {{ run.result.conflicts.length }} 项。
        </p>
        <button @click="reportOpen = true">查看归并报告</button>
        <DetailDialog v-model:open="reportOpen" title="协作归并报告">
          <p class="muted">
            原因状态保持未决。逐字引用已核对，模型语义判断尚未经人工复核。
          </p>
          <article v-for="claim in run.result.claims" :key="claim.claim_id">
            <p>
              {{ claim.text }} ·
              {{
                claim.support?.verdict === "supported"
                  ? "证据支持"
                  : claim.support?.verdict === "insufficient"
                    ? "依据不足"
                    : "不支持"
              }}
            </p>
            <button
              v-for="citation in claim.citations"
              :key="citation.evidence_id + citation.quote"
              :disabled="busy"
              @click="perform(() => evidence(citation.evidence_id))"
            >
              查看协作原文
            </button>
          </article>
          <p>未决冲突：{{ run.result.conflicts.length }}</p>
          <button
            v-if="run.result.conflicts.length"
            @click="inspect('未决证据冲突', run.result.conflicts)"
          >
            查看冲突
          </button>
          <p
            v-for="missing in run.result.missing_information"
            :key="missing"
            class="muted"
          >
            待补：{{ missing }}
          </p>
        </DetailDialog>
      </div>
    </div>
    <div
      v-for="item in history"
      :key="item.execution_id"
      class="execution-task"
    >
      <span
        >{{ labels[item.status] }} · {{ item.execution_id.slice(0, 8) }}</span
      >
      <button :disabled="busy" @click="perform(() => read(item.execution_id))">
        查看协作执行
      </button>
    </div>
    <DetailDialog v-model:open="detailOpen" :title="detailTitle">
      <pre class="raw-json">{{ JSON.stringify(detail, null, 2) }}</pre>
    </DetailDialog>
  </section>
</template>

<style scoped>
.execution-panel {
  border-top: 1px solid var(--line);
  padding-top: 16px;
  margin-top: 20px;
  min-width: 0;
}
.execution-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.execution-task {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  padding: 12px 0;
}
.execution-summary {
  overflow-wrap: anywhere;
}
</style>
