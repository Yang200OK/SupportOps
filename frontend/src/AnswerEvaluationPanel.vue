<script setup lang="ts">
import { onBeforeUnmount, ref } from "vue";
import { ApiError, type Api } from "./api";
const props = defineProps<{ api: Api }>();
type Summary = {
  total: number;
  completed: number;
  failed: number;
  not_run: number;
  outcome_match_all: number;
  rule_fact_coverage_all: number;
  p95_ms: number | null;
  input_tokens: number;
  output_tokens: number;
  known_model_calls: number;
  unknown_usage_calls: number;
  unknown_pipeline_attempts: number;
};
const emit = defineEmits<{ summary: [value: Summary | null] }>();
type Row = {
  task_id: string;
  split: string;
  category: string;
  status: string;
  error_code: string | null;
  metrics: null | {
    rule_fact_coverage: number | null;
    literal_citations: number;
    invalid_citations: number;
    model_supported_claims: number;
    total_claims: number;
    all_versions_match: boolean;
    forbidden_output_absent: boolean;
  };
};
type Result = {
  summary: { all: Summary; dev: Summary };
  report: { main_model: string; corpus_sha256: string; attempts: Row[] };
  limitation: string;
};
const result = ref<Result | null>(null),
  error = ref(""),
  busy = ref(false);
let generation = 0,
  disposed = false;
async function load(event: Event) {
  const ticket = ++generation;
  result.value = null;
  emit("summary", null);
  error.value = "";
  const file = (event.target as HTMLInputElement).files?.[0];
  if (!file) {
    busy.value = false;
    return;
  }
  busy.value = true;
  try {
    if (file.size > 1024 * 1024) throw new Error("报告不能超过 1 MiB。");
    const payload = JSON.parse(await file.text());
    if (disposed || ticket !== generation) return;
    const response = await props.api.request<Result>(
      "/api/evaluations/answers/validate-report",
      { method: "POST", body: JSON.stringify(payload) },
    );
    if (!disposed && ticket === generation) {
      result.value = response;
      emit("summary", response.summary.all);
    }
  } catch (exc) {
    if (
      !disposed &&
      ticket === generation &&
      !(exc instanceof ApiError && exc.code === "STALE_SESSION")
    )
      error.value = exc instanceof Error ? exc.message : "报告校验失败。";
  } finally {
    if (!disposed && ticket === generation) busy.value = false;
  }
}
const percent = (n: number | null) =>
  n === null ? "不适用" : `${(n * 100).toFixed(1)}%`;
onBeforeUnmount(() => {
  disposed = true;
  generation++;
});
</script>

<template>
  <section class="card answer-evaluation-panel">
    <h2>回答评测报告</h2>
    <p class="muted">
      读取离线生成的 answer-report
      JSON。页面不运行批量模型；只检查本地报告格式与分母，不认证执行来源。规则覆盖不是语义事实准确率，模型支持判断尚未人工复核。
    </p>
    <label
      >回答评测报告 JSON<input
        type="file"
        accept=".json,application/json"
        @change="load"
    /></label>
    <p v-if="busy" role="status">正在校验报告…</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <div v-if="result" class="answer-evaluation-result">
      <p>{{ result.limitation }}</p>
      <p>
        全部 {{ result.summary.all.total }} · 完成
        {{ result.summary.all.completed }} · 失败
        {{ result.summary.all.failed }} · 未执行
        {{ result.summary.all.not_run }}
      </p>
      <p>
        dev 状态符合 {{ percent(result.summary.dev.outcome_match_all) }} · dev
        规则事实覆盖（全部 dev 分母）{{
          percent(result.summary.dev.rule_fact_coverage_all)
        }}
      </p>
      <p>
        已知调用 {{ result.summary.all.known_model_calls }} · 未知用量调用
        {{ result.summary.all.unknown_usage_calls }} ·
        {{ result.summary.all.input_tokens }} 输入 /
        {{ result.summary.all.output_tokens }} 输出 token · 费用未核对
      </p>
      <p v-if="result.summary.all.unknown_pipeline_attempts">
        另有
        {{ result.summary.all.unknown_pipeline_attempts }}
        条整管道尝试未收到服务端计量，内部调用数无法确认。
      </p>
      <p>
        主模型 {{ result.report.main_model }} · p95
        {{ result.summary.dev.p95_ms ?? "未测" }} ms
      </p>
      <p class="mono digest">{{ result.report.corpus_sha256 }}</p>
      <div class="report-table">
        <table>
          <thead>
            <tr>
              <th>任务 / 分区</th>
              <th>状态</th>
              <th>规则覆盖</th>
              <th>引文有效 / 无效</th>
              <th>模型支持</th>
              <th>版本 / 禁用输出</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in result.report.attempts" :key="row.task_id">
              <td>{{ row.task_id }} / {{ row.split }}</td>
              <td>{{ row.status }} {{ row.error_code }}</td>
              <td>{{ percent(row.metrics?.rule_fact_coverage ?? null) }}</td>
              <td>
                {{ row.metrics?.literal_citations ?? "—" }} /
                {{ row.metrics?.invalid_citations ?? "—" }}
              </td>
              <td>
                {{ row.metrics?.model_supported_claims ?? "—" }} /
                {{ row.metrics?.total_claims ?? "—" }}
              </td>
              <td>
                {{ row.metrics?.all_versions_match ?? "未测" }} /
                {{ row.metrics?.forbidden_output_absent ?? "未测" }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>

<style scoped>
.answer-evaluation-panel {
  margin-top: 20px;
  min-width: 0;
}
label {
  display: grid;
  gap: 8px;
}
.answer-evaluation-result {
  overflow-wrap: anywhere;
  min-width: 0;
}
.report-table {
  max-width: 100%;
  overflow-x: auto;
}
table {
  width: 100%;
  min-width: 900px;
  font-size: 13px;
}
th,
td {
  padding: 10px;
  text-align: left;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
</style>
