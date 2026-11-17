"""专属错题：星火出题和给答案，程序把关。

把关规则：格式不对、重复、程序算得出却不一样的题都不发；留下的题，星火看不到答案
再做一遍，对不上或它说有问题的也不发。出题和重做都开着思考：关掉时 6 道题里错了
2 个答案，出题又在后台，不差这半分钟。
孩子做错过的原题也会发回去，这部分不用大模型，答案由程序算。
"""

from __future__ import annotations

import json
import re

from xiangke.answers import compute_answer
from xiangke.planner import TERMS, TRIES, LlmError, _parse
from xiangke.portrait import counts_as_judged

ASK_COUNT = 6
KEEP_COUNT = 3
MAX_REDO = 2
MAX_RULE = 60
_ANSWER = re.compile(r"[A-Za-z][A-Za-z'-]*")
_OPTIONS = re.compile(r"[(（]([^)）]*/[^)）]*)[)）]\s*$")
_LETTER_BLANK = re.compile(r"[A-Za-z_]*_[A-Za-z_]*")
_SPACED_BLANKS = re.compile(r"_(?:\s+_)+")

ASK = """你在给乡村小学生出英语练习题。

这个孩子的情况：
{situation}

请正好出 {count} 道题，专门练他要练的地方。题型可以是：
- 选词：句子里有 ___，题目末尾的括号里写选项，选项用 / 隔开。
- 填空：句子里有 ___，题目前面用中文说清意思。
- 改错：开头写“改错（只改一个词）：”，后面是一句只错一个词的英文。
- 拼写：开头写“看中文写单词：”，后面是中文和挖掉字母的单词，每个下划线代表一个字母。

要求：
1. 每道题只有一个答案说得通，答案只有一个英文词。出题前想一想，别的词填进去是不是也对，也对就换一道。
2. 只用小学生学过的简单词。不要重复，不要出下面这些已经有的题：
{known}
3. rule 写这道题用到的规则，一句中文，不超过 40 字，不能出现答案这个词，不用“第三人称”这类术语。

只输出 JSON：{{"items":[{{"text":"题目","answer":"答案","rule":"规则"}}]}}"""

SOLVE = """下面是小学英语题。逐题写出唯一答案（一个词）。这些题是别人出的，可能有错。出现下面任何一种情况，answer 写 "有问题"：
- 不止一个词填进去都说得通（比如没给选项，也没有中文意思能定下来）。
- 把答案填进去之后，整句英文语法不通。
- 空的数量、选项或中文提示和题目对不上。
只输出 JSON：{"answers":[{"id":1,"answer":"答案"}]}

题目：
"""


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def _has_word(text: str, word: str) -> bool:
    return re.search(r"(?<![A-Za-z])" + re.escape(word) + r"(?![A-Za-z])", text, re.I) is not None


def check_item(item, seen: set[str]) -> str | None:
    """返回不能发的原因。能发就返回 None，并把题目记进 ``seen``。"""
    if not isinstance(item, dict):
        return "不是一道题"
    text, answer, rule = item.get("text"), item.get("answer"), item.get("rule")
    if not all(isinstance(value, str) and value.strip() for value in (text, answer, rule)):
        return "缺内容"
    text, answer = text.strip(), answer.strip()
    if not _ANSWER.fullmatch(answer):
        return "答案不是一个英文词"
    if _norm(text) in seen:
        return "重复"

    options = _OPTIONS.search(text)
    choices = [choice.strip().casefold() for choice in options.group(1).split("/")] if options else []
    sentence = text[: options.start()] if options else text
    # “_ _ _” 和 “___” 算同一种写法
    joined = _SPACED_BLANKS.sub(lambda found: found.group().replace(" ", ""), sentence)
    blanks = _LETTER_BLANK.findall(joined)
    letter_blanks = [token for token in blanks if re.search(r"[A-Za-z]", token)]
    spelling = "写单词" in text or bool(letter_blanks)
    if spelling:
        if len(blanks) != 1 or not _fits(blanks[0], answer):
            return "拼写题的空和答案对不上"
    elif options:
        if "_" not in sentence:
            return "选词题没有空"
        if answer.casefold() not in choices:
            return "答案不在选项里"
        # He ___ run. (run / runs) 这种题填进去语法不通
        if any(_has_word(sentence, choice) for choice in choices):
            return "选项里的词又出现在句子里"
    elif "_" not in text and "改错" not in text:
        return "看不出题型"
    if not spelling and len(_ANSWER.findall(sentence)) < 2:
        return "没有英文句子"
    if _has_word(sentence, answer):
        return "题目里已经写了答案"

    computed = compute_answer(text)
    if computed and computed["answer"].casefold() != answer.casefold():
        return f"和程序算的 {computed['answer']} 不一样"
    if len(rule) > MAX_RULE or _has_word(rule, answer) or any(term in rule for term in TERMS):
        return "规则写得不合格"
    seen.add(_norm(text))
    return None


def _fits(blank: str, answer: str) -> bool:
    return len(blank) == len(answer) and all(mark == "_" or mark.casefold() == letter.casefold() for mark, letter in zip(blank, answer))


def redo_items(sessions: list[dict], known: set[str], limit: int = MAX_REDO) -> list[dict]:
    """孩子做错过的原题，最新的在前。答案由程序算，算不出的不发。"""
    items = []
    for session in reversed(sessions):
        if len(items) >= limit:
            break
        text = session.get("recognizedText", "")
        if not counts_as_judged(session) or _norm(text) in known:
            continue
        if not any(event.get("result") in ("miss", "assisted") for event in session.get("events", [])):
            continue
        computed = compute_answer(text)
        if computed is None:
            continue
        known.add(_norm(text))
        items.append({"text": text, "answer": computed["answer"], "rule": computed["rule"], "source": "redo"})
    return items


def _situation(portrait: dict, sessions: list[dict]) -> str:
    wrong = [
        session.get("recognizedText", "")
        for session in sessions[-10:]
        if any(event.get("result") in ("miss", "assisted") for event in session.get("events", [])) and counts_as_judged(session)
    ]
    lines = [
        f"判断：{portrait.get('judgement') or '还没有'}",
        f"要练的地方：{'；'.join(portrait.get('focus') or []) or '从他做错的题看'}",
        f"最近做错的题：{'；'.join(wrong) or '没有'}",
    ]
    return "\n".join(lines)


def make_drill_maker(chat, check_chat=None, ask_count: int = ASK_COUNT, keep: int = KEEP_COUNT):
    """``maker(portrait, sessions, known)`` 返回能发的题。``known`` 是不能再出的题目。

    ``check_chat`` 用来重做核对，没给就用 ``chat``。
    """

    def maker(portrait: dict, sessions: list[dict], known: set[str]) -> list[dict]:
        listing = "\n".join(f"- {text}" for text in sorted(known)) or "- 没有"
        prompt = ASK.format(situation=_situation(portrait, sessions), count=ask_count, known=listing)
        items = _ask_items(chat, prompt)
        passed = []
        seen = set(known)
        for item in items[: ask_count + 2] if isinstance(items, list) else []:
            reason = check_item(item, seen)
            if reason:
                print("这道题不发:", reason, json.dumps(item, ensure_ascii=False)[:120], flush=True)
                continue
            passed.append({"text": item["text"].strip(), "answer": item["answer"].strip(), "rule": item["rule"].strip(), "source": "cloud"})
        return _second_check(passed, check_chat or chat)[:keep]

    return maker


def _ask_items(chat, prompt: str):
    """星火偶尔超时或写坏，再试一次。"""
    error = None
    for _ in range(TRIES):
        try:
            return _parse(chat([{"role": "user", "content": prompt}])).get("items")
        except LlmError as problem:
            error = problem
        except ValueError as problem:
            error = LlmError(f"出的题看不懂：{problem}")
        print("出题没成功:", error, flush=True)
    raise error


def _second_check(items: list[dict], chat) -> list[dict]:
    """星火看不到答案再做一遍。程序能算的题也要做，程序查不出语法不通的题。"""
    if not items:
        return []
    listing = "\n".join(f"{index}. {item['text']}" for index, item in enumerate(items, start=1))
    try:
        rows = _parse(chat([{"role": "user", "content": SOLVE + listing}])).get("answers") or []
        again = {row.get("id"): str(row.get("answer", "")).strip().casefold() for row in rows if isinstance(row, dict)}
    except (LlmError, ValueError, AttributeError) as error:
        print("重做没成功，这批题这次不发:", error, flush=True)
        return []
    kept = []
    for index, item in enumerate(items, start=1):
        if again.get(index) == item["answer"].casefold():
            kept.append(item)
        else:
            print("重做对不上，不发:", item["text"], again.get(index), flush=True)
    return kept
