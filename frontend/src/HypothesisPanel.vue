<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { ApiError, type Api } from "./api";
import type { Page, Ticket } from "./types";
import type { Answer } from "./rag-types";
import type { SkillBundle } from "./skill-types";
import type { MemoryBundle } from "./memory-types";
import RetrospectiveComposer from "./RetrospectiveComposer.vue";

interface Index {
  index_id: string;
  entry_count: number;
  corpus_sha256: string;
}
interface LabRun {
  lab_run_id: string;
  product_version: string;
  mode: string;
  expires_at: string;
}
interface Ref {
  evidence_id: string;
  quote: string;
}
interface Hypothesis {
  hypothesis_id: string;
  cause: string;
  basis: string;
  status: string;
  reason: string;
  support_signal: string;
  refute_signal: string;
  missing_information: string[];
  support_citations: Ref[];
  refute_citations: Ref[];
  semantic_reviewed: boolean;
  proposed_status?: string;
  support_review?: { verdict: string; reason: string };
}
interface Step {
  step_id: string;
  tool: string;
  reason: string;
  status: string;
  error?: string;
}
interface Investigation {
  investigation_id: string;
  status: string;
  stop_reason: string;
  limitation: string;
  duration_ms: number;
  hypotheses: Hypothesis[];
  steps: Step[];
  plan_history: unknown[];
  events: unknown[];
  evidence: { evidence_id: string; reference_url: string }[];
  report: { claims: Answer["claims"]; missing_information: string[] } | null;
  usage: {
    known_model_calls: number;
    unknown_model_calls: number;
    input_tokens: number;
    output_tokens: number;
  };
  questions?: string[];
  skills?: SkillBundle;
  memory?: MemoryBundle;
}

const props = defineProps<{ api: Api; ticket: Ticket }>();
const indexes = ref<Index[]>([]);
const runs = ref<LabRun[]>([]);
const indexId = ref("");
const runId = ref("");
const useSkills = ref(false);
const useMemory = ref(false);
const usePublishedSkills = ref(false);
const result = ref<Investigation | null>(null);
const history = ref<Page<Investigation>>({
  items: [],
  total: 0,
  offset: 0,
  limit: 10,
});
const reference = ref<unknown>(null);
const busy = ref(false);
const error = ref("");
let generation = 0;
let mounted = true;
const available = computed(() =>
  runs.value.filter((r) => r.product_version === props.ticket.product_version),
);
const statuses: Record<string, string> = {
  proposed: "待取证",
  supported: "证据支持候选",
  refuted: "证据反驳候选",
  unresolved: "未决",
  pending: "待检查",
  running: "执行中",
  completed: "已完成",
  stopped: "已停止",
  failed: "失败",
  needs_clarification: "需要补充",
  no_evidence: "证据不足",
};
watch([indexId, runId, useSkills, useMemory, usePublishedSkills], () => {
  generation++;
  result.value = null;
  reference.value = null;
  error.value = "";
});
onUnmounted(() => {
  mounted = false;
  generation++;
});
const current = (epoch: number) => mounted && generation === epoch;

async function perform(action: (epoch: number) => Promise<void>) {
  if (busy.value) return;
  const epoch = generation;
  busy.value = true;
  error.value = "";
  try {
    await action(epoch);
  } catch (exc) {
    if (
      current(epoch) &&
      !(exc instanceof ApiError && exc.code === "STALE_SESSION")
    )
      error.value = exc instanceof Error ? exc.message : "假设调查请求失败。";
  } finally {
    if (mounted) busy.value = false;
  }
}
async function loadHistory(epoch: number, offset = 0) {
  const body = await props.api.request<Page<Investigation>>(
    `/api/tickets/${props.ticket.ticket_id}/hypothesis-investigations?offset=${offset}&limit=10`,
  );
  if (current(epoch)) history.value = body;
}
async function refreshRuns(epoch: number) {
  const body = await props.api.request<Page<LabRun>>(
    "/api/investigation-lab-runs?limit=30",
  );
  if (!current(epoch)) return;
  runs.value = body.items;
  if (!runs.value.some((r) => r.lab_run_id === runId.value)) runId.value = "";
}
async function start(epoch: number) {
  result.value = null;
  reference.value = null;
  const body = await props.api.request<Investigation>(
    `/api/tickets/${props.ticket.ticket_id}/hypothesis-investigations`,
    {
      method: "POST",
      body: JSON.stringify({
        index_id: indexId.value,
        lab_run_id: runId.value,
        ...(useSkills.value ? { use_skills: true } : {}),
        ...(useMemory.value ? { use_memory: true } : {}),
        ...(usePublishedSkills.value ? { use_published_skills: true } : {}),
      }),
    },
  );
  if (current(epoch)) {
    result.value = body;
    await loadHistory(epoch);
  }
}
async function read(epoch: number, id: string) {
  result.value = null;
  reference.value = null;
  const body = await props.api.request<Investigation>(
    `/api/investigations/${id}`,
  );
  if (current(epoch)) result.value = body;
}
async function inspect(epoch: number, id: string) {
  reference.value = null;
  const body = result.value;
  const item = body?.evidence.find((e) => e.evidence_id === id);
  if (!body || !item) throw new Error("引用身份无效。");
  const source = /^\/api\/chunk-sets\/[^/]+\/chunks\/[^/]+\/citation$/.test(
    item.reference_url,
  );
  const live =
    item.reference_url ===
    `/api/investigations/${body.investigation_id}/evidence/${id}`;
  if (!source && !live) throw new Error("引用范围无效。");
  const value = await props.api.request(item.reference_url);
  if (current(epoch)) reference.value = value;
}
onMounted(() =>
  perform(async (epoch) => {
    const [knowledge, registered] = await Promise.all([
      props.api.request<Page<Index>>("/api/retrieval/indexes?limit=100"),
      props.api.request<Page<LabRun>>("/api/investigation-lab-runs?limit=30"),
    ]);
    if (!current(epoch)) return;
    indexes.value = knowledge.items;
    runs.value = registered.items;
    indexId.value =
      [...knowledge.items].sort((a, b) => b.entry_count - a.entry_count)[0]
        ?.index_id ?? "";
    // 自动选知识快照触发范围监视后，再用新代次读取独立调查历史。
    await new Promise<void>((resolve) => queueMicrotask(resolve));
    await loadHistory(generation);
  }),
);
</script>

<template>
  <section class="card hypothesis-panel" aria-label="假设调查">
    <div class="card-heading">
      <h2>假设调查</h2>
      <span>RelayDesk {{ ticket.product_version ?? "版本待补充" }}</span>
    </div>
    <p class="notice subtle">
      提出原因候选，选择检查并读取本次本地实验观测。证据不足或冲突时保持未决，修复建议须通过独立的人工批准入口审核。
    </p>
    <div class="hypothesis-scope">
      <label
        >假设知识快照<select v-model="indexId">
          <option value="">请选择快照</option>
          <option v-for="i in indexes" :key="i.index_id" :value="i.index_id">
            {{ i.entry_count }} 条 · {{ i.corpus_sha256.slice(0, 12) }}
          </option>
        </select></label
      >
      <label
        >本次实验运行<select v-model="runId">
          <option value="">请选择新登记的本次实验</option>
          <option
            v-for="r in available"
            :key="r.lab_run_id"
            :value="r.lab_run_id"
          >
            {{ r.mode === "startup" ? "本次启动诊断" : "本次在线实验" }} ·
            {{ r.lab_run_id.slice(0, 8) }} · 有效至
            {{ new Date(r.expires_at).toLocaleTimeString() }}
          </option>
        </select></label
      >
    </div>
    <div class="hypothesis-actions">
      <label class="skill-toggle"
        ><input type="checkbox" v-model="useSkills" :disabled="busy" />使用适用
        Skill 辅助调查</label
      >
      <label class="skill-toggle"
        ><input
          type="checkbox"
          v-model="usePublishedSkills"
          :disabled="busy"
        />参考组织内已发布方法</label
      >
      <label class="skill-toggle"
        ><input
          type="checkbox"
          v-model="useMemory"
          :disabled="busy"
        />参考可召回经验候选（待审核）</label
      >
      <button
        class="primary"
        :disabled="busy || !indexId || !runId || !ticket.product_version"
        @click="perform(start)"
      >
        {{ busy ? "正在调查" : "开始假设调查" }}</button
      ><button :disabled="busy" @click="perform(refreshRuns)">
        刷新本次实验列表
      </button>
    </div>
    <p class="muted">
      最多 3 个假设、6 个串行步骤、6 次只读工具、8 次模型、240
      秒。启动会产生真实模型用量；范围变化清空显示，后台已开始的请求仍会完成并保存。
    </p>
    <p v-if="!available.length" class="notice">
      暂无有效的同版本实验。请先用本地实验准备脚本登记新运行，再刷新列表；历史调查仍可读取。
    </p>
    <p v-if="error" class="notice danger" role="alert">{{ error }}</p>
    <section v-if="result" class="hypothesis-result">
      <h3>
        {{ statuses[result.status] ?? result.status }} ·
        {{ result.stop_reason }}
      </h3>
      <p class="mono">{{ result.investigation_id }}</p>
      <p class="notice subtle">{{ result.limitation }}</p>
      <p class="muted">
        {{ result.duration_ms }} ms · 已知
        {{ result.usage.known_model_calls }} / 用量未知
        {{ result.usage.unknown_model_calls }} 次模型 · 输入
        {{ result.usage.input_tokens }} / 输出
        {{ result.usage.output_tokens }} token · 费用未对账
      </p>
      <p v-for="q in result.questions" :key="q">{{ q }}</p>
      <section v-if="result.memory" class="investigation-skills">
        <h4>本次加载的经验快照</h4>
        <p>{{ result.memory.limitation }}</p>
        <p v-if="!result.memory.loaded.length">没有可召回的匹配候选。</p>
        <p v-if="result.memory.selection_limited">
          本次候选扫描或加载达到上限。
        </p>
        <details v-for="c in result.memory.loaded" :key="c.candidate_id">
          <summary>{{ c.body.title }} · 修订 {{ c.revision }}</summary>
          <p class="skill-fingerprint">
            来源 {{ c.source_sha256 }} · 正文 {{ c.body_sha256 }}
          </p>
          <pre>{{ JSON.stringify(c.body, null, 2) }}</pre>
        </details>
      </section>
      <RetrospectiveComposer
        :api="api"
        :ticket-id="ticket.ticket_id"
        :investigation-id="result.investigation_id"
        :key="result.investigation_id"
      />
      <section v-if="result.skills" class="investigation-skills">
        <h4>本次加载的 Skill 快照</h4>
        <p class="muted">
          方法仅辅助规划，不能替代本次观测或批准。{{
            result.skills.selection_reason === "no_matching_skill"
              ? "没有匹配方法，未加载正文。"
              : "按本次版本、模式与症状匹配。"
          }}
        </p>
        <p v-if="result.skills.selection_limited">
          命中多个方法，本次最多加载两个。
        </p>
        <details
          v-for="s in result.skills.loaded"
          :key="s.skill_id + s.version"
        >
          <summary>
            {{ s.title }} · {{ s.version }} ·
            {{ s.matched_signals?.join(" / ") }}
          </summary>
          <p class="mono skill-fingerprint">
            正文 SHA-256：{{ s.body_sha256 }}
          </p>
          <p v-if="'publication_id' in s" class="skill-fingerprint">
            组织方法发布记录：{{ s.publication_id }} · 修订
            {{ s.publication_revision }}
          </p>
          <pre>{{ s.body }}</pre>
          <p
            v-for="source in s.sources"
            :key="source.path"
            class="skill-fingerprint"
          >
            来源：{{ source.path }} · {{ source.sha256 }}
          </p>
        </details>
      </section>
      <article
        v-for="h in result.hypotheses"
        :key="h.hypothesis_id"
        class="hypothesis-card"
      >
        <h4>{{ h.hypothesis_id }} · {{ h.cause }}</h4>
        <p class="badge">
          {{ statuses[h.status] ?? h.status }} ·
          {{
            h.semantic_reviewed
              ? "模型语义已核对"
              : "模型建议状态，尚未完成语义核对"
          }}
        </p>
        <p>
          <span v-if="h.proposed_status && h.proposed_status !== h.status">
            模型原建议（未通过语义核对）：
          </span>
          {{ h.reason }}
        </p>
        <p class="muted">依据：{{ h.basis }}</p>
        <dl>
          <dt>支持信号</dt>
          <dd>{{ h.support_signal }}</dd>
          <dt>反驳信号</dt>
          <dd>{{ h.refute_signal }}</dd>
        </dl>
        <p v-if="h.support_review" class="notice subtle">
          独立模型评审：{{ h.support_review.verdict }} ·
          {{ h.support_review.reason }}（尚未人工复核）
        </p>
        <div
          v-for="(refs, group) in [h.support_citations, h.refute_citations]"
          :key="group"
        >
          <div v-for="c in refs" :key="c.evidence_id + c.quote">
            <p class="muted">{{ group === 0 ? "支持依据" : "反驳依据" }}</p>
            <blockquote>{{ c.quote }}</blockquote>
            <button
              :disabled="busy"
              @click="perform((epoch) => inspect(epoch, c.evidence_id))"
            >
              回查假设依据
            </button>
          </div>
        </div>
        <p v-for="gap in h.missing_information" :key="gap" class="muted">
          待补充：{{ gap }}
        </p>
      </article>
      <h4>检查步骤</h4>
      <ol class="hypothesis-steps">
        <li v-for="s in result.steps" :key="s.step_id">
          <strong
            >{{ s.step_id }} · {{ statuses[s.status] ?? s.status }}</strong
          >
          <p>{{ s.tool }} · {{ s.reason }}</p>
          <p v-if="s.error" class="notice danger">{{ s.error }}</p>
        </li>
      </ol>
      <div v-if="result.report">
        <h4>调查结论与原文</h4>
        <article
          v-for="c in result.report.claims"
          :key="c.claim_id"
          class="hypothesis-claim"
        >
          <strong>{{ c.claim_id }} · {{ c.text }}</strong>
          <p class="muted">
            模型语义：{{ c.support.verdict }} ·
            {{ c.support.reason }}（尚未人工复核）
          </p>
          <div v-for="ref in c.citations" :key="ref.evidence_id + ref.quote">
            <blockquote>{{ ref.quote }}</blockquote>
            <button
              :disabled="busy"
              @click="perform((epoch) => inspect(epoch, ref.evidence_id))"
            >
              回查调查原文
            </button>
          </div>
        </article>
        <p
          v-for="gap in result.report.missing_information"
          :key="gap"
          class="muted"
        >
          {{ gap }}
        </p>
      </div>
      <details>
        <summary>假设与计划变化（当时的模型建议）</summary>
        <pre>{{ JSON.stringify(result.plan_history, null, 2) }}</pre>
      </details>
      <details>
        <summary>实际调用轨迹</summary>
        <pre>{{ JSON.stringify(result.events, null, 2) }}</pre>
      </details>
    </section>
    <details v-if="reference" open class="hypothesis-reference">
      <summary>本次原文、来源与摘要核对</summary>
      <pre>{{ JSON.stringify(reference, null, 2) }}</pre>
    </details>
    <h3>本工单假设调查历史</h3>
    <p v-if="!history.items.length" class="muted">还没有假设调查记录。</p>
    <div
      v-for="r in history.items"
      :key="r.investigation_id"
      class="hypothesis-history"
    >
      <span
        >{{ statuses[r.status] ?? r.status }} ·
        {{ r.investigation_id.slice(0, 12) }}</span
      ><button
        :disabled="busy"
        @click="perform((epoch) => read(epoch, r.investigation_id))"
      >
        读取历史假设调查
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
.hypothesis-panel {
  overflow-wrap: anywhere;
}
.hypothesis-scope {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
  margin: 20px 0;
}
.hypothesis-scope select {
  width: 100%;
  min-width: 0;
  margin-top: 8px;
}
.hypothesis-actions,
.hypothesis-history {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
.skill-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
}
.skill-toggle input {
  width: auto;
}
.skill-fingerprint {
  overflow-wrap: anywhere;
}
.investigation-skills details {
  padding: 12px 0;
  border-bottom: 1px solid var(--color-border);
}
.hypothesis-card,
.hypothesis-claim {
  padding: 20px 0;
  border-top: 1px solid var(--color-border);
}
.hypothesis-card .badge {
  white-space: normal;
}
.hypothesis-card dl {
  display: grid;
  grid-template-columns: 90px 1fr;
  gap: 10px;
}
.hypothesis-card dd {
  margin: 0;
}
blockquote {
  border-left: 3px solid var(--color-accent);
  padding: 12px;
  margin: 16px 0;
  background: var(--color-surface-soft);
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 12px;
}
.hypothesis-history {
  padding: 12px 0;
  justify-content: space-between;
}
.hypothesis-steps {
  padding-left: 24px;
}
@media (max-width: 760px) {
  .hypothesis-scope {
    grid-template-columns: 1fr;
  }
  .hypothesis-card dl {
    grid-template-columns: 1fr;
  }
}
</style>
