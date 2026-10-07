<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import type { Api } from "./api";
import { ApiError } from "./api";
import { encodeDocumentFile } from "./document-file";
import DetailDialog from "./DetailDialog.vue";
import ViewTabs from "./ViewTabs.vue";
import ChunkPanel from "./ChunkPanel.vue";

interface Summary {
  document_id: string;
  revision_id: string;
  revision_number: number;
  title: string;
  source_key: string;
  product_version: string;
  source_type: string;
  filename: string;
  format: string;
  license: string;
  content_sha256: string;
  byte_size: number;
  imported_at: string;
  status: "parsed" | "failed";
  error_code: string | null;
  error_message: string | null;
  parser_version: string;
}
interface Detail extends Summary {
  text: string | null;
  reused: boolean;
  blocks: {
    kind: string;
    text: string;
    line_start: number | null;
    line_end: number | null;
    page_number: number | null;
    json_pointer: string | null;
  }[];
}
interface Listing {
  items: Summary[];
  total: number;
  offset: number;
  limit: number;
}
const detailTrigger = ref<HTMLElement | null>(null);
const props = defineProps<{ api: Api }>();
const listing = ref<Listing>({ items: [], total: 0, offset: 0, limit: 10 });
const selected = ref<Detail | null>(null);
const detailOpen = ref(false);
const importOpen = ref(false);
const detailView = ref("identity");
const detailViews = [
  { value: "identity", label: "来源与修订" },
  { value: "chunks", label: "结构切片" },
  { value: "text", label: "解析正文" },
];
const history = ref<Listing>({ items: [], total: 0, offset: 0, limit: 10 });
const version = ref("");
const error = ref("");
const message = ref("");
const busy = ref(false);
const file = ref<File | null>(null);
const metadata = reactive({
  source_key: "",
  title: "",
  product_version: "1.1",
  source_type: "demo_product",
  license: "CC0-1.0",
});
async function perform(action: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    await action();
  } catch (exc) {
    if (!(exc instanceof ApiError && exc.code === "STALE_SESSION"))
      error.value = exc instanceof Error ? exc.message : "资料操作失败。";
  } finally {
    busy.value = false;
  }
}
async function load(offset = 0) {
  listing.value = await props.api.request<Listing>(
    `/api/documents?offset=${offset}&limit=10${version.value ? `&product_version=${version.value}` : ""}`,
  );
}
async function read(item: Summary) {
  selected.value = await props.api.request<Detail>(
    `/api/documents/${item.document_id}/revisions/${item.revision_id}`,
  );
}
async function loadHistory(offset = 0) {
  if (!selected.value) return;
  history.value = await props.api.request<Listing>(
    `/api/documents/${selected.value.document_id}/revisions?offset=${offset}&limit=10`,
  );
}
async function open(item: Summary) {
  await read(item);
  detailOpen.value = true;
  detailView.value = "identity";
  await loadHistory();
}
function choose(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0] ?? null;
  message.value = "";
}
async function submit() {
  await perform(async () => {
    if (!file.value) throw new Error("请先选择资料文件。");
    message.value = "";
    const encoded = await encodeDocumentFile(file.value);
    const result = await props.api.request<Detail>("/api/documents/import", {
      method: "POST",
      body: JSON.stringify({ ...metadata, product: "relaydesk", ...encoded }),
    });
    selected.value = result;
    message.value =
      result.status === "failed"
        ? "导入已记录，解析失败。"
        : result.reused
          ? "内容已存在，复用原修订。"
          : "导入并解析完成。";
    await load();
    await loadHistory();
    importOpen.value = false;
    detailOpen.value = true;
    detailView.value = "identity";
  });
}
async function download() {
  if (!selected.value) return;
  await perform(async () => {
    const item = selected.value!;
    const result = await props.api.request<{ content_base64: string }>(
      `/api/documents/${item.document_id}/revisions/${item.revision_id}/raw`,
    );
    const bytes = Uint8Array.from(atob(result.content_base64), (c) =>
      c.charCodeAt(0),
    );
    const url = URL.createObjectURL(
      new Blob([bytes], { type: "application/octet-stream" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = item.filename;
    link.click();
    URL.revokeObjectURL(url);
  });
}
onMounted(() => void perform(() => load()));
</script>

<template>
  <div class="documents-panel">
    <p class="notice subtle">
      资料保留版本、来源与原文修订。演示资料属于设计说明，实际行为将在故障实验中验证。
    </p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="message" class="notice" role="status">{{ message }}</p>
    <DetailDialog
      v-model:open="importOpen"
      title="导入资料"
      :busy="busy"
      width="880px"
    >
      <section class="document-import">
        <div class="card-heading">
          <h2>导入资料</h2>
          <span class="muted">Markdown / 文本 PDF / 案例 JSON · 2 MiB</span>
        </div>
        <p v-if="error" class="error" role="alert">{{ error }}</p>
        <form class="document-form" @submit.prevent="submit">
          <label
            >来源标识<input
              v-model="metadata.source_key"
              aria-label="来源标识"
              pattern="[a-z0-9][a-z0-9._-]*"
              maxlength="100"
              placeholder="config-guide"
              required
              :disabled="busy"
          /></label>
          <label
            >资料标题<input
              v-model="metadata.title"
              aria-label="资料标题"
              maxlength="200"
              required
              :disabled="busy"
          /></label>
          <label
            >导入版本<select
              v-model="metadata.product_version"
              aria-label="导入版本"
              :disabled="busy"
            >
              <option>1.0</option>
              <option>1.1</option>
              <option>2.0</option>
            </select></label
          >
          <label
            >来源类型<select
              v-model="metadata.source_type"
              aria-label="资料来源类型"
              :disabled="busy"
            >
              <option value="demo_product">自建演示资料</option>
              <option value="synthetic_case">构造案例</option>
              <option value="user_report">用户报告</option>
            </select></label
          >
          <label
            >分发许可<select
              v-model="metadata.license"
              aria-label="分发许可"
              :disabled="busy"
            >
              <option>CC0-1.0</option>
              <option value="proprietary">私有资料</option>
            </select></label
          >
          <label
            >原文文件<input
              type="file"
              aria-label="原文文件"
              accept=".md,.pdf,.json"
              @change="choose"
              :disabled="busy"
          /></label>
          <button class="primary" :disabled="busy">
            {{ busy ? "正在处理…" : "导入并解析" }}
          </button>
        </form>
      </section>
    </DetailDialog>
    <div class="content-grid">
      <section class="card list-card">
        <div class="card-heading">
          <h2>
            资料列表 <span class="count">{{ listing.total }}</span>
          </h2>
          <div class="list-tools">
            <button class="primary" :disabled="busy" @click="importOpen = true">
              导入资料
            </button>
            <label
              >筛选版本<select
                v-model="version"
                aria-label="筛选版本"
                :disabled="busy"
                @change="
                  perform(async () => {
                    selected = null;
                    await load();
                  })
                "
              >
                <option value="">全部版本</option>
                <option>1.0</option>
                <option>1.1</option>
                <option>2.0</option>
              </select></label
            >
          </div>
        </div>
        <div v-if="!listing.items.length" class="empty">
          <h3>暂无资料</h3>
          <p>导入带版本的原文，建立可核对的来源。</p>
        </div>
        <div v-else class="table-scroll">
          <table>
            <thead>
              <tr>
                <th>资料 / 来源</th>
                <th>版本</th>
                <th>修订</th>
                <th>解析状态</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in listing.items" :key="item.document_id">
                <td>
                  <strong>{{ item.title }}</strong
                  ><small class="mono">{{ item.source_key }}</small>
                </td>
                <td>{{ item.product_version }}</td>
                <td>r{{ item.revision_number }}</td>
                <td>
                  <span
                    class="badge"
                    :class="item.status === 'parsed' ? 'green' : 'amber'"
                    >{{
                      item.status === "parsed" ? "已解析" : "解析失败"
                    }}</span
                  >
                </td>
                <td>
                  <button
                    class="text-button"
                    :disabled="busy"
                    @click="
                      detailTrigger = $event.currentTarget as HTMLElement;
                      perform(() => open(item));
                    "
                  >
                    核对
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="pagination">
          <span>共 {{ listing.total }} 份</span>
          <div>
            <button
              :disabled="busy || listing.offset === 0"
              @click="perform(() => load(listing.offset - 10))"
            >
              上一页</button
            ><button
              :disabled="busy || listing.offset + 10 >= listing.total"
              @click="perform(() => load(listing.offset + 10))"
            >
              下一页
            </button>
          </div>
        </div>
      </section>
      <DetailDialog
        v-if="selected"
        v-model:open="detailOpen"
        :return-focus="detailTrigger"
        title="原文与解析核对"
        :busy="busy"
      >
        <section class="detail-card document-detail">
          <ViewTabs
            v-model="detailView"
            label="资料核对分区"
            :items="detailViews"
          />
          <p v-if="error" class="error" role="alert">{{ error }}</p>
          <div v-show="detailView === 'identity'">
            <h3>{{ selected.title }}</h3>
            <p>
              RelayDesk {{ selected.product_version }} · r{{
                selected.revision_number
              }}
              · {{ selected.status }}
            </p>
            <dl>
              <dt>来源标识</dt>
              <dd>{{ selected.source_key }}</dd>
              <dt>文件 / 字节</dt>
              <dd>{{ selected.filename }} / {{ selected.byte_size }}</dd>
              <dt>来源 / 许可</dt>
              <dd>{{ selected.source_type }} / {{ selected.license }}</dd>
              <dt>原文 SHA-256</dt>
              <dd class="mono">{{ selected.content_sha256 }}</dd>
              <dt>导入时间</dt>
              <dd>
                {{ new Date(selected.imported_at).toLocaleString("zh-CN") }}
              </dd>
              <dt>解析器</dt>
              <dd>{{ selected.parser_version }}</dd>
            </dl>
            <button :disabled="busy" @click="download">下载本修订原文</button>
            <div class="revision-buttons">
              <button
                v-for="item in history.items"
                :key="item.revision_id"
                :disabled="busy"
                @click="perform(() => read(item))"
              >
                修订 {{ item.revision_number }}
              </button>
            </div>
            <div v-if="history.total > 10" class="pagination">
              <button
                :disabled="busy || history.offset === 0"
                @click="perform(() => loadHistory(history.offset - 10))"
              >
                较新修订</button
              ><button
                :disabled="busy || history.offset + 10 >= history.total"
                @click="perform(() => loadHistory(history.offset + 10))"
              >
                较旧修订
              </button>
            </div>
          </div>
          <p v-if="selected.error_code" class="error" role="alert">
            {{ selected.error_code }}：{{ selected.error_message }}
          </p>
          <template v-else
            ><div v-show="detailView === 'chunks'">
              <ChunkPanel
                :key="selected.revision_id"
                :api="api"
                :document-id="selected.document_id"
                :revision-id="selected.revision_id"
              />
            </div>
            <div v-show="detailView === 'text'">
              <h4>提取正文</h4>
              <pre class="source-text">{{ selected.text }}</pre>
              <h4>基础解析位置</h4>
              <div
                v-for="(block, index) in selected.blocks"
                :key="index"
                class="source-block"
              >
                <small class="mono"
                  >{{ block.kind }} ·
                  {{
                    block.page_number
                      ? `第 ${block.page_number} 页`
                      : block.json_pointer
                        ? block.json_pointer
                        : `第 ${block.line_start}–${block.line_end} 行`
                  }}</small
                >
                <pre>{{ block.text }}</pre>
              </div>
            </div></template
          >
        </section>
      </DetailDialog>
    </div>
  </div>
</template>

<style scoped>
.documents-panel {
  display: grid;
  gap: 20px;
}
.document-import .card-heading {
  flex-wrap: wrap;
  gap: 8px 16px;
}
.documents-panel td small {
  display: block;
  margin-top: 5px;
  color: var(--color-muted);
  font-size: 11px;
}
.document-form {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;
}
.document-form label {
  display: grid;
  gap: 8px;
}
.document-form button {
  align-self: end;
}
.revision-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 20px 0;
}
.source-text,
.source-block pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 13px;
  line-height: 1.7;
}
.source-block {
  border-top: 1px solid var(--color-border);
  padding-top: 12px;
}
.source-text {
  max-height: 340px;
  overflow: auto;
}
.document-detail {
  min-width: 0;
}
@media (max-width: 900px) {
  .document-import .card-heading > span {
    flex-basis: 100%;
  }
  .document-form {
    grid-template-columns: 1fr;
  }
}
</style>
