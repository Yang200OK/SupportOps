<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { ApiError, type Api } from "./api";
import type { Page, Ticket } from "./types";
import type { Answer } from "./rag-types";

interface Index {
  index_id: string;
  entry_count: number;
  corpus_sha256: string;
}
interface Experiment {
  experiment_id: string;
  product_version: string;
  run_id: string;
}
interface Investigation {
  investigation_id: string;
  status: string;
  stop_reason: string;
  duration_ms: number;
  limitation: string;
  report: { claims: Answer["claims"]; missing_information: string[] } | null;
  questions?: string[];
  events: Record<string, unknown>[];
  evidence: { evidence_id: string; reference_url: string }[];
  usage: {
    known_model_calls: number;
    unknown_model_calls: number;
    input_tokens: number;
    output_tokens: number;
  };
  input_snapshot: Record<string, unknown>;
}

const props = defineProps<{ api: Api; ticket: Ticket }>();
const indexes = ref<Index[]>([]);
const experiments = ref<Experiment[]>([]);
const indexId = ref("");
const experimentId = ref("");
const history = ref<Page<Investigation>>({
  items: [],
  total: 0,
  offset: 0,
  limit: 10,
});
const result = ref<Investigation | null>(null);
const reference = ref<unknown>(null);
const busy = ref(false);
const error = ref("");
let generation = 0;
let mounted = true;
const available = computed(() =>
  experiments.value.filter(
    (e) => e.product_version === props.ticket.product_version,
  ),
);
const statuses: Record<string, string> = {
  completed: "基线整理完成",
  stopped: "已停止",
  failed: "调查失败",
  no_evidence: "没有可用证据",
  needs_clarification: "需要补充信息",
};

watch([indexId, experimentId], () => {
  generation++;
  result.value = null;
  reference.value = null;
  error.value = "";
});
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
      epoch === generation &&
      !(exc instanceof ApiError && exc.code === "STALE_SESSION")
    )
      error.value = exc instanceof Error ? exc.message : "调查请求失败。";
  } finally {
    if (mounted) busy.value = false;
  }
}
function current(epoch: number) {
  return mounted && epoch === generation;
}
async function loadHistory(epoch: number, offset = 0) {
  const value = await props.api.request<Page<Investigation>>(
    `/api/tickets/${props.ticket.ticket_id}/investigations?offset=${offset}&limit=10`,
  );
  if (current(epoch)) history.value = value;
}
async function start(epoch: number) {
  result.value = null;
  reference.value = null;
  const value = await props.api.request<Investigation>(
    `/api/tickets/${props.ticket.ticket_id}/investigations`,
    {
      method: "POST",
      body: JSON.stringify({
        index_id: indexId.value,
        experiment_id: experimentId.value || null,
      }),
    },
  );
  if (current(epoch)) {
    result.value = value;
    await loadHistory(epoch);
  }
}
async function read(epoch: number, id: string) {
  result.value = null;
  reference.value = null;
  const value = await props.api.request<Investigation>(
    `/api/investigations/${id}`,
  );
  if (current(epoch)) result.value = value;
}
async function inspect(epoch: number, id: string) {
  reference.value = null;
  const item = result.value?.evidence.find((e) => e.evidence_id === id);
  if (
    !item ||
    !/^\/api\/(chunk-sets\/[^/]+\/chunks\/[^/]+\/citation|experiments\/[^/]+)$/.test(
      item.reference_url,
    )
  )
    throw new Error("引用身份无效。");
  const value = await props.api.request(item.reference_url);
  if (current(epoch)) reference.value = value;
}
onMounted(() =>
  perform(async (epoch) => {
    const [snapshots, packages] = await Promise.all([
      props.api.request<Page<Index>>("/api/retrieval/indexes?limit=100"),
      props.api.request<Page<Experiment>>("/api/experiments?limit=100"),
    ]);
    if (!current(epoch)) return;
    indexes.value = snapshots.items;
    experiments.value = packages.items;
    indexId.value =
      [...snapshots.items].sort((a, b) => b.entry_count - a.entry_count)[0]
        ?.index_id ?? "";
    // 默认选中快照触发范围监视，历史列表使用新的代次读取。
    await new Promise<void>((resolve) => queueMicrotask(resolve));
    await loadHistory(generation);
  }),
);
</script>

<template>
  <section class="card investigation-panel" aria-label="只读调查基线">
    <div class="card-heading">
      <h2>只读调查基线</h2>
      <span>RelayDesk {{ ticket.product_version ?? "版本待补充" }}</span>
    </div>
    <p class="notice subtle">
      读取选定资料和历史实验异常观测，整理有引用的事实与待检查项。当前现场取证使用动态假设入口，执行动作须独立人工批准。
    </p>
    <div class="investigation-scope">
      <label
        >调查知识快照<select v-model="indexId">
          <option value="">请选择快照</option>
          <option
            v-for="item in indexes"
            :key="item.index_id"
            :value="item.index_id"
          >
            {{ item.entry_count }} 条 · {{ item.corpus_sha256.slice(0, 12) }}
          </option>
        </select></label
      >
      <label
        >调查历史实验<select v-model="experimentId">
          <option value="">不绑定历史实验</option>
          <option
            v-for="item in available"
            :key="item.experiment_id"
            :value="item.experiment_id"
          >
            {{ item.product_version }} · {{ item.run_id.slice(0, 12) }}
          </option>
        </select></label
      >
    </div>
    <button
      class="primary"
      :disabled="busy || !indexId || !ticket.product_version"
      @click="perform(start)"
    >
      {{ busy ? "调查处理中…" : "开始只读调查" }}
    </button>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="!ticket.product_version" class="notice">
      请补充工单版本后再开始调查。
    </p>
    <p class="muted">
      最多三次只读工具、五次模型调用、180
      秒。运行会产生模型用量并保存独立调查记录；范围变化会清空显示，已开始的后台请求仍会完成。
    </p>
    <section v-if="result" class="investigation-result">
      <h3>
        {{ statuses[result.status] ?? result.status }} ·
        {{ result.stop_reason }}
      </h3>
      <p class="mono">{{ result.investigation_id }}</p>
      <p class="notice subtle">{{ result.limitation }}</p>
      <p class="muted">
        {{ result.duration_ms }} ms · 已知
        {{ result.usage.known_model_calls }} / 用量未知
        {{ result.usage.unknown_model_calls }} 次模型调用 · 输入
        {{ result.usage.input_tokens }} / 输出
        {{ result.usage.output_tokens }} token · 费用未对账
      </p>
      <ul v-if="result.questions">
        <li v-for="q in result.questions" :key="q">{{ q }}</li>
      </ul>
      <article
        v-for="claim in result.report?.claims ?? []"
        :key="claim.claim_id"
        class="investigation-claim"
      >
        <h4>
          {{ claim.claim_id }} ·
          {{ claim.kind === "fact" ? "事实表述" : "待检查项" }}
        </h4>
        <p v-if="claim.support.verdict !== 'supported'" class="notice">
          该项未通过支持核对，仅供审阅，不应作为已证实事实。
        </p>
        <p>{{ claim.text }}</p>
        <p class="muted">
          模型语义判断：{{ claim.support.verdict }} ·
          {{ claim.support.reason }}（尚未人工复核）
        </p>
        <div v-for="(citation, n) in claim.citations" :key="n">
          <blockquote>{{ citation.quote }}</blockquote>
          <button
            :disabled="busy"
            @click="perform((epoch) => inspect(epoch, citation.evidence_id))"
          >
            回查调查引文
          </button>
        </div>
      </article>
      <ul>
        <li
          v-for="item in result.report?.missing_information ?? []"
          :key="item"
        >
          {{ item }}
        </li>
      </ul>
      <details open>
        <summary>调查轨迹</summary>
        <ol>
          <li v-for="event in result.events" :key="String(event.sequence)">
            <pre>{{ JSON.stringify(event, null, 2) }}</pre>
          </li>
        </ol>
      </details>
      <details>
        <summary>固定输入与范围快照</summary>
        <pre>{{ JSON.stringify(result.input_snapshot, null, 2) }}</pre>
      </details>
    </section>
    <details v-if="reference" class="investigation-reference" open>
      <summary>引用原文与完整性核对</summary>
      <pre>{{ JSON.stringify(reference, null, 2) }}</pre>
    </details>
    <h3>本工单调查历史</h3>
    <p v-if="!history.items.length" class="muted">还没有调查记录。</p>
    <div
      v-for="item in history.items"
      :key="item.investigation_id"
      class="investigation-history"
    >
      <span
        >{{ statuses[item.status] ?? item.status }} ·
        {{ item.investigation_id.slice(0, 12) }}</span
      ><button
        :disabled="busy"
        @click="perform((epoch) => read(epoch, item.investigation_id))"
      >
        读取历史调查
      </button>
    </div>
    <div class="pagination">
      <button
        :disabled="busy || history.offset === 0"
        @click="perform((epoch) => loadHistory(epoch, history.offset - 10))"
      >
        上一页</button
      ><span>共 {{ history.total }} 条</span
      ><button
        :disabled="busy || history.offset + 10 >= history.total"
        @click="perform((epoch) => loadHistory(epoch, history.offset + 10))"
      >
        下一页
      </button>
    </div>
  </section>
</template>

<style scoped>
.investigation-panel {
  padding: 24px;
  margin: 20px 0;
  min-width: 0;
}
.investigation-panel .card-heading,
.investigation-history {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.investigation-scope {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin: 16px 0;
}
.investigation-scope label {
  min-width: 0;
}
.investigation-panel pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 320px;
  overflow: auto;
}
.investigation-panel p,
.investigation-panel li,
.investigation-panel span {
  overflow-wrap: anywhere;
}
.investigation-claim {
  padding: 16px 0;
  border-top: 1px solid var(--color-border);
}
.investigation-claim blockquote {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin: 12px 0;
  padding: 12px;
  background: var(--color-surface-soft);
  border-left: 3px solid var(--color-accent);
}
.investigation-history {
  padding: 12px 0;
  border-top: 1px solid var(--color-border);
}
@media (max-width: 700px) {
  .investigation-scope {
    grid-template-columns: 1fr;
  }
  .investigation-panel {
    padding: 16px;
  }
}
</style>
