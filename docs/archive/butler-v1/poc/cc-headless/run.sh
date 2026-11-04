#!/bin/bash
# PoC-5：Claude Code headless 七项验证（⚠️ 运行会消耗 claude 账户费用，预估 <$0.5）
# 用法：cd testrepo 的父目录执行 bash run.sh
set -u
REPO="$(cd "$(dirname "$0")/testrepo" && pwd)"
PASS=0; FAIL=0
ok()   { echo "PASS $1"; PASS=$((PASS+1)); }
bad()  { echo "FAIL $1 —— $2"; FAIL=$((FAIL+1)); }

echo "=== 1) readonly 分析任务 + 5) 费用读取 + 6) session_id（一次调用验证三件事）==="
cd "$REPO"
OUT=$(claude -p "列出本仓库两个 py 文件中的全部函数名，不要读其他文件" \
  --output-format json \
  --allowedTools "Read" "Glob" "Grep" \
  --permission-mode dontAsk 2>/tmp/cc-err.log)
RC=$?
if [ $RC -eq 0 ]; then
  echo "$OUT" | grep -q "greet" && echo "$OUT" | grep -q "add" \
    && ok "readonly 任务返回正确结果" || bad "readonly 结果" "未包含预期函数名"
  COST=$(echo "$OUT" | /usr/bin/python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('total_cost_usd'))" 2>/dev/null)
  SESS=$(echo "$OUT" | /usr/bin/python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('session_id'))" 2>/dev/null)
  MODEL=$(echo "$OUT" | /usr/bin/python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('model',''))" 2>/dev/null)
  echo "    （实际后端模型: ${MODEL:-未知}）"
  [ -n "$COST" ] && [ "$COST" != "None" ] && ok "total_cost_usd 读取成功: \$$COST" || bad "费用读取" "字段缺失"
  [ -n "$SESS" ] && [ "${SESS:0:8}" != "None" ] && ok "session_id 获取: ${SESS:0:12}…" || bad "session_id" "字段缺失"
else
  bad "readonly 任务" "exit=$RC $(head -2 /tmp/cc-err.log)"
fi

echo "=== 1b) readonly 不改文件（git status 应干净）==="
if [ -z "$(git status --porcelain)" ]; then ok "工作区未被修改"; else bad "工作区污染" "$(git status --porcelain | head -3)"; fi

echo "=== 2) 结构化输出 --json-schema ===="
cat > /tmp/cc-schema.json <<'EOF'
{"type":"object","properties":{"functions":{"type":"array","items":{"type":"string"}}},"required":["functions"]}
EOF
OUT2=$(claude -p "列出 utils.py 中的函数名" --output-format json --json-schema /tmp/cc-schema.json \
  --allowedTools "Read" 2>/dev/null)
echo "$OUT2" | grep -q '"functions"' && ok "结构化输出符合 schema" || bad "结构化输出" "$(echo "$OUT2" | head -c 120)"

echo "=== 3) write 任务（白名单目录内写 + 本地验证执行）===="
OUT3=$(claude -p "在 utils.py 增加 multiply 函数；新建 test_utils.py 用 assert 覆盖 add 与 multiply；然后运行 python3 test_utils.py 确认通过" \
  --output-format json \
  --allowedTools "Read" "Write" "Edit" "Bash(python3:*)" \
  --permission-mode dontAsk 2>/dev/null)
RC3=$?
if [ $RC3 -eq 0 ] && grep -q "multiply" "$REPO/utils.py" && [ -f "$REPO/test_utils.py" ] \
   && "$REPO/../../.venv/bin/python" "$REPO/test_utils.py" 2>/dev/null; then
  ok "write 任务：代码已写入且测试真跑通"
else
  bad "write 任务" "exit=$RC3；multiply in utils.py=$(grep -c multiply "$REPO/utils.py")；test 文件存在=$([ -f "$REPO/test_utils.py" ] && echo yes || echo no)"
fi
git -C "$REPO" checkout -q . 2>/dev/null; rm -f "$REPO/test_utils.py"  # 还原，供后续复跑

echo "=== 4) 取消语义（SIGTERM → 退出码 143）===="
claude -p "用 python3 逐个打印 1 到 10000000 之间的全部质数，一个不少地输出完，不要提前停止" --allowedTools "Bash(python3:*)" > /tmp/cc-cancel.log 2>&1 &
CPID=$!
sleep 8
kill -TERM $CPID 2>/dev/null
wait $CPID; CRC=$?
[ $CRC -eq 143 ] && ok "SIGTERM → 退出码 143" || bad "取消语义" "exit=$CRC（143 以外的 15x 也可能是信号终止，人工判断）"

echo ""
echo "===== 结果: PASS=$PASS FAIL=$FAIL ====="
echo "（--resume 续跑验证见 README：claude -p '补充说明' --resume <上面的 session_id>）"
