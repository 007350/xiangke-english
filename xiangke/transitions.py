"""Let the local model compose a short transition from approved clauses."""

from __future__ import annotations

import json
from itertools import product


# Each event has its own allowed meaning. Only the program supplies the lesson text.
_CLAUSES = {
    "next": (
        ("这一步想对了", "对，你找到线索了", "对，就是这个"),
        ("接着看下一问", "咱们顺着这个线索往下想", "再往下想一步"),
    ),
    "hint": (
        ("先别急", "卡住了也没关系", "咱们慢慢来"),
        ("我们一起理一理", "先找找这句话里的线索", "我陪你再看看这一问"),
    ),
    "early": (
        ("最后一空你选对了", "你已经想到最后一空了，而且答对了", "你说的是最后一空的正确答案"),
        ("我们回头看看理由", "咱们再找找句子里的线索", "再想想这一步是怎么判断的"),
    ),
}
_EVENTS = {
    "next": "答对了当前小问，马上问下一小问",
    "hint": "孩子在当前小问卡住了，马上给提示",
    "early": "孩子提前答对最后一空，马上接回当前小问",
}


def transition(event: str, said: str, ask: str, write, fallback: str) -> str:
    if write is None:
        return fallback
    heads, tails = _CLAUSES[event]
    grammar = (
        f"root ::= head \"，\" tail \"。\"\n"
        f"head ::= {' | '.join(map(_literal, heads))}\n"
        f"tail ::= {' | '.join(map(_literal, tails))}"
    )
    context = json.dumps({"情况": _EVENTS[event], "孩子刚说": said[:100], "接下来问": ask[:120]}, ensure_ascii=False)
    try:
        text = write("你在给小学生讲题。根据对话选最贴切的短句承接，接下来会由老师继续提问或给提示。",
                     context, grammar, 48).strip()
    except (OSError, RuntimeError, ValueError, KeyError, AttributeError):
        return fallback
    allowed = {f"{head}，{tail}。" for head, tail in product(heads, tails)}
    return text if text in allowed else fallback


def _literal(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)
