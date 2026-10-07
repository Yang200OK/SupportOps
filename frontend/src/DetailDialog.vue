<script setup lang="ts">
import { ElDialog } from "element-plus";
import "element-plus/es/components/dialog/style/css";

withDefaults(
  defineProps<{
    open: boolean;
    title: string;
    busy?: boolean;
    width?: string;
    returnFocus?: HTMLElement | null;
  }>(),
  { busy: false, width: "1080px", returnFocus: null },
);
const emit = defineEmits<{ "update:open": [value: boolean] }>();
</script>

<template>
  <!-- 隐藏时保留内容状态：关闭详情不代表取消已提交的调查或动作。 -->
  <ElDialog
    :model-value="open"
    :title="title"
    :width="width"
    class="detail-dialog"
    append-to-body
    top="5vh"
    :close-on-click-modal="false"
    :close-on-press-escape="!busy"
    :show-close="!busy"
    :before-close="
      (done) => {
        if (!busy) done();
      }
    "
    @update:model-value="emit('update:open', $event)"
    @closed="
      () => {
        if (returnFocus?.isConnected) returnFocus.focus();
      }
    "
  >
    <slot />
    <template #footer>
      <span v-if="busy" class="muted">请求处理中，请稍候。</span>
      <button :disabled="busy" @click="emit('update:open', false)">
        返回列表
      </button>
    </template>
  </ElDialog>
</template>
