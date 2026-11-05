<script setup>
// DAG 调用图：任务的步骤依赖关系可视化（vue-flow 渲染）
// 节点=步骤（状态着色），边=数据流依赖（depends_on）；点击节点展开输入输出
import { computed } from "vue";
import { VueFlow } from "@vue-flow/core";
import "@vue-flow/core/dist/style.css";
import "@vue-flow/core/dist/theme-default.css";

const props = defineProps({ steps: { type: Array, required: true } });
const emit = defineEmits(["select"]);

const STATUS_COLOR = {
  done: "#2f9e63", running: "#316ef3", failed: "#d64545",
  pending: "#86868b", skipped: "#86868b",
};

function parseDeps(s) {
  try { return JSON.parse(s.depends_on || "[]"); } catch { return []; }
}

// 分层布局：无依赖为第 0 层，否则 = max(依赖层)+1（无需 dagre，步骤数少）
const levelOf = (seq, cache = {}) => {
  if (cache[seq] !== undefined) return cache[seq];
  const st = props.steps.find((x) => x.seq === seq);
  const deps = st ? parseDeps(st) : [];
  cache[seq] = deps.length ? Math.max(...deps.map((d) => levelOf(d, cache))) + 1 : 0;
  return cache[seq];
};

const nodes = computed(() => {
  const byLevel = {};
  for (const s of props.steps) {
    const lv = levelOf(s.seq, {});
    byLevel[lv] = (byLevel[lv] || 0);
    const idx = byLevel[lv]++;
    s._x = 40 + lv * 230;
    s._y = 30 + idx * 110;
  }
  return props.steps.map((s) => ({
    id: String(s.seq),
    position: { x: s._x, y: s._y },
    data: { label: `步骤${s.seq} ${s.step_desc.slice(0, 18)}`, status: s.status, seq: s.seq },
    style: {
      border: `2px solid ${STATUS_COLOR[s.status] || "#86868b"}`,
      borderRadius: "10px", padding: "6px 10px", fontSize: "12px",
      background: "var(--card, #fff)", color: "var(--text, #1d1d1f)", width: "190px",
    },
  }));
});

const edges = computed(() => {
  const out = [];
  for (const s of props.steps) {
    for (const d of parseDeps(s)) {
      out.push({
        id: `${d}->${s.seq}`, source: String(d), target: String(s.seq),
        animated: s.status === "running",
        style: { stroke: "#86868b" },
      });
    }
  }
  return out;
});
</script>

<template>
  <div class="dag-box">
    <p class="hint">调用图（节点=步骤，箭头=数据依赖；点击节点查看输入输出；滚轮缩放/拖拽平移）</p>
    <div class="dag-canvas">
      <VueFlow :nodes="nodes" :edges="edges" :fit-view-on-init="true" :min-zoom="0.3"
               @node-click="(_, n) => emit('select', n.data.seq)">
        <template #node-default="{ id, data }">
          <div class="dag-node" :style="{ borderColor: STATUS_COLOR[data.status] }"
               :title="`状态：${data.status}`">
            <span class="dot" :style="{ background: STATUS_COLOR[data.status] }"></span>
            {{ data.label }}
          </div>
        </template>
      </VueFlow>
    </div>
  </div>
</template>

<style scoped>
.dag-box { border: 1px solid var(--line, #e5e5e8); border-radius: 10px;
           margin: 8px 0; overflow: hidden; }
.dag-canvas { height: 260px; }
.dag-node { padding: 6px 10px; font-size: 12px; border: 2px solid #86868b;
            border-radius: 10px; background: #fff; color: #1d1d1f; width: 170px; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
       margin-right: 6px; background: #86868b; }
.hint { color: var(--dim, #86868b); font-size: 12px; margin: 6px 10px; }
</style>
