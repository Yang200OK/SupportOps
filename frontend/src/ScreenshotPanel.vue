<script setup lang="ts">
import { onBeforeUnmount, ref } from "vue";
import { ApiError, type Api } from "./api";
const props = defineProps<{ api: Api; disabled: boolean }>();
const emit = defineEmits<{ confirm: [text: string] }>();
type Extraction = {
  recognized: boolean;
  product: string | null;
  product_version: string | null;
  error_code: string | null;
  visible_lines: string[];
  uncertainty: string;
};
type Result = {
  image_sha256: string;
  sent_image_sha256: string;
  width: number;
  height: number;
  extraction: Extraction;
  limitation: string;
  usage: {
    requested_model: string;
    input_tokens: number;
    output_tokens: number;
  };
};
const file = ref<File | null>(null),
  result = ref<Result | null>(null),
  busy = ref(false),
  error = ref(""),
  text = ref("");
const failureUsage = ref<{
  known_model_calls: number;
  unknown_usage_calls: number;
  input_tokens: number;
  output_tokens: number;
} | null>(null);
let generation = 0,
  disposed = false;
function choose(event: Event) {
  if (props.disabled) return;
  generation++;
  result.value = null;
  text.value = "";
  error.value = "";
  busy.value = false;
  failureUsage.value = null;
  file.value = (event.target as HTMLInputElement).files?.[0] ?? null;
  if (
    file.value &&
    (file.value.size > 2 * 1024 * 1024 ||
      !file.value.name.toLowerCase().endsWith(".png"))
  ) {
    error.value = "只支持不超过 2 MiB 的 PNG。";
    file.value = null;
  }
}
async function extract() {
  if (!file.value || busy.value || props.disabled) return;
  const ticket = ++generation,
    selected = file.value;
  busy.value = true;
  error.value = "";
  result.value = null;
  text.value = "";
  failureUsage.value = null;
  try {
    const bytes = new Uint8Array(await selected.arrayBuffer());
    let binary = "";
    for (let i = 0; i < bytes.length; i += 8192)
      binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
    if (disposed || ticket !== generation) return;
    const response = await props.api.request<Result>(
      "/api/rag/screenshots/extract",
      { method: "POST", body: JSON.stringify({ image_base64: btoa(binary) }) },
    );
    if (disposed || ticket !== generation) return;
    result.value = response;
    const d = response.extraction;
    text.value =
      `截图识别（用户确认，尚未验证）：产品 ${d.product ?? "未知"}；版本 ${d.product_version ?? "未知"}；错误 ${d.error_code ?? "未知"}。可见文字：${d.visible_lines.join("；")}`.slice(
        0,
        900,
      );
  } catch (exc) {
    if (
      !disposed &&
      ticket === generation &&
      exc instanceof ApiError &&
      exc.diagnostics
    ) {
      const detail = exc.diagnostics as { usage: typeof failureUsage.value };
      failureUsage.value = detail.usage;
    }
    if (
      !disposed &&
      ticket === generation &&
      !(exc instanceof ApiError && exc.code === "STALE_SESSION")
    )
      error.value = exc instanceof Error ? exc.message : "截图识别失败。";
  } finally {
    if (!disposed && ticket === generation) busy.value = false;
  }
}
onBeforeUnmount(() => {
  disposed = true;
  generation++;
});
</script>

<template>
  <section class="card screenshot-panel">
    <h2>截图补充</h2>
    <p class="muted">
      仅支持 RelayDesk 错误 / 配置界面单张 PNG，2 MiB、64–2400 像素、最多 400
      万像素。识别会发送到百炼，请先去掉敏感信息；不保存图片。
    </p>
    <label
      >RelayDesk 截图 PNG<input
        type="file"
        accept="image/png"
        :disabled="disabled"
        @change="choose"
    /></label>
    <button
      type="button"
      :disabled="disabled || busy || !file"
      @click="extract"
    >
      {{ busy ? "正在识别截图…" : "识别截图" }}
    </button>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="failureUsage" class="muted">
      失败用量：已知调用 {{ failureUsage.known_model_calls }} · 用量未知调用
      {{ failureUsage.unknown_usage_calls }} ·
      {{ failureUsage.input_tokens }} 输入 /
      {{ failureUsage.output_tokens }} 输出 token，费用未核对。
    </p>
    <div v-if="result" class="screenshot-result">
      <p>
        图像 {{ result.width }} × {{ result.height }} ·
        {{ result.usage.requested_model }} ·
        {{ result.usage.input_tokens }} 输入 /
        {{ result.usage.output_tokens }} 输出 token
      </p>
      <p class="mono digest">原图 SHA-256：{{ result.image_sha256 }}</p>
      <p>{{ result.extraction.uncertainty }}</p>
      <p>{{ result.limitation }}</p>
      <template v-if="result.extraction.recognized">
        <label
          >截图识别待确认内容<textarea
            v-model="text"
            rows="4"
            maxlength="900"
          />
        </label>
        <button
          type="button"
          :disabled="disabled || !text.trim()"
          @click="emit('confirm', text)"
        >
          确认并加入补充信息
        </button>
        <p class="footnote">
          确认后只加入分步问答补充；产品版本和查询范围仍由你选择，不会自动生成回答。
        </p>
      </template>
      <p v-else>未识别为支持的 RelayDesk 界面，请换图或手动补充。</p>
    </div>
  </section>
</template>

<style scoped>
.screenshot-panel {
  margin-bottom: 20px;
  display: grid;
  gap: 12px;
  min-width: 0;
}
label {
  display: grid;
  gap: 8px;
}
button {
  justify-self: start;
}
input,
textarea {
  max-width: 100%;
  box-sizing: border-box;
}
textarea {
  width: 100%;
  padding: 10px;
  font: inherit;
}
.screenshot-result {
  min-width: 0;
  overflow-wrap: anywhere;
}
</style>
