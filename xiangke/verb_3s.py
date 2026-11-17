"""Split one fix-one-word item into two questions.

The program computes the corrected word from the confirmed text and the verb
list, and writes each question so its form matches its answer. The model only
writes the explanations.
"""

from __future__ import annotations

import json
import re
import zlib
from functools import lru_cache
from pathlib import Path

_VERBS = Path(__file__).resolve().parents[1] / "data" / "动词表.json"
_OPTIONS = re.compile(r"[(（][^)）]*/")
_TOKEN = re.compile(r"[A-Za-z][A-Za-z']*|[.,!?;]")
_EN_WORD = re.compile(r"(?<![A-Za-z])(?:am|is|are)(?![A-Za-z])", re.I)
_OTHER_SUBJECT = re.compile(r"(?<![A-Za-z])(?:I|[Yy]ou|[Ww]e|[Tt]hey)(?![A-Za-z])")
_CALLED_ME = re.compile(r"(?<!不)是我(?!们)")
_CALLED_PERSON = re.compile(r"一个人|别人")
_ADD_S = re.compile(r"加\s*s(?![A-Za-z])")
_ADD_ES = re.compile(r"加\s*es(?![A-Za-z])")
_JARGON = ("第三人称", "单数", "宾格", "比较级", "过去式", "物主代词", "谓语")
_PRONOUNS = {
    "i": ("我", "I"),
    "you": ("你", "you"),
    "he": ("他", "one"),
    "she": ("她", "one"),
    "it": ("它", "one"),
    "we": ("我们", "many"),
    "they": ("他们", "many"),
}
_RULES = {
    "v_3s": "主语是 he、she、it，动词要加 s。",
    "v_es": "主语是 he、she、it，这个动词要加 es。",
    "v_ies": "主语是 he、she、it，这个动词要把 y 改成 i，再加 es。",
    "v_irregular": "主语是 he、she、it，这个动词有特殊变化。",
}
# Every wording asks for one English word, so a right reply can match expect.
_ASK_WRONG = (
    "这句话里有一个词用错了，是哪个词？说出那个英文词。",
    "读一读这句话，哪个英文词用得不对？",
    "这句话只有一个词错了，把那个英文词找出来。",
)
_ASK_RIGHT = (
    "主语是 {subject}，{wrong} 应该改成什么？写出改好的词。",
    "{subject} 后面的 {wrong} 要变个样子，应该怎么写？",
    "把 {wrong} 改对，应该写成什么？",
)


def plan_correction(recognized_text: str) -> dict:
    """Build the two questions from the confirmed text alone.

    Only he, she, or it followed directly by a listed base verb is computed.
    Anything else raises ``ValueError`` and no answer is decided.
    """
    text = recognized_text.strip()
    if "_" in text or _OPTIONS.search(text):
        raise ValueError("item has a blank or options")

    tokens = _TOKEN.findall(text)
    while tokens and not tokens[0][0].isalpha():
        tokens.pop(0)
    if any(token in ".!?" for token in tokens[:-1]):
        raise ValueError("item has more than one sentence")
    if len(tokens) < 2:
        raise ValueError("sentence is too short")

    subject, verb = tokens[0], tokens[1]
    gloss, category = _PRONOUNS.get(subject.lower(), (None, None))
    if category != "one":
        raise ValueError("subject is not he, she, or it")
    verbs, needs_word_after = _verb_table()
    base = verb.casefold()
    if base not in verbs:
        raise ValueError("word after the subject is not a listed base verb")
    answer, rule_key = verbs[base]

    tail = tokens[2:]
    if base in needs_word_after and not any(token[0].isalpha() for token in tail):
        raise ValueError("this word may be an adjective when nothing follows it")
    for left, right in zip(tail, tail[1:]):
        if left.casefold() == "and" and right.casefold() in verbs:
            raise ValueError("more than one word needs correcting")

    corrected_tokens = list(tokens)
    corrected_tokens[1] = answer
    kind = "一样东西" if subject.lower() == "it" else "一个人"
    pick = zlib.crc32(" ".join(tokens).encode("utf-8"))

    return {
        "type": "verb_3s",
        "ruleKey": rule_key,
        "rule": _RULES[rule_key],
        "clue": f"{subject} 是{gloss}，{kind}",
        "subject": subject,
        "gloss": gloss,
        "wrong": verb,
        "right": answer,
        "corrected": _join(corrected_tokens),
        "steps": [
            {
                "intent": "找错词",
                "ask": _ASK_WRONG[pick % len(_ASK_WRONG)],
                "expect": verb,
            },
            {
                "intent": "改正",
                "ask": _ASK_RIGHT[pick % len(_ASK_RIGHT)].format(subject=subject, wrong=verb),
                "expect": answer,
            },
        ],
    }


def model_input(recognized_text: str, plan: dict) -> str:
    """The user message. Questions and answers are already fixed."""
    wrong, right = plan["steps"]
    return "\n".join(
        [
            f"题目：{recognized_text.strip()}",
            "每一问写一句 explain，不改问句和答案：",
            f"第1问：{wrong['ask']}  答案={wrong['expect']}  线索={plan['clue']}",
            f"第1问的 explain 要说出 {wrong['expect']}，不要说出 {right['expect']}。",
            f"第2问：{right['ask']}  答案={right['expect']}  规则={plan['rule']}",
            f"第2问的 explain 只讲这条规则，并说出 {right['expect']}。",
        ]
    )


def lock_grammar(plan: dict) -> str:
    """GBNF that allows only one explanation per planned question."""
    root = (
        'root ::= "{" space "\\"steps\\"" space ":" space "[" space '
        + ' "," space '.join("step" for _ in plan["steps"])
        + ' space "]" space "}"'
    )
    return "\n".join(
        [
            root,
            'step ::= "{" space "\\"explain\\"" space ":" space string space "}"',
            'string ::= "\\"" char* "\\""',
            'char ::= [^"\\\\\\x7F\\x00-\\x1F] | "\\\\" (["\\\\bfnrt] | "u" [0-9a-fA-F]{4})',
            'space ::= | " " | "\\n" [ \\t]{0,20}',
        ]
    )


def check_wording(model_json: str | dict, plan: dict) -> tuple[bool, list[str], list[dict] | None]:
    """Check the model's explanations against the fixed answers."""
    try:
        obj = json.loads(model_json) if isinstance(model_json, str) else model_json
    except json.JSONDecodeError as error:
        return False, [f"JSON 解析失败：{error}"], None
    steps = obj.get("steps") if isinstance(obj, dict) else None
    if not isinstance(steps, list):
        return False, ["格式不对：必须是 {\"steps\":[...]} "], None
    errors: list[str] = []
    cleaned: list[dict] = []
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict) or set(step) != {"explain"}:
            errors.append(f"第{index}问格式不对：必须且只能有 explain")
            continue
        if not isinstance(step["explain"], str) or not step["explain"].strip():
            errors.append(f"第{index}问格式不对：explain 不能空")
            continue
        cleaned.append({"explain": step["explain"].strip()})
    if errors:
        return False, errors, None
    if len(cleaned) != len(plan["steps"]):
        return False, [f"步数不对：应该 {len(plan['steps'])} 问，输出了 {len(cleaned)} 问"], cleaned

    for index, (step, planned) in enumerate(zip(cleaned, plan["steps"]), start=1):
        said = step["explain"]
        if not _has_word(said, planned["expect"]):
            errors.append(f"第{index}问的解释没有说出答案“{planned['expect']}”")
        for expect in [item["expect"] for item in plan["steps"][index:]]:
            if _has_word(said, expect):
                errors.append(f"第{index}问提前说出了后面的答案“{expect}”")
        stray = []
        for match in _EN_WORD.finditer(said):
            word = match.group(0).casefold()
            if word not in stray:
                stray.append(word)
        if stray:
            errors.append(f"第{index}问出现了和这道题无关的 {'/'.join(stray)}")
        terms = [term for term in _JARGON if term in said]
        if terms:
            errors.append(f"第{index}问用了孩子听不懂的术语：{'、'.join(terms)}")
        if _CALLED_ME.search(said):
            errors.append(f"第{index}问把 {plan['subject']} 说成了「我」")
        if plan["subject"].lower() == "it" and _CALLED_PERSON.search(said):
            errors.append(f"第{index}问把 {plan['subject']} 说成了人")
        if _OTHER_SUBJECT.search(said):
            errors.append(f"第{index}问拿别的主语举例，容易讲反规则")
        if plan["ruleKey"] == "v_3s" and _ADD_ES.search(said):
            errors.append(f"第{index}问把变化说成了加 es")
        if plan["ruleKey"] != "v_3s" and _ADD_S.search(said):
            errors.append(f"第{index}问把变化说成了只加 s")
    return (not errors), errors, cleaned


def template_steps(plan: dict) -> dict:
    """Explanations written by the program when the model cannot pass the check."""
    return {
        "steps": [
            {"explain": f"{plan['clue']}，后面的动词要变样子，所以 {plan['wrong']} 不对。"},
            {"explain": f"{plan['rule']}所以改成 {plan['right']}。"},
        ]
    }


def run_correction(recognized_text: str, write) -> dict:
    """Plan, ask the model for explanations, check, retry once, then use the template.

    ``write(message, grammar)`` returns the model's raw JSON text.
    """
    plan = plan_correction(recognized_text)
    grammar = lock_grammar(plan)
    message = model_input(recognized_text, plan)
    attempts: list[dict] = []

    def once(prompt: str) -> tuple[bool, list[str], list[dict] | None]:
        raw = write(prompt, grammar)
        ok, errors, steps = check_wording(raw, plan)
        attempts.append({"raw": raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False), "ok": ok, "errors": errors})
        return ok, errors, steps

    ok, errors, steps = once(message)
    if ok:
        return {"source": "model", "steps": _with_ids(steps, plan), "plan": plan, "attempts": attempts}

    retry = message + "\n上次输出有这些问题，请改正后重新输出：\n" + "\n".join(
        f"- {error}" for error in errors
    )
    ok, errors, steps = once(retry)
    if ok:
        return {"source": "model_retry", "steps": _with_ids(steps, plan), "plan": plan, "attempts": attempts}

    patched = _patch(steps, plan, errors)
    if patched is not None:
        return {"source": "template_patch", "steps": _with_ids(patched, plan), "plan": plan, "attempts": attempts}
    fallback = template_steps(plan)["steps"]
    passed, _, checked = check_wording({"steps": fallback}, plan)
    if not passed or checked is None:
        raise RuntimeError("template wording failed its own check")
    return {"source": "template", "steps": _with_ids(checked, plan), "plan": plan, "attempts": attempts}


@lru_cache(maxsize=1)
def _verb_table() -> tuple[dict[str, tuple[str, str]], frozenset[str]]:
    data = json.loads(_VERBS.read_text(encoding="utf-8"))
    table: dict[str, tuple[str, str]] = {}
    for word in data["v_3s"]:
        table[word] = (word + "s", "v_3s")
    for word in data["v_es"]:
        table[word] = (word + "es", "v_es")
    for word in data["v_ies"]:
        table[word] = (word[:-1] + "ies", "v_ies")
    for word, form in data["v_irregular"].items():
        table[word] = (form, "v_irregular")
    return table, frozenset(data["needsWordAfter"])


def _join(tokens: list[str]) -> str:
    text = ""
    for token in tokens:
        if not text:
            text = token
        elif re.fullmatch(r"[.,!?;]", token):
            text += token
        else:
            text += " " + token
    return text


def _has_word(text: str, word: str) -> bool:
    if re.fullmatch(r"[A-Za-z']+", word):
        return (
            re.search(
                r"(?<![A-Za-z])" + re.escape(word) + r"(?![A-Za-z])",
                text,
                re.I,
            )
            is not None
        )
    return word in text


def _with_ids(steps: list[dict], plan: dict) -> list[dict]:
    numbered = []
    for index, (step, planned) in enumerate(zip(steps, plan["steps"]), start=1):
        numbered.append(
            {
                "stepId": f"s{index}",
                "ask": planned["ask"],
                "expect": planned["expect"],
                "explain": step["explain"],
            }
        )
    return numbered


def _patch(steps: list[dict] | None, plan: dict, errors: list[str]) -> list[dict] | None:
    if not steps or len(steps) != len(plan["steps"]):
        return None
    found = [int(number) for error in errors for number in re.findall(r"^第(\d+)问", error)]
    if not found or any(not error.startswith("第") for error in errors):
        return None
    bad = set(found)
    fallback = template_steps(plan)["steps"]
    patched = [
        {"explain": fallback[index - 1]["explain"] if index in bad else step["explain"]}
        for index, step in enumerate(steps, start=1)
    ]
    ok, _, _ = check_wording({"steps": patched}, plan)
    if not ok:
        return None
    return patched
