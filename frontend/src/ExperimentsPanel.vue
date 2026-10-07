<script setup lang="ts">
import ViewTabs from "./ViewTabs.vue";
import DetailDialog from "./DetailDialog.vue";
import { onMounted, ref } from "vue";
import { ApiError, type Api } from "./api";
import type { Page } from "./types";

interface Experiment {
  experiment_id: string;
  run_id: string;
  product_version: string;
  sha256: string;
  created_at: string;
  observation_count: number;
}
interface Observation {
  request_id: string;
  observed_at: string;
  phase: "baseline" | "failure" | "retest";
  service: string;
  event: string;
  status?: number;
  error_code?: string;
  elapsed_ms?: number;
  effective_config?: Record<string, unknown>;
  database?: string;
  checked_out?: number;
  db_target?: string;
  cache_target?: string;
  db_generation?: number;
  cache_generation?: number;
  delay_ms?: number;
  target?: string;
}
interface Detail extends Experiment {
  artifact: {
    product_version: string;
    run_id: string;
    observations: Observation[];
  };
  evidence_ids: string[];
  text_verified: true;
  execution_verified_by_api: false;
}
const detailOpen = ref(false);
const phaseView = ref("baseline");
const detailTrigger = ref<HTMLElement | null>(null);
const props = defineProps<{ api: Api }>();
const listing = ref<Page<Experiment>>({
  items: [],
  total: 0,
  offset: 0,
  limit: 10,
});
const selected = ref<Detail | null>(null);
const busy = ref(false);
const error = ref("");
const phases = {
  baseline: "正常请求",
  failure: "异常观测",
  retest: "恢复复测",
};
const events: Record<string, string> = {
  request_received: "收到请求",
  db_acquired: "获取数据库连接",
  target_observed: "目标与缓存观测",
  downstream_received: "下游收到请求",
  downstream_finished: "下游响应完成",
  request_finished: "请求完成",
  config_checked: "启动配置校验",
};
async function perform(action: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    await action();
  } catch (exc) {
    if (!(exc instanceof ApiError && exc.code === "STALE_SESSION"))
      error.value = exc instanceof Error ? exc.message : "观测读取失败。";
  } finally {
    busy.value = false;
  }
}
async function load(offset = 0) {
  selected.value = null;
  listing.value = await props.api.request<Page<Experiment>>(
    `/api/experiments?offset=${offset}&limit=10`,
  );
}
async function open(item: Experiment) {
  detailOpen.value = true;
  selected.value = null;
  selected.value = await props.api.request<Detail>(
    `/api/experiments/${item.experiment_id}`,
  );
}
function ending(phase: string) {
  return selected.value?.artifact.observations.find(
    (o) =>
      o.phase === phase &&
      ["request_finished", "config_checked"].includes(o.event),
  );
}
onMounted(() => void perform(() => load()));
</script>

<template>
  <div class="experiments-panel">
    <p class="notice subtle">
      由隔离的本地实验导入。正常、异常和恢复的原始观测可按版本与请求逐项核对。
    </p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <div class="experiment-grid">
      <section class="card">
        <div class="card-heading">
          <h2>
            实验观测 <span class="count">{{ listing.total }}</span>
          </h2>
          <button :disabled="busy" @click="perform(() => load(listing.offset))">
            刷新观测
          </button>
        </div>
        <div v-if="!listing.items.length" class="empty">
          <h3>暂无实验观测</h3>
          <p>运行本地实验并导入公共观测包后，在此核对三阶段记录。</p>
        </div>
        <div v-else class="table-scroll">
          <table>
            <thead>
              <tr>
                <th>运行标识</th>
                <th>版本</th>
                <th>观测数</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in listing.items" :key="item.experiment_id">
                <td class="mono">{{ item.run_id.slice(0, 8) }}</td>
                <td>{{ item.product_version }}</td>
                <td>{{ item.observation_count }}</td>
                <td>
                  <button
                    class="text-button"
                    :disabled="busy"
                    :aria-label="`查看观测 ${item.run_id.slice(0, 8)}`"
                    @click="
                      detailTrigger = $event.currentTarget as HTMLElement;
                      perform(() => open(item));
                    "
                  >
                    查看观测
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="pagination">
          <span>共 {{ listing.total }} 次</span>
          <div>
            <button
              :disabled="busy || listing.offset === 0"
              @click="perform(() => load(listing.offset - 10))"
            >
              上一页观测</button
            ><button
              :disabled="busy || listing.offset + 10 >= listing.total"
              @click="perform(() => load(listing.offset + 10))"
            >
              下一页观测
            </button>
          </div>
        </div>
      </section>
      <DetailDialog
        v-if="selected"
        v-model:open="detailOpen"
        :return-focus="detailTrigger"
        title="三阶段观测"
        :busy="busy"
      >
        <p v-if="error" class="error" role="alert">{{ error }}</p>
        <section class="experiment-detail">
          <p>
            RelayDesk {{ selected.product_version }} ·
            {{ selected.observation_count }} 条实际记录
          </p>
          <p class="muted">
            来源：上传的实验观测。字面摘要已核对，执行真实性需结合原始实验记录。
          </p>
          <dl>
            <dt>运行标识</dt>
            <dd class="mono">{{ selected.run_id }}</dd>
            <dt>观测包 SHA-256</dt>
            <dd class="mono">{{ selected.sha256 }}</dd>
          </dl>
          <div class="phase-summaries">
            <div
              v-for="(name, phase) in phases"
              :key="phase"
              class="phase-summary"
            >
              <strong>{{ name }}</strong>
              <p>
                {{ ending(phase)?.service === "boot" ? "启动校验" : "HTTP" }}
                {{ ending(phase)?.status }} · {{ ending(phase)?.elapsed_ms }} ms
              </p>
              <span v-if="ending(phase)?.error_code" class="badge amber">{{
                ending(phase)?.error_code
              }}</span>
            </div>
          </div>
          <ViewTabs
            v-model="phaseView"
            label="观测阶段"
            :items="
              Object.entries(phases).map(([value, label]) => ({ value, label }))
            "
          />
          <section
            v-for="(name, phase) in phases"
            :key="phase"
            v-show="phaseView === phase"
            class="experiment-phase"
          >
            <h3>{{ name }}</h3>
            <article
              v-for="(item, index) in selected.artifact.observations"
              :key="index"
              v-show="item.phase === phase"
              class="observation"
            >
              <div class="observation-heading">
                <strong>{{ events[item.event] }}</strong
                ><span class="mono">{{ item.service }}</span>
              </div>
              <small class="muted"
                >{{ new Date(item.observed_at).toLocaleString("zh-CN") }} · 请求
                {{ item.request_id.slice(0, 8) }}</small
              >
              <p v-if="item.status">
                状态 {{ item.status }}
                <span v-if="item.error_code" class="badge amber">{{
                  item.error_code
                }}</span>
              </p>
              <p v-if="item.elapsed_ms !== undefined">
                实测耗时 {{ item.elapsed_ms }} ms
              </p>
              <p v-if="item.checked_out !== undefined">
                已占用连接 {{ item.checked_out }}
              </p>
              <p v-if="item.database">实验数据库 {{ item.database }}</p>
              <p v-if="item.db_target">
                数据库目标 {{ item.db_target }} / 代次 {{ item.db_generation
                }}<br />缓存目标 {{ item.cache_target }} / 代次
                {{ item.cache_generation }}
              </p>
              <p v-if="item.delay_ms !== undefined">
                接收器设定等待 {{ item.delay_ms }} ms · 目标 {{ item.target }}
              </p>
              <details v-if="item.effective_config">
                <summary>实际生效配置</summary>
                <pre>{{ JSON.stringify(item.effective_config, null, 2) }}</pre>
              </details>
              <small class="mono evidence-id">{{
                selected.evidence_ids[index]
              }}</small>
            </article>
          </section>
          <details>
            <summary>公共观测包</summary>
            <pre>{{ JSON.stringify(selected.artifact, null, 2) }}</pre>
          </details>
        </section>
      </DetailDialog>
    </div>
  </div>
</template>

<style scoped>
.experiments-panel {
  display: grid;
  gap: 16px;
}
.experiment-grid {
  display: grid;
  gap: 20px;
}
.experiment-grid.expanded {
  grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.2fr);
  align-items: start;
}
.experiment-detail {
  min-width: 0;
}
.experiment-detail .mono {
  overflow-wrap: anywhere;
}
.phase-summaries {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin: 22px 0;
}
.phase-summary {
  background: var(--color-surface-soft);
  border-radius: 8px;
  padding: 12px;
}
.phase-summary p {
  font-size: 12px;
}
.experiment-phase {
  margin-top: 24px;
}
.observation {
  border-top: 1px solid var(--color-border);
  padding: 14px 0;
}
.observation-heading {
  display: flex;
  justify-content: space-between;
  gap: 8px;
}
.observation .evidence-id {
  display: block;
  margin-top: 12px;
  color: var(--color-muted);
}
.experiment-detail pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 320px;
  overflow-y: auto;
  font-size: 12px;
}
.experiment-detail summary {
  cursor: pointer;
}
@media (max-width: 1000px) {
  .experiment-grid.expanded {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 600px) {
  .phase-summaries {
    grid-template-columns: 1fr;
  }
}
</style>
