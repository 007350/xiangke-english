"""Answer judging, document checks, and reply recording.

The model may draft speech. These functions decide what is stored.
"""

from __future__ import annotations

import copy
import re
import unicodedata

_PREFIXES = ("是", "选", "用")
# Phone keyboards and scanned books use curly quotes. Only these fold to '.
_APOSTROPHES = str.maketrans({"\u2018": "'", "\u2019": "'"})


def _is_punct(char: str) -> bool:
    return unicodedata.category(char).startswith("P")


def normalize_answer(text: str) -> str:
    """Trim, ignore case, drop surrounding punctuation and a leading 是/选/用.

    Curly apostrophes fold to a straight one. Other internal punctuation stays,
    so ``a.m`` does not become ``am``. Internal spaces stay, so ``a m`` does
    not become ``am`` and ``I am`` does not become ``am``.
    """
    value = unicodedata.normalize("NFKC", text).translate(_APOSTROPHES).strip().casefold()
    changed = True
    while value and changed:
        changed = False
        while value and (value[0].isspace() or _is_punct(value[0])):
            value = value[1:]
            changed = True
        while value and (value[-1].isspace() or _is_punct(value[-1])):
            value = value[:-1]
            changed = True
        for prefix in _PREFIXES:
            if value.startswith(prefix):
                value = value[len(prefix) :]
                changed = True
                break
    return value.strip()


def judge(said: str, expect: str) -> str:
    """Return ``hit`` or ``miss``. A longer reply is a miss when a single word is expected."""
    if normalize_answer(said) == normalize_answer(expect):
        return "hit"
    return "miss"


def _answer_in_text(text: str, expect: str) -> bool:
    needle = normalize_answer(expect)
    if not needle:
        return False
    haystack = unicodedata.normalize("NFKC", text).translate(_APOSTROPHES).casefold()
    pattern = r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])"
    return re.search(pattern, haystack) is not None


def check_session(session: dict) -> dict:
    """Validate a tutoring session before it can be taught or trusted.

    Reasons, in order: ``empty_steps``, ``missing_expect``, ``answer_leaked``.
    A later ``expect`` may appear in its own question, but not in an earlier ``ask``.
    """
    reasons: list[str] = []
    steps = session.get("steps") or []
    if not steps:
        reasons.append("empty_steps")
    else:
        if any(not str(step.get("expect") or "").strip() for step in steps):
            reasons.append("missing_expect")
        leaked = False
        for index, step in enumerate(steps):
            expect = str(step.get("expect") or "")
            if not expect.strip():
                continue
            for previous in steps[:index]:
                if _answer_in_text(str(previous.get("ask") or ""), expect):
                    leaked = True
        if leaked:
            reasons.append("answer_leaked")
    return {"checkPassed": not reasons, "checkReasons": reasons}


def check_plan(plan: dict, previous_version: int | None) -> dict:
    """Validate a plan before it can be stored on the phone.

    ``previous_version`` is ``None`` when the phone has no plan yet.
    The next version must be exactly one greater.
    """
    reasons: list[str] = []
    expected_version = 1 if previous_version is None else previous_version + 1
    if plan.get("planVersion") != expected_version:
        reasons.append("version_gap")
    if not str(plan.get("voice") or "").strip():
        reasons.append("empty_voice")
    reminders = plan.get("reminders") or []
    examples = plan.get("examples") or []
    if len(reminders) > 3:
        reasons.append("too_many_reminders")
    if len(examples) > 3:
        reasons.append("too_many_examples")
    return {"checkPassed": not reasons, "checkReasons": reasons}


def record_replies(session: dict, saids: list[str]) -> list[dict]:
    """Judge each reply in order and return the events to store.

    The split check is run here. A failed check refuses to teach.
    One question allows two misses. A third miss is ``assisted`` and ends
    the whole session. Replies after that are not recorded.
    """
    check = check_session(session)
    if not check["checkPassed"]:
        raise ValueError("split check did not pass")

    steps = session.get("steps") or []
    events: list[dict] = []
    step_index = 0
    hints = 0
    for said in saids:
        if step_index >= len(steps):
            break
        step = steps[step_index]
        if judge(said, str(step.get("expect") or "")) == "hit":
            events.append(
                {
                    "stepId": step["stepId"],
                    "said": said,
                    "result": "hit",
                    "hintUsed": hints,
                }
            )
            step_index += 1
            hints = 0
            continue
        if hints < 2:
            hints += 1
            events.append(
                {
                    "stepId": step["stepId"],
                    "said": said,
                    "result": "miss",
                    "hintUsed": hints,
                }
            )
            continue
        events.append(
            {
                "stepId": step["stepId"],
                "said": said,
                "result": "assisted",
                "hintUsed": 2,
            }
        )
        break
    return events


_SESSION_FACTS = (
    "sessionId",
    "childId",
    "planVersion",
    "source",
    "recognizedText",
    "confirmed",
    "offline",
    "startedAt",
    "endedAt",
)


def finish_session(session: dict, saids: list[str], note: str | None = None) -> dict:
    """Return one complete tutoring record.

    Program fields are copied from ``session``. The split check is run again.
    Passed checks judge ``saids``. A failed check keeps the drafted steps and
    stores only the caller's note.
    """
    check = check_session(session)
    if check["checkPassed"]:
        events = record_replies(session, saids)
    else:
        if note is None or not str(note).strip():
            raise ValueError("failed split needs a note")
        events = [{"stepId": "", "said": note, "result": "note", "hintUsed": 0}]
    finished = {key: session[key] for key in _SESSION_FACTS}
    finished["checkPassed"] = check["checkPassed"]
    finished["checkReasons"] = list(check["checkReasons"])
    finished["steps"] = copy.deepcopy(session.get("steps") or [])
    finished["events"] = events
    return finished
