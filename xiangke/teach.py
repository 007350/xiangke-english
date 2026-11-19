"""The program gives the answer, the model does the talking.

The model's last ``expect`` is locked to the computed answer. If its wording
fails the checks, the child sees the program's wording. When the program cannot
compute the answer, the model tries alone and the reply is not judged.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from xiangke.answers import compute_answer
from xiangke.kernel import check_session
from xiangke.topic_plans import topic_key, valid_topic_entry
from xiangke.verb_3s import plan_correction, template_steps

ROOT = Path(__file__).resolve().parents[1]
SYSTEM = (ROOT / "训练" / "v6" / "system_prompt.txt").read_text(encoding="utf-8")
FREE_GRAMMAR = (ROOT / "prompts" / "steps.gbnf").read_text(encoding="utf-8")
EXK = "拆题样例（examples，只模仿拆法，答案与本题无关）：\n"
DEFAULT_PLAN = {
    "voice": "一步一步问，让他自己说出答案。",
    "reminders": [],
    "forbid": "不要直接说出标准答案。",
    "examples": [],
}
_REPAIRS = (
    ("这叫第三人称单数。", ""),
    ("是第三人称单数（一个别人）", "是「一个别人」"),
    ("不是第三人称单数", "不是「一个别人」"),
    ("是第三人称单数", "是「一个别人」"),
    ("动词用第三人称单数", "动词要变"),
    ("动词要用第三人称单数", "动词要变"),
)
_TERMS = ("第三人称", "第一人称", "宾格", "物主代词", "谓语")
_BAD_REASONS = ("句子里还有", "句子里用的是", "过去式", "比较级")
_ASKS_FOR_FILL = re.compile(r"(?:空格|空)(?:里|中|内|是|该|要|可|填|缺)|(?:填|补)(?:入|上)?(?:什么|哪个|一个词)")
_ASKS_FOR_CUE = re.compile(r"(?:空格|空)(?:的)?(?:前|左)|(?:空格|空)里.{0,12}词前")
_TRY_PRAISE = re.compile(r"^(?:(?:很好|真棒|不错|太棒了|答对了|对了|没错|你答对了)[！!。，,、\s]+)+")


def user_message(text: str, plan: dict, answer: str | None) -> str:
    reminders = " ".join(plan.get("reminders") or [])
    lines = [f"题目（recognizedText，孩子已确认）：{text}"]
    if answer is not None:
        lines.append(f"这道题的最后答案（程序已算出）：{answer}")
    lines += [
        f"讲法（voice）：{plan['voice']}",
        f"孩子犯过的错（reminders）：{reminders or '（无）'}",
        f"禁令（forbid）：{plan['forbid']}",
    ]
    examples = [
        {"recognizedText": item["recognizedText"], "steps": item["steps"]}
        for item in plan.get("examples") or []
    ]
    if examples:
        lines.append(EXK + json.dumps(examples, ensure_ascii=False, indent=1))
    lines.append("请按要求只输出 JSON。")
    return "\n".join(lines)


def answer_grammar(answer: str) -> str:
    literal = json.dumps(json.dumps(answer, ensure_ascii=False), ensure_ascii=False)
    return "\n".join(
        [
            'root ::= "{" space "\\"steps\\"" space ":" space "[" space (step "," space)? (step "," space)? last space "]" space "}"',
            'step ::= "{" space "\\"ask\\"" space ":" space string "," space "\\"expect\\"" space ":" space string "," space "\\"explain\\"" space ":" space string space "}"',
            f'last ::= "{{" space "\\"ask\\"" space ":" space string "," space "\\"expect\\"" space ":" space {literal} "," space "\\"explain\\"" space ":" space string space "}}"',
            'string ::= "\\"" char* "\\""',
            'char ::= [^"\\\\\\x7F\\x00-\\x1F] | "\\\\" (["\\\\bfnrt] | "u" [0-9a-fA-F]{4})',
            'space ::= | " " | "\\n" [ \\t]{0,20}',
        ]
    )


def _fixed_grammar(steps: list[dict]) -> str:
    def literal(text):
        return json.dumps(text, ensure_ascii=False)

    fields = []
    for step in steps:
        fixed = json.dumps({"ask": step["ask"], "expect": step["expect"]}, ensure_ascii=False)
        prefix = literal(fixed[:-1] + ', "explain": ')
        fields.append(f'{prefix} string "}}"')
    sequence = ' ", " '.join(fields)
    opening = literal('{"steps": [')
    root = f'root ::= {opening} {sequence} "]}}"'
    return "\n".join([root, *answer_grammar(steps[-1]["expect"]).splitlines()[3:]])


def repair_terms(steps: list[dict]) -> list[dict]:
    """Swap a known grammar term for plain words. Nothing else is rewritten."""
    fixed = []
    for step in steps:
        explain = step.get("explain", "")
        for old, new in _REPAIRS:
            explain = explain.replace(old, new)
        fixed.append({**step, "explain": explain})
    return fixed


def check_talk(raw: str, text: str, computed: dict) -> tuple[list[dict] | None, list[str]]:
    """Return the usable steps and the reasons they cannot be shown."""
    try:
        steps = json.loads(raw)["steps"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None, ["不是合法 JSON"]
    if not isinstance(steps, list) or not steps:
        return None, ["没有问"]
    if any(not isinstance(s, dict) or set(s) != {"ask", "expect", "explain"} or not all(isinstance(s[k], str) and s[k].strip() for k in s) for s in steps):
        return None, ["字段不全"]
    steps = repair_terms(steps)
    answer = computed["answer"]
    errors = []
    if len(steps) > 3:
        errors.append("问得太多")
    if steps[-1]["expect"].strip().casefold() != answer.casefold():
        errors.append("最后一问不是程序的答案")
    if not check_session({"steps": steps})["checkPassed"]:
        errors.append("提前说出答案")
    last = steps[-1]["explain"]
    if not _has_word(last, answer):
        errors.append("最后的解释没有说出答案")
    joined = " ".join(s["ask"] + s["explain"] for s in steps)
    sentence = text.split("：")[-1]
    if _called_me(steps, sentence):
        errors.append("把主语说成了我")
    terms = [term for term in _TERMS if term in joined]
    if terms:
        errors.append(f"用了术语 {'、'.join(terms)}")
    if "he、she、it" in joined and "帮忙" not in joined:
        errors.append("规则没有讲清什么时候才变")
    reasons = [item for item in _BAD_REASONS if item in joined and not ("过去式" == item and computed["kind"] == "past")]
    if reasons:
        errors.append(f"理由不可靠 {'、'.join(reasons)}")
    if computed["kind"] != "be_verb" and re.search(r"\b(?:am|is|are)\b", last) and not re.search(r"\b(?:am|is|are)\b", text):
        errors.append("讲成了 am、is、are")

    errors += _reason_errors(steps, computed)

    others = _wrong_answers(text, computed)
    needs_es = computed["kind"] == "verb_choice" and any(word + "es" == answer.casefold() for word in others)
    wrong_word = (computed.get("wrong") or "").casefold()
    for index, step in enumerate(steps[:-1]):
        expect = step["expect"].strip().casefold()
        # 改错题第一问问的就是错词，这不算选错
        if expect in others and not (index == 0 and expect == wrong_word):
            errors.append(f"中间一问选了错的答案 {step['expect']}")
        if needs_es and re.fullmatch(r"(?:直接|只)?加(?:上)?\s*s", expect):
            errors.append("中间一问把加 es 说成了加 s")
        if computed.get("ruleKey") == "n_a_an" and expect in {"a", "an", "one"}:
            asks_for_cue = _ASKS_FOR_CUE.search(step["ask"])
            if asks_for_cue and expect != computed.get("cue"):
                errors.append("中间一问认错了空格前的提示词")
            elif not asks_for_cue and _ASKS_FOR_FILL.search(step["ask"]):
                errors.append("中间一问把空格前的提示词当成了填空答案")
    for other in others:
        if re.search(r"(?:填|选|改成|→)\s*" + re.escape(other) + r"(?![A-Za-z])", last, re.I):
            errors.append(f"解释讲成了 {other}")
    for step in steps[1:]:
        if computed.get("wrong") and step["expect"].strip().casefold() == computed["wrong"].casefold():
            errors.append("后面又把错词当答案")
    return steps, errors


def _called_me(steps: list[dict], sentence: str) -> bool:
    """A subject other than I called 「我」. 「不是「我」」 is fine."""
    words = re.findall(r"[A-Za-z']+", sentence)
    if not words or words[0] == "I" or " I " in f" {sentence} ":
        return False
    return any(step["expect"].strip() == "我" or re.search(r"(?<!不)是「我」", step["explain"]) for step in steps)


def _reason_errors(steps: list[dict], computed: dict) -> list[str]:
    """The program knows why the answer is right, so it can catch a reason that disagrees."""
    errors = []
    answer = computed["answer"].casefold()
    key = computed.get("ruleKey", "")
    all_explain = " ".join(s["explain"] for s in steps)
    last = steps[-1]["explain"]

    # A → A 等于没讲
    for left, right in re.findall(r"([A-Za-z']+)\s*→\s*([A-Za-z']+)", all_explain):
        if left.casefold() == right.casefold():
            errors.append(f"讲成了 {left} 变 {right}")

    change_steps = [s for s in steps if "变形" in s["ask"] or "加 s" in s["ask"]]
    if key in ("v_base_after", "v_base"):
        if any(s["expect"].strip() == "要" for s in change_steps):
            errors.append("该用原形却说要变")
        if re.search(r"(?:动词要变|直接加\s*s|要加\s*s)", all_explain):
            errors.append("该用原形却讲成加 s")
    if key == "v_base_after":
        cue = computed["rule"].split(" ")[0]
        match = re.search(r"已经有 (\S+) 了", computed["rule"])
        word = match.group(1) if match else cue
        if word.casefold() not in all_explain.casefold():
            errors.append(f"没有讲到 {word}")
    if key in ("v_3s",) and computed["kind"] == "verb_choice":
        if any(s["expect"].strip() == "不要" for s in change_steps):
            errors.append("该变形却说不要")
        if re.search(r"不加\s*s", all_explain):
            errors.append("该变形却讲成不加 s")

    # 变化的规则要和答案的样子对得上
    if "直接加 s" in last and re.search(r"(?:shes|ches|xes|sses|oes|ies)$", answer):
        errors.append("把加 es 说成了直接加 s")
    if re.search(r"(?:加 es|结尾的.{0,8}加 es)", last) and not re.search(r"(?:es|has)$", answer):
        errors.append("把加 s 说成了加 es")
    return errors


def _wrong_answers(text: str, computed: dict) -> set[str]:
    answer = computed["answer"].casefold()
    found = re.search(r"[(（]([^)）]*/[^)）]*)[)）]\s*$", text.strip())
    options = {item.strip().casefold() for item in found.group(1).split("/")} if found else set()
    if computed.get("wrong"):
        options.add(computed["wrong"].casefold())
    options.discard(answer)
    return options


def _has_word(text: str, word: str) -> bool:
    return re.search(r"(?<![A-Za-z])" + re.escape(word) + r"(?![A-Za-z])", text, re.I) is not None


def program_steps(text: str, computed: dict) -> list[dict]:
    """The program's own wording. Used whenever the model's wording is refused."""
    answer, rule = computed["answer"], computed["rule"]
    if computed["kind"] == "pack_spelling":
        missing = [(index + 1, answer[index]) for index, letter in enumerate(computed["mask"]) if letter == "_"]
        return [
            {"ask": f"从左往右数，第 {position} 个字母是什么？只写一个字母。", "expect": letter,
             "explain": f"第 {position} 个字母填 {letter}。"}
            for position, letter in missing
        ] + [{"ask": "把这个单词完整拼出来。", "expect": answer, "explain": f"完整拼写是 {answer}。"}]
    if computed["kind"] == "verb_3s":
        plan = plan_correction(text)
        talk = template_steps(plan)["steps"]
        return [
            {"ask": planned["ask"], "expect": planned["expect"], "explain": said["explain"]}
            for planned, said in zip(plan["steps"], talk)
        ]
    choices = _choices(text)
    ask = f"想一想，空里选 {choices}？" if choices else "想一想，这道题的答案是什么？说出那个英文词。"
    counter = computed.get("counter", "")
    return [{"ask": ask, "expect": answer, "explain": f"{rule}所以答案是 {answer}。{counter}" if not choices else f"{rule}所以填 {answer}。{counter}"}]


def _planned_steps(text: str, computed: dict, strategy: str) -> list[dict]:
    steps = program_steps(text, computed)
    if strategy == "self_first":
        return [{"ask": "这句话说的是自己还是别人？用中文说。", "expect": "自己",
                 "explain": "I 是「我」，说的是说话的人自己。"}, *steps]
    if strategy == "cue_first":
        cue = computed["cue"]
        return [
            {"ask": "题目里哪个英文词表示「能」？", "expect": cue, "explain": f"{cue} 表示「能」。"},
            {"ask": f"{cue} 后面的动词要不要变形？回答「要」或「不要」。", "expect": "不要",
             "explain": computed["rule"]},
            *steps,
        ]
    return steps


def _teach_topic(text: str, computed: dict, plan: dict, entry: dict, write) -> dict:
    scaffold = _planned_steps(text, computed, entry["strategy"])
    prompt = user_message(text, {**plan, "voice": entry["voice"]}, computed["answer"])
    prompt += (
        "\n步骤已由程序确定。保留每一步的 ask 和 expect，只用适合这个孩子的话改写 explain。"
        "\n以下是可用的正确解释：\n" + json.dumps(scaffold, ensure_ascii=False)
    )
    raw = ""
    try:
        raw = write(SYSTEM, prompt, _fixed_grammar(scaffold))
        steps, errors = check_talk(raw, text, computed)
        if steps is not None and [(s["ask"], s["expect"]) for s in steps] != [
            (s["ask"], s["expect"]) for s in scaffold
        ]:
            errors.append("改变了专属讲法的小问或答案")
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        steps, errors = None, ["本地模型没有返回可用的讲解"]
    if steps is not None and not errors:
        return {"source": "model", "steps": with_ids(steps), "judged": True,
                "computed": computed, "errors": [], "raw": raw}
    return {"source": "program", "steps": with_ids(scaffold), "judged": True,
            "computed": computed, "errors": errors, "raw": raw}


def _choices(text: str) -> str | None:
    found = re.search(r"[(（]([^)）]*/[^)）]*)[)）]\s*$", text.strip())
    if not found:
        return None
    items = [item.strip() for item in found.group(1).split("/")]
    return "、".join(items[:-1]) + " 还是 " + items[-1]


def with_ids(steps: list[dict]) -> list[dict]:
    return [
        {"stepId": f"s{index}", "ask": s["ask"], "expect": s["expect"], "explain": s["explain"]}
        for index, s in enumerate(steps, start=1)
    ]


def looks_like_item(text: str) -> bool:
    """Whether the model may try. A blank, options or 改错 on the page, with some English."""
    if not re.search(r"[A-Za-z]", text):
        return False
    return "_" in text or "改错" in text or re.search(r"[(（][^)）]*[)）]", text) is not None


def teach(text: str, write, plan: dict | None = None, known: dict | None = None, *, assessment: bool = False) -> dict:
    """Prepare one item.

    ``write(system, user, grammar)`` is the model. ``source`` is ``model``,
    ``program`` or ``model_try``. Only ``judged`` items may be marked right or
    wrong. ``None`` steps means the item cannot be taught.
    ``known`` carries the answer and rule of a cloud item the program cannot
    compute. Its ``kind`` is ``cloud``.
    """
    plan = {**DEFAULT_PLAN, **(plan or {})}
    computed = compute_answer(text) or known
    if computed and (assessment or computed["kind"] == "pack_spelling"):
        # 验收先让孩子独立选词。规则和解释留到他主动求助或答过以后。
        return {"source": "program", "steps": with_ids(program_steps(text, computed)),
                "judged": True, "computed": computed, "errors": [], "raw": ""}
    if "topicPlans" in plan:
        topics = plan["topicPlans"]
        key = topic_key(computed)
        entry = topics.get(key) if isinstance(topics, dict) else None
        # 题型计划启用后，旧的跨题型提醒和例题不再发给 v5。
        plan = {**DEFAULT_PLAN, "forbid": plan["forbid"]}
        if valid_topic_entry(key, entry):
            return _teach_topic(text, computed, plan, entry, write)
    if computed is None and not looks_like_item(text):
        return {"source": "none", "steps": None, "judged": False, "computed": None, "errors": ["看不出是一道题"], "raw": ""}
    if computed is None:
        raw = write(SYSTEM, user_message(text, plan, None), FREE_GRAMMAR)
        steps, errors = _check_try(raw, text)
        if errors:
            return {"source": "none", "steps": None, "judged": False, "computed": None, "errors": errors, "raw": raw}
        return {"source": "model_try", "steps": with_ids(steps), "judged": False, "computed": None, "errors": [], "raw": raw}

    raw = write(SYSTEM, user_message(text, plan, computed["answer"]), answer_grammar(computed["answer"]))
    steps, errors = check_talk(raw, text, computed)
    if steps is not None and not errors:
        return {"source": "model", "steps": with_ids(steps), "judged": True, "computed": computed, "errors": [], "raw": raw}
    return {
        "source": "program",
        "steps": with_ids(program_steps(text, computed)),
        "judged": True,
        "computed": computed,
        "errors": errors,
        "raw": raw,
    }


def _check_try(raw: str, text: str = "") -> tuple[list[dict] | None, list[str]]:
    try:
        steps = json.loads(raw)["steps"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None, ["不是合法 JSON"]
    if not isinstance(steps, list) or not steps:
        return None, ["没有问"]
    if any(not isinstance(s, dict) or set(s) != {"ask", "expect", "explain"}
           or not all(isinstance(value, str) for value in s.values()) for s in steps):
        return None, ["字段不全"]
    steps = repair_terms(steps)
    for step in steps:
        step["ask"] = _TRY_PRAISE.sub("", step["ask"].strip())
        if not step["ask"] or re.search(r"答对了|答错了|说对了|选对了|说错了|选错了|真棒|很好", step["ask"]):
            return None, ["试讲的小问带了判分或表扬"]
    if not check_session({"steps": steps})["checkPassed"]:
        return None, ["提前说出答案"]
    if _called_me(steps, text.split("：")[-1]):
        return None, ["把主语说成了我"]
    return steps, []
