<script setup lang="ts">
import { onMounted, ref } from "vue";
import { ApiError, type Api } from "./api";
import type { Page } from "./types";
import type { Chunk, ChunkPage, ChunkSet, Citation } from "./chunk-types";

const props = defineProps<{
  api: Api;
  documentId: string;
  revisionId: string;
}>();
const maxChars = ref(600);
const overlapChars = ref(80);
const sets = ref<Page<ChunkSet>>({ items: [], total: 0, offset: 0, limit: 10 });
const snapshot = ref<ChunkSet | null>(null);
const chunks = ref<ChunkPage | null>(null);
const citation = ref<Citation | null>(null);
const busy = ref(false);
const error = ref("");
const message = ref("");
const endpoint = `/api/documents/${props.documentId}/revisions/${props.revisionId}/chunk-sets`;
const kinds: Record<string, string> = {
  heading: "标题",
  prose: "正文",
  table: "配置表",
  code: "代码",
  page: "PDF 页",
  field: "JSON 字段",
};
async function perform(action: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    await action();
  } catch (exc) {
    if (!(exc instanceof ApiError && exc.code === "STALE_SESSION"))
      error.value = exc instanceof Error ? exc.message : "切片操作失败。";
  } finally {
    busy.value = false;
  }
}
async function loadSets(offset = 0) {
  sets.value = await props.api.request<Page<ChunkSet>>(
    `${endpoint}?offset=${offset}&limit=10`,
  );
}
async function loadChunks(offset = 0) {
  if (!snapshot.value) return;
  citation.value = null;
  chunks.value = await props.api.request<ChunkPage>(
    `/api/chunk-sets/${snapshot.value.chunk_set_id}/chunks?offset=${offset}&limit=10`,
  );
}
async function select(item: ChunkSet) {
  citation.value = null;
  chunks.value = null;
  snapshot.value = item;
  await loadChunks();
}
async function generate() {
  await perform(async () => {
    const result = await props.api.request<ChunkSet>(endpoint, {
      method: "POST",
      body: JSON.stringify({
        max_chars: maxChars.value,
        overlap_chars: overlapChars.value,
      }),
    });
    message.value = result.reused
      ? "配置已存在，复用切片快照。"
      : "结构切片已生成。";
    await loadSets();
    await select(result);
  });
}
async function preview(item: Chunk) {
  citation.value = null;
  citation.value = await props.api.request<Citation>(
    `/api/chunk-sets/${snapshot.value!.chunk_set_id}/chunks/${item.chunk_id}/citation`,
  );
}
function location(item: Chunk) {
  if (item.page_number) return `第 ${item.page_number} 页（提取文本）`;
  if (item.json_pointer) return `字段 ${item.json_pointer}（解码文本）`;
  return item.spans
    .map((s) => `第 ${s.line_start}–${s.line_end} 行`)
    .join("；");
}
// 由父组件按修订标识重新挂载，旧修订的快照与预览不会进入新视图。
onMounted(
  () =>
    void perform(async () => {
      await loadSets();
      if (sets.value.items.length) await select(sets.value.items[0]);
    }),
);
</script>

<template>
  <section class="chunk-panel" aria-label="结构切片">
    <h4>结构切片与引用</h4>
    <p class="muted">
      固定当前修订，按章节保留父级上下文。长度按 Unicode 字符计算。
    </p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="message" class="notice" role="status">{{ message }}</p>
    <form class="chunk-form" @submit.prevent="generate">
      <label
        >目标字符数<input
          v-model.number="maxChars"
          aria-label="目标字符数"
          type="number"
          min="128"
          max="4000"
          required
          :disabled="busy"
      /></label>
      <label
        >正文重叠字符<input
          v-model.number="overlapChars"
          aria-label="正文重叠字符"
          type="number"
          min="0"
          :max="Math.ceil(maxChars / 2) - 1"
          required
          :disabled="busy"
      /></label>
      <button class="primary" :disabled="busy">生成结构切片</button>
    </form>
    <p v-if="!sets.total && !busy" class="muted">当前修订尚未生成切片。</p>
    <div class="snapshot-list">
      <button
        v-for="item in sets.items"
        :key="item.chunk_set_id"
        :disabled="busy"
        :aria-pressed="snapshot?.chunk_set_id === item.chunk_set_id"
        @click="perform(() => select(item))"
      >
        {{ item.config.max_chars }} / {{ item.config.overlap_chars }} ·
        {{ item.chunk_count }} 段
      </button>
    </div>
    <div v-if="sets.total > 10" class="pagination">
      <button
        :disabled="busy || sets.offset === 0"
        @click="perform(() => loadSets(sets.offset - 10))"
      >
        较新快照
      </button>
      <button
        :disabled="busy || sets.offset + 10 >= sets.total"
        @click="perform(() => loadSets(sets.offset + 10))"
      >
        较旧快照
      </button>
    </div>
    <template v-if="snapshot && chunks">
      <p class="chunk-summary">
        {{ snapshot.chunker_version }} · {{ snapshot.parent_count }} 个父级 /
        {{ snapshot.chunk_count }} 个切片
      </p>
      <details class="snapshot-identity">
        <summary>固定快照身份</summary>
        <dl>
          <dt>快照 ID</dt>
          <dd class="mono">{{ snapshot.chunk_set_id }}</dd>
          <dt>修订 ID</dt>
          <dd class="mono">{{ snapshot.revision_id }}</dd>
          <dt>快照 SHA-256</dt>
          <dd class="mono">{{ snapshot.snapshot_sha256 }}</dd>
          <dt>解析正文 SHA-256</dt>
          <dd class="mono">{{ snapshot.parsed_sha256 }}</dd>
        </dl>
      </details>
      <article
        v-for="item in chunks.items"
        :key="item.chunk_id"
        class="chunk-item"
      >
        <div class="chunk-heading">
          <strong>#{{ item.ordinal + 1 }} {{ kinds[item.kind] }}</strong
          ><button
            :disabled="busy"
            :aria-label="`预览切片 ${item.ordinal + 1}`"
            @click="perform(() => preview(item))"
          >
            原文预览
          </button>
        </div>
        <p class="muted">
          {{ item.heading_path.join(" / ") || "无标题章节" }} ·
          {{ location(item) }}
        </p>
        <p v-if="item.table_headers.length">
          表头：{{ item.table_headers.join(" / ") }}
        </p>
        <p v-if="item.code_language">代码语言：{{ item.code_language }}</p>
        <p v-if="item.atomic_oversize" class="notice">
          完整行超过目标长度，保留该行原文。
        </p>
        <pre>{{ item.text }}</pre>
      </article>
      <div class="pagination">
        <span>共 {{ chunks.total }} 段</span>
        <div>
          <button
            :disabled="busy || chunks.offset === 0"
            @click="perform(() => loadChunks(chunks!.offset - 10))"
          >
            上一组切片
          </button>
          <button
            :disabled="busy || chunks.offset + 10 >= chunks.total"
            @click="perform(() => loadChunks(chunks!.offset + 10))"
          >
            下一组切片
          </button>
        </div>
      </div>
    </template>
    <section v-if="citation" class="citation-preview" aria-label="引用原文预览">
      <h4>引用原文预览</h4>
      <p>
        {{ citation.source.title }} · {{ citation.source.product_version }} ·
        修订 {{ citation.source.revision_number }}
      </p>
      <p class="notice">字面核对通过；语义支持尚未验证。</p>
      <p class="mono">原文 SHA-256：{{ citation.source.content_sha256 }}</p>
      <p class="muted">
        {{ location(citation.chunk) }} · Unicode 字符区间 [start, end)
      </p>
      <div
        v-for="(part, index) in citation.parts"
        :key="index"
        class="citation-part"
      >
        <small>原文区间 [{{ part.start }}, {{ part.end }})</small>
        <pre><span>{{ part.before }}</span><mark>{{ part.excerpt }}</mark><span>{{ part.after }}</span></pre>
      </div>
      <details>
        <summary>
          父级完整上下文 · [{{ citation.parent.start }},
          {{ citation.parent.end }})
        </summary>
        <pre class="parent-text">{{ citation.parent.text }}</pre>
      </details>
    </section>
  </section>
</template>

<style scoped>
.chunk-panel {
  border-top: 1px solid var(--color-border);
  margin-top: 24px;
  padding-top: 12px;
}
.chunk-form {
  display: grid;
  gap: 12px;
}
.chunk-form label {
  display: grid;
  gap: 6px;
}
.snapshot-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 16px 0;
}
.snapshot-list button[aria-pressed="true"] {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
}
.chunk-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.chunk-item {
  border-top: 1px solid var(--color-border);
  padding-top: 12px;
  margin-top: 14px;
}
.chunk-panel pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.7;
  font-size: 13px;
  max-height: 300px;
  overflow-y: auto;
}
.chunk-panel .mono {
  overflow-wrap: anywhere;
}
.citation-preview {
  background: var(--color-accent-soft);
  border: 1px solid var(--color-border);
  padding: 14px;
  margin-top: 20px;
  border-radius: 8px;
}
.citation-part mark {
  background: #ffd4ad;
  color: var(--color-text);
}
.citation-part span {
  color: var(--color-text-secondary);
}
.chunk-panel summary {
  cursor: pointer;
}
.snapshot-identity {
  margin-bottom: 16px;
}
</style>
