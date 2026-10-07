<script setup lang="ts">
defineProps<{
  modelValue: string;
  label: string;
  items: { value: string; label: string }[];
}>();
const emit = defineEmits<{ "update:modelValue": [value: string] }>();
</script>
<template>
  <nav class="view-tabs" :aria-label="label">
    <button
      v-for="item in items"
      :key="item.value"
      :aria-pressed="modelValue === item.value"
      @click="emit('update:modelValue', item.value)"
    >
      {{ item.label }}
    </button>
  </nav>
</template>
