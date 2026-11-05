# 待办领域逻辑：语法解析与排序（API 与对话路由共用）
# 设计来源：docs/superpowers/specs/2026-11-05-todo-management-design.md
#   - todo.txt 哲学：结构字段只留 priority/due，其余归自由文本
#   - taskwarrior 调度语义：due 驱动排序（逾期>今日>未来>无），排序由系统算
import re
from datetime import date, timedelta

# 优先级：(A) 或 !A（A-Z 单字母）；日期：@2026-11-08 / 截止2026-11-08 / 今天|明天|后天
_PRI_RE = re.compile(r"^\(?([A-Z])\)[\s]+|^!([A-Z])[\s]+")
_DUE_MD_RE = re.compile(r"(?:@|截止[:：]?)(\d{4}-\d{2}-\d{2})")
_DUE_WORDS = {"今天": 0, "明天": 1, "后天": 2}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_content(text: str) -> dict:
    """从自由文本抽出优先级与截止日，剩余为正文（抽不到不强求，全可空）"""
    content = text.strip()
    priority = None
    m = _PRI_RE.match(content)
    if m:
        priority = m.group(1) or m.group(2)
        content = content[m.end():].strip()
    due = None
    m = _DUE_MD_RE.search(content)
    if m:
        if _DATE_RE.match(m.group(1)):
            due = m.group(1)
            content = (content[:m.start()] + content[m.end():]).strip()
    else:
        for word, offset in _DUE_WORDS.items():
            if word in content:
                due = (date.today() + timedelta(days=offset)).isoformat()
                content = content.replace(word, "", 1).strip()
                break
    return {"content": content or text.strip(), "priority": priority, "due": due}


def _due_rank(todo: dict, today: str) -> int:
    """学 taskwarrior：逾期最急、今日次之、未来再次、无日期垫底"""
    due = todo.get("due")
    if not due:
        return 3
    if due < today:
        return 0
    if due == today:
        return 1
    return 2


def sort_key(todo: dict, today: str | None = None):
    """进行中排序：due 档位 → 优先级（A 最前，无优先级垫底）→ 创建时间"""
    today = today or date.today().isoformat()
    return (
        _due_rank(todo, today),
        todo.get("priority") or "[",  # "[" 在 Z 之后：无优先级排最后
        todo.get("created_at") or 0,
    )
