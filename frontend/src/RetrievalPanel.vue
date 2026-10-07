<script setup lang="ts">
import {
  computed,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  watch,
} from "vue";
import { ApiError, type Api } from "./api";
import AnswerPanel from "./AnswerPanel.vue";
import GuidedPanel from "./GuidedPanel.vue";
import ScreenshotPanel from "./ScreenshotPanel.vue";
import type { Guided } from "./guided-types";
import type { Answer } from "./rag-types";
import type { Page } from "./types";
import type { Entry, Hit, Index, Kind, Mode, Result } from "./retrieval-types";
import {
  referencePath,
  verifyPreview,
  type Preview,
} from "./retrieval-preview";

const props = defineProps<{ api: Api }>();
const indexes = ref<Page<Index>>({ items: [], total: 0, offset: 0, limit: 10 });
const indexId = ref("");
const entries = ref<Entry[]>([]);
const form = reactive({
  query: "",
  clarification: "",
  version: "1.1",
  mode: "vector" as Mode,
  kinds: ["document", "case", "log"] as Kind[],
  document: "",
  experiment: "",
  rerank: false,
  expandParent: false,
});
const result = ref<Result | null>(null);
const answer = ref<Answer | null>(null);
const guided = ref<Guided | null>(null);
const answering = ref(false);
const preview = ref<Preview | null>(null);
const selected = ref<Hit | null>(null);
const busy = ref(false),
  loading = ref(false),
  loadingSources = ref(false),
  error = ref("");
let generation = 0,
  sourceGeneration = 0,
  disposed = false;
const names: Record<Kind, string> = {
  document: "资料",
  case: "案例",
  log: "日志",
};
const modes: Record<Mode, string> = {
  vector: "向量检索",
  bm25: "BM25 关键词",
  rrf: "RRF 融合",
};
function clearResults() {
  generation++;
  result.value = null;
  answer.value = null;
  guided.value = null;
  preview.value = null;
  selected.value = null;
  error.value = "";
}
function addScreenshot(text: string) {
  const next = [form.clarification, text].filter(Boolean).join("\n");
  if (next.length > 1000) {
    error.value = "补充信息超过 1000 字，请先缩短现有内容。";
    return;
  }
  form.clarification = next;
}
watch(
  () => [
    form.query,
    form.clarification,
    form.version,
    form.mode,
    [...form.kinds].join(","),
    form.document,
    form.experiment,
    form.rerank,
    form.expandParent,
  ],
  clearResults,
  { flush: "sync" },
);
const documents = computed(() => [
  ...new Map(
    entries.value
      .filter((e) => e.product_version === form.version && e.kind !== "log")
      .map((e) => [
        e.payload.source.document_id!,
        { id: e.payload.source.document_id!, label: e.payload.source.title! },
      ]),
  ).values(),
]);
const experiments = computed(() => [
  ...new Map(
    entries.value
      .filter((e) => e.product_version === form.version && e.kind === "log")
      .map((e) => [
        e.payload.source.experiment_id!,
        {
          id: e.payload.source.experiment_id!,
          label: e.payload.source.run_id!.slice(0, 8),
        },
      ]),
  ).values(),
]);
watch(
  () => form.version,
  () => {
    form.document = "";
    form.experiment = "";
  },
  { flush: "sync" },
);
watch(
  indexId,
  async (id) => {
    clearResults();
    entries.value = [];
    form.document = "";
    form.experiment = "";
    const ticket = ++sourceGeneration;
    if (!id) {
      loadingSources.value = false;
      return;
    }
    loadingSources.value = true;
    try {
      const all: Entry[] = [];
      for (let offset = 0; offset < 2000; offset += 100) {
        const page = await props.api.request<Page<Entry>>(
          `/api/retrieval/indexes/${id}/entries?offset=${offset}&limit=100`,
        );
        if (disposed || ticket !== sourceGeneration) return;
        all.push(...page.items);
        if (offset + page.items.length >= page.total) break;
      }
      entries.value = all;
    } catch (exc) {
      showError(exc, ticket === sourceGeneration);
    } finally {
      if (!disposed && ticket === sourceGeneration)
        loadingSources.value = false;
    }
  },
  { flush: "sync" },
);
function showError(exc: unknown, current: boolean) {
  if (
    !disposed &&
    current &&
    !(exc instanceof ApiError && exc.code === "STALE_SESSION")
  )
    error.value = exc instanceof Error ? exc.message : "检索失败。";
}
async function load(offset = 0) {
  if (loading.value) return;
  clearResults();
  indexId.value = "";
  entries.value = [];
  indexes.value = { items: [], total: 0, offset, limit: 10 };
  loading.value = true;
  try {
    const page = await props.api.request<Page<Index>>(
      `/api/retrieval/indexes?offset=${offset}&limit=10`,
    );
    if (disposed) return;
    indexes.value = page;
    indexId.value = page.items[0]?.index_id ?? "";
  } catch (exc) {
    showError(exc, true);
  } finally {
    if (!disposed) loading.value = false;
  }
}
async function search(forAnswer = false, forGuided = false) {
  if (busy.value || loading.value || loadingSources.value) return;
  if (!forGuided && form.version === "unknown") return;
  clearResults();
  if (!form.kinds.length) {
    error.value = "请至少选择一种证据类型。";
    return;
  }
  const ticket = generation;
  busy.value = true;
  answering.value = forAnswer;
  try {
    const response = await props.api.request<Result | Answer | Guided>(
      `/api/retrieval/indexes/${indexId.value}/${forGuided ? "guided-answer" : forAnswer ? "answer" : "search"}`,
      {
        method: "POST",
        body: JSON.stringify({
          query: form.query,
          product_version: form.version === "unknown" ? null : form.version,
          ...(forGuided
            ? { clarification: form.clarification.trim() || null }
            : {}),
          mode: form.mode,
          source_kinds: form.kinds,
          document_ids: form.document ? [form.document] : [],
          experiment_ids: form.experiment ? [form.experiment] : [],
          top_k: 5,
          candidate_limit: 20,
          rerank: form.rerank,
          expand_parent: form.expandParent,
        }),
      },
    );
    if (!disposed && ticket === generation) {
      if (forGuided) {
        guided.value = response as Guided;
        answer.value = guided.value.answer;
        result.value = guided.value.retrieval;
      } else if (forAnswer) {
        answer.value = response as Answer;
        result.value = answer.value.retrieval;
      } else result.value = response as Result;
    }
  } catch (exc) {
    if (
      forGuided &&
      !disposed &&
      ticket === generation &&
      exc instanceof ApiError &&
      exc.diagnostics
    ) {
      const detail = exc.diagnostics as Pick<Guided, "trace" | "usage">;
      if (detail.trace && detail.usage)
        guided.value = {
          original_query: form.query,
          clarification: form.clarification || null,
          rewritten_query: null,
          status: "failed",
          stop_reason: "failure",
          questions: [],
          conflicts: [],
          answer: null,
          retrieval: null,
          trace: detail.trace,
          usage: detail.usage,
        };
    }
    showError(exc, ticket === generation);
  } finally {
    if (!disposed) {
      busy.value = false;
      answering.value = false;
    }
  }
}
function inspect(evidenceId: string) {
  const hit = result.value?.items.find(
    (item) => item.evidence_id === evidenceId,
  );
  if (hit) void open(hit);
}
async function open(hit: Hit) {
  if (busy.value) return;
  preview.value = null;
  selected.value = null;
  error.value = "";
  const ticket = ++generation;
  busy.value = true;
  try {
    const data = await props.api.request(referencePath(hit));
    if (!disposed && ticket === generation) {
      preview.value = verifyPreview(hit, data);
      selected.value = hit;
    }
  } catch (exc) {
    showError(exc, ticket === generation);
  } finally {
    if (!disposed) busy.value = false;
  }
}
function location(hit: Hit) {
  const s = hit.source;
  if (hit.kind === "log")
    return `观测序号 ${s.ordinal} · ${s.run_id?.slice(0, 8)}`;
  if (s.page_number) return `第 ${s.page_number} 页（提取文本）`;
  if (s.json_pointer) return `字段 ${s.json_pointer}（解码文本）`;
  return (
    s.spans?.map((p) => `第 ${p.line_start}–${p.line_end} 行`).join("；") ?? ""
  );
}
onMounted(() => void load());
onBeforeUnmount(() => {
  disposed = true;
  generation++;
  sourceGeneration++;
});
</script>

<template>
  <div class="retrieval-panel">
    <p class="notice subtle">
      按产品版本检索资料、案例和实验日志。候选分数用于本次排序，结论是否得到支持仍需后续核对。
    </p>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <section class="card retrieval-controls">
      <div class="card-heading">
        <h2>候选证据检索</h2>
        <button :disabled="loading || busy" @click="load(indexes.offset)">
          刷新索引
        </button>
      </div>
      <p v-if="!loading && !indexes.items.length" class="muted">
        当前组织暂无检索索引。
      </p>
      <form v-else @submit.prevent="search()">
        <div class="retrieval-fields">
          <label
            >检索索引<select v-model="indexId" :disabled="loading">
              <option
                v-for="index in indexes.items"
                :key="index.index_id"
                :value="index.index_id"
              >
                {{ index.entry_count }} 条证据 ·
                {{ index.created_at.slice(0, 10) }} ·
                {{ index.index_id.slice(0, 8) }}
              </option>
            </select></label
          >
          <label
            >产品版本<select v-model="form.version">
              <option>1.0</option>
              <option>1.1</option>
              <option>2.0</option>
              <option value="unknown">未确认</option>
            </select></label
          >
          <label
            >检索模式<select v-model="form.mode">
              <option v-for="(label, key) in modes" :key="key" :value="key">
                {{ label }}
              </option>
            </select></label
          >
          <label
            >指定资料<select v-model="form.document" :disabled="loadingSources">
              <option value="">全部资料 / 案例</option>
              <option v-for="doc in documents" :key="doc.id" :value="doc.id">
                {{ doc.label }}
              </option>
            </select></label
          >
          <label
            >指定实验<select
              v-model="form.experiment"
              :disabled="loadingSources"
            >
              <option value="">全部实验</option>
              <option
                v-for="experiment in experiments"
                :key="experiment.id"
                :value="experiment.id"
              >
                运行 {{ experiment.label }}
              </option>
            </select></label
          >
        </div>
        <fieldset>
          <legend>证据类型</legend>
          <label v-for="(label, key) in names" :key="key"
            ><input v-model="form.kinds" type="checkbox" :value="key" />{{
              label
            }}</label
          >
        </fieldset>
        <label class="query-label"
          >检索问题<textarea
            v-model="form.query"
            required
            maxlength="2000"
            rows="3"
            placeholder="输入现象、错误码或配置项，例如 RD_TIMEOUT"
          />
        </label>
        <label class="query-label"
          >补充信息（分步问答）<textarea
            v-model="form.clarification"
            maxlength="1000"
            rows="2"
            placeholder="补充现象、实际配置或需要核对的适用条件"
          />
        </label>
        <div class="retrieval-options">
          <label
            ><input v-model="form.rerank" type="checkbox" />模型重排序</label
          >
          <label
            ><input
              v-model="form.expandParent"
              type="checkbox"
            />扩展章节上下文</label
          >
          <p class="muted">
            重排序会增加模型调用；章节扩展保留原命中，追加同版本的原文上下文。
          </p>
        </div>
        <div class="search-actions">
          <button
            class="primary"
            :disabled="
              busy ||
              loading ||
              loadingSources ||
              !indexId ||
              form.version === 'unknown' ||
              !form.query.trim()
            "
          >
            检索候选</button
          ><button
            type="button"
            :disabled="
              busy ||
              loading ||
              loadingSources ||
              !indexId ||
              form.version === 'unknown' ||
              !form.query.trim()
            "
            @click="search(true)"
          >
            生成证据回答</button
          ><button
            type="button"
            :disabled="
              busy ||
              loading ||
              loadingSources ||
              !indexId ||
              !form.query.trim()
            "
            @click="search(true, true)"
          >
            分步证据问答</button
          ><span class="muted">{{
            loadingSources ? "正在读取索引范围…" : "最多返回 5 条候选"
          }}</span>
        </div>
      </form>
      <div v-if="indexes.total > 10" class="search-actions">
        <button
          :disabled="loading || busy || indexes.offset === 0"
          @click="load(indexes.offset - 10)"
        >
          上一页索引</button
        ><span
          >{{ indexes.offset + 1 }}–{{
            Math.min(indexes.offset + 10, indexes.total)
          }}
          / {{ indexes.total }}</span
        ><button
          :disabled="loading || busy || indexes.offset + 10 >= indexes.total"
          @click="load(indexes.offset + 10)"
        >
          下一页索引
        </button>
      </div>
    </section>
    <p v-if="busy || loading" role="status" class="loading">
      {{ answering ? "正在检索、生成回答并核对引用…" : "正在读取证据…" }}
    </p>
    <ScreenshotPanel
      :key="`${indexId}:${form.version}:${form.mode}:${form.kinds.join(',')}:${form.document}:${form.experiment}`"
      :api="api"
      :disabled="busy || loading || loadingSources"
      @confirm="addScreenshot"
    />
    <GuidedPanel
      v-if="guided"
      :guided="guided"
      :busy="busy"
      @inspect="inspect"
    />
    <AnswerPanel
      v-if="answer"
      :answer="answer"
      :busy="busy"
      @inspect="inspect"
    />
    <div v-if="result" class="retrieval-summary">
      <strong>{{ modes[result.mode] }}</strong
      ><span
        >范围内 {{ result.eligible_count }} 条 · 返回
        {{ result.items.length }} 条 · {{ result.latency_ms }} ms</span
      ><span
        >{{ result.usage.model_called ? "调用模型" : "未调用模型" }} ·
        {{ result.usage.input_tokens ?? "未知" }} 输入 token · 费用未核对</span
      >
    </div>
    <p v-if="result?.rerank_usage?.model_called" class="muted">
      排序模型 {{ result.rerank_usage.requested_model }} ·
      {{ result.rerank_usage.input_tokens }} 输入 token
    </p>
    <p v-if="result && !result.items.length" class="notice subtle">
      当前范围没有匹配候选，可核对版本、来源或查询词。
    </p>
    <div class="retrieval-grid" :class="{ expanded: preview }">
      <section
        v-if="result?.items.length"
        class="retrieval-results"
        aria-label="检索候选列表"
      >
        <article
          v-for="hit in result.items"
          :key="hit.evidence_id"
          class="card retrieval-hit"
        >
          <div class="card-heading">
            <h3>
              {{ hit.rank }}.
              {{ hit.source.title ?? `实验 ${hit.source.run_id?.slice(0, 8)}` }}
            </h3>
            <span>{{ names[hit.kind] }} · {{ hit.product_version }}</span>
          </div>
          <p class="muted">{{ location(hit) }}</p>
          <p class="retrieval-excerpt">{{ hit.text }}</p>
          <p class="retrieval-scores mono">
            向量 {{ hit.cosine_similarity?.toFixed(4) ?? "—"
            }}<template v-if="hit.vector_rank"
              >（第 {{ hit.vector_rank }}）</template
            >
            · BM25 {{ hit.bm25_score?.toFixed(4) ?? "—"
            }}<template v-if="hit.bm25_rank"
              >（第 {{ hit.bm25_rank }}）</template
            ><template v-if="hit.rrf_score != null">
              · RRF {{ hit.rrf_score.toFixed(6) }}</template
            >
            <template v-if="hit.rerank_score != null">
              · 排序分数 {{ hit.rerank_score.toFixed(4) }}（原第
              {{ hit.retrieval_rank }}）</template
            >
          </p>
          <button :disabled="busy" @click="open(hit)">查看原文</button>
        </article>
      </section>
      <aside v-if="preview && selected" class="card retrieval-preview">
        <div class="card-heading">
          <h2>原文预览</h2>
          <button
            @click="
              preview = null;
              selected = null;
            "
          >
            关闭预览
          </button>
        </div>
        <p class="notice subtle">来源已核对；结论是否得到支持尚未验证。</p>
        <p class="mono">{{ selected.evidence_id }}</p>
        <p>{{ location(selected) }}</p>
        <p class="mono">
          SHA-256 {{ selected.source.content_sha256 ?? selected.source.sha256 }}
        </p>
        <template v-if="preview.kind === 'document'"
          ><p>
            {{ preview.citation.source.filename }} ·
            {{ preview.citation.source.position_basis }}
          </p>
          <pre
            v-for="(part, i) in preview.citation.parts"
            :key="
              i
            ">{{part.before}}<mark>{{part.excerpt}}</mark>{{part.after}}</pre>
        </template>
        <pre v-else>{{ JSON.stringify(preview.event, null, 2) }}</pre>
        <template
          v-if="
            result?.contexts?.some(
              (c) =>
                c.kind === 'parent' &&
                c.anchor_evidence_ids.includes(selected!.evidence_id),
            )
          "
        >
          <h3>章节上下文</h3>
          <section
            v-for="context in result.contexts.filter(
              (c) =>
                c.kind === 'parent' &&
                c.anchor_evidence_ids.includes(selected!.evidence_id),
            )"
            :key="context.context_id"
            class="expanded-context"
          >
            <p class="muted">
              原文字符 {{ context.start }}–{{ context.end }} · 来源已核对
            </p>
            <p class="mono">
              命中锚点 {{ context.anchor_evidence_ids.join("；") }}
            </p>
            <pre>{{ context.text }}</pre>
          </section>
        </template>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.retrieval-options label {
  display: inline-flex;
  gap: 8px;
  align-items: center;
  margin: 0 24px 0 0;
}
.retrieval-options input {
  width: auto;
}
.expanded-context {
  border-top: 1px solid #e1d9cf;
  padding-top: 8px;
}
.retrieval-panel {
  min-width: 0;
}
.retrieval-controls {
  padding: 24px;
}
.retrieval-fields {
  display: grid;
  grid-template-columns: 2fr 1fr 1fr;
  gap: 16px;
}
.retrieval-fields label,
.query-label {
  display: grid;
  gap: 7px;
  font-weight: 600;
  min-width: 0;
}
.retrieval-fields select,
.query-label textarea {
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
}
.query-label {
  margin: 14px 0;
}
fieldset {
  border: 0;
  padding: 0;
  margin-top: 18px;
}
fieldset label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin: 8px 24px 8px 0;
}
fieldset input {
  width: auto;
}
.search-actions {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-top: 16px;
  flex-wrap: wrap;
}
.retrieval-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin: 24px 0;
  color: var(--muted);
}
.retrieval-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 20px;
}
.retrieval-grid.expanded {
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
}
.retrieval-results {
  display: grid;
  gap: 14px;
  min-width: 0;
}
.retrieval-hit,
.retrieval-preview {
  padding: 22px;
  min-width: 0;
}
.retrieval-hit h3 {
  margin: 0;
  min-width: 0;
}
.retrieval-hit .card-heading {
  gap: 12px;
  flex-wrap: wrap;
}
.retrieval-excerpt {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 180px;
  overflow: auto;
  line-height: 1.7;
}
.retrieval-scores {
  font-size: 12px;
  overflow-wrap: anywhere;
}
.retrieval-preview {
  align-self: start;
}
.retrieval-preview p {
  overflow-wrap: anywhere;
}
.retrieval-preview pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.65;
  background: #f4f2ee;
  padding: 14px;
  border-radius: 8px;
}
.retrieval-preview mark {
  background: #f0d8bf;
  color: inherit;
}
@media (max-width: 850px) {
  .retrieval-grid.expanded {
    grid-template-columns: 1fr;
  }
  .retrieval-fields {
    grid-template-columns: 1fr;
  }
  .retrieval-controls,
  .retrieval-hit,
  .retrieval-preview {
    padding: 18px;
  }
}
</style>
