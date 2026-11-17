"""Check the handwritten synthetic cases and the combinations they expand to."""

from __future__ import annotations

import copy
import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from xiangke.kernel import check_session, finish_session, judge, record_replies

_STEP_KEYS = ("stepId", "ask", "expect", "explain")
_EVENT_KEYS = {"stepId", "said", "result", "hintUsed"}
_FACTS = (
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
_RECORD_KEYS = set(_FACTS) | {"checkPassed", "checkReasons", "steps", "events"}


def evaluate(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    failures: list[str] = []
    failures.extend(_answer_failures(data["answers"]))
    failures.extend(_pool_failures(data["pools"], data["answers"]))
    combo = _combo_cases(data["pools"], data["patterns"])
    for name, steps, saids, expected in combo:
        failures.extend(_replay_failures(name, steps, saids, expected))
    for trace in data["traces"]:
        failures.extend(
            _replay_failures(trace["name"], trace["steps"], trace["saids"], trace["events"])
        )
    for item in data["refused"]:
        failures.extend(_refused_failures(item))
    assemblies = _assembly_count(data)
    failures.extend(_assembly_failures(data))
    return {
        "failures": failures,
        "answers": len(data["answers"]),
        "traces": len(data["traces"]),
        "refused": len(data["refused"]),
        "combo": len(combo),
        "assemblies": assemblies,
    }


def _answer_failures(rows: list[dict]) -> list[str]:
    failures = []
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(rows, start=1):
        key = (row["said"], row["expect"])
        if key in seen:
            failures.append(f"[判题 {index}] 重复 {row['said']!r} / {row['expect']!r}")
        seen.add(key)
        got = judge(row["said"], row["expect"])
        if got != row["result"]:
            failures.append(
                f"[判题 {index}] {row['said']!r} / {row['expect']!r} 期望 {row['result']} 实际 {got}"
            )
    return failures


def _pool_failures(pools: list[dict], rows: list[dict]) -> list[str]:
    labeled = {(row["said"], row["expect"]): row["result"] for row in rows}
    failures = []
    for pool in pools:
        for kind, result in (("hits", "hit"), ("misses", "miss")):
            for said in pool[kind]:
                got = labeled.get((said, pool["expect"]))
                if got != result:
                    failures.append(
                        f"[词库 {pool['stepId']}] {said!r} 应是 {result}，判题表里是 {got}"
                    )
        overlap = set(pool["hits"]) & set(pool["misses"])
        if overlap:
            failures.append(f"[词库 {pool['stepId']}] 对和错用了同一句 {overlap}")
    return failures


def _combo_cases(pools: list[dict], patterns: list[list[dict]]) -> list[tuple]:
    """Build replies that obey the teaching order.

    A later question is reached only after the current one is hit.
    ``assisted`` or an unfinished miss ends the reply list.
    """
    ending = [pattern for pattern in patterns if pattern[-1]["kind"] == "H"]
    stopping = [pattern for pattern in patterns if pattern[-1]["kind"] == "A"]
    partial = [pattern for pattern in patterns if pattern[-1]["kind"] == "M"]
    cases = []
    for width in range(1, len(pools) + 1):
        for start in range(len(pools) - width + 1):
            group = pools[start : start + width]
            choices: list[tuple] = list(itertools.product(ending, repeat=width))
            for stop_at in range(width):
                for prefix in itertools.product(ending, repeat=stop_at):
                    for stopper in stopping:
                        choices.append(prefix + (stopper,))
                    for tail in partial:
                        choices.append(prefix + (tail,))
            steps = [{key: pool[key] for key in _STEP_KEYS} for pool in group]
            for choice in choices:
                saids, expected = _expand(group, choice)
                name = f"{group[0]['stepId']}起{width}问 " + " | ".join(
                    "".join(token["kind"] for token in pattern) for pattern in choice
                )
                cases.append((name, steps, saids, expected))
    return cases


def _expand(pools: list[dict], choice: tuple) -> tuple[list[str], list[dict]]:
    saids: list[str] = []
    expected: list[dict] = []
    for step_index, pattern in enumerate(choice):
        pool = pools[step_index]
        for reply_index, token in enumerate(pattern):
            choices = pool["hits"] if token["kind"] == "H" else pool["misses"]
            said = choices[(step_index + reply_index) % len(choices)]
            result = {"H": "hit", "M": "miss", "A": "assisted"}[token["kind"]]
            saids.append(said)
            expected.append(
                {
                    "stepId": pool["stepId"],
                    "said": said,
                    "result": result,
                    "hintUsed": token["hintUsed"],
                }
            )
        last = pattern[-1]["kind"]
        if last == "A":
            extra = pools[step_index + 1] if step_index + 1 < len(pools) else pool
            saids.append(extra["hits"][0])
            break
        if last != "H":
            break
    return saids, expected


def _replay_failures(name: str, steps: list[dict], saids: list[str], expected: list[dict]) -> list[str]:
    session = {"steps": steps}
    before = copy.deepcopy(session)
    try:
        got = record_replies(session, saids)
    except ValueError:
        return [f"[回放 {name}] 不该拒绝"]
    failures = []
    if session != before:
        failures.append(f"[回放 {name}] 改动了原来的辅导")
    if got != expected:
        failures.append(f"[回放 {name}] 记录不一致\n    期望 {expected}\n    实际 {got}")
    failures.extend(f"[回放 {name}] {problem}" for problem in _structure_problems(steps, saids, got))
    return failures


def _structure_problems(steps: list[dict], saids: list[str], events: list[dict]) -> list[str]:
    problems = []
    if [event["said"] for event in events] != saids[: len(events)]:
        problems.append("原话没有按顺序留下")
    if any(set(event) != _EVENT_KEYS for event in events):
        problems.append("记录字段不对")
    assisted_at = [index for index, event in enumerate(events) if event["result"] == "assisted"]
    if len(assisted_at) > 1 or (assisted_at and assisted_at[0] != len(events) - 1):
        problems.append("assisted 不在最后")
    expect_of = {step["stepId"]: step["expect"] for step in steps}
    index = 0
    seen: list[str] = []
    for step in steps:
        group = []
        while index < len(events) and events[index]["stepId"] == step["stepId"]:
            group.append(events[index])
            index += 1
        if not group:
            continue
        seen.append(step["stepId"])
        misses = [event for event in group if event["result"] == "miss"]
        if len(misses) > 2:
            problems.append(f"{step['stepId']} 错了超过两次还在记 miss")
        for number, event in enumerate(misses, start=1):
            if event["hintUsed"] != number:
                problems.append(f"{step['stepId']} 第 {number} 次错记成了 {event['hintUsed']}")
        endings = [event for event in group if event["result"] != "miss"]
        if len(endings) > 1:
            problems.append(f"{step['stepId']} 有多条结束记录")
        if endings and endings[0]["hintUsed"] != len(misses):
            problems.append(f"{step['stepId']} 结束时的次数不对")
        if endings and endings[0]["result"] == "assisted" and len(misses) != 2:
            problems.append(f"{step['stepId']} 不是错满两次就 assisted")
        for event in group:
            if event["hintUsed"] not in {0, 1, 2}:
                problems.append("次数不是 0、1、2")
            judged = judge(event["said"], expect_of[step["stepId"]])
            if event["result"] == "hit" and judged != "hit":
                problems.append(f"{event['said']!r} 记成 hit，判题是 {judged}")
            if event["result"] in {"miss", "assisted"} and judged != "miss":
                problems.append(f"{event['said']!r} 记成 {event['result']}，判题是 {judged}")
    if index != len(events):
        problems.append("问的顺序乱了，或出现了没有的问")
    if seen != [step["stepId"] for step in steps][: len(seen)]:
        problems.append("跳过了前面的问")
    finished = bool(events) and events[-1]["result"] == "hit" and events[-1]["stepId"] == steps[-1]["stepId"]
    stopped = bool(events) and events[-1]["result"] == "assisted"
    if not finished and not stopped and len(events) != len(saids):
        problems.append("还没讲完，却丢掉了后面的原话")
    if (finished or stopped) and len(events) > len(saids):
        problems.append("记录比原话还多")
    return problems


def _refused_failures(item: dict) -> list[str]:
    session = {} if item.get("steps") is None else {"steps": item["steps"]}
    before = copy.deepcopy(session)
    reasons = check_session(session)["checkReasons"]
    failures = []
    if reasons != item["checkReasons"]:
        failures.append(
            f"[拒绝 {item['name']}] 原因不一致 期望 {item['checkReasons']} 实际 {reasons}"
        )
    try:
        record_replies(session, item["saids"])
    except ValueError:
        refused = True
    else:
        refused = False
    if not refused:
        failures.append(f"[拒绝 {item['name']}] 仍然讲了题")
    if session != before:
        failures.append(f"[拒绝 {item['name']}] 改动了原来的辅导")
    return failures


def _assembly_count(data: dict) -> int:
    profiles = data["facts"]
    return len(profiles) * (len(data["traces"]) + len(data["refused"])) + len(data["assemblies"])


def _assembly_failures(data: dict) -> list[str]:
    failures = []
    for fact in data["facts"]:
        for trace in data["traces"]:
            failures.extend(_finish_pass(fact, trace["name"], trace["steps"], trace["saids"], trace["events"]))
        for item in data["refused"]:
            failures.extend(
                _finish_fail(
                    fact,
                    item["name"],
                    item.get("steps"),
                    item["saids"],
                    item["checkReasons"],
                    f"这道题先不讲。{fact['sessionId']}",
                )
            )
    for item in data["assemblies"]:
        failures.extend(_finish_written(item))
    return failures


def _plant(fact: dict, steps: list[dict] | None) -> dict:
    session = {key: fact[key] for key in _FACTS}
    if steps is not None:
        session["steps"] = copy.deepcopy(steps)
    session["events"] = [{"stepId": "old", "said": "旧记录", "result": "hit", "hintUsed": 9}]
    session["checkPassed"] = False
    session["checkReasons"] = ["empty_steps"]
    session["title"] = "不该留下"
    return session


def _finish_pass(fact: dict, name: str, steps: list[dict], saids: list[str], events: list[dict]) -> list[str]:
    session = _plant(fact, steps)
    before = copy.deepcopy(session)
    try:
        got = finish_session(session, saids, note="不该出现的兜底")
    except ValueError:
        return [f"[成稿 {fact['name']} / {name}] 不该拒绝"]
    problems = _record_problems(got, fact, steps, True, [])
    if session != before:
        problems.append("改动了输入")
    if got["events"] != events:
        problems.append(f"记录不一致\n    期望 {events}\n    实际 {got['events']}")
    if any(event["said"] == "不该出现的兜底" for event in got["events"]):
        problems.append("检查通过时写入了兜底的话")
    if got["events"] != record_replies({"steps": steps}, saids):
        problems.append("和回放函数的结果不一致")
    return [f"[成稿 {fact['name']} / {name}] {problem}" for problem in problems]


def _finish_fail(
    fact: dict,
    name: str,
    steps: list[dict] | None,
    saids: list[str],
    reasons: list[str],
    note: str,
) -> list[str]:
    session = _plant(fact, steps)
    before = copy.deepcopy(session)
    try:
        got = finish_session(session, saids, note)
    except ValueError:
        return [f"[成稿 {fact['name']} / {name}] 不该拒绝"]
    kept = [] if steps is None else steps
    problems = _record_problems(got, fact, kept, False, reasons)
    if session != before:
        problems.append("改动了输入")
    expected = [{"stepId": "", "said": note, "result": "note", "hintUsed": 0}]
    if got["events"] != expected:
        problems.append(f"失败时不是这一条 note\n    期望 {expected}\n    实际 {got['events']}")
    judged = [event for event in got["events"] if event["result"] != "note"]
    if judged:
        problems.append("失败时仍判了对错")
    return [f"[成稿 {fact['name']} / {name}] {problem}" for problem in problems]


def _finish_written(item: dict) -> list[str]:
    steps = item.get("steps")
    session = _plant(item, steps)
    before = copy.deepcopy(session)
    try:
        got = finish_session(session, item["saids"], item.get("note"))
    except ValueError:
        if item.get("raises"):
            if session != before:
                return [f"[成稿 {item['name']}] 拒绝时改动了输入"]
            return []
        return [f"[成稿 {item['name']}] 不该拒绝"]
    if item.get("raises"):
        return [f"[成稿 {item['name']}] 应该拒绝"]
    kept = [] if steps is None else steps
    problems = _record_problems(got, item, kept, item["checkPassed"], item["checkReasons"])
    if session != before:
        problems.append("改动了输入")
    if got["events"] != item["events"]:
        problems.append(f"记录不一致\n    期望 {item['events']}\n    实际 {got['events']}")
    return [f"[成稿 {item['name']}] {problem}" for problem in problems]


def _record_problems(
    got: dict,
    fact: dict,
    steps: list[dict],
    passed: bool,
    reasons: list[str],
) -> list[str]:
    problems = []
    if set(got) != _RECORD_KEYS:
        problems.append(f"字段不对 {sorted(set(got) ^ _RECORD_KEYS)}")
    for key in _FACTS:
        if got[key] != fact[key]:
            problems.append(f"{key} 被改了")
    if got["steps"] != steps:
        problems.append("原来的问没有原样留下")
    if got["checkPassed"] != passed or got["checkReasons"] != reasons:
        problems.append(
            f"检查结果不一致 期望 {passed} {reasons} 实际 {got['checkPassed']} {got['checkReasons']}"
        )
    if got["steps"] is steps and steps:
        problems.append("问和输入是同一份")
    return problems
