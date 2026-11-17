"""Decide what to do with a child's reply. Only ``judge`` decides right or wrong.

``label`` is the model's reading of the reply. It may steer the next message,
but it can never turn a reply into a hit. Without a label the program treats
the reply as an answer.
"""

from __future__ import annotations

import re

from xiangke.kernel import finish_session, judge, normalize_answer

LABELS = ("答题", "不会", "提问", "其他")
_OPTION_GROUP = re.compile(r"[(（]([^)）]*/[^)）]*)[)）]\s*$")
_LATIN = re.compile(r"[A-Za-z][A-Za-z']*")
_YES = {"是", "是的", "对", "对的", "嗯", "嗯嗯", "好", "没错", "yes", "y", "ok"}
_NO = {"不是", "不", "不对", "错了", "没有", "no", "n"}
_ENDINGS = ("ies", "es", "ed", "ing", "s", "d")


def route_reply(said: str, expect: str, label: str | None = None) -> dict:
    """Return the next action and whether this reply counts as a wrong answer.

    Actions: ``hit``, ``miss``, ``confirm``, ``hint``, ``show_rule``, ``redirect``.
    ``show_rule`` means the rule without the answer, not ``explain``, which
    states the answer.
    """
    if label is not None and label not in LABELS:
        raise ValueError(f"unknown label: {label}")
    if judge(said, expect) == "hit":
        return {"action": "hit", "candidate": None, "countsAsMiss": False}
    if label == "不会":
        return {"action": "hint", "candidate": None, "countsAsMiss": False}
    if label == "提问":
        return {"action": "show_rule", "candidate": None, "countsAsMiss": False}
    if label == "其他":
        return {"action": "redirect", "candidate": None, "countsAsMiss": False}
    candidate = find_candidate(said, expect)
    if candidate is not None:
        return {"action": "confirm", "candidate": candidate, "countsAsMiss": False}
    return {"action": "miss", "candidate": None, "countsAsMiss": True}


def find_candidate(said: str, expect: str) -> str | None:
    """The single English word inside a longer reply, for 「你是想说 X 吗」.

    Returns ``None`` when the reply is already just that word, holds several
    English words, or the expected answer is not English.
    """
    if not _LATIN.search(expect):
        return None
    words = _LATIN.findall(said)
    if len(words) != 1:
        return None
    word = words[0]
    if normalize_answer(said) == normalize_answer(word):
        return None
    return word


def confirm_reply(said: str, candidate: str, expect: str) -> dict:
    """Handle the answer to 「你是想说 X 吗」. The program then judges X."""
    value = normalize_answer(said)
    if value in _NO or said.strip().casefold() in _NO:
        return {"action": "ask_again", "result": None, "countsAsMiss": False}
    if value in _YES or said.strip().casefold() in _YES:
        result = judge(candidate, expect)
        return {"action": result, "result": result, "countsAsMiss": result == "miss"}
    return route_reply(said, expect)


def classify_error(said: str, expect: str, wrong: str | None = None, options: list[str] | None = None) -> str:
    """Name what went wrong with a missed answer.

    Kinds: ``empty``, ``not_english``, ``no_change``, ``wrong_option``,
    ``wrong_form``, ``spelling``, ``extra_words``, ``other``.
    """
    value = normalize_answer(said)
    target = normalize_answer(expect)
    if not value:
        return "empty"
    expects_english = _LATIN.search(expect) is not None
    if expects_english and not _LATIN.search(value):
        return "not_english"
    if wrong is not None and value == normalize_answer(wrong):
        return "no_change"
    if options and value in {normalize_answer(option) for option in options}:
        return "wrong_option"
    if not expects_english:
        return "other"
    if len(value.split()) > 1:
        return "extra_words" if target in value.split() else "other"
    if _stem(value) == _stem(target):
        return "wrong_form"
    if _distance(value, target) <= 2:
        return "spelling"
    return "other"


def finish_with_error_kinds(session: dict, saids: list[str], note: str | None = None) -> dict:
    """Return the record from ``finish_session`` with ``errorKind`` on wrong replies.

    Only ``miss`` and ``assisted`` events get the field. ``hit``, ``note`` and
    the results themselves are left as the kernel wrote them.
    """
    finished = finish_session(session, saids, note)
    steps = {step.get("stepId"): step for step in finished["steps"]}
    options = _options_in(finished["recognizedText"])
    wrong = _wrong_word(finished)
    for event in finished["events"]:
        if event["result"] not in ("miss", "assisted"):
            continue
        step = steps[event["stepId"]]
        is_last = step is finished["steps"][-1]
        event["errorKind"] = classify_error(
            event["said"],
            str(step.get("expect") or ""),
            wrong=wrong if is_last else None,
            options=options if is_last else None,
        )
    return finished


def _options_in(text: str) -> list[str] | None:
    found = _OPTION_GROUP.search(text.strip())
    if not found:
        return None
    return [item.strip() for item in found.group(1).split("/")]


def _wrong_word(session: dict) -> str | None:
    """In a fix-one-word item the first question asks for the wrong word."""
    text = session["recognizedText"]
    steps = session["steps"]
    if "_" in text or _OPTION_GROUP.search(text.strip()) or len(steps) < 2:
        return None
    first = str(steps[0].get("expect") or "")
    return first if re.search(r"(?<![A-Za-z])" + re.escape(first) + r"(?![A-Za-z])", text) else None


def _stem(word: str) -> str:
    """Remove a verb or noun ending. ``likes``, ``liked``, ``liking`` share ``lik``."""
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 2:
            word = word[: -len(ending)] + ("y" if ending == "ies" else "")
            break
    if word.endswith("e") and len(word) > 2:
        word = word[:-1]
    if word.endswith("i") and len(word) > 2:
        word = word[:-1] + "y"
    return word


def _distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for row, a in enumerate(left, start=1):
        current = [row]
        for column, b in enumerate(right, start=1):
            current.append(min(previous[column] + 1, current[-1] + 1, previous[column - 1] + (a != b)))
        previous = current
    return previous[-1]
