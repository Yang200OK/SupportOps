<script setup lang="ts">
import DetailDialog from "./DetailDialog.vue";
import { onMounted, onUnmounted, ref, watch } from "vue";
import { ApiError, type Api } from "./api";
import type { SkillCatalog, SkillDetail, SkillSummary } from "./skill-types";

const detailOpen = ref(false);
const detailTrigger = ref<HTMLElement | null>(null);
const props = defineProps<{ api: Api }>();
const catalog = ref<SkillCatalog | null>(null);
const version = ref("1.1");
const mode = ref("online");
const detail = ref<SkillDetail | null>(null);
const busy = ref(false);
const error = ref("");
let generation = 0;
let mounted = true;
watch([version, mode], () => {
  generation++;
  detail.value = null;
  error.value = "";
});
onUnmounted(() => {
  mounted = false;
  generation++;
});
const current = (epoch: number) => mounted && epoch === generation;
const applicable = (row: SkillSummary) =>
  row.product_versions.includes(version.value) &&
  row.modes.includes(mode.value);
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
      error.value = exc instanceof Error ? exc.message : "Skill 读取失败。";
  } finally {
    if (mounted) busy.value = false;
  }
}
async function inspect(epoch: number, row: SkillSummary) {
  detailOpen.value = true;
  detail.value = null;
  const body = await props.api.request<SkillDetail>(
    `/api/skills/${encodeURIComponent(row.skill_id)}/versions/${encodeURIComponent(row.version)}?product_version=${version.value}&mode=${mode.value}`,
  );
  if (current(epoch)) detail.value = body;
}
onMounted(() =>
  perform(async (epoch) => {
    const body = await props.api.request<SkillCatalog>("/api/skills");
    if (current(epoch)) catalog.value = body;
  }),
);
</script>

<template>
  <section class="card skills-panel" aria-label="Skill 目录">
    <div class="card-heading">
      <h2>项目 Skill 目录</h2>
      <span>4 类排查方法</span>
    </div>
    <p class="notice subtle">
      方法辅助安排检查，不能作为本次事实证据或动作批准。来源与哈希已核对，不代表方法语义或效果已验证。
      经验候选可在本页复盘区域治理，组织方法通过独立审批与发布记录管理。
    </p>
    <div class="skill-scope">
      <label
        >Skill 产品版本<select v-model="version">
          <option>1.0</option>
          <option>1.1</option>
          <option>2.0</option>
        </select></label
      >
      <label
        >Skill 运行模式<select v-model="mode">
          <option value="online">在线实验</option>
          <option value="startup">启动诊断</option>
        </select></label
      >
    </div>
    <p v-if="error" role="alert" class="notice danger">{{ error }}</p>
    <details v-if="catalog">
      <summary>目录身份</summary>
      <p class="mono skill-hash">目录 SHA-256：{{ catalog.catalog_sha256 }}</p>
    </details>
    <div class="skill-grid">
      <article
        v-for="row in catalog?.items"
        :key="row.skill_id"
        class="skill-card"
      >
        <h3>
          {{ row.title }} <small>{{ row.version }}</small>
        </h3>
        <p>{{ row.summary }}</p>
        <p class="muted">
          版本：{{ row.product_versions.join(" / ") }} · 模式：{{
            row.modes.join(" / ")
          }}
        </p>

        <button
          :disabled="busy || !applicable(row)"
          @click="
            detailTrigger = $event.currentTarget as HTMLElement;
            perform((epoch) => inspect(epoch, row));
          "
        >
          {{ applicable(row) ? "读取适用方法" : "本次范围不适用" }}
        </button>
      </article>
    </div>
    <DetailDialog
      v-if="detail"
      v-model:open="detailOpen"
      :return-focus="detailTrigger"
      title="项目 Skill 详情"
      :busy="busy"
    >
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <section class="skill-detail">
        <h3>{{ detail.title }} · {{ detail.version }}</h3>
        <p>
          本次版本 {{ detail.selected_product_version }} ·
          {{ detail.selected_mode }} · 正文与来源核对通过
        </p>
        <p class="mono skill-hash">正文 SHA-256：{{ detail.body_sha256 }}</p>
        <pre>{{ detail.body }}</pre>
        <h4>原始来源</h4>
        <article v-for="source in detail.sources" :key="source.path">
          <p>{{ source.path }} · 自建演示设计资料 · {{ source.license }}</p>
          <p class="mono skill-hash">SHA-256：{{ source.sha256 }}</p>
        </article>
      </section>
    </DetailDialog>
  </section>
</template>

<style scoped>
.skill-scope {
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
  margin: 20px 0;
}
.skill-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}
.skill-card {
  border: 1px solid var(--color-border);
  border-radius: 12px;
  padding: 20px;
}
.skill-hash {
  overflow-wrap: anywhere;
  font-size: 12px;
}
.skill-detail {
  margin-top: 24px;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.8;
}
@media (max-width: 700px) {
  .skill-grid {
    grid-template-columns: 1fr;
  }
}
</style>
