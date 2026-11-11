#!/bin/bash
# 平台自改守护脚本：合并已批准的自改后，重建前端 → 重启服务 → 健康检查 → 回写任务状态
# 失败自动回滚合并并通知（git revert），保证平台不会死在自己手里
# 用法：bash tools/selfapply.sh <task_id>
set -u
TASK_ID="$1"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"

notify() {  # 通知 + 任务状态回写（kind/task_id/text）
  "$PY" - "$1" "$2" "$3" <<'PYEOF'
import sys, sqlite3, time, subprocess
kind, tid, text = sys.argv[1], int(sys.argv[2]), sys.argv[3]
conn = sqlite3.connect("data/aster.db")
conn.execute("INSERT INTO notifications(kind, text, ref_task_id, created_at) VALUES(?,?,?,?)",
             (kind, text, tid, int(time.time())))
if kind != "alarm":
    conn.execute("UPDATE tasks SET status=?, result_summary=COALESCE(result_summary,'')||?, "
                 "updated_at=? WHERE id=?",
                 ("completed" if kind == "task_done" else "failed",
                  f"\n[{text[:120]}]", int(time.time()), tid))
conn.commit(); conn.close()
PYEOF
}

sleep 3
cd "$ROOT/web" && pnpm build > /tmp/selfapply_build.log 2>&1 || {
  notify "task_failed" "$TASK_ID" "自改应用失败：前端构建未过，已回滚合并"
  git revert --no-edit HEAD > /dev/null 2>&1
  exit 1
}

# 重启服务（杀监听进程再拉起；脚本进程组独立，不受影响）
kill $(lsof -ti :8317 -sTCP:LISTEN) 2>/dev/null
sleep 1
cd "$ROOT" && nohup "$PY" server/run.py > /tmp/aster_server.log 2>&1 &
sleep 5

# 健康检查（30 秒窗口）
for i in $(seq 1 6); do
  CODE=$(curl -s --noproxy '*' -o /dev/null -w "%{http_code}" http://127.0.0.1:8317/api/system/status 2>/dev/null)
  if [ "$CODE" = "200" ]; then
    notify "task_done" "$TASK_ID" "平台自改已应用并重启成功，服务健康"
    exit 0
  fi
  sleep 5
done

# 未恢复：回滚合并，尝试用旧代码重启，人工介入通知
cd "$ROOT" && git revert --no-edit HEAD > /dev/null 2>&1
kill $(lsof -ti :8317 -sTCP:LISTEN) 2>/dev/null; sleep 1
cd "$ROOT/web" && pnpm build > /dev/null 2>&1
cd "$ROOT" && nohup "$PY" server/run.py > /tmp/aster_server.log 2>&1 &
sleep 6
notify "task_failed" "$TASK_ID" "自改重启后健康检查失败，已自动回滚合并并恢复服务——需人工检查 /tmp/selfapply.log"
