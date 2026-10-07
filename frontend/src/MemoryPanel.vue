<script setup lang="ts">
import ViewTabs from "./ViewTabs.vue";
import DetailDialog from "./DetailDialog.vue";
import { computed, onMounted, onUnmounted, ref } from "vue";
import { ApiError, type Api } from "./api";
import type { Page } from "./types";
import type { Candidate, Retrospective } from "./memory-types";
const detailOpen = ref(false);
const detailView = ref("summary");
const detailViews = [
  { value: "summary", label: "复盘摘要" },
  { value: "candidate", label: "经验与治理" },
  { value: "source", label: "原始记录" },
];
const detailTrigger = ref<HTMLElement | null>(null);
const props = defineProps<{ api: Api }>();
const rows = ref<Page<Retrospective>>({
  items: [],
  total: 0,
  offset: 0,
  limit: 10,
});
const detail = ref<Retrospective | null>(null);
const reference = ref<unknown>(null);
const otherId = ref("");
const reason = ref("");
const busy = ref(false);
const error = ref("");
let generation = 0;
let mounted = true;
const status: Record<string, string> = {
  candidate: "待审核",
  revoked: "已撤销",
  invalidated: "已失效",
  expired: "已过期",
  conflicted: "冲突待处理",
  eligible_unreviewed: "可召回待审核方法",
};
const candidate = computed(() => detail.value?.candidate);
const otherCandidates = computed(() =>
  rows.value.items
    .map((r) => r.candidate)
    .filter(
      (c): c is Candidate =>
        !!c &&
        c.status === "candidate" &&
        c.candidate_id !== candidate.value?.candidate_id &&
        c.body.product_version === candidate.value?.body.product_version &&
        c.body.mode === candidate.value?.body.mode,
    ),
);
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
      error.value = exc instanceof Error ? exc.message : "经验请求失败。";
  } finally {
    if (mounted) busy.value = false;
  }
}
async function load(epoch: number, offset = rows.value.offset) {
  const body = await props.api.request<Page<Retrospective>>(
    `/api/retrospectives?offset=${offset}&limit=10`,
  );
  if (current(epoch)) rows.value = body;
}
async function inspect(row: Retrospective) {
  // 切换详情使迟到的旧详情失效，组织切换时组件也会卸载。
  if (busy.value) return;
  generation++;
  detailView.value = "summary";
  detail.value = null;
  reference.value = null;
  reason.value = "";
  otherId.value = "";
  await perform(async (epoch) => {
    const body = await props.api.request<Retrospective>(
      `/api/retrospectives/${row.retrospective_id}?include_source=true`,
    );
    if (current(epoch)) {
      detail.value = body;
      detailOpen.value = true;
    }
  });
}
async function decide(epoch: number, decision: string) {
  const c = candidate.value;
  if (!c || !detail.value) return;
  const body = await props.api.request<Candidate>(
    `/api/experiences/${c.candidate_id}/decisions`,
    {
      method: "POST",
      body: JSON.stringify({
        revision: c.revision,
        decision,
        reason: reason.value,
      }),
    },
  );
  if (current(epoch)) {
    detail.value.candidate = body;
    await load(epoch);
  }
}
async function markConflict(epoch: number) {
  const c = candidate.value;
  const other = otherCandidates.value.find(
    (c) => c.candidate_id === otherId.value,
  );
  if (!c || !other || !detail.value) return;
  await props.api.request(`/api/experiences/${c.candidate_id}/conflicts`, {
    method: "POST",
    body: JSON.stringify({
      revision: c.revision,
      other_id: other.candidate_id,
      other_revision: other.revision,
      reason: reason.value,
    }),
  });
  const body = await props.api.request<Retrospective>(
    `/api/retrospectives/${detail.value.retrospective_id}?include_source=true`,
  );
  if (current(epoch)) {
    detail.value = body;
    await load(epoch);
  }
}
async function original(epoch: number, path: string) {
  const body = await props.api.request(path);
  if (current(epoch)) reference.value = body;
}
onMounted(() => perform(load));
</script>
<template>
  <section class="card memory-panel" aria-label="经验候选">
    <div class="card-heading">
      <h2>事件复盘与经验候选</h2>
      <button :disabled="busy" @click="perform((e) => load(e))">
        刷新经验列表
      </button>
    </div>
    <p class="notice subtle">
      摘要来自原始事件摘录，候选只供排查规划参考。人工标注冲突、到期、失效或撤销会阻止后续召回；旧调查快照仍保留。候选
      Skill 审批与效果比较请切换“组织方法”与“成对实验”。
    </p>
    <p v-if="error" class="notice danger" role="alert">{{ error }}</p>
    <p v-if="!rows.items.length" class="muted">
      暂无复盘。在工单的假设调查历史中选择记录，准备事件复盘。
    </p>
    <div class="memory-grid">
      <article
        v-for="r in rows.items"
        :key="r.retrospective_id"
        class="memory-card"
      >
        <h3>{{ r.candidate?.body.title || "调查停止或失败复盘" }}</h3>
        <p class="fingerprint">{{ r.retrospective_id.slice(0, 12) }}</p>
        <p>
          {{ r.summary.event_count }} 个事件 · {{ r.summary.status }} ·
          摘录新增调用 {{ r.model_calls }}
        </p>
        <p v-if="r.candidate">
          {{ r.candidate.body.product_version }} · {{ r.candidate.body.mode }} ·
          {{ status[r.candidate.eligibility] || r.candidate.eligibility }}
        </p>
        <button
          :disabled="busy"
          @click="
            detailTrigger = $event.currentTarget as HTMLElement;
            inspect(r);
          "
        >
          查看复盘与来源
        </button>
      </article>
    </div>
    <div class="pagination">
      <button
        :disabled="busy || rows.offset === 0"
        @click="perform((e) => load(e, rows.offset - 10))"
      >
        经验上一页</button
      ><span>{{ rows.total }} 条复盘</span
      ><button
        :disabled="busy || rows.offset + 10 >= rows.total"
        @click="perform((e) => load(e, rows.offset + 10))"
      >
        经验下一页
      </button>
    </div>
    <DetailDialog
      v-if="detail"
      v-model:open="detailOpen"
      :return-focus="detailTrigger"
      title="复盘与经验详情"
      :busy="busy"
    >
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <section class="memory-detail">
        <ViewTabs
          v-model="detailView"
          label="事件复盘详情分区"
          :items="detailViews"
        />
        <div v-show="detailView === 'summary'">
          <h3>冻结摘录摘要</h3>
          <p class="fingerprint">来源 SHA-256：{{ detail.source_sha256 }}</p>
          <p class="fingerprint">摘要 SHA-256：{{ detail.summary_sha256 }}</p>
          <p>
            调查 {{ detail.summary.status }} ·
            {{ detail.summary.event_count }} 个事件 ·
            {{ detail.summary.plan_count }} 次计划建议
          </p>
          <p v-if="detail.summary.action">
            动作 {{ detail.summary.action.status }} · 复测
            {{
              detail.summary.action.retest_passed === null
                ? "未完成"
                : detail.summary.action.retest_passed
                  ? "通过"
                  : "未通过"
            }}；不证明唯一根因。
          </p>
          <h4>执行过的检查</h4>
          <ol>
            <li v-for="s in detail.summary.steps" :key="s.step_id">
              <strong>{{ s.step_id }} · {{ s.tool }} · {{ s.status }}</strong>
              <p>{{ s.reason }}</p>
            </li>
          </ol>
          <h4>原调查原因候选（保留原状态）</h4>
          <article
            v-for="h in detail.summary.hypotheses"
            :key="h.hypothesis_id"
          >
            <p>{{ h.hypothesis_id }} · {{ h.cause }} · {{ h.status }}</p>
            <p class="muted">{{ h.reason }}</p>
          </article>
          <h4>原结论与待补信息</h4>
          <p v-for="c in detail.summary.claims" :key="c.claim_id">
            {{ c.kind }} · {{ c.text }} · 原模型评审 {{ c.support?.verdict }}
          </p>
          <p
            v-for="gap in detail.summary.missing_information"
            :key="gap"
            class="muted"
          >
            {{ gap }}
          </p>
          <details>
            <summary>摘要结构与原引用</summary>
            <pre>{{ JSON.stringify(detail.summary, null, 2) }}</pre>
          </details>
        </div>
        <div v-if="candidate" v-show="detailView === 'candidate'">
          <h3>
            {{ status[candidate.status] }} · 修订 {{ candidate.revision }}
          </h3>
          <p>
            {{ status[candidate.eligibility] }} · 有效至
            {{ new Date(candidate.expires_at).toLocaleString() }}
          </p>
          <p>{{ candidate.body.limitation }}</p>
          <p>历史症状：{{ candidate.body.symptoms }}</p>
          <ol>
            <li v-for="c in candidate.body.checks" :key="c.step_id">
              <strong>{{ c.tool }}</strong>
              <p>{{ c.reason }}</p>
            </li>
          </ol>
          <article
            v-for="(signal, i) in candidate.body.distinguishing_signals"
            :key="i"
          >
            <p>历史区分信号：{{ signal.support_signal }}</p>
            <p class="muted">相反信号：{{ signal.refute_signal }}</p>
          </article>
          <details>
            <summary>候选结构与适用条件</summary>
            <pre>{{ JSON.stringify(candidate.body, null, 2) }}</pre>
          </details>
          <p class="fingerprint">正文 SHA-256：{{ candidate.body_sha256 }}</p>
          <button
            v-for="e in candidate.body.source_evidence"
            :key="e.evidence_id"
            :disabled="busy"
            @click="perform((epoch) => original(epoch, e.reference_url))"
          >
            读取历史原文 {{ e.evidence_id.slice(-8) }}
          </button>
          <pre v-if="reference">{{ JSON.stringify(reference, null, 2) }}</pre>
          <div
            v-if="candidate.status === 'candidate'"
            class="memory-governance"
          >
            <label
              >经验治理原因<textarea
                v-model="reason"
                maxlength="400"
                :disabled="busy"
                rows="2"
              />
            </label>
            <button
              :disabled="busy || !reason.trim()"
              @click="perform((e) => decide(e, 'invalidate'))"
            >
              标记经验失效
            </button>
            <button
              :disabled="busy || !reason.trim()"
              @click="perform((e) => decide(e, 'revoke'))"
            >
              撤销经验
            </button>
            <label
              >冲突另一候选<select v-model="otherId" :disabled="busy">
                <option value="">选择本页同范围候选</option>
                <option
                  v-for="c in otherCandidates"
                  :key="c.candidate_id"
                  :value="c.candidate_id"
                >
                  {{ c.body.title }} · {{ c.candidate_id.slice(0, 8) }}
                </option>
              </select></label
            >
            <button
              :disabled="busy || !otherId || !reason.trim()"
              @click="perform(markConflict)"
            >
              人工标注冲突
            </button>
          </div>
          <h4>治理审计与冲突</h4>
          <pre>{{
            JSON.stringify(
              { events: candidate.events, conflicts: candidate.conflicts },
              null,
              2,
            )
          }}</pre>
        </div>
        <div v-show="detailView === 'source'">
          <details open>
            <summary>冻结原始记录</summary>
            <pre>{{ JSON.stringify(detail.source, null, 2) }}</pre>
          </details>
        </div>
      </section>
    </DetailDialog>
  </section>
</template>
<style scoped>
.memory-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}
.memory-card {
  overflow-wrap: anywhere;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  padding: 18px;
}
.memory-detail {
  margin-top: 24px;
}
.memory-detail pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 420px;
  overflow-y: auto;
}
.fingerprint {
  overflow-wrap: anywhere;
  font-size: 12px;
}
.memory-governance {
  display: grid;
  gap: 12px;
  margin: 20px 0;
}
.memory-governance textarea,
.memory-governance select {
  width: 100%;
}
.memory-panel {
  margin-bottom: 24px;
}
@media (max-width: 760px) {
  .memory-grid {
    grid-template-columns: 1fr;
  }
}
</style>
