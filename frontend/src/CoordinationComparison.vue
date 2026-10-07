<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { ApiError, type Api } from "./api";
import DetailDialog from "./DetailDialog.vue";

interface Metrics {
  status: string;
  model_calls: number;
  tool_calls: number;
  input_tokens: number;
  output_tokens: number;
  duration_ms: number;
  current_evidence: boolean;
  claim_count: number;
  review_not_supported: number | null;
}
interface Group {
  attempted: number;
  completed: number;
  failed_or_stopped: number;
  missing_responses: number;
  known_calls: number;
  unknown_calls: number;
  input_tokens: number;
  output_tokens: number;
  duration_ms: number;
}
interface Pair {
  family: string;
  same_total_budget_conditions: boolean;
  differences: string[];
  single: Metrics | null;
  multi: Metrics | null;
}
interface Record {
  family: string;
  arm: string;
  status: string;
  path: string | null;
  stop_reason: string | null;
}
interface Report {
  available: boolean;
  complete?: boolean;
  status?: string;
  model?: string;
  limitation?: string;
  groups?: { single: Group; multi: Group };
  pairs?: Pair[];
  records?: Record[];
  protocol_differences?: string[];
}
interface Flow {
  status: string;
  stop_reason?: string;
  error?: string;
  report?: Result;
  result?: Result;
  tasks?: { [key: string]: { status: string; error?: string } };
  failure_details?: unknown;
  evidence?: { evidence_id: string; reference_url: string }[];
}
interface Result {
  claims: {
    claim_id: string;
    text: string;
    support?: { verdict: string; reason: string };
    citations: { evidence_id: string; quote: string; reference_url?: string }[];
  }[];
  missing_information: string[];
  conflicts?: unknown[];
}
const props = defineProps<{ api: Api }>();
const report = ref<Report | null>(null);
const flow = ref<Flow | null>(null);
const raw = ref<unknown>(null);
const evidence = ref<unknown>(null);
const flowPath = ref("");
const evidencePaths = ref<{ [key: string]: string }>({});
const detailOpen = ref(false);
const evidenceOpen = ref(false);
const busy = ref(false);
const error = ref("");
const labels: { [key: string]: string } = {
  single: "单 Agent",
  multi: "多 Agent",
  configuration: "启动配置",
  pool: "连接池",
  cache: "缓存",
  downstream: "下游",
  completed: "已完成",
  failed: "失败",
  stopped: "预算停止",
  pending: "待推进",
  uncertain: "回执未知",
  cancelled: "已取消",
};
let active = true;
let generation = 0;
onUnmounted(() => {
  active = false;
  generation++;
});
async function read(path: string, target: "report" | "flow" | "evidence") {
  if (busy.value) return;
  const epoch = ++generation;
  busy.value = true;
  error.value = "";
  if (target === "report") report.value = null;
  if (target === "flow") flow.value = null;
  try {
    const body = await props.api.request(path);
    if (!active || epoch !== generation) return;
    if (target === "report") report.value = body as Report;
    else if (target === "flow") {
      flow.value = body as Flow;
      raw.value = body;
      flowPath.value = path;
      evidencePaths.value = Object.fromEntries(
        (body as Flow).evidence?.map((e) => [e.evidence_id, e.reference_url]) ??
          [],
      );
      detailOpen.value = true;
    } else {
      evidence.value = body;
      evidenceOpen.value = true;
    }
  } catch (exc) {
    if (
      active &&
      epoch === generation &&
      !(exc instanceof ApiError && exc.code === "STALE_SESSION")
    )
      error.value = exc instanceof Error ? exc.message : "比较记录读取失败";
  } finally {
    if (active) busy.value = false;
  }
}
const load = () => read("/api/coordination-comparison", "report");
function reference(id: string) {
  return flowPath.value.startsWith("/api/coordination-executions/")
    ? `${flowPath.value}/evidence/${id}`
    : evidencePaths.value[id];
}
function readEvidence(id: string) {
  const path = reference(id);
  if (path) return read(path, "evidence");
}
onMounted(load);
</script>

<template>
  <section class="card comparison-panel" aria-label="单 / 多 Agent 比较">
    <div class="card-heading">
      <h2>单 / 多 Agent 比较</h2>
      <button :disabled="busy" @click="load">刷新比较结果</button>
    </div>
    <p class="notice subtle">
      同一任务、模型、来源与总预算：6 工具 / 8 模型 / 240 秒 / 16000
      字符。每臂重新准备故障，交替执行顺序，失败计入分母。
    </p>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <p v-if="report && !report.available" class="muted">
      尚无比较报告：{{ report.status }}
    </p>
    <template v-if="report?.available">
      <p>
        {{ report.model }} ·
        {{ report.complete ? "已收齐" : "运行中，尚未收齐" }} ·
        {{ report.limitation }}
      </p>
      <div class="comparison-groups">
        <article v-for="(g, arm) in report.groups" :key="arm">
          <h3>{{ labels[arm] }}</h3>
          <p>
            完成 {{ g.completed }} / {{ g.attempted }} · 失败或停止
            {{ g.failed_or_stopped }} · 无响应 {{ g.missing_responses }}
          </p>
          <p>
            已知调用 {{ g.known_calls }} / 未知 {{ g.unknown_calls }} · 输入
            {{ g.input_tokens }} / 输出 {{ g.output_tokens }} token
          </p>
          <p>流程累计 {{ g.duration_ms }} ms；费用未知，人工准确率未评估。</p>
        </article>
      </div>
      <details>
        <summary>比较条件与协议差异</summary>
        <p v-for="text in report.protocol_differences" :key="text">
          {{ text }}
        </p>
      </details>
      <article
        v-for="pair in report.pairs"
        :key="pair.family"
        class="comparison-pair"
      >
        <h3>
          {{ labels[pair.family] }} ·
          {{
            pair.same_total_budget_conditions
              ? "总预算与固定条件一致"
              : "条件失配，不可比较"
          }}
        </h3>
        <p v-if="pair.differences.length" class="error">
          差异：{{ pair.differences.join("、") }}
        </p>
        <div class="comparison-groups">
          <div v-for="arm in ['single', 'multi'] as const" :key="arm">
            <template v-if="pair[arm]">
              <p>
                {{ labels[arm] }}：{{
                  labels[pair[arm]!.status] ?? pair[arm]!.status
                }}
                · {{ pair[arm]!.duration_ms }} ms
              </p>
              <p>
                模型 {{ pair[arm]!.model_calls }} / 工具
                {{ pair[arm]!.tool_calls }} · 本次取证
                {{ pair[arm]!.current_evidence ? "有" : "无" }}
              </p>
              <p>
                结论 {{ pair[arm]!.claim_count }} · 语义未支持
                {{ pair[arm]!.review_not_supported ?? "未评审" }}
              </p>
            </template>
            <p v-else>{{ labels[arm] }}：无可绑定响应</p>
          </div>
        </div>
      </article>
      <div
        v-for="(record, index) in report.records"
        :key="index"
        class="comparison-record"
      >
        <span
          >{{ labels[record.family] }} · {{ labels[record.arm] }} ·
          {{ labels[record.status] ?? record.status }} ·
          {{ record.stop_reason }}</span
        >
        <button
          :disabled="busy || !record.path"
          @click="record.path && read(record.path, 'flow')"
        >
          查看比较记录
        </button>
      </div>
    </template>
    <DetailDialog v-model:open="detailOpen" title="比较流程详情" :busy="busy">
      <template v-if="flow">
        <p>
          状态：{{ labels[flow.status] ?? flow.status }} ·
          {{ flow.stop_reason ?? flow.error }}
        </p>
        <p class="notice">
          模型语义未人工复核；流程完成与逐字引用不等于原因已确认。
        </p>
        <p v-for="(task, name) in flow.tasks" :key="name">
          {{ name }}：{{ labels[task.status] ?? task.status }} ·
          {{ task.error }}
        </p>
        <template v-if="flow.report ?? flow.result">
          <article
            v-for="claim in (flow.report ?? flow.result)!.claims"
            :key="claim.claim_id"
            class="comparison-claim"
          >
            <p>{{ claim.text }}</p>
            <p>
              语义：{{ claim.support?.verdict ?? "未评审" }} ·
              {{ claim.support?.reason }}
            </p>
            <blockquote
              v-for="(citation, index) in claim.citations"
              :key="index"
            >
              {{ citation.quote }}
              <button
                :disabled="busy || !reference(citation.evidence_id)"
                @click="readEvidence(citation.evidence_id)"
              >
                查看比较原文
              </button>
            </blockquote>
          </article>
          <p
            v-for="text in (flow.report ?? flow.result)!.missing_information"
            :key="text"
          >
            待补：{{ text }}
          </p>
        </template>
        <details>
          <summary>阶段、失败诊断与完整记录</summary>
          <pre>{{ JSON.stringify(raw, null, 2) }}</pre>
        </details>
      </template>
    </DetailDialog>
    <DetailDialog v-model:open="evidenceOpen" title="比较证据原文" :busy="busy">
      <pre>{{ JSON.stringify(evidence, null, 2) }}</pre>
    </DetailDialog>
  </section>
</template>

<style scoped>
.comparison-panel {
  min-width: 0;
  margin-top: 20px;
}
.comparison-groups {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 260px), 1fr));
  gap: 16px;
}
.comparison-pair {
  border-top: 1px solid var(--line);
  padding: 12px 0;
}
.comparison-record {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 12px 0;
}
.comparison-record span {
  flex: 1;
  min-width: 160px;
  overflow-wrap: anywhere;
}
.comparison-claim {
  padding: 12px 0;
  border-bottom: 1px solid var(--line);
}
blockquote {
  margin: 12px 0;
  padding: 12px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
