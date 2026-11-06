<script setup>
// 统一的任务结果渲染组件：当前任务卡 / 首页历史 / 任务中心三处共用
// 此前结果渲染抄了三份且逐渐漂移（历史无回答面板、无事件卡）——组件化终结该问题
import { ref } from "vue";
import MarkdownIt from "markdown-it";
import DagView from "./DagView.vue";

// Markdown 渲染（html:false 转义原始标签，本地内容的安全底线）
const md = new MarkdownIt({ html: false, linkify: true });
const looksMd = (s) => /^#|\n#{1,3} |\*\*|^\s*[-*] /m.test(s || "");
const mdHtml = (s) => md.render(s || "");

const props = defineProps({
  task: { type: Object, required: true },
  pending: { type: Array, default: () => [] },  // 全局待审批列表（父级传入，避免组件内重复轮询）
});
const emit = defineEmits(["approve", "reject"]);

const STATUS_TEXT = {
  pending: "待处理", planning: "规划中", executing: "执行中", awaiting_user: "等待确认",
  verifying: "验证中", completed: "已完成", partial: "部分完成", failed: "失败", cancelled: "已取消",
};
const isTerminal = (s) => ["completed", "partial", "failed", "cancelled"].includes(s);

async function api(path) {
  const res = await fetch(`/api${path}`, { headers: { "Content-Type": "application/json" } });
  return res.ok ? res.json() : { error: "请求失败" };
}

// 步骤级 trace：点击步骤行展开输入/输出
const openStep = ref(null);
const stepIO = ref(null);
async function toggleStep(sid) {
  if (openStep.value === sid) { openStep.value = null; return; }
  openStep.value = sid;
  stepIO.value = await api(`/steps/${sid}/io`);
}

// 本任务待审批单
const approvalsFor = (tid) => props.pending.filter((a) => a.task_id === tid);

// 质量报告（库列为字符串，安全解析）
import { computed } from "vue";
const qj = computed(() => {
  try { return typeof props.task.quality_json === "string"
    ? JSON.parse(props.task.quality_json) : props.task.quality_json; }
  catch { return null; }
});
</script>

<template>
  <div>
    <!-- DAG 调用图（多步任务才有图；单步不占地方） -->
    <DagView v-if="(task.steps || []).length > 1" :steps="task.steps"
             @select="toggleStep" />

    <!-- 步骤时间线（图之下的明细列表） -->
    <div v-for="s in task.steps || []" :key="s.id" class="step trace-step"
         :class="{ running: s.status === 'running' }" @click="toggleStep(s.id)">
      {{ s.status === 'done' ? '✓' : s.status === 'running' ? '▸' : s.status === 'failed' ? '✗' : s.status === 'skipped' ? '−跳过' : '·' }}
      步骤 {{ s.seq }}：{{ s.step_desc }}
      <span class="hint">（点击查看输入输出）</span>
      <span v-if="s.error" class="err">{{ s.error }}</span>
      <div v-if="openStep === s.id && stepIO" class="io-box" @click.stop>
        <b>输入</b>
        <pre class="result">{{ stepIO.input || "（无）" }}</pre>
        <b>输出</b>
        <pre class="result">{{ stepIO.output || stepIO.error || "（无）" }}</pre>
      </div>
    </div>

    <div v-if="task.cancel_requested && !isTerminal(task.status)" class="warn-note">
      已收到取消请求，将在当前步骤停止后生效
    </div>

    <!-- 质量报告（QA）：验证链与证据的结构化呈现，未验证事项显式列出 -->
    <div v-if="qj && isTerminal(task.status)" class="quality-card">
      <div class="q-head">质量报告</div>
      <div v-if="qj.结构校验 !== undefined" class="q-row">
        <span>结构校验</span><b>{{ qj.结构校验 ? '✓ 通过' : '✗ 未过' }}</b>
      </div>
      <div v-if="qj.交付终检" class="q-row">
        <span>交付终检</span>
        <b :class="{ bad: qj.交付终检 === '未通过' }">
          {{ qj.交付终检 === '通过' ? '✓ 通过' : qj.交付终检 === '未通过' ? '✗ 未通过' : '· 未适用' }}</b>
      </div>
      <div v-if="qj.evidence_sources !== undefined" class="q-row">
        <span>证据来源</span><b>{{ qj.evidence_sources }} 条</b>
      </div>
      <div v-if="qj.reviews?.length" class="q-row">
        <span>步骤评审</span>
        <b>{{ qj.reviews.filter(r => r.pass).length }}/{{ qj.reviews.length }} 通过</b>
      </div>
      <div v-if="qj.skipped?.length" class="q-row">
        <span>降级跳过</span><b>步骤 {{ qj.skipped.join('、') }}</b>
      </div>
      <div v-if="qj.replanned" class="q-row">
        <span>失败恢复</span><b>曾重规划</b>
      </div>
      <div v-if="qj.gate_reason" class="q-note">{{ qj.gate_reason }}</div>
    </div>

    <!-- 审批：批准/拒绝事件抛给父级处理（批准后自动续跑的策略归父级） -->
    <div v-if="task.status === 'awaiting_user'" class="approval-box">
      <div v-for="a in approvalsFor(task.id)" :key="a.id" class="row spread approval">
        <span>{{ a.reason }}</span>
        <span>
          <button class="primary" @click="emit('approve', a.id, task.id)">批准并继续</button>
          <button @click="emit('reject', a.id)">拒绝</button>
        </span>
      </div>
      <p v-if="!approvalsFor(task.id).length" class="hint">没有待审批项——若此前已拒绝，可修改输入后重试。</p>
    </div>

    <!-- 回答：专属面板；热点类渲染事件卡榜单 -->
    <div v-if="isTerminal(task.status)" class="answer-panel">
      <div class="answer-head">{{ task.error ? '错误' : '回答' }}</div>
      <div v-if="task.result_detail?.top_items" class="hotboard">
        <div v-for="(it, i) in task.result_detail.top_items" :key="i" class="hotcard">
          <span class="rank" :class="'r' + (i + 1)">{{ i + 1 }}</span>
          <div class="hotbody">
            <a v-if="it.url" :href="it.url" target="_blank" class="hottitle">{{ it.title_cn || it.title }}</a>
            <span v-else class="hottitle">{{ it.title_cn || it.title }}</span>
            <div v-if="it.title_cn && it.title_cn !== it.title" class="hotorig">{{ it.title }}</div>
            <div v-if="it.summary_cn" class="hotsum">{{ it.summary_cn }}</div>
            <div class="hotmeta">
              <span class="tag">{{ it.category }}</span>
              <span>{{ it.source }}</span>
              <span class="heat">热度 {{ it.weighted_score }}</span>
            </div>
          </div>
        </div>
        <p class="hint">输入 {{ task.result_detail.total_input }} 条 · 聚合去重 {{ task.result_detail.removed_duplicates }} 条同题报道</p>
      </div>
      <div v-else-if="task.result_summary && looksMd(task.result_summary)"
           class="md result" v-html="mdHtml(task.result_summary)"></div>
      <pre v-else-if="task.result_summary" class="result">{{ task.result_summary }}</pre>
      <pre v-else-if="task.error" class="result err">{{ task.error }}</pre>
      <!-- 产物下载：报告/结果文件（路径由服务端从库取，客户端不传路径） -->
      <div v-if="task.result_ref" class="row" style="padding:8px 12px">
        <a :href="`/api/tasks/${task.id}/file`" download>
          <button>下载 {{ task.result_ref.split('/').pop() }}</button>
        </a>
      </div>
    </div>

    <details v-if="task.result_detail">
      <summary>完整报告（事实与推测已分级）</summary>
      <pre class="result">{{ JSON.stringify(task.result_detail, null, 2) }}</pre>
    </details>
  </div>
</template>

<style scoped>
.step { color: var(--dim); font-size: 13px; padding: 2px 0; }
.step.running { color: var(--run); font-weight: 600; }
.trace-step { cursor: pointer; padding: 2px 4px; border-radius: 6px; }
.trace-step:hover { background: var(--bg); }
.io-box { margin: 6px 0 4px 18px; }
.io-box b { font-size: 12px; color: var(--dim); }
.row { display: flex; gap: 8px; align-items: center; margin: 10px 0; }
.row.spread { justify-content: space-between; }
button { border: 1px solid var(--line); background: var(--card); color: inherit;
         border-radius: 8px; padding: 5px 12px; cursor: pointer; font: inherit; }
button.primary { background: #316ef3; border-color: #316ef3; color: #fff; }
.hint { color: var(--dim); font-size: 12px; }
.err { color: #d64545; }
.warn-note { color: #d97a06; font-size: 13px; }
.approval-box { border-left: 3px solid #d97a06; padding: 8px 12px; margin: 8px 0;
                background: var(--bg); border-radius: 8px; }
.result { background: var(--bg); border-radius: 8px; padding: 12px;
          white-space: pre-wrap; font-size: 13px; }
/* markdown 渲染样式 */
.md :is(h1, h2, h3) { margin: 10px 0 6px; font-size: 15px; }
.md h1 { font-size: 17px; }
.md p { margin: 6px 0; }
.md ul, .md ol { padding-left: 20px; margin: 6px 0; }
.md blockquote { border-left: 3px solid var(--line); margin: 6px 0; padding: 2px 10px;
                 color: var(--dim); }
.md code { background: rgba(127,127,127,.15); border-radius: 4px; padding: 1px 5px; }
.md a { color: #316ef3; }
.answer-panel { border: 1px solid #316ef3; border-radius: 10px; margin: 10px 0 4px; overflow: hidden; }
.answer-head { background: rgba(49, 110, 243, .12); color: #316ef3; font-size: 12px;
               font-weight: 700; padding: 4px 12px; letter-spacing: 2px; }
.hotboard { display: flex; flex-direction: column; gap: 8px; margin: 8px; }
.hotcard { display: flex; gap: 12px; align-items: flex-start; padding: 10px 12px;
           background: var(--bg); border-radius: 10px; }
.rank { min-width: 26px; height: 26px; border-radius: 8px; display: flex; align-items: center;
        justify-content: center; font-weight: 700; font-size: 13px;
        background: var(--line); color: var(--dim); }
.rank.r1 { background: #fde2e2; color: #c53030; }
.rank.r2 { background: #fff3d6; color: #b7791f; }
.rank.r3 { background: #e6ffed; color: #2f855a; }
.hottitle { font-weight: 600; color: inherit; text-decoration: none; }
a.hottitle:hover { color: #316ef3; }
.hotorig { font-size: 12px; color: var(--dim); margin-top: 2px; overflow: hidden;
           text-overflow: ellipsis; white-space: nowrap; }
.hotsum { font-size: 13px; margin-top: 4px; opacity: .85; }
.hotmeta { display: flex; gap: 10px; font-size: 12px; color: var(--dim); margin-top: 3px; align-items: center; }
.heat { color: #d97a06; }
.tag { background: var(--bg); border-radius: 6px; padding: 0 6px; font-size: 12px; color: var(--dim); }
/* 质量报告卡 */
.quality-card { border: 1px solid var(--line); border-left: 3px solid #2f9e63;
                border-radius: 10px; margin: 8px 0; padding: 8px 12px; }
.q-head { font-size: 12px; font-weight: 700; color: var(--dim); letter-spacing: 2px; margin-bottom: 4px; }
.q-row { display: flex; justify-content: space-between; font-size: 13px; padding: 2px 0; }
.q-row span { color: var(--dim); }
.q-row b.bad { color: #d64545; }
.q-note { font-size: 12px; color: #d97a06; margin-top: 4px; }
</style>
