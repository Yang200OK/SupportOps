<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { ApiError, type Api } from "./api";
import type { Page, Ticket } from "./types";
import DetailDialog from "./DetailDialog.vue";
import CoordinationExecution from "./CoordinationExecution.vue";
import CoordinationComparison from "./CoordinationComparison.vue";

interface Task {
  task_id: string;
  role: string;
  depends_on: string[];
  status: string;
  objective: string;
  tools: string[];
  budget: { tool_calls: number; model_calls: number; context_chars: number };
}
interface Board {
  board_id: string;
  status: string;
  revision: number;
  tasks: Task[];
  created_at: string;
  budget: {
    tool_calls: number;
    model_calls: number;
    time_budget_ms: number;
    context_chars: number;
  };
  events: unknown[];
}
interface LabRun {
  lab_run_id: string;
  product_version: string;
  mode: string;
}
const props = defineProps<{ api: Api; ticket: Ticket }>();
const indexes = ref<{ index_id: string; entry_count: number }[]>([]);
const runs = ref<LabRun[]>([]);
const indexId = ref("");
const runId = ref("");
const history = ref<Page<Board>>({ items: [], total: 0, offset: 0, limit: 10 });
const result = ref<Board | null>(null);
const detail = ref<unknown>(null);
const detailTitle = ref("");
const detailOpen = ref(false);
const busy = ref(false);
const error = ref("");
let mounted = true;
let generation = 0;
let pendingRequest: {
  request_id: string;
  index_id: string;
  lab_run_id: string;
} | null = null;
const available = computed(() =>
  runs.value.filter((r) => r.product_version === props.ticket.product_version),
);
const names: Record<string, string> = {
  documents: "文档取证",
  runtime: "现场取证",
  synthesis: "证据归并",
};
const states: Record<string, string> = {
  ready: "可调度",
  blocked: "等待依赖",
  planned: "已规划",
  cancelled: "已取消",
};
onUnmounted(() => {
  mounted = false;
  generation++;
});
watch([indexId, runId], () => {
  generation++;
  result.value = null;
  detail.value = null;
  detailOpen.value = false;
  error.value = "";
  pendingRequest = null;
});
const current = (epoch: number) => mounted && epoch === generation;
async function perform(action: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    await action();
  } catch (exc) {
    if (mounted && !(exc instanceof ApiError && exc.code === "STALE_SESSION"))
      error.value = exc instanceof Error ? exc.message : "任务板请求失败。";
  } finally {
    if (mounted) busy.value = false;
  }
}
async function loadHistory(offset = 0) {
  const body = await props.api.request<Page<Board>>(
    `/api/tickets/${props.ticket.ticket_id}/coordination-boards?offset=${offset}&limit=10`,
  );
  if (mounted) history.value = body;
}
async function refreshRuns() {
  const body = await props.api.request<Page<LabRun>>(
    "/api/investigation-lab-runs?limit=30",
  );
  if (mounted) {
    runs.value = body.items;
    if (!body.items.some((r) => r.lab_run_id === runId.value)) runId.value = "";
  }
}
async function create() {
  const epoch = generation;
  // 同一输入的显式重发保留请求键，响应丢失也不会重复创建；输入改变才换键。
  if (
    !pendingRequest ||
    pendingRequest.index_id !== indexId.value ||
    pendingRequest.lab_run_id !== runId.value
  )
    pendingRequest = {
      request_id: crypto.randomUUID(),
      index_id: indexId.value,
      lab_run_id: runId.value,
    };
  const body = await props.api.request<Board>(
    `/api/tickets/${props.ticket.ticket_id}/coordination-boards`,
    { method: "POST", body: JSON.stringify(pendingRequest) },
  );
  if (current(epoch)) {
    result.value = body;
    pendingRequest = null;
    await loadHistory();
  }
}
async function read(id: string) {
  const epoch = generation;
  const body = await props.api.request<Board>(`/api/coordination-boards/${id}`);
  if (current(epoch)) result.value = body;
}
async function inspect(taskId: string) {
  const epoch = generation;
  if (!result.value) return;
  const body = await props.api.request(
    `/api/coordination-boards/${result.value.board_id}/tasks/${taskId}/package`,
  );
  if (current(epoch)) {
    detail.value = body;
    detailTitle.value = "私有任务包";
    detailOpen.value = true;
  }
}
async function cancel() {
  const epoch = generation;
  if (!result.value) return;
  const body = await props.api.request<Board>(
    `/api/coordination-boards/${result.value.board_id}/cancel`,
    {
      method: "POST",
      body: JSON.stringify({
        revision: result.value.revision,
        reason: "操作者取消尚未执行的计划",
      }),
    },
  );
  if (current(epoch)) {
    result.value = body;
    await loadHistory();
  }
}
onMounted(() =>
  perform(async () => {
    const body = await props.api.request<
      Page<{ index_id: string; entry_count: number }>
    >("/api/retrieval/indexes?limit=30");
    if (!mounted) return;
    indexes.value = body.items;
    indexId.value = body.items[0]?.index_id ?? "";
    await refreshRuns();
    await loadHistory();
  }),
);
</script>

<template>
  <section class="coordination-panel">
    <div class="panel-heading">
      <div>
        <h3>协作任务板</h3>
        <p class="muted">
          文档与现场分工取证，再核对证据。创建计划后可显式启动协作执行。
        </p>
      </div>
    </div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <form class="coordination-form" @submit.prevent="perform(create)">
      <label
        >协作知识快照<select v-model="indexId" :disabled="busy">
          <option value="">选择快照</option>
          <option v-for="i in indexes" :key="i.index_id" :value="i.index_id">
            {{ i.entry_count }} 条 · {{ i.index_id.slice(0, 8) }}
          </option>
        </select></label
      >
      <label
        >协作本次实验<select v-model="runId" :disabled="busy">
          <option value="">选择同版本登记</option>
          <option
            v-for="r in available"
            :key="r.lab_run_id"
            :value="r.lab_run_id"
          >
            {{ r.mode === "startup" ? "启动诊断" : "在线观测" }} ·
            {{ r.lab_run_id.slice(0, 8) }}
          </option>
        </select></label
      >
      <button type="button" :disabled="busy" @click="perform(refreshRuns)">
        刷新登记
      </button>
      <button
        class="primary"
        :disabled="busy || !indexId || !runId || !ticket.product_version"
      >
        创建协作计划
      </button>
    </form>
    <p v-if="!ticket.product_version" class="muted">请先补充工单版本。</p>
    <div v-if="result" class="coordination-result">
      <div class="panel-heading">
        <strong>{{ states[result.status] }} · 计划创建时尚未执行</strong
        ><button
          :disabled="busy || result.status !== 'planned'"
          @click="perform(cancel)"
        >
          取消协作计划
        </button>
      </div>
      <p class="muted">
        总配额：{{ result.budget.tool_calls }} 次工具 /
        {{ result.budget.model_calls }} 次模型 /
        {{ result.budget.context_chars }} 字符，共同时间上限
        {{ result.budget.time_budget_ms / 1000 }} 秒。此计划创建时实际调用 0
        次，执行用量见下方。
      </p>
      <article
        v-for="task in result.tasks"
        :key="task.task_id"
        class="coordination-task"
      >
        <div class="panel-heading">
          <strong>{{ names[task.task_id] }} · {{ states[task.status] }}</strong
          ><button
            :disabled="busy"
            @click="perform(() => inspect(task.task_id))"
          >
            查看任务包
          </button>
        </div>
        <p>{{ task.objective }}</p>
        <p class="muted">
          依赖：{{ task.depends_on.map((d) => names[d]).join("、") || "无" }} ·
          配额 {{ task.budget.tool_calls }} 工具 /
          {{ task.budget.model_calls }} 模型 /
          {{ task.budget.context_chars }} 字符
        </p>
      </article>
      <button
        @click="
          detail = result.events;
          detailTitle = '任务板审计';
          detailOpen = true;
        "
      >
        查看审计
      </button>
      <CoordinationExecution
        :api="api"
        :board-id="result.board_id"
        :planned="result.status === 'planned'"
      />
    </div>
    <div class="panel-heading">
      <h4>计划历史（{{ history.total }}）</h4>
      <button :disabled="busy" @click="perform(() => loadHistory())">
        刷新计划
      </button>
    </div>
    <div
      v-for="item in history.items"
      :key="item.board_id"
      class="coordination-history"
    >
      <span
        >{{ states[item.status] }} ·
        {{ new Date(item.created_at).toLocaleString("zh-CN") }} ·
        {{ item.board_id.slice(0, 8) }}</span
      ><button :disabled="busy" @click="perform(() => read(item.board_id))">
        查看计划
      </button>
    </div>
    <p v-if="!history.items.length" class="muted">尚无协作计划。</p>
    <div class="pagination">
      <button
        :disabled="busy || history.offset === 0"
        @click="perform(() => loadHistory(history.offset - 10))"
      >
        上一页计划</button
      ><button
        :disabled="busy || history.offset + 10 >= history.total"
        @click="perform(() => loadHistory(history.offset + 10))"
      >
        下一页计划
      </button>
    </div>
    <DetailDialog v-model:open="detailOpen" :title="detailTitle">
      <pre class="raw-json">{{ JSON.stringify(detail, null, 2) }}</pre>
    </DetailDialog>
  </section>
  <CoordinationComparison :api="api" />
</template>

<style scoped>
.coordination-form {
  display: flex;
  gap: 12px;
  align-items: end;
  flex-wrap: wrap;
  margin: 20px 0;
}
.coordination-form label {
  flex: 1;
  min-width: 160px;
}
.coordination-task {
  padding: 16px;
  margin: 12px 0;
  border: 1px solid var(--line, #dedbd5);
  border-radius: 8px;
}
.coordination-history {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 12px 0;
  border-bottom: 1px solid var(--line, #dedbd5);
}
.coordination-panel {
  min-width: 0;
}
@media (max-width: 600px) {
  .coordination-history {
    align-items: start;
    flex-direction: column;
  }
  .coordination-form label {
    min-width: 100%;
  }
}
</style>
