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
const newMem = ref({ scope: "preference", content: "" });
// Stage 2：发现与评估
const cands = ref([]);
const candUrl = ref("");
const report = ref(null);
const draft = ref(null);

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
}

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
onMounted(refresh);
</script>

<template>
  <div class="shell">
    <nav class="side">
      <div class="brand">Aster</div>
      <button v-for="t in [['home','首页'],['tasks','任务中心'],['caps','能力中心'],['disc','发现与评估'],['settings','设置']]"
              :key="t[0]" :class="{ on: tab === t[0] }" @click="tab = t[0]">{{ t[1] }}</button>
      <div v-if="sys" class="side-status" :class="{ warn: sys.paused }">
        {{ sys.paused ? "已暂停" : sys.provider_ready ? "运行中" : "模型未配置" }}
        <span v-if="pendingApprovals.length" class="badge">{{ pendingApprovals.length }} 待审批</span>
      </div>
    </nav>

    <main>
      <!-- 首页：围绕“我要做什么”（PRD §10.3），不放技术细节 -->
      <section v-if="tab === 'home'">
        <h1>今天要做点什么？</h1>
        <textarea v-model="goalText" rows="3" placeholder="描述你的目标，或直接粘贴 GitHub 仓库链接…"
                  @keydown.meta.enter="submitGoal" @keydown.ctrl.enter="submitGoal" />
        <div class="row">
          <button class="primary" @click="submitGoal">开始任务</button>
          <span class="hint">⌘/Ctrl + Enter 发送</span>
        </div>
        <div v-if="activeTask" class="card answer">
          <div class="row spread">
            <span class="st" :class="activeTask.status">{{ STATUS_TEXT[activeTask.status] || activeTask.status }}</span>
            <button class="ghost" @click="activeTask = null">收起</button>
          </div>
          <!-- 运行中：状态与步骤实时可见（PRD §10.4） -->
          <div v-if="!isTerminal(activeTask.status)" class="running-note">
            正在处理你的请求…（当前状态：{{ STATUS_TEXT[activeTask.status] || activeTask.status }}）
            <div v-for="s in activeTask.steps || []" :key="s.id" class="step">
              步骤 {{ s.seq }}：{{ s.step_desc }} — {{ s.status }}
            </div>
          </div>
          <!-- 完成：热点类结果渲染为 AIHOT 风格事件卡榜单，其余走文本 -->
          <div v-else-if="activeTask.result_detail?.top_items" class="hotboard">
            <div v-for="(it, i) in activeTask.result_detail.top_items" :key="i" class="hotcard">
              <span class="rank" :class="'r' + (i + 1)">{{ i + 1 }}</span>
              <div class="hotbody">
                <a v-if="it.url" :href="it.url" target="_blank" class="hottitle">{{ it.title }}</a>
                <span v-else class="hottitle">{{ it.title }}</span>
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
          <button v-if="isTerminal(activeTask.status)" class="ghost"
                  @click="openDetail(activeTask.id); tab = 'tasks'">查看完整过程 →</button>
        </div>
        <h2>最近任务</h2>
        <div v-for="t in homeRecent" :key="t.id" class="card task-row" @click="openDetail(t.id); tab = 'tasks'">
          <span class="st" :class="t.status">{{ STATUS_TEXT[t.status] || t.status }}</span>
          <span class="goal">{{ t.goal.slice(0, 60) }}</span>
        </div>
        <p v-if="!homeRecent.length" class="empty">还没有任务。粘贴一个仓库链接试试，例如 https://github.com/asg017/sqlite-vec</p>
      </section>

      <!-- 任务中心 -->
      <section v-if="tab === 'tasks'">
        <h1>任务中心</h1>
        <div class="task-list">
          <div v-for="t in tasks" :key="t.id" class="card task-row" @click="openDetail(t.id)">
            <span class="st" :class="t.status">{{ STATUS_TEXT[t.status] || t.status }}</span>
            <span class="goal">{{ t.goal.slice(0, 70) }}</span>
            <span class="time">{{ new Date(t.created_at * 1000).toLocaleString() }}</span>
          </div>
        </div>
        <div v-if="detail" class="detail card">
          <div class="row spread">
            <h2>#{{ detail.id }} <span class="st" :class="detail.status">{{ STATUS_TEXT[detail.status] }}</span></h2>
            <div>
              <button v-if="!isTerminal(detail.status)" @click="cancelTask(detail.id)">取消</button>
              <button v-if="['failed','cancelled','partial'].includes(detail.status)" @click="retryTask(detail.id)">重试</button>
              <button class="ghost" @click="detail = null">关闭</button>
            </div>
          </div>
          <p class="goal-full">{{ detail.goal }}</p>
          <div v-for="s in detail.steps" :key="s.id" class="step">
            步骤 {{ s.seq }}：{{ s.step_desc }} — {{ s.status }}
            <span v-if="s.error" class="err">{{ s.error }}</span>
          </div>
          <div v-if="detail.cancel_requested && !isTerminal(detail.status)" class="warn-note">已收到取消请求，将在当前步骤停止后生效</div>
          <pre v-if="detail.result_summary" class="result">{{ detail.result_summary }}</pre>
          <pre v-if="detail.error" class="result err">{{ detail.error }}</pre>
          <details v-if="detail.result_detail">
            <summary>完整报告（事实与推测已分级）</summary>
            <pre class="result">{{ JSON.stringify(detail.result_detail, null, 2) }}</pre>
          </details>
        </div>
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

        <div class="card" v-if="pendingApprovals.length">
          <b>待审批</b>
          <div v-for="a in pendingApprovals" :key="a.id" class="row spread approval">
            <span>{{ a.reason }}（任务 #{{ a.task_id }}）</span>
            <span>
              <button class="primary" @click="decideApproval(a.id, 'approved')">批准</button>
              <button @click="decideApproval(a.id, 'rejected')">拒绝</button>
            </span>
          </div>
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
.approval span { font-size: 13px; }
</style>
