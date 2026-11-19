"""Ask the model what kind of reply the child gave. It never decides right or wrong."""

from __future__ import annotations

import json
import re

from xiangke.reply import LABELS

SYSTEM = (
    "你负责判断小学生对这一问的回复属于哪一类，只选一类：\n"
    "答题：孩子说出了一个答案，不管对错。\n"
    "不会：孩子说不知道、不会、想不出来。\n"
    "提问：孩子在问为什么、是什么意思、怎么做。\n"
    "其他：和题目无关的话。\n"
    '只输出一个 JSON 对象，格式：{"label":"类别"}，不要输出任何其他文字。'
)
GRAMMAR = 'root ::= "{\\"label\\": \\"" ("答题" | "不会" | "提问" | "其他") "\\"}"'
_ONE_WORD = re.compile(r"[A-Za-z][A-Za-z']*")
_WHY = re.compile(r"为什么|为啥|为何")
_QUESTION = re.compile(r"什么意思|是什么含义|(?:怎么|如何)(?:做|知道|判断|区分|选)|在问什么")
_HELP = re.compile(
    r"(?:我)?(?:还是|真的|暂时)?(?:不知道|不会(?:做|答)?|不懂|想不出来|想不出|卡住了)"
    r"|(?:能不能|能|可以|请)?(?:再)?(?:给我?|来)(?:个|点|一点)?提示(?:吗)?"
    r"|提示一下"
)
_ANSWER_CLAIM = re.compile(
    r"(?:我(?:觉得|想)(?:是|选|填)|我(?:选|填)|应该(?:是|选|填)|是|选|填)"
    r"\s*[A-Za-z][A-Za-z']*(?:\s*吧)?"
)


def asks_why(said: str) -> bool:
    return bool(_WHY.search(said))


def explicit_intent(said: str) -> str | None:
    """Use local rules for direct responses and leave less clear wording to v5."""
    text = said.strip().rstrip("。.!！?？")
    if asks_why(text) or _QUESTION.search(text):
        return "提问"
    if _HELP.fullmatch(text):
        return "不会"
    if "?" not in said and "？" not in said and _ANSWER_CLAIM.fullmatch(text):
        return "答题"
    return None


def classify(question: str, said: str, write) -> str | None:
    """Return one of ``LABELS``. ``None`` means treat the reply as an answer.

    One bare English word is plainly an answer, so the model is not asked.
    A broken model reply is also ``None``: the child must not be stuck.
    """
    stripped = said.strip().strip("。.!！?？")
    if not stripped or _ONE_WORD.fullmatch(stripped):
        return None
    try:
        raw = write(SYSTEM, f"这一问：{question}\n孩子说：{said}", GRAMMAR, 32)
        label = json.loads(raw).get("label")
    except (json.JSONDecodeError, AttributeError, OSError, RuntimeError):
        return None
    return label if label in LABELS else None


def make_label_of(write):
    """Bind the model so ``run_item`` can call ``label_of(question, said)``."""

    def label_of(question: str, said: str) -> str | None:
        return classify(question, said, write)

    return label_of
