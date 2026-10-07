<script setup lang="ts">
import type { Guided } from "./guided-types";
defineProps<{ guided: Guided; busy: boolean }>();
defineEmits<{ inspect: [id: string] }>();
const statuses: Record<Guided["status"], string> = {
  answered: "证据回答已完成",
  limited_answer: "检索已停止，回答范围有限",
  needs_clarification: "需要补充信息",
  conflict: "发现待核对的证据冲突",
  stopped: "预算已停止流程",
  no_evidence: "当前范围没有可用证据",
  failed: "分步问答失败",
};
const reasons: Record<string, string> = {
  answered: "已有证据，完成回答与引文核对",
  version_required: "产品版本未确认",
  query_clarification: "原始现象需要澄清",
  evidence_clarification: "证据需要补充条件",
  empty_scope: "所选范围没有资料",
  no_matching_evidence: "没有匹配证据",
  conflicting_evidence: "冲突尚未解决，等待核对来源",
  repeated_query: "查询重复，停止继续检索",
  no_new_evidence: "追加检索没有新增证据",
  search_budget: "已达到检索次数上限",
  model_budget: "剩余调用额度不足",
  time_budget: "剩余时间不足",
  evidence_limit: "证据有限，停止继续检索",
  failure: "调用或证据校验失败，未返回部分回答",
};
</script>

<template>
  <section class="card guided-panel" aria-label="分步问答过程">
    <h2>分步证据问答</h2>
    <p class="guided-state">
      <strong>{{ statuses[guided.status] }}</strong> ·
      {{ reasons[guided.stop_reason] ?? guided.stop_reason }}
    </p>
    <p><strong>原始问题：</strong>{{ guided.original_query }}</p>
    <p v-if="guided.clarification">
      <strong>补充信息：</strong>{{ guided.clarification }}
    </p>
    <p v-if="guided.rewritten_query">
      <strong>检索改写：</strong>{{ guided.rewritten_query }}
    </p>
    <p class="notice subtle">
      改写仅用于检索。冲突与结论为模型判断，尚未人工复核，不能确认当前客户根因。
    </p>
    <ul v-if="guided.questions.length" class="guided-questions">
      <li v-for="question in guided.questions" :key="question">
        {{ question }}
      </li>
    </ul>
    <p v-if="guided.questions.length" class="muted">
      核对版本并在上方补充信息后，重新提交分步问答。
    </p>
    <article
      v-for="(conflict, n) in guided.conflicts"
      :key="n"
      class="guided-conflict"
    >
      <h3>{{ conflict.topic }}</h3>
      <p>{{ conflict.reason }}</p>
      <div
        v-for="citation in conflict.citations"
        :key="citation.context_id + citation.start"
      >
        <blockquote>{{ citation.quote }}</blockquote>
        <button
          :disabled="busy"
          @click="$emit('inspect', citation.evidence_id)"
        >
          回查冲突原文
        </button>
      </div>
    </article>
    <details open>
      <summary>检索与停止过程</summary>
      <ol>
        <li v-for="(step, n) in guided.trace" :key="n">
          <template v-if="step.stage === 'retrieval'"
            >第 {{ step.search }} 次检索：{{ step.query }}；新增
            {{ step.new_evidence }} 条，合计
            {{ step.total_evidence }} 条。</template
          >
          <template v-else-if="step.stage === 'decision'"
            >证据决策：{{ step.reason
            }}<span v-if="step.next_query"
              >；追加查询：{{ step.next_query }}</span
            ></template
          >
          <template v-else-if="step.stage === 'rewrite'"
            >查询规划：{{ step.reason }}</template
          >
          <template v-else-if="step.stage === 'answer'"
            >完成回答与引文核对。</template
          >
          <template v-else>{{
            reasons[step.reason ?? ""] ?? "流程已停止"
          }}</template>
        </li>
      </ol>
    </details>
    <p class="muted">
      整个流程：{{ guided.usage.known_model_calls }} 次用量已知调用 ·
      {{ guided.usage.unknown_usage_calls }} 次用量未知调用 ·
      {{ guided.usage.input_tokens }} 输入 /
      {{ guided.usage.output_tokens }} 输出 token · 费用未对账；本次过程不保存。
    </p>
  </section>
</template>

<style scoped>
.guided-panel {
  padding: 22px;
  margin: 22px 0;
  min-width: 0;
  overflow-wrap: anywhere;
}
.guided-panel p,
.guided-panel li,
blockquote {
  white-space: pre-wrap;
  line-height: 1.7;
}
.guided-conflict {
  border-left: 3px solid #bb784c;
  padding-left: 16px;
  margin: 18px 0;
}
blockquote {
  margin: 12px 0;
  padding: 12px;
  background: #f4f2ee;
}
li {
  margin: 8px 0;
}
summary {
  cursor: pointer;
  font-weight: 600;
}
</style>
