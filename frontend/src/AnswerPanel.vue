<script setup lang="ts">
import type { Answer } from "./rag-types";

defineProps<{ answer: Answer; busy: boolean }>();
const emit = defineEmits<{ inspect: [evidenceId: string] }>();
const kinds = {
  fact: "原文事实表述",
  hypothesis: "可能原因（待验证）",
  check: "建议检查",
};
const verdicts = {
  supported: "支持该表述",
  unsupported: "不支持该表述",
  insufficient: "支持不足",
};
</script>

<template>
  <section class="card answer-panel" aria-label="证据回答">
    <div class="card-heading">
      <h2>证据回答</h2>
      <span>RelayDesk {{ answer.product_version }}</span>
    </div>
    <p class="notice subtle">{{ answer.limitation }}</p>
    <p v-if="answer.status === 'no_evidence'" class="notice subtle">
      当前范围没有证据，未调用回答模型。
    </p>
    <p v-else-if="!answer.claims.length" class="notice subtle">
      现有证据不足以形成有引用的结论。
    </p>
    <article
      v-for="claim in answer.claims"
      :key="claim.claim_id"
      class="answer-claim"
      :class="claim.support.verdict"
    >
      <div class="card-heading">
        <h3>{{ claim.claim_id }} · {{ kinds[claim.kind] }}</h3>
        <strong>模型语义判断：{{ verdicts[claim.support.verdict] }}</strong>
      </div>
      <p v-if="claim.support.verdict !== 'supported'" class="review-notice">
        该项未通过支持核对，仅供审阅，不应作为已证实事实。
      </p>
      <p class="claim-text">{{ claim.text }}</p>
      <p class="muted">核对理由：{{ claim.support.reason }}</p>
      <section
        v-for="(citation, n) in claim.citations"
        :key="n"
        class="answer-citation"
      >
        <p class="muted">引用 {{ n + 1 }} · 身份与原文字面已核对</p>
        <blockquote>{{ citation.quote }}</blockquote>
        <details>
          <summary>引用身份与位置</summary>
          <p class="mono">{{ citation.evidence_id }}</p>
          <p>
            本次上下文字符 {{ citation.start }}–{{
              citation.end
            }}（结束位置不含）
          </p>
          <p class="mono">上下文 SHA-256 {{ citation.context_text_sha256 }}</p>
        </details>
        <button :disabled="busy" @click="emit('inspect', citation.evidence_id)">
          回查引用原文
        </button>
      </section>
    </article>
    <h3>还需补充的信息</h3>
    <ul>
      <li v-for="(item, n) in answer.missing_information" :key="n">
        {{ item }}
      </li>
    </ul>
    <p class="muted">
      {{ answer.latency_ms }} ms ·
      {{ answer.usage.known_model_calls }} 次已知模型调用 · 输入
      {{ answer.usage.input_tokens }} / 输出
      {{ answer.usage.output_tokens }} token · 费用未核对
    </p>
  </section>
</template>

<style scoped>
.answer-panel {
  padding: 24px;
  margin: 20px 0;
  min-width: 0;
}
.answer-panel .card-heading {
  flex-wrap: wrap;
  gap: 12px;
}
.answer-claim {
  border-top: 1px solid #e1d9cf;
  padding: 18px 0;
}
.answer-claim h3 {
  margin: 0;
}
.answer-claim strong {
  color: #56634c;
  font-size: 13px;
}
.answer-claim.unsupported strong,
.answer-claim.insufficient strong,
.review-notice {
  color: #9a482d;
}
.answer-citation {
  background: #f7f4ef;
  border-radius: 8px;
  padding: 14px;
  margin-top: 12px;
}
.answer-citation blockquote {
  margin: 10px 0;
  border-left: 3px solid #c78d61;
  padding: 0 12px;
  white-space: pre-wrap;
}
.answer-panel p,
.answer-panel li,
.answer-citation {
  overflow-wrap: anywhere;
}
.claim-text {
  white-space: pre-wrap;
  line-height: 1.7;
}
.answer-citation details {
  margin-bottom: 12px;
}
@media (max-width: 850px) {
  .answer-panel {
    padding: 18px;
  }
}
</style>
