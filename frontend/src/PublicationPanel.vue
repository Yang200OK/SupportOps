<script setup lang="ts">
import ViewTabs from "./ViewTabs.vue";
import DetailDialog from "./DetailDialog.vue";
import { computed, onMounted, onUnmounted, ref } from "vue";
import { ApiError, type Api } from "./api";
import type { Page } from "./types";
import type { Retrospective } from "./memory-types";
import type { SkillPublication, Regression } from "./publication-types";

const detailOpen = ref(false);
const detailView = ref("method");
const detailViews = [
  { value: "method", label: "方法与来源" },
  { value: "approval", label: "回归与审批" },
  { value: "audit", label: "审计记录" },
];
const createOpen = ref(false);
const detailTrigger = ref<HTMLElement | null>(null);
const props = defineProps<{ api: Api }>();
const rows = ref<Page<SkillPublication>>({
  items: [],
  total: 0,
  offset: 0,
  limit: 10,
});
const sources = ref<Page<Retrospective>>({
  items: [],
  total: 0,
  offset: 0,
  limit: 10,
});
const candidateId = ref("");
const detail = ref<SkillPublication | null>(null);
const reportId = ref("");
const reason = ref("");
const source = ref<unknown>(null);
const busy = ref(false);
const error = ref("");
let mounted = true;
let generation = 0;
const labels: Record<string, string> = {
  draft: "待审批",
  approved: "已批准",
  rejected: "已拒绝",
  published: "已发布",
  revoked: "已撤销",
  eligible: "当前可使用",
};
const candidates = computed(() =>
  sources.value.items.flatMap((r) =>
    r.candidate?.eligibility === "eligible_unreviewed" ? [r.candidate] : [],
  ),
);
const selectedReport = computed(() =>
  detail.value?.reports.find((r) => r.report_id === reportId.value),
);
const current = (epoch: number) => mounted && epoch === generation;
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
      current(epoch) &&
      !(exc instanceof ApiError && exc.code === "STALE_SESSION")
    )
      error.value = exc instanceof Error ? exc.message : "方法发布请求失败。";
  } finally {
    if (mounted) busy.value = false;
  }
}
async function list(epoch: number, offset = rows.value.offset) {
  const body = await props.api.request<Page<SkillPublication>>(
    `/api/skill-drafts?offset=${offset}&limit=10`,
  );
  if (current(epoch)) rows.value = body;
}
async function listSources(epoch: number, offset = sources.value.offset) {
  const body = await props.api.request<Page<Retrospective>>(
    `/api/retrospectives?offset=${offset}&limit=10`,
  );
  if (current(epoch)) {
    sources.value = body;
    candidateId.value = "";
  }
}
async function inspect(row: SkillPublication) {
  if (busy.value) return;
  generation++;
  detailView.value = "method";
  detail.value = null;
  reportId.value = "";
  source.value = null;
  reason.value = "";
  await perform(async (epoch) => {
    const body = await props.api.request<SkillPublication>(
      `/api/skill-drafts/${row.draft_id}`,
    );
    if (current(epoch)) {
      detail.value = body;
      detailOpen.value = true;
    }
  });
}
async function create(epoch: number) {
  const c = candidates.value.find((c) => c.candidate_id === candidateId.value);
  if (!c) return;
  const body = await props.api.request<SkillPublication>("/api/skill-drafts", {
    method: "POST",
    body: JSON.stringify({
      request_id: crypto.randomUUID(),
      candidate_id: c.candidate_id,
      candidate_revision: c.revision,
    }),
  });
  if (current(epoch)) {
    detail.value = body;
    detailView.value = "approval";
    createOpen.value = false;
    detailOpen.value = true;
    reportId.value = "";
    source.value = null;
    reason.value = "";
    await list(epoch, 0);
  }
}
async function regress(epoch: number) {
  if (!detail.value) return;
  const id = detail.value.draft_id;
  const report = await props.api.request<Regression>(
    `/api/skill-drafts/${id}/regressions`,
    { method: "POST" },
  );
  const row = await props.api.request<SkillPublication>(
    `/api/skill-drafts/${id}`,
  );
  if (current(epoch)) {
    detail.value = row;
    reportId.value = report.report_id;
  }
}
async function transition(epoch: number, kind: string) {
  const d = detail.value;
  if (!d) return;
  const approval = kind === "approve" || kind === "reject";
  const body = await props.api.request<SkillPublication>(
    `/api/skill-drafts/${d.draft_id}/${approval ? "decisions" : kind}`,
    {
      method: "POST",
      body: JSON.stringify({
        revision: d.revision,
        reason: reason.value,
        ...(approval
          ? {
              request_id: crypto.randomUUID(),
              decision: kind,
              report_id: reportId.value,
              payload_sha256: d.payload_sha256,
            }
          : {}),
      }),
    },
  );
  if (current(epoch)) {
    detail.value = body;
    await list(epoch);
  }
}
async function readSource(epoch: number) {
  if (!detail.value) return;
  const body = await props.api.request<unknown>(
    `/api/retrospectives/${detail.value.payload.retrospective_id}?include_source=true`,
  );
  if (current(epoch)) source.value = body;
}
onMounted(() =>
  perform(async (epoch) => {
    await list(epoch);
    await listSources(epoch);
  }),
);
</script>

<template>
  <section class="card publication-panel" aria-label="候选 Skill 审批与发布">
    <div class="card-heading">
      <h2>候选 Skill 审批与发布</h2>
      <button :disabled="busy" @click="createOpen = true">生成方法草稿</button>
    </div>
    <p class="notice subtle">
      从有来源的经验摘录方法，固定回归后人工审批，再显式发布。只供规划参考，不授予工具权限或批准动作；结构回归不证明语义正确或记忆收益。
    </p>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <DetailDialog
      v-model:open="createOpen"
      title="生成方法草稿"
      :busy="busy"
      width="880px"
    >
      <p v-if="error" role="alert" class="error">{{ error }}</p>
      <div class="publication-actions">
        <label
          >方法来源候选<select v-model="candidateId" :disabled="busy">
            <option value="">选择当前有效候选</option>
            <option
              v-for="c in candidates"
              :key="c.candidate_id"
              :value="c.candidate_id"
            >
              {{ c.body.title }} · {{ c.body.product_version }} /
              {{ c.body.mode }} · {{ c.candidate_id.slice(0, 8) }}
            </option>
          </select></label
        >
        <button :disabled="busy || !candidateId" @click="perform(create)">
          确认生成草稿
        </button>
        <button
          :disabled="busy || sources.offset === 0"
          @click="perform((e) => listSources(e, sources.offset - 10))"
        >
          候选上一页
        </button>
        <button
          :disabled="busy || sources.offset + 10 >= sources.total"
          @click="perform((e) => listSources(e, sources.offset + 10))"
        >
          候选下一页
        </button>
        <button :disabled="busy" @click="perform((e) => listSources(e, 0))">
          刷新候选
        </button>
      </div>
    </DetailDialog>
    <p v-if="!rows.items.length" class="empty">尚无组织方法草稿。</p>
    <article v-for="r in rows.items" :key="r.draft_id" class="publication-card">
      <strong>{{ r.payload.method.title }}</strong
      ><span
        >{{ labels[r.status] }} · {{ r.payload.method.product_version }} /
        {{ r.payload.method.mode }} · {{ r.draft_id.slice(0, 8) }}</span
      ><button
        :disabled="busy"
        @click="
          detailTrigger = $event.currentTarget as HTMLElement;
          inspect(r);
        "
      >
        查看方法发布
      </button>
    </article>
    <div class="publication-actions">
      <button
        :disabled="busy || rows.offset === 0"
        @click="perform((e) => list(e, rows.offset - 10))"
      >
        方法上一页</button
      ><button
        :disabled="busy || rows.offset + 10 >= rows.total"
        @click="perform((e) => list(e, rows.offset + 10))"
      >
        方法下一页
      </button>
    </div>
    <DetailDialog
      v-if="detail"
      v-model:open="detailOpen"
      :return-focus="detailTrigger"
      title="组织方法详情"
      :busy="busy"
    >
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <section class="publication-detail" aria-label="组织方法详情">
        <ViewTabs
          v-model="detailView"
          label="组织方法详情分区"
          :items="detailViews"
        />
        <h3>{{ detail.payload.method.title }}</h3>
        <p>
          {{ labels[detail.status] }} · 修订 {{ detail.revision }} ·
          {{ labels[detail.eligibility] ?? detail.eligibility }}
        </p>
        <div v-show="detailView === 'method'">
          <p class="muted">方法用于安排检查；本次事实仍须读取现场证据。</p>
          <ol>
            <li
              v-for="(step, index) in detail.payload.method.checks"
              :key="index"
            >
              <strong>{{ step.tool }}</strong>
              <p>{{ step.reason }}</p>
            </li>
          </ol>
          <details>
            <summary>方法正文与身份摘要</summary>
            <p>审批正文摘要：{{ detail.payload_sha256 }}</p>
            <pre>{{ JSON.stringify(detail.payload, null, 2) }}</pre>
          </details>
          <div class="publication-actions">
            <button :disabled="busy" @click="perform(readSource)">
              查看方法经验来源
            </button>
          </div>
          <pre v-if="source">{{ JSON.stringify(source, null, 2) }}</pre>
        </div>
        <div v-show="detailView === 'approval'">
          <div class="publication-actions">
            <button
              :disabled="busy || detail.status !== 'draft'"
              @click="perform(regress)"
            >
              运行固定回归
            </button>
          </div>
          <label
            >审批绑定回归报告<select v-model="reportId" :disabled="busy">
              <option value="">明确选择报告</option>
              <option
                v-for="r in detail.reports"
                :key="r.report_id"
                :value="r.report_id"
              >
                {{ r.report.passed ? "通过" : "未通过" }} · {{ r.report_id }}
              </option>
            </select></label
          >
          <p
            v-if="selectedReport"
            class="badge"
            :class="selectedReport.report.passed ? 'green' : 'amber'"
          >
            固定回归{{ selectedReport.report.passed ? "通过" : "未通过" }}
          </p>
          <details v-if="selectedReport">
            <summary>回归报告明细</summary>
            <pre>{{ JSON.stringify(selectedReport, null, 2) }}</pre>
          </details>
          <label
            >方法审批与发布原因<textarea
              v-model="reason"
              maxlength="400"
              :disabled="busy"
            ></textarea>
          </label>
          <div class="publication-actions">
            <button
              :disabled="
                busy ||
                detail.status !== 'draft' ||
                !selectedReport?.report.passed ||
                !reason.trim()
              "
              @click="perform((e) => transition(e, 'approve'))"
            >
              批准方法
            </button>
            <button
              :disabled="
                busy ||
                detail.status !== 'draft' ||
                !selectedReport ||
                !reason.trim()
              "
              @click="perform((e) => transition(e, 'reject'))"
            >
              拒绝方法
            </button>
            <button
              :disabled="busy || detail.status !== 'approved' || !reason.trim()"
              @click="perform((e) => transition(e, 'publish'))"
            >
              显式发布方法
            </button>
            <button
              :disabled="
                busy ||
                !['approved', 'published'].includes(detail.status) ||
                !reason.trim()
              "
              @click="perform((e) => transition(e, 'revoke'))"
            >
              撤销发布方法
            </button>
          </div>
        </div>
        <div v-show="detailView === 'audit'">
          <pre>{{
            JSON.stringify(
              { decisions: detail.decisions, events: detail.events },
              null,
              2,
            )
          }}</pre>
        </div>
      </section>
    </DetailDialog>
  </section>
</template>

<style scoped>
.publication-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: end;
  margin: 12px 0;
}
.publication-actions label {
  flex: 1 1 230px;
  min-width: 0;
}
.publication-panel label {
  display: grid;
  gap: 6px;
}
.publication-card {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  padding: 12px 0;
  border-bottom: 1px solid #ddd7cf;
}
.publication-card strong {
  flex: 1 1 180px;
  overflow-wrap: anywhere;
}
.publication-detail {
  margin-top: 20px;
  min-width: 0;
}
.publication-panel,
.publication-panel label,
.publication-card span,
.publication-card strong {
  min-width: 0;
}
.publication-detail p {
  overflow-wrap: anywhere;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 380px;
  overflow: auto;
  background: #f5f3ee;
  padding: 12px;
  font-size: 12px;
}
select,
textarea {
  width: 100%;
  min-width: 0;
  max-width: 100%;
}
</style>
