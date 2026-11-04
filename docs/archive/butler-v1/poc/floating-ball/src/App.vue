<script setup lang="ts">
// PoC-1/2/7 前端：按窗口 label 渲染悬浮球（ball）或控制台（panel）
import { onMounted, onUnmounted, ref } from "vue";
import { getCurrentWindow, Window } from "@tauri-apps/api/window";
import { invoke } from "@tauri-apps/api/core";
import { isPermissionGranted, requestPermission, sendNotification } from "@tauri-apps/plugin-notification";
import { enable, disable, isEnabled } from "@tauri-apps/plugin-autostart";

const label = ref("");
const state = ref("idle");
const coreUp = ref<boolean | null>(null);
const uptime = ref("");
const tokenTail = ref("");
const autostartOn = ref<boolean | null>(null);
const ballWin = ref<Window | null>(null);
const passThrough = ref(false);

const STATES = ["idle", "thinking", "executing", "waiting", "success", "failed"];
const STATE_TEXT: Record<string, string> = {
  idle: "空闲",
  thinking: "思考",
  executing: "执行",
  waiting: "待确认",
  success: "成功",
  failed: "失败",
};
let timer: number | undefined;

async function startDrag() {
  // 系统级拖动：Tauri 原生 startDragging，无需手写坐标计算
  await getCurrentWindow().startDragging();
}

async function pollHealth() {
  try {
    const token = await invoke<string>("get_token");
    tokenTail.value = token.slice(-6);
    const res = await fetch("http://127.0.0.1:18321/health", {
      headers: { "X-Aster-Token": token },
    });
    const body = await res.json();
    coreUp.value = body.ok === true;
    uptime.value = String(body.uptime ?? "");
  } catch {
    coreUp.value = false;
  }
}

async function togglePassThrough() {
  if (!ballWin.value) return;
  passThrough.value = !passThrough.value;
  await ballWin.value.setIgnoreCursorEvents(passThrough.value);
}

async function testNotify() {
  let granted = await isPermissionGranted();
  if (!granted) granted = (await requestPermission()) === "granted";
  if (granted) {
    await sendNotification({ title: "Aster PoC", body: "通知通道验证（未签名场景行为记录）" });
  }
}

async function toggleAutostart() {
  if (autostartOn.value) {
    await disable();
  } else {
    await enable();
  }
  autostartOn.value = await isEnabled();
}

let poller: number | undefined;

onMounted(async () => {
  label.value = (await getCurrentWindow()).label;
  if (label.value === "ball") {
    // 悬浮球窗口：页面背景必须透明，否则 transparent 配置无效
    document.documentElement.style.background = "transparent";
    document.body.style.background = "transparent";
    timer = window.setInterval(() => {
      const i = STATES.indexOf(state.value);
      state.value = STATES[(i + 1) % STATES.length];
    }, 1500);
  } else {
    ballWin.value = await Window.getByLabel("ball");
    autostartOn.value = await isEnabled().catch(() => null);
    await pollHealth();
    poller = window.setInterval(pollHealth, 2000);
  }
});

onUnmounted(() => {
  if (timer) clearInterval(timer);
  if (poller) clearInterval(poller);
});
</script>

<template>
  <!-- 悬浮球窗口：六态循环 + 整窗可拖 -->
  <div v-if="label === 'ball'" class="stage">
    <div class="ball" :class="state" @pointerdown.prevent="startDrag">
      <span class="core"></span>
    </div>
    <div class="tag">{{ STATE_TEXT[state] }}</div>
  </div>

  <!-- 控制台窗口 -->
  <div v-else class="panel">
    <h2>Aster PoC 控制台</h2>

    <section>
      <h3>核心服务（sidecar）</h3>
      <p>
        状态：<b :class="coreUp ? 'ok' : 'bad'">{{ coreUp === null ? "…" : coreUp ? "运行中" : "已断开" }}</b>
        <span v-if="coreUp"> · uptime {{ uptime }}s</span>
        <br />token 尾 6 位：{{ tokenTail || "…" }}
      </p>
      <p class="hint">验证：手动 kill -9 python 进程，此栏应 5 秒内回到“运行中”。</p>
    </section>

    <section>
      <h3>悬浮球</h3>
      <button @click="togglePassThrough">
        {{ passThrough ? "恢复鼠标事件" : "开启点击穿透" }}
      </button>
      <span class="hint">穿透后悬浮球完全透明于鼠标（点击落到下层窗口）。</span>
    </section>

    <section>
      <h3>系统通知（PoC-7）</h3>
      <button @click="testNotify">发送测试通知</button>
    </section>

    <section>
      <h3>开机自启（PoC-7）</h3>
      <button @click="toggleAutostart">
        {{ autostartOn ? "关闭自启" : "开启自启" }}
      </button>
      <span class="hint">当前：{{ autostartOn === null ? "未知" : autostartOn ? "已开启" : "未开启" }}（dev 模式注册的是调试二进制，打包后需复验）</span>
    </section>
  </div>
</template>

<style scoped>
/* ===== 悬浮球 ===== */
.stage {
  width: 100vw;
  height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  background: transparent;
  user-select: none;
}
.ball {
  width: 72px;
  height: 72px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: grab;
  border: 2px solid transparent;
  transition: background 0.3s, border-color 0.3s;
  -webkit-backdrop-filter: blur(6px);
}
.ball .core {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #fff;
  opacity: 0.9;
}
/* 六态配色 */
.ball.idle { background: rgba(70, 130, 240, 0.75); }
.ball.thinking { background: rgba(150, 110, 240, 0.75); animation: pulse 1s ease-in-out infinite; }
.ball.executing { background: rgba(240, 170, 60, 0.75); }
.ball.executing .core { animation: spin 0.8s linear infinite; }
.ball.waiting { background: rgba(240, 120, 90, 0.75); animation: pulse 1.4s ease-in-out infinite; }
.ball.success { background: rgba(90, 190, 120, 0.8); }
.ball.failed { background: rgba(230, 80, 80, 0.8); }
.tag {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.85);
  background: rgba(0, 0, 0, 0.35);
  padding: 1px 8px;
  border-radius: 8px;
}
@keyframes pulse { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.12); } }
@keyframes spin { to { transform: rotate(360deg); border-radius: 4px; } }

/* ===== 控制台 ===== */
.panel { padding: 18px 20px; max-width: 360px; }
.panel h2 { font-size: 17px; margin: 0 0 12px; }
.panel h3 { font-size: 14px; margin: 0 0 6px; }
.panel section { margin-bottom: 18px; }
.panel p { margin: 4px 0; font-size: 13px; }
button {
  border-radius: 8px;
  border: 1px solid #58a6ff;
  background: transparent;
  color: inherit;
  padding: 5px 12px;
  font-size: 13px;
  cursor: pointer;
  margin-right: 8px;
}
button:hover { background: rgba(88, 166, 255, 0.15); }
.ok { color: #3fb950; }
.bad { color: #f85149; }
.hint { display: block; font-size: 12px; opacity: 0.6; margin-top: 4px; }
</style>

<style>
:root {
  color-scheme: light dark;
  font-family: -apple-system, "PingFang SC", "Helvetica Neue", sans-serif;
}
/* 控制台窗口正常背景；球窗口在 onMounted 里动态置透明 */
body { background: rgba(245, 245, 250, 0.98); margin: 0; }
@media (prefers-color-scheme: dark) {
  body { background: rgba(30, 32, 40, 0.98); color: #e6e6e6; }
}
</style>
