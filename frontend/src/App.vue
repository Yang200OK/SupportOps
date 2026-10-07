<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { ElDialog } from "element-plus";
import "element-plus/es/components/dialog/style/css";
import { Api, ApiError } from "./api";
import type {
  EvaluationResult,
  Page,
  Principal,
  Run,
  Ticket,
  TicketDraft,
} from "./types";
import example from "../../examples/evaluation-dataset.v1.json";
import DetailDialog from "./DetailDialog.vue";
import ViewTabs from "./ViewTabs.vue";
import AnswerEvaluationPanel from "./AnswerEvaluationPanel.vue";
import DocumentsPanel from "./DocumentsPanel.vue";
import ExperimentsPanel from "./ExperimentsPanel.vue";
import InvestigationPanel from "./InvestigationPanel.vue";
import ActionPanel from "./ActionPanel.vue";
import HypothesisPanel from "./HypothesisPanel.vue";
import RetrievalPanel from "./RetrievalPanel.vue";
import SkillsPanel from "./SkillsPanel.vue";
import MemoryPanel from "./MemoryPanel.vue";
import PublicationPanel from "./PublicationPanel.vue";
import MemoryPairsPanel from "./MemoryPairsPanel.vue";
import CoordinationPanel from "./CoordinationPanel.vue";

const api = new Api();
const principal = ref<Principal | null>(null);
const section = ref<"tickets" | "runs" | "evaluation" | "documents" | "skills">(
  "tickets",
);
const runView = ref<"intake" | "lab">("intake");
const knowledgeView = ref<"documents" | "retrieval">("documents");
const busy = ref(false);
const error = ref("");
const ready = ref(false);
const loginForm = reactive({ username: "", password: "" });
const tickets = ref<Page<Ticket>>({
  items: [],
  total: 0,
  offset: 0,
  limit: 10,
});
const runs = ref<Page<Run>>({ items: [], total: 0, offset: 0, limit: 10 });
const selectedTicket = ref<Ticket | null>(null);
const selectedRun = ref<Run | null>(null);
const createOpen = ref(false);
const ticketOpen = ref(false);
const ticketTrigger = ref<HTMLElement | null>(null);
const runTrigger = ref<HTMLElement | null>(null);
const runOpen = ref(false);
const ticketView = ref("overview");
const skillView = ref("publications");
const datasetOpen = ref(false);
const reportOpen = ref(false);
const reportSummary = ref<{
  total: number;
  completed: number;
  failed: number;
  not_run: number;
} | null>(null);
const skillViews = [
  { value: "publications", label: "组织方法" },
  { value: "memory", label: "事件复盘" },
  { value: "catalog", label: "项目 Skill" },
  { value: "pairs", label: "成对实验" },
];
const ticketViews = [
  { value: "overview", label: "工单概况" },
  { value: "readonly", label: "只读调查" },
  { value: "hypothesis", label: "假设调查" },
  { value: "actions", label: "批准动作" },
  { value: "coordination", label: "协作任务板" },
];
// 标签按需创建业务面板；KeepAlive 保留请求与结果，组织或工单变化再卸载旧上下文。
const skillComponents = {
  publications: PublicationPanel,
  memory: MemoryPanel,
  catalog: SkillsPanel,
  pairs: MemoryPairsPanel,
};
const ticketComponents = {
  readonly: InvestigationPanel,
  hypothesis: HypothesisPanel,
  actions: ActionPanel,
  coordination: CoordinationPanel,
};
const draft = reactive<TicketDraft>({
  title: "",
  description: "",
  product: "relaydesk",
  product_version: null,
  environment: "local_lab",
  source_type: "synthetic_case",
});
const datasetText = ref(JSON.stringify(example, null, 2));
const evaluation = ref<EvaluationResult | null>(null);
const heading = computed(
  () =>
    ({
      tickets: "工单工作台",
      runs: "运行记录",
      evaluation: "评测准备",
      documents: "知识与资料",
      skills: "经验与 Skill",
    })[section.value],
);

function resetViews() {
  principal.value = null;
  selectedTicket.value = null;
  selectedRun.value = null;
  tickets.value = { items: [], total: 0, offset: 0, limit: 10 };
  runs.value = { items: [], total: 0, offset: 0, limit: 10 };
  evaluation.value = null;
  datasetText.value = JSON.stringify(example, null, 2);
  createOpen.value = false;
  ticketOpen.value = false;
  runOpen.value = false;
  datasetOpen.value = false;
  reportOpen.value = false;
  reportSummary.value = null;
  skillView.value = "publications";
  ticketView.value = "overview";
  Object.assign(draft, {
    title: "",
    description: "",
    product_version: null,
    source_type: "synthetic_case",
  });
  section.value = "tickets";
  runView.value = "intake";
  knowledgeView.value = "documents";
  ready.value = false;
}
api.onExpired = resetViews;
async function perform(action: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    await action();
  } catch (exc) {
    if (!(exc instanceof ApiError && exc.code === "STALE_SESSION"))
      error.value = exc instanceof Error ? exc.message : "操作失败。";
  } finally {
    busy.value = false;
  }
}
async function loadTickets(offset = 0) {
  tickets.value = await api.request<Page<Ticket>>(
    `/api/tickets?offset=${offset}&limit=10`,
  );
}
async function loadRuns(offset = 0) {
  runs.value = await api.request<Page<Run>>(
    `/api/runs?offset=${offset}&limit=10`,
  );
}
async function initialize() {
  principal.value = await api.request<Principal>("/api/auth/me");
  await api.request("/health/ready");
  ready.value = true;
  await loadTickets();
}
async function login() {
  await perform(async () => {
    const result = await api.request<{ access_token: string }>(
      "/api/auth/login",
      { method: "POST", body: JSON.stringify(loginForm) },
    );
    api.setToken(result.access_token);
    loginForm.password = "";
    await initialize();
  });
}
async function logout() {
  await perform(async () => {
    await api.logout();
    resetViews();
  });
}
async function navigate(next: typeof section.value) {
  await perform(async () => {
    section.value = next;
    ticketOpen.value = false;
    runOpen.value = false;
    datasetOpen.value = false;
    reportOpen.value = false;
    reportSummary.value = null;
    selectedTicket.value = null;
    selectedRun.value = null;
    if (next === "tickets") await loadTickets();
    if (next === "runs") await loadRuns();
    runView.value = "intake";
    knowledgeView.value = "documents";
  });
}
async function readTicket(id: string) {
  await perform(async () => {
    selectedTicket.value = await api.request<Ticket>(`/api/tickets/${id}`);
    ticketView.value = "overview";
    ticketOpen.value = true;
    selectedRun.value = null;
  });
}
async function createTicket() {
  await perform(async () => {
    const created = await api.request<Ticket>("/api/tickets", {
      method: "POST",
      body: JSON.stringify(draft),
    });
    createOpen.value = false;
    await loadTickets();
    selectedTicket.value = created;
    ticketView.value = "overview";
    ticketOpen.value = true;
    Object.assign(draft, { title: "", description: "", product_version: null });
  });
}
async function createRun() {
  if (!selectedTicket.value) return;
  const id = selectedTicket.value.ticket_id;
  await perform(async () => {
    selectedRun.value = await api.request<Run>(`/api/tickets/${id}/runs`, {
      method: "POST",
      body: JSON.stringify({ kind: "intake_check" }),
    });
    runOpen.value = true;
  });
}
async function readRun(id: string) {
  await perform(async () => {
    selectedRun.value = await api.request<Run>(`/api/runs/${id}`);
    runOpen.value = true;
  });
}
async function validateDataset() {
  evaluation.value = null;
  await perform(async () => {
    let data: unknown;
    try {
      data = JSON.parse(datasetText.value);
    } catch {
      throw new Error("JSON 格式无效，请检查逗号、引号和括号。");
    }
    evaluation.value = await api.request<EvaluationResult>(
      "/api/evaluations/validate",
      { method: "POST", body: JSON.stringify(data) },
    );
  });
}
const date = (value: string) =>
  new Date(value).toLocaleString("zh-CN", { hour12: false });
const eventNames: Record<string, string> = {
  run_created: "创建运行",
  input_checked: "校验输入",
  run_finished: "结束运行",
};
onMounted(() => {
  if (api.hasToken()) void perform(initialize);
});
</script>

<template>
  <div v-if="!principal" class="login-page">
    <section class="login-story">
      <div class="brand"><span class="brand-mark">S</span> SupportOps</div>
      <div class="story-content">
        <span class="eyebrow">SOFTWARE SUPPORT WORKBENCH</span>
        <h1>每一次判断，<br />都留下依据。</h1>
        <p>
          从工单输入、运行快照到可复核的评测，<br />逐步构建有证据的软件支持调查。
        </p>
        <div class="story-rule"></div>
        <span class="story-note">工单 · 证据 · 调查 · 复盘</span>
      </div>
      <span class="story-foot">本地演示 · RelayDesk · 版本 0.1.0</span>
    </section>
    <section class="login-panel">
      <div class="login-card">
        <span class="eyebrow">欢迎回来</span>
        <h2>登录支持工作台</h2>
        <p class="muted">账号所属组织决定可见工单和运行记录。</p>
        <form @submit.prevent="login">
          <label
            >用户名<input
              v-model="loginForm.username"
              name="username"
              autocomplete="username"
              required
              minlength="3"
              maxlength="64"
              placeholder="输入演示账号"
          /></label>
          <label
            >密码<input
              v-model="loginForm.password"
              name="password"
              type="password"
              autocomplete="current-password"
              required
              maxlength="128"
              placeholder="输入本地演示密码"
          /></label>
          <p v-if="error" role="alert" class="error">{{ error }}</p>
          <button class="primary wide" :disabled="busy">
            {{ busy ? "正在登录…" : "进入工作台" }}
          </button>
        </form>
        <p class="login-help">
          演示账号保存在项目的
          <code>local/demo-accounts.json</code>。<br />密钥和密码无需提交到
          GitHub。
        </p>
      </div>
    </section>
  </div>
  <div v-else class="shell">
    <aside class="sidebar">
      <div class="brand"><span class="brand-mark">S</span> SupportOps</div>
      <span class="sidebar-label">支持中心</span>
      <nav aria-label="工作台导航">
        <button
          :class="{ active: section === 'tickets' }"
          :disabled="busy"
          @click="navigate('tickets')"
        >
          <span>01</span>工单工作台</button
        ><button
          :class="{ active: section === 'runs' }"
          :disabled="busy"
          @click="navigate('runs')"
        >
          <span>02</span>运行记录</button
        ><button
          :class="{ active: section === 'evaluation' }"
          :disabled="busy"
          @click="navigate('evaluation')"
        >
          <span>03</span>评测准备
        </button>
        <button
          :class="{ active: section === 'documents' }"
          :disabled="busy"
          @click="navigate('documents')"
        >
          <span>04</span>知识与资料
        </button>
        <button
          :class="{ active: section === 'skills' }"
          :disabled="busy"
          @click="navigate('skills')"
        >
          <span>05</span>经验与 Skill
        </button>
      </nav>
      <div class="sidebar-bottom">
        <span class="status-dot" :class="{ ready }"></span
        >{{ ready ? "数据库已就绪" : "数据库待检查" }}
        <p>本地演示环境<br />版本资料 · 当前取证</p>
      </div>
    </aside>
    <div class="workspace">
      <header class="topbar">
        <span>支持中心 <span class="slash">/</span> {{ heading }}</span>
        <div class="identity">
          <span class="avatar">{{
            principal.username.slice(0, 1).toUpperCase()
          }}</span>
          <div>
            <strong>{{ principal.organization_name }}</strong
            ><small>{{ principal.username }}</small>
          </div>
          <button class="text-button" :disabled="busy" @click="logout">
            退出登录
          </button>
        </div>
      </header>
      <main>
        <div class="page-heading">
          <div>
            <span class="eyebrow">SUPPORT OPERATIONS</span>
            <h1>{{ heading }}</h1>
            <p class="muted">
              {{
                section === "tickets"
                  ? "记录问题描述，确认输入完整性，再开始调查。"
                  : section === "runs"
                    ? "复核每次输入检查的状态、快照与事件。"
                    : section === "documents"
                      ? "保留资料版本与原文身份，核对解析结果和历史修订。"
                      : section === "skills"
                        ? "按版本和场景读取排查方法，保留方法来源与加载记录。"
                        : "先固定任务、标注与数据分区，再比较系统表现。"
              }}
            </p>
          </div>
          <button
            v-if="section === 'tickets'"
            class="primary"
            :disabled="busy"
            @click="createOpen = true"
          >
            + 新建工单
          </button>
        </div>
        <p v-if="error" role="alert" class="error">{{ error }}</p>
        <p v-if="busy" role="status" class="loading">正在处理请求…</p>
        <template v-if="section === 'skills'">
          <ViewTabs
            v-model="skillView"
            label="经验与方法分区"
            :items="skillViews"
          />
          <KeepAlive>
            <component
              :is="skillComponents[skillView as keyof typeof skillComponents]"
              :api="api"
              :key="principal.organization_id + skillView"
            />
          </KeepAlive>
        </template>
        <template v-if="section === 'documents'">
          <div class="run-view-tabs">
            <button
              :aria-pressed="knowledgeView === 'documents'"
              @click="knowledgeView = 'documents'"
            >
              资料管理
            </button>
            <button
              :aria-pressed="knowledgeView === 'retrieval'"
              @click="knowledgeView = 'retrieval'"
            >
              证据检索
            </button>
          </div>
          <DocumentsPanel
            v-if="knowledgeView === 'documents'"
            :api="api"
            :key="principal.organization_id"
          />
          <RetrievalPanel v-else :api="api" :key="principal.organization_id" />
        </template>
        <template v-if="section === 'tickets'">
          <div class="summary-strip">
            <span
              ><strong>{{ tickets.total }}</strong> 条组织工单</span
            ><span>RelayDesk · 本地实验</span
            ><span class="muted">选择工单，查看调查与动作</span>
          </div>
          <div class="content-grid">
            <section class="card list-card">
              <div class="card-heading">
                <h2>工单列表</h2>
                <button
                  class="text-button"
                  :disabled="busy"
                  @click="perform(() => loadTickets(tickets.offset))"
                >
                  刷新
                </button>
              </div>
              <div v-if="tickets.items.length === 0" class="empty">
                <span class="empty-symbol">—</span>
                <h3>还没有工单</h3>
                <p>创建一个构造案例，开始记录与检查。</p>
              </div>
              <div v-else class="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>工单 / 问题</th>
                      <th>版本</th>
                      <th>输入状态</th>
                      <th>创建时间</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="ticket in tickets.items"
                      :key="ticket.ticket_id"
                      :class="{
                        selected:
                          selectedTicket?.ticket_id === ticket.ticket_id,
                      }"
                    >
                      <td>
                        <strong class="ticket-title">{{ ticket.title }}</strong
                        ><small class="mono">{{
                          ticket.ticket_id.slice(0, 8)
                        }}</small>
                      </td>
                      <td>{{ ticket.product_version ?? "待补充" }}</td>
                      <td>
                        <span
                          class="badge"
                          :class="
                            ticket.intake_status === 'needs_clarification'
                              ? 'amber'
                              : 'green'
                          "
                          >{{
                            ticket.intake_status === "needs_clarification"
                              ? "需要澄清"
                              : "输入完整"
                          }}</span
                        >
                      </td>
                      <td class="date">{{ date(ticket.created_at) }}</td>
                      <td>
                        <button
                          class="text-button"
                          :disabled="busy"
                          @click="
                            ticketTrigger = $event.currentTarget as HTMLElement;
                            readTicket(ticket.ticket_id);
                          "
                        >
                          查看
                        </button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div class="pagination">
                <span>共 {{ tickets.total }} 条 · 每页 10 条</span>
                <div>
                  <button
                    :disabled="busy || tickets.offset === 0"
                    @click="perform(() => loadTickets(tickets.offset - 10))"
                  >
                    上一页</button
                  ><span>{{ Math.floor(tickets.offset / 10) + 1 }}</span
                  ><button
                    :disabled="busy || tickets.offset + 10 >= tickets.total"
                    @click="perform(() => loadTickets(tickets.offset + 10))"
                  >
                    下一页
                  </button>
                </div>
              </div>
            </section>
          </div>
          <DetailDialog
            v-if="selectedTicket"
            v-model:open="ticketOpen"
            title="工单详情"
            :return-focus="ticketTrigger"
            :busy="busy"
          >
            <ViewTabs
              v-model="ticketView"
              label="工单流程分区"
              :items="ticketViews"
            />
            <p v-if="error" class="error" role="alert">{{ error }}</p>
            <section v-show="ticketView === 'overview'" class="detail-card">
              <span class="eyebrow"
                >RELAYDESK /
                {{ selectedTicket.product_version ?? "版本待补充" }}</span
              >
              <h3>{{ selectedTicket.title }}</h3>
              <p class="description">{{ selectedTicket.description }}</p>
              <dl>
                <dt>工单 ID</dt>
                <dd class="mono">{{ selectedTicket.ticket_id }}</dd>
                <dt>来源声明</dt>
                <dd>
                  {{
                    selectedTicket.source_type === "synthetic_case"
                      ? "构造案例"
                      : "用户报告"
                  }}
                </dd>
                <dt>环境</dt>
                <dd>本地实验环境</dd>
              </dl>
              <div v-if="selectedTicket.missing_fields.length" class="notice">
                需要补充产品版本。当前输入不足以开始调查。
              </div>
              <button
                class="primary wide"
                :disabled="busy"
                @click="
                  runTrigger = $event.currentTarget as HTMLElement;
                  createRun();
                "
              >
                记录一次输入检查
              </button>
              <p class="footnote">
                此检查会真实入库，保存输入快照和事件；调查请切换上方标签。
              </p>
            </section>

            <KeepAlive :key="selectedTicket.ticket_id">
              <component
                v-if="ticketView !== 'overview'"
                :is="
                  ticketComponents[ticketView as keyof typeof ticketComponents]
                "
                :api="api"
                :ticket="selectedTicket"
                :key="ticketView + selectedTicket.ticket_id"
              />
            </KeepAlive>
          </DetailDialog>
        </template>
        <template v-if="section === 'runs'"
          ><div class="run-view-tabs">
            <button
              :disabled="busy"
              :aria-pressed="runView === 'intake'"
              @click="
                runView = 'intake';
                selectedRun = null;
              "
            >
              输入检查
            </button>
            <button
              :disabled="busy"
              :aria-pressed="runView === 'lab'"
              @click="
                runView = 'lab';
                selectedRun = null;
              "
            >
              故障实验
            </button>
          </div>
          <ExperimentsPanel v-if="runView === 'lab'" :api="api" />
          <template v-else
            ><div class="notice subtle">
              当前只执行 intake_check。succeeded 表示输入完整，blocked
              表示需要补充版本。
            </div>
            <section class="card">
              <div class="card-heading">
                <h2>
                  运行历史 <span class="count">{{ runs.total }}</span>
                </h2>
                <button
                  class="text-button"
                  :disabled="busy"
                  @click="perform(() => loadRuns(runs.offset))"
                >
                  刷新
                </button>
              </div>
              <div v-if="!runs.items.length" class="empty">
                <h3>暂无运行记录</h3>
                <p>在工单详情中记录一次输入检查。</p>
              </div>
              <div v-else class="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>运行 ID</th>
                      <th>工单 ID</th>
                      <th>状态</th>
                      <th>实际耗时</th>
                      <th>开始时间</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="run in runs.items" :key="run.run_id">
                      <td class="mono">{{ run.run_id.slice(0, 8) }}</td>
                      <td class="mono">{{ run.ticket_id.slice(0, 8) }}</td>
                      <td>
                        <span
                          class="badge"
                          :class="run.status === 'blocked' ? 'amber' : 'green'"
                          >{{ run.status }}</span
                        >
                      </td>
                      <td>{{ run.duration_ms }} ms</td>
                      <td class="date">{{ date(run.started_at) }}</td>
                      <td>
                        <button
                          class="text-button"
                          :disabled="busy"
                          @click="
                            runTrigger = $event.currentTarget as HTMLElement;
                            readRun(run.run_id);
                          "
                        >
                          查看记录
                        </button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div class="pagination">
                <span>共 {{ runs.total }} 条 · 每页 10 条</span>
                <div>
                  <button
                    :disabled="busy || runs.offset === 0"
                    @click="perform(() => loadRuns(runs.offset - 10))"
                  >
                    上一页</button
                  ><span>{{ Math.floor(runs.offset / 10) + 1 }}</span
                  ><button
                    :disabled="busy || runs.offset + 10 >= runs.total"
                    @click="perform(() => loadRuns(runs.offset + 10))"
                  >
                    下一页
                  </button>
                </div>
              </div>
            </section></template
          ></template
        >
        <DetailDialog
          v-if="selectedRun && (section === 'runs' || section === 'tickets')"
          v-model:open="runOpen"
          title="运行详情"
          :busy="busy"
          width="880px"
          :return-focus="runTrigger"
        >
          <section class="run-detail">
            <div class="card-heading">
              <h2>运行详情</h2>
              <span
                class="badge"
                :class="selectedRun.status === 'blocked' ? 'amber' : 'green'"
                >{{ selectedRun.status }}</span
              >
            </div>
            <p>{{ selectedRun.output.message }}</p>
            <dl class="run-meta">
              <div>
                <dt>运行 ID</dt>
                <dd class="mono">{{ selectedRun.run_id }}</dd>
              </div>
              <div>
                <dt>工作流版本</dt>
                <dd>{{ selectedRun.workflow_version }}</dd>
              </div>
              <div>
                <dt>模型 / token / 费用</dt>
                <dd>未调用 / 未使用 / 未计费</dd>
              </div>
              <div>
                <dt>输入快照 SHA-256</dt>
                <dd class="mono digest">{{ selectedRun.input_sha256 }}</dd>
              </div>
            </dl>
            <ol class="events">
              <li v-for="event in selectedRun.events" :key="event.sequence">
                <span class="event-number">{{ event.sequence }}</span
                ><strong>{{ eventNames[event.event] }}</strong
                ><code>{{ event.event }}</code
                ><span>{{ event.elapsed_ms }} ms</span>
              </li>
            </ol>
            <details>
              <summary>查看输入快照</summary>
              <pre>{{
                JSON.stringify(selectedRun.input_snapshot, null, 2)
              }}</pre>
            </details>
          </section>
        </DetailDialog>
        <template v-if="section === 'evaluation'">
          <p class="muted">校验数据契约与查看离线报告；此页面不执行模型。</p>
          <div class="entry-grid">
            <section class="card entry-card">
              <span class="eyebrow">DATASET</span>
              <h2>评测数据集</h2>
              <p class="muted">检查任务、标注与 dev / holdout 分区。</p>
              <p v-if="evaluation" class="badge green">
                {{ evaluation.total }} 个任务 · 格式通过
              </p>
              <button class="primary" @click="datasetOpen = true">
                编辑与校验数据集
              </button>
            </section>
            <section class="card entry-card">
              <span class="eyebrow">REPORT</span>
              <h2>回答评测报告</h2>
              <p class="muted">查看完成数、失败、用量与逐任务指标。</p>
              <p v-if="reportSummary" class="report-overview">
                全部 {{ reportSummary.total }} · 完成
                {{ reportSummary.completed }} · 失败
                {{ reportSummary.failed }} · 未执行 {{ reportSummary.not_run }}
              </p>
              <button @click="reportOpen = true">打开离线报告</button>
            </section>
          </div>
          <DetailDialog
            v-model:open="datasetOpen"
            title="数据集格式校验"
            :busy="busy"
          >
            <p v-if="error" class="error" role="alert">{{ error }}</p>
            <div class="evaluation-grid">
              <section class="card">
                <div class="card-heading">
                  <h2>Dataset v1</h2>
                  <button
                    class="text-button"
                    :disabled="busy"
                    @click="
                      datasetText = JSON.stringify(example, null, 2);
                      evaluation = null;
                    "
                  >
                    加载格式样例
                  </button>
                </div>
                <label class="dataset-label"
                  >评测数据集 JSON<textarea
                    v-model="datasetText"
                    spellcheck="false"
                    class="json-editor"
                    :disabled="busy"
                  ></textarea></label
                ><button
                  class="primary"
                  :disabled="busy"
                  @click="validateDataset"
                >
                  校验数据集格式
                </button>
              </section>
              <section class="card evaluation-info">
                <span class="eyebrow">EVALUATION CONTRACT</span>
                <h2>先确定比较的边界</h2>
                <ul>
                  <li>任务 ID 唯一，所有任务进入分母。</li>
                  <li>同一来源家族不能跨 dev / holdout。</li>
                  <li>expected 标注不进入模型输入。</li>
                  <li>失败与未执行必须保留记录。</li>
                  <li>费用未知保留 null。</li>
                </ul>
                <div v-if="evaluation" class="validation-result" role="status">
                  <span class="badge green">格式通过</span>
                  <h3>{{ evaluation.dataset_id }}</h3>
                  <p>
                    任务 {{ evaluation.total }} · dev {{ evaluation.dev }} ·
                    holdout {{ evaluation.holdout }}
                  </p>
                  <p class="mono digest">{{ evaluation.sha256 }}</p>
                  <p class="footnote">executed: false · 未执行质量评测</p>
                </div>
                <p v-else class="footnote">
                  左侧样例仅含两个构造案例，用于演示数据契约。
                </p>
              </section>
            </div>
          </DetailDialog>
          <DetailDialog v-model:open="reportOpen" title="回答评测报告">
            <AnswerEvaluationPanel
              :api="api"
              @summary="reportSummary = $event"
            />
          </DetailDialog>
        </template>
        <footer class="page-footer">
          SupportOps / 本地工程演示<span>工单 → 输入检查 → 可复核记录</span>
        </footer>
      </main>
    </div>
    <ElDialog
      v-model="createOpen"
      title="新建工单"
      width="560px"
      :close-on-click-modal="false"
      :close-on-press-escape="!busy"
      :show-close="!busy"
      :before-close="
        (done) => {
          if (!busy) done();
        }
      "
    >
      <form @submit.prevent="createTicket" class="ticket-form">
        <p class="muted">
          填写可观察的现象。版本未知时可先创建，系统会标记需要澄清。
        </p>
        <label
          >问题标题<input
            v-model="draft.title"
            required
            maxlength="200"
            :disabled="busy"
            placeholder="例如：投递请求返回 RD_TIMEOUT" /></label
        ><label
          >问题描述<textarea
            v-model="draft.description"
            required
            maxlength="10000"
            rows="4"
            :disabled="busy"
            placeholder="描述现象、发生时间及已观察的信息"
          ></textarea>
        </label>
        <div class="form-row">
          <label
            >产品版本<select v-model="draft.product_version" :disabled="busy">
              <option :value="null">未知 / 待补充</option>
              <option value="1.0">1.0</option>
              <option value="1.1">1.1</option>
              <option value="2.0">2.0</option>
            </select></label
          ><label
            >来源声明<select v-model="draft.source_type" :disabled="busy">
              <option value="synthetic_case">构造案例</option>
              <option value="user_report">用户报告</option>
            </select></label
          >
        </div>
        <p class="footnote">固定产品 RelayDesk · 环境 local_lab</p>
        <p v-if="error" role="alert" class="error">{{ error }}</p>
        <div class="dialog-actions">
          <button type="button" :disabled="busy" @click="createOpen = false">
            取消</button
          ><button class="primary" :disabled="busy">
            {{ busy ? "正在保存…" : "创建工单" }}
          </button>
        </div>
      </form>
    </ElDialog>
  </div>
</template>

<style scoped>
.run-view-tabs {
  display: flex;
  gap: 10px;
  margin-bottom: 20px;
}
.run-view-tabs button[aria-pressed="true"] {
  color: var(--color-accent);
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
}
</style>
