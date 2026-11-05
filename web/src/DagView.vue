<script setup>
// DAG 调用图：任务的步骤依赖关系可视化（vue-flow 渲染）
// 节点=步骤（状态着色），边=数据流依赖（depends_on）；点击节点展开输入输出
import { computed, ref } from "vue";
import { MarkerType, VueFlow } from "@vue-flow/core";
import "@vue-flow/core/dist/style.css";
import "@vue-flow/core/dist/theme-default.css";

const props = defineProps({ steps: { type: Array, required: true } });
const emit = defineEmits(["select"]);
// 默认折叠（用户偏好）：点标题行展开图，避免多步任务详情一打开就是一大块
const expanded = ref(false);

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
        type: "smoothstep",
        animated: s.status === "running",
        // 对象形式显式给颜色：枚举简写的默认 markerColor 曾渲染不出箭头（实测反馈）
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: "#5a5a60",
          width: 22,
          height: 22,
        },
        style: { stroke: "#5a5a60", strokeWidth: 1.8 },
      });
    }
  }
  return out;
});
</script>

<template>
  <div class="dag-box">
    <div class="dag-head" @click="expanded = !expanded">
      {{ expanded ? '▾' : '▸' }} 调用图（{{ props.steps.length }} 步 · 依赖箭头 · 点击节点看输入输出）
    </div>
    <div v-show="expanded" class="dag-canvas">
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
.dag-head { padding: 6px 12px; font-size: 13px; cursor: pointer;
            color: var(--dim, #86868b); user-select: none; }
.dag-head:hover { background: var(--bg, #f7f7f8); }
.dag-canvas { height: 260px; border-top: 1px solid var(--line, #e5e5e8); }
.dag-node { padding: 6px 10px; font-size: 12px; border: 2px solid #86868b;
            border-radius: 10px; background: #fff; color: #1d1d1f; width: 170px; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
       margin-right: 6px; background: #86868b; }
.hint { color: var(--dim, #86868b); font-size: 12px; margin: 6px 10px; }
</style>
