<script setup>
// Aster 平台 Phase 1 前端：四个一级页面（PRD §10.2）
// 视觉基线遵循 PRD §10.7：中性色为主、状态色语义化、清晰层级、无装饰性动画
import { computed, nextTick, onMounted, ref, watch } from "vue";
import MarkdownIt from "markdown-it";
import ResultView from "./ResultView.vue";

// 对话流里的 markdown 渲染（与 ResultView 同规则：html:false 防注入）
const md = new MarkdownIt({ html: false, linkify: true });
const mdHtml = (s) => md.render(s || "");

const tab = ref("home");
const tasks = ref([]);
const caps = ref([]);
const memories = ref([]);
const pendingApprovals = ref([]);
const sys = ref(null);
const goalText = ref("");
const detail = ref(null);
const runningTask = ref(null);
// 多轮对话线程（与任务卡并存：任务型输入走任务链，对话型挂线程）
const convId = ref(null);
const thread = ref([]);
function newConversation() { convId.value = null; thread.value = []; }
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
// 历史任务全部收在任务中心；首页只保留对话（用户 2026-11-05 定稿）
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
  // 重试后继续跟踪该任务，对话流任务卡实时刷新
  taskCards.value[tid] = { id: tid, goal: taskCards.value[tid]?.goal || "", status: "pending" };
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
  goalText.value = "";
  // 单一入口：服务端分类——任务型在流里开任务卡气泡，对话/待办型进线程
  const r = await api("/chat", { method: "POST", body: JSON.stringify({ message: goal, conversation_id: convId.value || undefined }) });
  if (r.type === "task") {
    thread.value.push({ role: "user", content: goal }, { role: "task", id: r.id });
    taskCards.value[r.id] = { id: r.id, goal, status: "pending" };
    await refresh();
    watchTask(r.id);
  } else if (r.type === "chat") {
    convId.value = r.conversation_id;
    thread.value.push({ role: "user", content: goal }, { role: "assistant", content: r.reply });
  } else if (r.type === "todo") {
    // 待办/闹钟/定时回执进对话线程，同时刷新待办与调度列表
    thread.value.push({ role: "user", content: goal }, { role: "assistant", content: r.reply });
    loadTodos(); loadSchedules();
  } else {
    thread.value.push({ role: "user", content: goal },
                      { role: "assistant", content: r.error || "发送失败" });
  }
}

// 对话流里的任务卡：watchTask 把状态/结果实时写进这里（模板按 thread 里的 id 取）
const taskCards = ref({});
function watchTask(id) {
  if (runningTask.value) runningTask.value.close();
  const es = new EventSource(`/api/tasks/${id}/events`);
  runningTask.value = es;
  es.onmessage = (ev) => {
    if (ev.data === "[DONE]") {
      es.close(); runningTask.value = null;
      api(`/tasks/${id}`).then((d) => { taskCards.value[id] = d; });
      refresh();
    } else {
      api(`/tasks/${id}`).then((d) => { taskCards.value[id] = d; });
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

// 待办管理（学 todo.txt/taskwarrior，设计见 docs/superpowers/specs/2026-11-05-todo-management-design.md）
const todoList = ref({ pending: [], done: [] });
const todoText = ref("");
const todoFilter = ref("open");
const todoQuery = ref("");
const editingTodo = ref(null);   // { id, content } 就地编辑
const todoEdit = ref("");
async function loadTodos() { todoList.value = await api("/todos"); }
async function addTodo() {
  const t = todoText.value.trim();
  if (!t) return;
  todoText.value = "";
  await api("/todos", { method: "POST", body: JSON.stringify({ content: t }) });
  loadTodos();
}
async function toggleTodo(t) {
  await api(`/todos/${t.id}`, { method: "PATCH", body: JSON.stringify({ done: !t.done }) });
  loadTodos();
}
async function delTodo(id) { await api(`/todos/${id}`, { method: "DELETE" }); loadTodos(); }
function startEdit(t) { editingTodo.value = t.id; todoEdit.value = t.content; }
async function saveEdit(id) {
  const t = todoEdit.value.trim();
  if (t) await api(`/todos/${id}`, { method: "PATCH", body: JSON.stringify({ content: t }) });
  editingTodo.value = null;
  loadTodos();
}
// 转任务：待办交给 agent 执行（结果不自动勾选——执行完成≠达到目的，由用户判断）
async function runTodo(t) {
  const r = await api(`/todos/${t.id}/run`, { method: "POST" });
  if (r.error) return alert(r.error);
  await loadTodos();
  refresh();
}
function dueBadge(t) {
  if (!t.due) return null;
  const today = new Date().toISOString().slice(0, 10);
  return { text: t.due, cls: t.due < today ? "overdue" : t.due === today ? "today" : "" };
}
// 过滤在客户端做（待办量级小），搜索按 todo.txt 哲学切自由文本
const todoShown = computed(() => {
  const q = todoQuery.value.trim();
  const match = (t) => !q || (t.content + (t.priority || "") + (t.due || "")).includes(q);
  if (todoFilter.value === "open") return todoList.value.pending.filter(match);
  if (todoFilter.value === "done") return todoList.value.done.filter(match);
  return [...todoList.value.pending, ...todoList.value.done].filter(match);
});

// 定时调度与通知（APScheduler 后端的界面侧）
const schedules = ref([]);
const notifications = ref([]);
const notifOpen = ref(false);
const schedForm = ref({ kind: "task", goal: "", when: "" });
async function loadSchedules() { schedules.value = await api("/schedules"); }
async function loadNotifications() { notifications.value = await api("/notifications?limit=20"); }
async function markAllRead() {
  await api("/notifications/read", { method: "POST", body: JSON.stringify({ ids: [] }) });
  loadNotifications();
}
const unreadCount = computed(() => notifications.value.filter((n) => !n.read).length);
// 触发描述（与后端 describe_trigger 同义，展示用）
function schedDesc(s) {
  const t = JSON.parse(s.trigger_json || "{}");
  if (t.type === "cron") return `每天 ${String(t.hour).padStart(2, "0")}:${String(t.minute || 0).padStart(2, "0")}`;
  if (t.type === "interval") return t.minutes != null ? `每隔 ${t.minutes} 分钟` : `每隔 ${t.hours} 小时`;
  if (t.run_at) return new Date(t.run_at * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  return "";
}
async function addSchedule() {
  const { kind, goal, when } = schedForm.value;
  if (!goal.trim() || !when.trim()) return alert("目标和时间都要填");
  const r = await api("/schedules", { method: "POST", body: JSON.stringify({ kind, goal, when: `${goal} ${when}` }) });
  if (r.error) return alert(r.error);
  schedForm.value = { kind: "task", goal: "", when: "" };
  loadSchedules();
}
async function toggleSched(s) {
  await api(`/schedules/${s.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !s.enabled }) });
  loadSchedules();
}
async function delSched(id) { await api(`/schedules/${id}`, { method: "DELETE" }); loadSchedules(); }

// 对话流自动滚底（学 chatgpt-web：新消息永远在视野内）
const chatScroll = ref(null);
watch(() => thread.value.length, async () => {
  await nextTick();
  if (chatScroll.value) chatScroll.value.scrollTop = chatScroll.value.scrollHeight;
});

onMounted(() => {
  refresh();
  loadTodos();
  loadSchedules();
  loadNotifications();
  // 恢复最近会话（多轮上下文连续；任务卡历史在任务中心）
  api("/chat/latest").then((d) => {
    if (d && d.conversation_id) {
      convId.value = d.conversation_id;
      thread.value = (d.messages || []).map((m) => ({ role: m.role, content: m.content }));
    }
  });
  // 待审批/任务状态/通知自动刷新：否则审批角标与闹钟不出现（实测反馈）
  setInterval(() => { refresh(); loadNotifications(); }, 5000);
});
// 切到待办页时重拉：在首页对话里加的待办否则看不到（实测踩坑）
watch(tab, (t) => { if (t === "todos") loadTodos(); if (t === "sched") loadSchedules(); });
</script>

<template>
  <div class="shell">
    <nav class="side">
      <div class="brand">Aster</div>
      <button v-for="t in [['home','首页'],['todos','待办'],['sched','定时'],['tasks','任务中心'],['caps','能力中心'],['disc','发现与评估'],['evo','成长与进化'],['settings','设置']]":key="t[0]"
              :class="{ on: tab === t[0] }" @click="tab = t[0]">{{ t[1] }}</button>
      <!-- 通知铃铛：闹钟/定时任务完成推送（点开看最近通知） -->
      <div class="bell-wrap">
        <button class="bell" :class="{ on: notifOpen }" @click="notifOpen = !notifOpen">
          通知 <span v-if="unreadCount" class="badge">{{ unreadCount }}</span>
        </button>
        <div v-if="notifOpen" class="notif-pop">
          <div class="row spread" style="padding:6px 10px">
            <b style="font-size:13px">通知</b>
            <button class="ghost tiny" @click="markAllRead">全部已读</button>
          </div>
          <div v-for="n in notifications" :key="n.id" class="notif-item" :class="{ unread: !n.read }">
            <div class="notif-text">{{ n.text }}</div>
            <div class="hint">{{ new Date(n.created_at * 1000).toLocaleString() }}</div>
          </div>
          <p v-if="!notifications.length" class="empty" style="padding:10px">还没有通知</p>
        </div>
      </div>
      <div v-if="sys" class="side-status" :class="{ warn: sys.paused }">
        {{ sys.paused ? "已暂停" : sys.provider_ready ? "运行中" : "模型未配置" }}
        <span v-if="pendingApprovals.length" class="badge">{{ pendingApprovals.length }} 待审批</span>
      </div>
    </nav>

    <main>
      <!-- 首页 = 主战场：输入 → 执行流程 → 审批 → 结果，一条流全在这（用户实测反馈重构） -->
      <!-- 首页 = 纯对话（学 chatgpt-web/lobe-chat 三段式：消息流+底部输入+自动滚底） -->
      <section v-if="tab === 'home'" class="chat-home">
        <div class="chat-top">
          <span class="chat-title">对话</span>
          <span class="hint">任务类输入自动走能力 · 结果以任务卡呈现在流里 · 全量历史在任务中心</span>
          <button v-if="convId" class="ghost" @click="newConversation">新对话</button>
        </div>
        <div class="chat-scroll" ref="chatScroll">
          <div v-if="!thread.length" class="chat-empty">
            <p>试试：</p>
            <p>· 粘贴 GitHub 仓库链接，如 https://github.com/asg017/sqlite-vec</p>
            <p>· 深度调研：MCP 生态 2026 现状</p>
            <p>· 帮我加个待办：明天整理周报 (A)</p>
            <p>· 提醒我 30 分钟后休息 · 每天早上 9 点给我热点日报</p>
          </div>
          <template v-for="(m, i) in thread" :key="i">
            <div v-if="m.role === 'user'" class="bub user">{{ m.content }}</div>
            <div v-else-if="m.role === 'assistant'" class="bub bot md" v-html="mdHtml(m.content)"></div>
            <!-- 任务卡气泡：状态实时刷新；审批内联（保持"审批不离开首页"） -->
            <div v-else-if="m.role === 'task' && taskCards[m.id]" class="task-bub">
              <div class="row spread">
                <span class="st" :class="taskCards[m.id].status">
                  {{ taskCards[m.id].status === 'executing' ? '▸ 执行中' :
                     STATUS_TEXT[taskCards[m.id].status] || taskCards[m.id].status }}
                </span>
                <button class="ghost tiny" @click="tab = 'tasks'; toggleDetail(m.id)">详情</button>
              </div>
              <div class="task-goal">{{ taskCards[m.id].goal }}</div>
              <div v-for="a in approvalsFor(m.id)" :key="a.id" class="approval-inline">
                <span>{{ a.reason }}</span>
                <span>
                  <button class="primary tiny" @click="approveAndRetry(a.id, m.id)">批准并继续</button>
                  <button class="tiny" @click="rejectApproval(a.id)">拒绝</button>
                </span>
              </div>
              <template v-if="isTerminal(taskCards[m.id].status)">
                <div v-if="taskCards[m.id].result_summary" class="md task-sum"
                     v-html="mdHtml(String(taskCards[m.id].result_summary).slice(0, 600))"></div>
                <div class="row" v-if="taskCards[m.id].result_ref">
                  <a :href="`/api/tasks/${m.id}/file`" download><button class="tiny">下载产物</button></a>
                </div>
                <div class="row" v-if="['failed','cancelled','partial'].includes(taskCards[m.id].status)">
                  <button class="tiny" @click="retryTask(m.id); watchTask(m.id)">重试</button>
                </div>
              </template>
            </div>
          </template>
        </div>
        <div class="chat-input">
          <textarea v-model="goalText" rows="2"
                    placeholder="描述目标 / 提问 / 加待办 / 设提醒…（Enter 发送，Shift+Enter 换行）"
                    @keydown.enter.exact.prevent="submitGoal"></textarea>
          <button class="primary" @click="submitGoal">发送</button>
        </div>
      </section>

      <!-- 待办管理：学 todo.txt（自由文本+语法）与 taskwarrior（due 驱动排序） -->
      <section v-if="tab === 'todos'">
        <div class="row spread">
          <h1>待办</h1>
          <div class="row" style="margin:0">
            <input v-model="todoQuery" style="width:180px" placeholder="搜索待办" />
            <button v-for="f in [['open','进行中'],['done','已完成'],['all','全部']]":key="f[0]"
                    :class="{ ghost: todoFilter !== f[0] }" @click="todoFilter = f[0]">{{ f[1] }}</button>
          </div>
        </div>
        <div class="card row">
          <input v-model="todoText" style="flex:1" placeholder="新增待办，支持语法：(A) 优先级 · @2026-11-08 或 截止2026-11-08 或 明天"
                 @keydown.enter="addTodo" />
          <button class="primary" @click="addTodo">添加</button>
        </div>
        <div v-for="t in todoShown" :key="t.id" class="card todo-row" :class="{ done: t.done }">
          <input type="checkbox" :checked="t.done" @change="toggleTodo(t)" />
          <span v-if="t.priority" class="pri">{{ t.priority }}</span>
          <span v-if="dueBadge(t)" class="due" :class="dueBadge(t).cls">{{ dueBadge(t).text }}</span>
          <template v-if="editingTodo === t.id">
            <input v-model="todoEdit" style="flex:1" @keydown.enter="saveEdit(t.id)"
                   @keydown.esc="editingTodo = null" />
            <button class="primary" @click="saveEdit(t.id)">保存</button>
          </template>
          <template v-else>
            <span class="todo-content" @dblclick="startEdit(t)">{{ t.content }}</span>
            <span v-if="t.done" class="hint">完成于 {{ new Date((t.done_at || 0) * 1000).toLocaleDateString() }}</span>
            <span v-if="t.task_id" class="tag">任务#{{ t.task_id }}</span>
            <span class="row-actions">
              <button v-if="!t.done" class="ghost" @click="startEdit(t)">编辑</button>
              <button v-if="!t.done && !t.task_id" class="ghost" @click="runTodo(t)">转任务</button>
              <button class="ghost danger" @click="delTodo(t.id)">删除</button>
            </span>
          </template>
        </div>
        <p v-if="!todoShown.length" class="empty">没有符合条件的待办</p>
      </section>

      <!-- 定时：定时任务与闹钟（APScheduler：每天/每隔/一次性三类触发） -->
      <section v-if="tab === 'sched'">
        <div class="row spread">
          <h1>定时</h1>
          <span class="hint">定时任务到点照常走门禁（L1 需批准）· 闹钟到点推通知</span>
        </div>
        <div class="card row">
          <select v-model="schedForm.kind">
            <option value="task">定时任务</option>
            <option value="alarm">闹钟</option>
          </select>
          <input v-model="schedForm.goal" style="flex:1"
                 :placeholder="schedForm.kind === 'task' ? '任务目标，如：给我热点日报' : '提醒内容，如：开会'" />
          <input v-model="schedForm.when" style="width:220px"
                 placeholder="时间：每天 9:30 / 每 30 分钟 / 明天 15 点" @keydown.enter="addSchedule" />
          <button class="primary" @click="addSchedule">添加</button>
        </div>
        <div v-for="s in schedules" :key="s.id" class="card task-row">
          <span class="tag">{{ s.kind === 'task' ? '任务' : '闹钟' }}</span>
          <span class="goal">{{ s.goal.slice(0, 60) }}</span>
          <span class="hint">{{ schedDesc(s) }}</span>
          <span v-if="s.next_run_at" class="hint">下次 {{ new Date(s.next_run_at * 1000).toLocaleString() }}</span>
          <span v-if="s.last_task_id" class="tag">最近 #{{ s.last_task_id }}</span>
          <span class="row-actions">
            <button class="ghost" @click="toggleSched(s)">{{ s.enabled ? '停用' : '启用' }}</button>
            <button class="ghost danger" @click="delSched(s.id)">删除</button>
          </span>
        </div>
        <p v-if="!schedules.length" class="empty">还没有定时。试试上面加一个"每天 9:30 热点日报"</p>
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
            <ResultView :task="detail" :pending="pendingApprovals"
                        @approve="approveAndRetry" @reject="(aid) => rejectApproval(aid)" />
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
/* 首页对话区：占满视口高三段式（消息流滚动 + 输入常底），学 chatgpt-web */
.chat-home { display: flex; flex-direction: column; height: calc(100vh - 56px); margin: -28px -36px; padding: 0 36px; }
.chat-top { display: flex; align-items: center; gap: 12px; padding: 14px 0 10px; border-bottom: 1px solid var(--line); }
.chat-top .chat-title { font-weight: 700; font-size: 16px; }
.chat-top .hint { flex: 1; }
.chat-scroll { flex: 1; overflow-y: auto; padding: 16px 4px; display: flex; flex-direction: column; gap: 10px; }
.chat-empty { margin: auto; color: var(--dim); text-align: center; }
.bub { max-width: 76%; padding: 9px 14px; border-radius: 14px; font-size: 14px; white-space: pre-wrap; }
.bub.user { align-self: flex-end; background: var(--accent); color: #fff; border-bottom-right-radius: 4px; }
.bub.bot { align-self: flex-start; background: var(--card); border: 1px solid var(--line); border-bottom-left-radius: 4px; }
.bub.bot :is(p, ul, ol) { margin: 4px 0; }
.bub.bot code { background: rgba(127,127,127,.15); border-radius: 4px; padding: 1px 5px; }
.chat-input { display: flex; gap: 10px; align-items: flex-end; padding: 12px 0 18px; border-top: 1px solid var(--line); }
.chat-input textarea { flex: 1; resize: none; }
.task-bub { align-self: flex-start; width: 82%; background: var(--card); border: 1px solid var(--line);
            border-left: 3px solid var(--accent); border-radius: 10px; padding: 10px 14px; }
.task-goal { font-weight: 600; margin: 4px 0; }
.task-sum { font-size: 13px; border-top: 1px dashed var(--line); margin-top: 8px; padding-top: 8px; }
.approval-inline { display: flex; justify-content: space-between; gap: 8px; align-items: center;
                   background: rgba(217,122,6,.08); border-radius: 8px; padding: 8px 10px; margin: 8px 0; font-size: 13px; }
button.tiny, .tiny { font-size: 12px; padding: 3px 10px; }
/* 侧栏通知铃铛 */
.bell-wrap { position: relative; margin-top: auto; }
.bell { width: 100%; text-align: left; }
.bell.on { background: var(--bg); }
.notif-pop { position: absolute; bottom: 36px; left: 0; width: 300px; max-height: 380px; overflow-y: auto;
             background: var(--card); border: 1px solid var(--line); border-radius: 10px;
             box-shadow: 0 8px 24px rgba(0,0,0,.12); z-index: 30; }
.notif-item { padding: 8px 10px; border-top: 1px solid var(--line); }
.notif-item.unread { background: rgba(49,110,243,.05); }
.notif-text { font-size: 13px; white-space: pre-wrap; }
.side-status { margin-top: 0 !important; }
h1 { font-size: 20px; margin: 0 0 16px; } h2 { font-size: 15px; margin: 20px 0 10px; }
textarea, input, select { width: 100%; border: 1px solid var(--line); border-radius: 8px; padding: 10px;
       background: var(--card); color: var(--text); font: inherit; }
/* 勾选框豁免：不吃全宽+内边距（待办/任务行里曾被撑成占满整行的大块，实测反馈） */
input[type="checkbox"] { width: auto; padding: 0; accent-color: var(--accent); }
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
/* 待办：学 taskwarrior 的逾期强调与 todo.txt 的完成沉底 */
.todo-row { display: flex; gap: 10px; align-items: center; padding: 8px 12px; }
.todo-row .todo-content { flex: 1; font-size: 14px; }
.todo-row.done .todo-content { text-decoration: line-through; color: var(--dim); }
.pri { font-size: 12px; font-weight: 700; color: #b7791f;
       background: #fff3d6; border-radius: 6px; padding: 1px 7px; }
.due { font-size: 12px; color: var(--dim); }
.due.overdue { color: #c53030; font-weight: 600; }
.due.today { color: #b7791f; font-weight: 600; }
.row-actions { display: flex; gap: 6px; }
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
