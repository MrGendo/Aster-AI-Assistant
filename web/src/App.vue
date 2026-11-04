<script setup>
// Aster 平台 Phase 1 前端：四个一级页面（PRD §10.2）
// 视觉基线遵循 PRD §10.7：中性色为主、状态色语义化、清晰层级、无装饰性动画
import { computed, onMounted, ref } from "vue";

const tab = ref("home");
const tasks = ref([]);
const caps = ref([]);
const memories = ref([]);
const pendingApprovals = ref([]);
const sys = ref(null);
const goalText = ref("");
const detail = ref(null);
const runningTask = ref(null);
// 首页对话式回显：提交后就地展示执行状态与最终回答（PRD §10.4/§10.3）
const activeTask = ref(null);
// 任务中心就地展开 + 行内管理 + 搜索/批量
const detailId = ref(null);
const searchQuery = ref("");
const selectedIds = ref([]);
function toggleSelect(tid) {
  selectedIds.value = selectedIds.value.includes(tid)
    ? selectedIds.value.filter((x) => x !== tid) : [...selectedIds.value, tid];
}
async function searchTasks() { tasks.value = await api(`/tasks?query=${encodeURIComponent(searchQuery.value.trim())}&limit=100`); }
async function clearSearch() { searchQuery.value = ""; refresh(); }
const allSelected = computed(() =>
  tasks.value.length > 0 && tasks.value.every((t) => selectedIds.value.includes(t.id)));
function toggleSelectAll() {
  // 只全选当前列表（搜索过滤后也成立），不做全库操作——误删面可控
  selectedIds.value = allSelected.value ? [] : tasks.value.map((t) => t.id);
}
// 步骤级 trace：点击步骤行就地展开输入/输出（借鉴 agent-prism 的 trace 展示概念）
const openStep = ref(null);
const stepIO = ref(null);
async function toggleStep(sid) {
  if (openStep.value === sid) { openStep.value = null; return; }
  openStep.value = sid;
  stepIO.value = await api(`/steps/${sid}/io`);
}
async function batchDelete() {
  const ids = [...selectedIds.value];
  if (!ids.length || !confirm(`确认删除选中的 ${ids.length} 个任务？（审计记录保留）`)) return;
  await api("/tasks/batch_delete", { method: "POST", body: JSON.stringify({ ids }) });
  selectedIds.value = [];
  refresh();
}
async function toggleDetail(tid) {
  if (detailId.value === tid) { detailId.value = null; return; }
  detailId.value = tid;
  detail.value = await api(`/tasks/${tid}`);
}
async function deleteTask(tid) {
  if (!confirm(`确认删除任务 #${tid}？（执行审计记录会保留）`)) return;
  await api(`/tasks/${tid}`, { method: "DELETE" });
  refresh();
}
// 历史就地展开（不跳任务中心）
const expandedId = ref(null);
const histDetail = ref(null);
async function toggleHistory(tid) {
  if (expandedId.value === tid) { expandedId.value = null; return; }
  expandedId.value = tid;
  histDetail.value = await api(`/tasks/${tid}`);
}
const newMem = ref({ scope: "preference", content: "" });
// Stage 2：发现与评估
const cands = ref([]);
const candUrl = ref("");
const report = ref(null);
const draft = ref(null);
// Stage 4：成长与进化
const proposals = ref([]);
const analyzing = ref(false);

const STATUS_TEXT = {
  pending: "待处理", planning: "规划中", executing: "执行中", awaiting_user: "等待确认",
  verifying: "验证中", completed: "已完成", partial: "部分完成", failed: "失败", cancelled: "已取消",
};
const SCOPE_TEXT = { preference: "偏好", background: "背景", project: "项目" };
const isTerminal = (s) => ["completed", "partial", "failed", "cancelled"].includes(s);

async function api(path, opts) {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" }, ...opts,
  });
  return res.ok ? res.json() : { error: (await res.json().catch(() => ({}))).detail || "请求失败" };
}

async function refresh() {
  tasks.value = await api("/tasks?limit=30");
  caps.value = await api("/capabilities");
  memories.value = await api("/memories");
  pendingApprovals.value = await api("/approvals?status=pending");
  sys.value = await api("/system/status");
  cands.value = await api("/candidates");
  proposals.value = await api("/proposals");
}

// 审批：任务详情内联批准并自动重试（此前藏在设置页，用户找不到——实测反馈）
function approvalsFor(tid) { return pendingApprovals.value.filter((a) => a.task_id === tid); }
async function approveAndRetry(aid, tid) {
  const r = await api(`/approvals/${aid}/decide`, { method: "POST", body: JSON.stringify({ decision: "approved" }) });
  if (r.error) return alert(r.error);
  await refresh();
  await retryTask(tid);
  // 重试后继续跟踪该任务，首页当前卡实时刷新
  activeTask.value = { id: tid, goal: activeTask.value?.goal || "", status: "pending" };
  watchTask(tid);
}
async function rejectApproval(aid) {
  await api(`/approvals/${aid}/decide`, { method: "POST", body: JSON.stringify({ decision: "rejected" }) });
  refresh();
}
async function runAnalyze() {
  analyzing.value = true;
  await api("/evolution/analyze", { method: "POST" });
  proposals.value = await api("/proposals");
  analyzing.value = false;
}
async function runExperiment(id) { await api(`/proposals/${id}/experiment`, { method: "POST" }); proposals.value = await api("/proposals"); }
async function applyProposal(id) { await api(`/proposals/${id}/apply`, { method: "POST", body: JSON.stringify({}) }); proposals.value = await api("/proposals"); }
async function rollbackProposal(id) { await api(`/proposals/${id}/rollback`, { method: "POST" }); proposals.value = await api("/proposals"); }

// Stage 2：候选评估流
const LIFECYCLE_TEXT = {
  discovered: "已发现", screened: "初筛过", deep_analysis: "深析过", compared: "已比较",
  isolated_test: "测试过", integration_eval: "接入评估", pending_approval: "待批准",
  integration_verified: "验证过", active: "已接入", archived: "已归档",
};
async function submitCand() {
  const u = candUrl.value.trim();
  if (!u) return;
  await api("/candidates", { method: "POST", body: JSON.stringify({ repo_url: u }) });
  candUrl.value = ""; refresh();
}
async function openReport(id) { report.value = await api(`/candidates/${id}/report`); }
async function genScaffold(id) { await api(`/candidates/${id}/scaffold`, { method: "POST" }); }
async function openDraft(id) { draft.value = await api(`/candidates/${id}/draft`); }
async function testAdapter(id) { await api(`/candidates/${id}/test_adapter`, { method: "POST" }); refresh(); }
async function integrateCand(id) { await api(`/candidates/${id}/integrate`, { method: "POST" }); refresh(); }
async function archiveCand(id) { await api(`/candidates/${id}/archive`, { method: "POST", body: JSON.stringify({ reason: "用户手动归档" }) }); refresh(); }

async function submitGoal() {
  const goal = goalText.value.trim();
  if (!goal) return;
  const r = await api("/tasks", { method: "POST", body: JSON.stringify({ goal }) });
  if (r.id) {
    goalText.value = "";
    activeTask.value = { id: r.id, goal, status: "pending" };
    await refresh();
    watchTask(r.id);
  }
}

function watchTask(id) {
  if (runningTask.value) runningTask.value.close();
  const es = new EventSource(`/api/tasks/${id}/events`);
  runningTask.value = es;
  es.onmessage = (ev) => {
    if (ev.data === "[DONE]") {
      es.close(); runningTask.value = null;
      // 完成后把完整回答拉回首页答案卡，同时刷新列表
      api(`/tasks/${id}`).then((d) => { activeTask.value = d; });
      refresh();
    } else {
      // 状态每次变化拉一次详情：首页就地刷新步骤进度（任务级状态变更次数有限，开销可忽略）
      api(`/tasks/${id}`).then((d) => {
        if (activeTask.value && activeTask.value.id === id) activeTask.value = d;
      });
    }
  };
  es.onerror = () => { es.close(); runningTask.value = null; };
}

async function openDetail(id) {
  detail.value = await api(`/tasks/${id}`);
  if (!isTerminal(detail.value.status)) setTimeout(() => openDetail(id), 3000);
}

async function cancelTask(id) { await api(`/tasks/${id}/cancel`, { method: "POST" }); refresh(); openDetail(id); }
async function retryTask(id) { await api(`/tasks/${id}/retry`, { method: "POST" }); refresh(); watchTask(id); }
async function toggleCap(name, status) {
  await api(`/capabilities/${name}/${status === "active" ? "disable" : "enable"}`, { method: "POST" });
  refresh();
}
async function togglePause() {
  await api(`/system/${sys.value.paused ? "resume" : "pause"}`, { method: "POST" }); refresh();
}
async function decideApproval(id, decision) {
  await api(`/approvals/${id}/decide`, { method: "POST", body: JSON.stringify({ decision }) });
  refresh();
}
async function addMemory() {
  const c = newMem.value.content.trim();
  if (!c) return;
  await api("/memories", { method: "POST", body: JSON.stringify(newMem.value) });
  newMem.value.content = ""; refresh();
}
async function delMemory(id) { await api(`/memories/${id}`, { method: "DELETE" }); refresh(); }

const homeRecent = computed(() => tasks.value.slice(0, 6));
onMounted(() => {
  refresh();
  // 待审批/任务状态自动刷新：否则审批角标不出现，用户只能干等（实测反馈）
  setInterval(refresh, 5000);
});
</script>

<template>
  <div class="shell">
    <nav class="side">
      <div class="brand">Aster</div>
      <button v-for="t in [['home','首页'],['tasks','任务中心'],['caps','能力中心'],['disc','发现与评估'],['evo','成长与进化'],['settings','设置']]":key="t[0]"
              :class="{ on: tab === t[0] }" @click="tab = t[0]">{{ t[1] }}</button>
      <div v-if="sys" class="side-status" :class="{ warn: sys.paused }">
        {{ sys.paused ? "已暂停" : sys.provider_ready ? "运行中" : "模型未配置" }}
        <span v-if="pendingApprovals.length" class="badge">{{ pendingApprovals.length }} 待审批</span>
      </div>
    </nav>

    <main>
      <!-- 首页 = 主战场：输入 → 执行流程 → 审批 → 结果，一条流全在这（用户实测反馈重构） -->
      <section v-if="tab === 'home'">
        <h1>今天要做点什么？</h1>
        <textarea v-model="goalText" rows="3" placeholder="描述你的目标，或直接粘贴 GitHub 仓库链接…"
                  @keydown.meta.enter="submitGoal" @keydown.ctrl.enter="submitGoal" />
        <div class="row">
          <button class="primary" @click="submitGoal">开始任务</button>
          <span class="hint">⌘/Ctrl + Enter 发送</span>
        </div>

        <!-- 当前任务：对话式呈现——问题气泡 → 过程 → 专属回答面板（借鉴 shadcn-chatbot 模式） -->
        <div v-if="activeTask" class="card answer">
          <div class="row spread">
            <span class="st" :class="activeTask.status">{{ STATUS_TEXT[activeTask.status] || activeTask.status }}</span>
            <button class="ghost" @click="activeTask = null">收起</button>
          </div>
          <div class="q-bubble">{{ activeTask.goal }}</div>
          <!-- 步骤时间线：运行中实时刷（PRD §10.4） -->
          <div v-for="s in activeTask.steps || []" :key="s.id" class="step"
               :class="{ running: s.status === 'running' }">
            {{ s.status === 'done' ? '✓' : s.status === 'running' ? '▸' : s.status === 'failed' ? '✗' : '·' }}
            步骤 {{ s.seq }}：{{ s.step_desc }}{{ s.status === 'running' ? '（进行中）' : '' }}
          </div>
          <!-- 审批就在这里：批准并自动续跑（不再去设置页找） -->
          <div v-if="activeTask.status === 'awaiting_user'" class="approval-box">
            <div v-for="a in approvalsFor(activeTask.id)" :key="a.id" class="row spread approval">
              <span>{{ a.reason }}</span>
              <span>
                <button class="primary" @click="approveAndRetry(a.id, activeTask.id)">批准并继续</button>
                <button @click="rejectApproval(a.id)">拒绝</button>
              </span>
            </div>
          </div>
          <!-- 回答：专属面板（不再混在过程文本里），热点类为事件卡榜单 -->
          <div v-if="isTerminal(activeTask.status)" class="answer-panel">
            <div class="answer-head">回答</div>
            <div v-if="activeTask.result_detail?.top_items" class="hotboard">
              <div v-for="(it, i) in activeTask.result_detail.top_items" :key="i" class="hotcard">
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
              <p class="hint">输入 {{ activeTask.result_detail.total_input }} 条 · 聚合去重 {{ activeTask.result_detail.removed_duplicates }} 条同题报道</p>
            </div>
            <pre v-else-if="activeTask.result_summary" class="result">{{ activeTask.result_summary }}</pre>
            <pre v-else-if="activeTask.error" class="result err">{{ activeTask.error }}</pre>
          </div>
          <div v-if="['failed', 'cancelled', 'partial'].includes(activeTask.status)" class="row">
            <button @click="retryTask(activeTask.id); watchTask(activeTask.id)">重试</button>
          </div>
        </div>

        <!-- 历史：就地展开完整卡，不跳任务中心 -->
        <h2>历史任务</h2>
        <template v-for="t in homeRecent" :key="t.id">
          <div class="card task-row" @click="toggleHistory(t.id)">
            <span class="st" :class="t.status">{{ STATUS_TEXT[t.status] || t.status }}</span>
            <span class="goal">{{ t.goal.slice(0, 60) }}</span>
            <span class="time">{{ new Date(t.created_at * 1000).toLocaleTimeString() }}</span>
          </div>
          <div v-if="expandedId === t.id && histDetail" class="card">
            <div v-for="s in histDetail.steps || []" :key="s.id" class="step">
              {{ s.status === 'done' ? '✓' : s.status === 'failed' ? '✗' : '·' }} 步骤 {{ s.seq }}：{{ s.step_desc }}
            </div>
            <div v-if="histDetail.status === 'awaiting_user'" class="approval-box">
              <div v-for="a in approvalsFor(t.id)" :key="a.id" class="row spread approval">
                <span>{{ a.reason }}</span>
                <span>
                  <button class="primary" @click="approveAndRetry(a.id, t.id)">批准并继续</button>
                  <button @click="rejectApproval(a.id)">拒绝</button>
                </span>
              </div>
            </div>
            <pre v-if="histDetail.result_summary" class="result">{{ histDetail.result_summary }}</pre>
            <pre v-else-if="histDetail.error" class="result err">{{ histDetail.error }}</pre>
          </div>
        </template>
        <p v-if="!homeRecent.length" class="empty">还没有任务。粘贴一个仓库链接试试，例如 https://github.com/asg017/sqlite-vec</p>
      </section>

      <!-- 任务中心：搜索 + 多选批量 + 行内管理 -->
      <section v-if="tab === 'tasks'">
        <div class="row spread">
          <h1>任务中心</h1>
          <div class="row" style="margin:0">
            <input v-model="searchQuery" style="width:220px" placeholder="关键词搜索目标/结果/错误"
                   @keydown.enter="searchTasks" />
            <button @click="searchTasks">搜索</button>
            <button v-if="searchQuery" class="ghost" @click="clearSearch">清空</button>
          </div>
        </div>
        <div v-if="selectedIds.length" class="card batch-bar">
          已选 {{ selectedIds.length }} 项
          <button class="danger" @click="batchDelete">批量删除</button>
          <button class="ghost" @click="selectedIds = []">取消选择</button>
        </div>
        <div v-if="tasks.length" class="select-all-row" @click="toggleSelectAll">
          <input type="checkbox" :checked="allSelected" @click.stop="toggleSelectAll" />
          <span class="hint">全选当前列表（{{ tasks.length }} 项{{ searchQuery ? " · 搜索结果" : "" }}）</span>
        </div>
        <template v-for="t in tasks" :key="t.id">
          <div class="card task-row" @click="toggleDetail(t.id)">
            <input type="checkbox" :checked="selectedIds.includes(t.id)"
                   @click.stop="toggleSelect(t.id)" />
            <span class="st" :class="t.status">{{ STATUS_TEXT[t.status] || t.status }}</span>
            <span class="goal">{{ t.goal.slice(0, 70) }}</span>
            <span class="time">{{ new Date(t.created_at * 1000).toLocaleString() }}</span>
            <span class="row-actions" @click.stop>
              <button v-if="!isTerminal(t.status)" class="ghost" @click="cancelTask(t.id)">取消</button>
              <button v-if="['failed','cancelled','partial','awaiting_user'].includes(t.status)" class="ghost" @click="retryTask(t.id)">重试</button>
              <button v-if="isTerminal(t.status)" class="ghost danger" @click="deleteTask(t.id)">删除</button>
            </span>
          </div>
          <div v-if="detailId === t.id && detail" class="card detail">
            <div class="row spread">
              <h2>#{{ detail.id }} <span class="st" :class="detail.status">{{ STATUS_TEXT[detail.status] }}</span></h2>
              <button class="ghost" @click="detailId = null">收起</button>
            </div>
            <p class="goal-full">{{ detail.goal }}</p>
            <div v-for="s in detail.steps" :key="s.id" class="step trace-step"
                 :class="{ running: s.status === 'running' }" @click="toggleStep(s.id)">
              {{ s.status === 'done' ? '✓' : s.status === 'running' ? '▸' : s.status === 'failed' ? '✗' : '·' }}
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
            <div v-if="detail.cancel_requested && !isTerminal(detail.status)" class="warn-note">已收到取消请求，将在当前步骤停止后生效</div>
            <div v-if="detail.status === 'awaiting_user'" class="approval-box">
              <div v-for="a in approvalsFor(detail.id)" :key="a.id" class="row spread approval">
                <span>{{ a.reason }}</span>
                <span>
                  <button class="primary" @click="approveAndRetry(a.id, detail.id)">批准并继续</button>
                  <button @click="rejectApproval(a.id)">拒绝</button>
                </span>
              </div>
              <p v-if="!approvalsFor(detail.id).length" class="hint">
                没有待审批项——若此前已拒绝，可修改输入后重试任务。
              </p>
            </div>
            <div class="answer-panel" v-if="detail.result_summary || detail.error">
              <div class="answer-head">{{ detail.error ? '错误' : '回答' }}</div>
              <pre v-if="detail.result_summary" class="result">{{ detail.result_summary }}</pre>
              <pre v-else class="result err">{{ detail.error }}</pre>
            </div>
            <details v-if="detail.result_detail">
              <summary>完整报告（事实与推测已分级）</summary>
              <pre class="result">{{ JSON.stringify(detail.result_detail, null, 2) }}</pre>
            </details>
          </div>
        </template>
      </section>

      <!-- 能力中心 -->
      <section v-if="tab === 'caps'">
        <h1>能力中心</h1>
        <p class="hint">候选能力未经验证，不会被执行；正式能力可随时停用。</p>
        <div v-for="c in caps" :key="c.id" class="card cap">
          <div class="row spread">
            <b>{{ JSON.parse(c.descriptor_json).name }}</b>
            <div class="row">
              <span class="st" :class="c.status === 'active' ? 'completed' : 'pending'">
                {{ { active: "正式可用", candidate: "候选（未开放）", disabled: "已停用", archived: "已归档" }[c.status] }}
              </span>
              <button v-if="c.status === 'active'" @click="toggleCap(c.name, c.status)">停用</button>
              <button v-else-if="c.status === 'disabled'" @click="toggleCap(c.name, c.status)">启用</button>
            </div>
          </div>
          <p>{{ JSON.parse(c.descriptor_json).purpose }}</p>
          <p class="meta">风险 {{ c.risk_level }} · 输入 {{ JSON.parse(c.descriptor_json).input }} · {{ JSON.parse(c.descriptor_json).cost }}</p>
        </div>
      </section>

      <!-- 发现与评估（Stage 2）：结论先行，证据可展开（PRD §10.6） -->
      <section v-if="tab === 'disc'">
        <h1>发现与评估</h1>
        <div class="row">
          <input v-model="candUrl" placeholder="粘贴 GitHub 仓库链接启动评估" @keydown.enter="submitCand" />
          <button class="primary" @click="submitCand">评估</button>
        </div>
        <div v-for="c in cands" :key="c.id" class="card cand">
          <div class="row spread">
            <div><b>{{ c.name }}</b> <span class="tag">{{ LIFECYCLE_TEXT[c.lifecycle] || c.lifecycle }}</span>
              <span v-if="c.recommendation" class="tag rec">建议：{{ c.recommendation }}</span></div>
            <div>
              <button @click="openReport(c.id)">报告</button>
              <button v-if="c.lifecycle === 'compared'" @click="genScaffold(c.id); openDraft(c.id)">生成适配草稿</button>
              <button v-if="draft && c.lifecycle === 'compared'" @click="openDraft(c.id)">看草稿</button>
              <button v-if="['compared','integration_eval'].includes(c.lifecycle)" @click="testAdapter(c.id)">隔离测试</button>
              <button v-if="c.lifecycle === 'pending_approval'" class="primary" @click="integrateCand(c.id)">批准接入</button>
              <button class="ghost" v-if="c.lifecycle !== 'archived'" @click="archiveCand(c.id)">归档</button>
            </div>
          </div>
        </div>
        <div v-if="report" class="card">
          <div class="row spread"><b>评估报告</b><button class="ghost" @click="report = null">关闭</button></div>
          <p>建议 <b>{{ report.recommendation }}</b>（置信度 {{ report.confidence }}）—— {{ report.reason }}</p>
          <details>
            <summary>十二维评估与证据（verified 与 inferred 分级）</summary>
            <pre class="result">{{ JSON.stringify({ 维度: report.dimensions_json, 证据: report.evidence_json, 同类仓: report.peers_json }, null, 2) }}</pre>
          </details>
        </div>
        <div v-if="draft" class="card">
          <div class="row spread"><b>适配草稿（须人工审阅后才可隔离测试）</b><button class="ghost" @click="draft = null">关闭</button></div>
          <pre class="result">{{ draft.script }}</pre>
        </div>
      </section>

      <!-- 成长与进化：让用户看懂系统为什么变、变了什么、是否更好（PRD §14.2） -->
      <section v-if="tab === 'evo'">
        <div class="row spread">
          <h1>成长与进化</h1>
          <button class="primary" :disabled="analyzing" @click="runAnalyze">
            {{ analyzing ? "分析中…" : "分析运行瓶颈" }}
          </button>
        </div>
        <p class="hint">系统从真实运行数据发现瓶颈并提改进方案；任何变更都可回滚，高风险永不自动应用。</p>
        <div v-for="p in proposals" :key="p.id" class="card">
          <div class="row spread">
            <div>
              <b>提案 #{{ p.id }}</b>
              <span class="tag">{{ { draft: "草稿", experimented: "已实验", applied: "已应用", rolled_back: "已回滚", rejected: "已拒绝" }[p.status] || p.status }}</span>
              <span class="tag" :class="p.risk === 'high' ? 'err' : ''">{{ p.risk }} 风险</span>
            </div>
            <div>
              <button v-if="p.status === 'draft'" @click="runExperiment(p.id)">回放实验</button>
              <button v-if="['draft', 'experimented'].includes(p.status)" class="primary" @click="applyProposal(p.id)">应用</button>
              <button v-if="p.status === 'applied'" @click="rollbackProposal(p.id)">回滚</button>
            </div>
          </div>
          <p>{{ p.hypothesis }}</p>
          <details>
            <summary>证据 · 变更 · 冻结验收标准</summary>
            <p class="meta">证据：{{ p.evidence_json }}</p>
            <p class="meta">变更：{{ p.change_json }}</p>
            <p class="meta">验收标准（冻结，仅人工可改）：{{ p.acceptance_frozen }}</p>
            <pre v-if="p.experiment_result_json" class="result">{{ p.experiment_result_json }}</pre>
          </details>
        </div>
        <p v-if="!proposals.length" class="empty">暂无提案——点右上「分析运行瓶颈」，系统会从能力统计与失败记录里找问题。</p>
      </section>

      <!-- 设置 -->
      <section v-if="tab === 'settings'">
        <h1>设置</h1>
        <div class="card">
          <div class="row spread">
            <b>系统</b>
            <button @click="togglePause">{{ sys?.paused ? "恢复运行" : "紧急停止" }}</button>
          </div>
          <p v-if="sys" class="meta">
            模型：{{ sys.provider.model }}（{{ sys.provider.base_url }}）· 密钥 …{{ sys.provider.key_tail || "未配置" }}
          </p>
        </div>

        <div class="card">
          <b>记忆（用户可查看、修改、删除）</b>
          <div class="row">
            <select v-model="newMem.scope">
              <option value="preference">偏好</option><option value="background">背景</option><option value="project">项目</option>
            </select>
            <input v-model="newMem.content" placeholder="例如：回复请用中文" @keydown.enter="addMemory" />
            <button class="primary" @click="addMemory">添加</button>
          </div>
          <div v-for="m in memories" :key="m.id" class="row spread mem">
            <span><span class="tag">{{ SCOPE_TEXT[m.scope] }}</span> {{ m.content }}</span>
            <button class="ghost" @click="delMemory(m.id)">删除</button>
          </div>
        </div>
      </section>
    </main>
  </div>
</template>

<style>
/* 视觉基线：中性底、单一主色、状态色仅表义（PRD §10.7） */
:root { --bg: #f7f7f8; --card: #fff; --line: #e5e5e8; --text: #1d1d1f; --dim: #86868b;
        --accent: #316ef3; --ok: #2f9e63; --run: #316ef3; --warn: #d97a06; --err: #d64545; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #1e1e20; --card: #2a2a2d; --line: #3a3a3e; --text: #e8e8ea; --dim: #98989d; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
       font: 14px/1.6 -apple-system, "PingFang SC", sans-serif; }
.shell { display: flex; min-height: 100vh; }
.side { width: 168px; padding: 20px 12px; border-right: 1px solid var(--line);
        display: flex; flex-direction: column; gap: 4px; position: sticky; top: 0; height: 100vh; }
.brand { font-size: 20px; font-weight: 700; margin-bottom: 16px; }
.side button { text-align: left; border: 0; background: none; padding: 8px 10px; border-radius: 8px;
               font-size: 14px; color: var(--text); cursor: pointer; }
.side button.on { background: var(--card); font-weight: 600; }
.side-status { margin-top: auto; font-size: 12px; color: var(--dim); }
.side-status.warn { color: var(--warn); }
.badge { background: var(--warn); color: #fff; border-radius: 8px; padding: 0 6px; margin-left: 4px; }
main { flex: 1; padding: 28px 36px; max-width: 860px; }
h1 { font-size: 20px; margin: 0 0 16px; } h2 { font-size: 15px; margin: 20px 0 10px; }
textarea, input, select { width: 100%; border: 1px solid var(--line); border-radius: 8px; padding: 10px;
       background: var(--card); color: var(--text); font: inherit; }
textarea { resize: vertical; }
.row { display: flex; gap: 8px; align-items: center; margin: 10px 0; }
.row.spread { justify-content: space-between; }
button { border: 1px solid var(--line); background: var(--card); color: var(--text); border-radius: 8px;
         padding: 6px 14px; cursor: pointer; font: inherit; }
button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
button.ghost { border-color: transparent; color: var(--dim); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; margin-bottom: 10px; }
.card.running { border-color: var(--run); }
.task-row { display: flex; gap: 10px; align-items: center; cursor: pointer; }
.goal { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.time { color: var(--dim); font-size: 12px; }
.st { font-size: 12px; padding: 1px 8px; border-radius: 8px; white-space: nowrap;
      background: var(--bg); color: var(--dim); }
.st.completed { color: var(--ok); } .st.executing, .st.planning, .st.verifying { color: var(--run); }
.st.failed { color: var(--err); } .st.awaiting_user { color: var(--warn); } .st.cancelled { color: var(--dim); }
.step { padding: 4px 0; color: var(--dim); font-size: 13px; }
.err { color: var(--err); }
.warn-note { color: var(--warn); font-size: 13px; }
.result { background: var(--bg); border-radius: 8px; padding: 12px; white-space: pre-wrap; font-size: 13px; }
.goal-full { color: var(--dim); }
.meta { color: var(--dim); font-size: 12px; margin: 4px 0; }
.hint { color: var(--dim); font-size: 12px; }
.empty { color: var(--dim); }
.detail { margin-top: 14px; }
.mem .tag { background: var(--bg); border-radius: 6px; padding: 0 6px; font-size: 12px; color: var(--dim); }
/* AIHOT 风格事件卡榜单 */
.hotboard { display: flex; flex-direction: column; gap: 8px; margin: 8px 0; }
.hotcard { display: flex; gap: 12px; align-items: flex-start; padding: 10px 12px;
           background: var(--bg); border-radius: 10px; }
.rank { min-width: 26px; height: 26px; border-radius: 8px; display: flex; align-items: center;
        justify-content: center; font-weight: 700; font-size: 13px; background: var(--line); color: var(--dim); }
.rank.r1 { background: #fde2e2; color: #c53030; } .rank.r2 { background: #fff3d6; color: #b7791f; }
.rank.r3 { background: #e6ffed; color: #2f855a; }
.hottitle { font-weight: 600; color: var(--text); text-decoration: none; }
a.hottitle:hover { color: var(--accent); }
.hotmeta { display: flex; gap: 10px; font-size: 12px; color: var(--dim); margin-top: 3px; align-items: center; }
.heat { color: var(--warn); }
.hotorig { font-size: 12px; color: var(--dim); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hotsum { font-size: 13px; margin-top: 4px; opacity: .85; }
.approval span { font-size: 13px; }
</style>
<style scoped>
/* 审批盒与运行步骤的视觉强调（首页主流程用） */
.approval-box { border-left: 3px solid var(--warn); padding: 8px 12px; margin: 8px 0;
                background: var(--bg); border-radius: 8px; }
.step.running { color: var(--run); font-weight: 600; }
</style>
<style scoped>
/* 对话式回答呈现（借鉴 shadcn-chatbot 模式，零依赖自实现） */
.q-bubble { background: color-mix(in srgb, var(--accent) 12%, transparent);
            border: 1px solid color-mix(in srgb, var(--accent) 30%, transparent);
            border-radius: 12px 12px 4px 12px; padding: 10px 14px; margin: 8px 0;
            font-weight: 500; }
.answer-panel { border: 1px solid var(--accent); border-radius: 10px;
                margin: 10px 0 4px; overflow: hidden; }
.answer-head { background: color-mix(in srgb, var(--accent) 15%, transparent);
               color: var(--accent); font-size: 12px; font-weight: 700;
               padding: 4px 12px; letter-spacing: 2px; }
.answer-panel .result { border-radius: 0; margin: 0; }
.row-actions button { padding: 2px 8px; font-size: 12px; }
button.danger { color: var(--err); }
</style>
<style scoped>
.batch-bar { display: flex; align-items: center; gap: 10px; border-color: var(--err);
             font-size: 13px; padding: 8px 14px; }
.task-row input[type="checkbox"] { width: auto; }
</style>
<style scoped>
.select-all-row { display: flex; align-items: center; gap: 8px; padding: 4px 8px;
                  cursor: pointer; font-size: 13px; }
.select-all-row input { width: auto; }
</style>
<style scoped>
.trace-step { cursor: pointer; padding: 2px 4px; border-radius: 6px; }
.trace-step:hover { background: var(--bg); }
.io-box { margin: 6px 0 4px 18px; }
.io-box b { font-size: 12px; color: var(--dim); }
</style>
