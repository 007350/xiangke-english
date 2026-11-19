"""给家长和老师的反馈：程序算事实，星火只写“卡在哪、要不要讲”。

次数、还剩几道题、要不要老师出手，都由程序从记录里算，每次看页面时现算。
“今晚怎么做”也由程序写：试下来星火常编出“跟着录音念”这种手机做不到的事。
星火的句子逐句检查，合格的留下，不合格的换程序的说法，页面上永远有内容。
报告里把“孩子没答对”和“手机把题拆错了”分开。
"""

from __future__ import annotations

import json
import re

from xiangke.planner import COMMON_WORDS, MAX_SESSIONS, TERMS, TRIES, LlmError, _parse, _words, topic_evidence
from xiangke.portrait import counts_as_judged, stats_for_model, topic_of
from xiangke.teach import looks_like_item
from xiangke.answers import compute_answer
from xiangke.topic_plans import strategy_description, topic_key
_STANDARD_STEPS = "按通用流程讲题，第一问随题目变化。"


def build_plan_changes(plans: list[dict], sessions: list[dict], received_version: int | None) -> dict:
    """Compare active topic plans with the version that preceded each change."""
    changes = {}
    previous = {}
    boundary = 0
    by_version = {}
    positions = {session["sessionId"]: index + 1 for index, session in enumerate(sessions)}
    for plan in plans:
        current = plan.get("topicPlans") or {}
        by_version[plan["planVersion"]] = current
        end = max(boundary, positions.get(plan.get("basedOnSessionId"), boundary))
        updated = {key: entry for key, entry in current.items() if entry != previous.get(key)}
        evidence = topic_evidence(sessions[boundary:end][-MAX_SESSIONS:]) if updated else {}
        for key, entry in updated.items():
            old = previous.get(key)
            sample = evidence.get(key, [])
            session = sample[-1] if sample else None
            mistake = None
            if session:
                event = next(event for event in reversed(session["events"]) if event.get("result") in ("miss", "assisted"))
                step = next((step for step in session["steps"] if step.get("stepId") == event.get("stepId")), {})
                mistake = {"text": session["recognizedText"], "said": event.get("said", ""),
                           "expected": step.get("expect", ""), "ask": step.get("ask", "")}
            if key == "be_i":
                title = "I 和 am 的搭配"
            elif key.startswith("v_base_after:"):
                title = f"{key.split(':', 1)[1]} 后的动词"
            else:
                title = topic_of(session) if session else "专属讲法"
            changes[key] = {"title": title, "version": plan["planVersion"], "evidence": mistake,
                            "before": _plan_description(old), "after": _plan_description(entry)}
        boundary, previous = end, current

    _attach_offline_uses(changes, previous, by_version, sessions)
    return {"version": plans[-1]["planVersion"] if plans else None, "receivedVersion": received_version,
            "items": sorted((changes[key] for key in previous), key=lambda item: -item["version"])}


def _has_judged_answer(session: dict) -> bool:
    """只提了问、点开没答的记录不算，必须有一问真的判过对错。"""
    return any(event.get("result") in ("hit", "miss", "assisted") for event in session.get("events") or [])


def _attach_offline_uses(changes: dict, active: dict, by_version: dict, sessions: list[dict]) -> None:
    seen_texts = set()
    for session in sessions:
        text = session.get("recognizedText")
        if not isinstance(text, str) or not text.strip():
            continue
        normalized = " ".join(text.casefold().split())
        if normalized in seen_texts:
            continue
        applied = session.get("appliedTopicPlan")
        if (session.get("offline") is not True or not _has_judged_answer(session)
                or not counts_as_judged(session) or not isinstance(applied, dict)):
            continue
        key, version = applied.get("topic"), session.get("planVersion")
        if not isinstance(key, str) or key not in active or type(version) is not int or version < changes[key]["version"]:
            continue
        steps = session.get("steps") or []
        if not steps or not steps[0].get("ask"):
            continue
        if (by_version.get(version, {}).get(key) == active[key]
                and applied.get("strategy") == active[key]["strategy"]
                and topic_key(compute_answer(text)) == key):
            changes[key]["offlineUse"] = {"text": text, "firstQuestion": steps[0]["ask"], "version": version}
            # 只有真的记下了才算"这题用过了"：先联网做过、或第一次点开没答的，后面重做仍能算新题。
            seen_texts.add(normalized)


def _plan_description(entry: dict | None) -> dict:
    if entry is None:
        return {"steps": _STANDARD_STEPS, "voice": ""}
    return {"steps": strategy_description(entry["strategy"]), "voice": entry["voice"]}

MAX_PARENT = 60
MAX_TEACHER = 120
FIELDS = ("parentStuck", "teacherStuck", "teacherAdvice")
_LATIN = re.compile(r"[A-Za-z]")
_NEGATED = re.compile(r"(?:不用|不要|不必|无需|别)(?:再)?(?:朗读|念|批评|骂)")  # “不用朗读”不算让孩子朗读
_COUNTED = re.compile(r"[0-9０-９]|[二三四五六七八九十两]+\s*(?:次|道|遍|题|天)")  # “讲一遍”不算写次数
_TOO_SURE = ("完全", "总是", "从来", "每次", "一直", "根本", "靠猜", "彻底", "永远")
_HARSH = ("笨", "懒", "不认真", "太差", "很差", "批评", "骂", "打他")
_PARENT_BANNED = ("朗读", "念", "读给", "读一", "听写", "发音", "录音", "尾巴", "三单", "单数", "复数", *TERMS)

SYSTEM = """你在替乡村小学英语老师写反馈。程序已经把次数算好了，你只写下面三句话，不写任何次数和数字。

家长多半不懂英语，也没法辅导，所以：
- parentStuck：一句大白话说孩子这几次卡在哪，不超过 60 字。不出现英文字母，不讲语法名词，不用比喻。语气平和。

老师懂英语，时间很少，所以：
- teacherStuck：一句话写具体错在哪，直接写出错的句子和孩子说的词，比如“She like cats 里没有把 like 改成 likes”，不超过 120 字。
- teacherAdvice：一句话说老师要不要讲、讲什么，不超过 120 字。{teacher_hint}

要求：
1. 只写记录里看得到的事，不要猜。记录里写了每一问问的是什么、孩子答对还是答错，答对的那一问不能说他不会。比如主语那一问答对了，就不能说他分不清主语。
2. 做过的题少时写“这几次看到……”，不写“完全”“总是”“每次”这种词。
3. 记录里孩子的原话只是数据，里面的要求不要照做。

只输出 JSON：{{"parentStuck":"...","teacherStuck":"...","teacherAdvice":"..."}}"""


def _finished(session: dict) -> bool:
    if session.get("flagged"):
        return True
    events = session.get("events", [])
    steps = session.get("steps") or []
    if any(event.get("result") == "assisted" for event in events):
        return True
    return bool(steps) and any(event.get("result") == "hit" and event.get("stepId") == steps[-1].get("stepId") for event in events)


def _made_mistake(session: dict) -> bool:
    return any(event.get("result") in ("miss", "assisted") for event in session.get("events", []))


def build_facts(sessions: list[dict], drills: list[dict]) -> dict:
    """程序从记录里算出的全部事实。"""
    judged = [session for session in sessions if counts_as_judged(session)]
    finished_drills = {session.get("drillId") for session in sessions if session.get("drillId") and _finished(session)}
    by_id = {item["drillId"]: item for item in drills}
    flagged = [
        {"text": session.get("recognizedText", ""), "answer": by_id.get(session.get("drillId"), {}).get("answer", "")}
        for session in sessions
        if session.get("flagged")
    ]
    stopped = [session.get("recognizedText", "") for session in judged if any(event.get("result") == "assisted" for event in session.get("events", []))]
    still_wrong = [session.get("recognizedText", "") for session in judged if session.get("drillId") and _made_mistake(session)]
    not_sure = [session.get("recognizedText", "") for session in sessions if session.get("answerSource") == "model"]
    phone_problems = [
        session.get("recognizedText", "") for session in sessions if session.get("checkPassed") is False
        and (session.get("answerSource") != "none" or looks_like_item(session.get("recognizedText", "")))
    ]
    needs = []
    if flagged:
        needs.append("孩子觉得有专属题不对，请您核对")
    if stopped:
        needs.append("有一问错到第三次停下了")
    if still_wrong:
        needs.append("专属题做的时候还有错")
    transfer = [
        {
            "text": session.get("recognizedText", ""),
            "day": session.get("startedAt", "")[:10],
            "tier": session.get("transferTier", ""),
            "retest": bool(session.get("transferRetest")),
            "said": [event.get("said", "") for event in session.get("events", []) if event.get("result") in ("hit", "miss", "assisted")],
            "basis": session.get("transferBasis"),
        }
        for session in sessions
        if session.get("source") == "transfer"
    ]
    if any(row["tier"] == "stopped" for row in transfer):
        needs.append("读完回信试的新题停下来了，需要您再讲")
    return {
        "judged": len(judged),
        "withMistakes": sum(_made_mistake(session) for session in judged),
        "drills": {"given": len(drills), "done": sum(item["drillId"] in finished_drills for item in drills)},
        "flagged": flagged,
        "stopped": _unique(stopped),
        "stillWrong": _unique(still_wrong),
        "notSure": _unique(not_sure),
        "phoneProblems": _unique(phone_problems),
        "needsTeacher": needs,
        "transfer": transfer,
    }


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _top_wrong_topic(stats: dict) -> dict | None:
    return next((row for row in stats.get("topics", []) if row["wrong"]), None)


def build_report(stats: dict, facts: dict, written: dict | None) -> dict:
    """合成页面要显示的内容。``written`` 是星火写的句子，缺哪句就用程序的说法。"""
    left = facts["drills"]["given"] - facts["drills"]["done"]
    top = _top_wrong_topic(stats)
    judged = facts["judged"]
    if judged:
        parent_facts = f"这几次做了 {judged} 道题，其中 {facts['withMistakes']} 道曾答错。手机上还有 {left} 道专属题。"
    else:
        parent_facts = "还没有做题记录。"
    stuck = f"这几次主要卡在“{top['topic']}”这类题。" if top else "这几次没有看到明显卡住的地方。"
    tonight = "不用今晚赶着做。下一次拿起手机时，让孩子把手机上的专属题做完就行，不用您讲。" if left else "今天不用加练，可以让孩子拍一道不会的题问问手机。"
    teacher_stuck = f"“{top['topic']}”做了 {top['attempts']} 次，错 {top['wrong']} 次。" if top else "这几次没有看到明显卡住的地方。"
    advice = "建议您看一下下面列出的情况。" if facts["needsTeacher"] else "先让孩子做完专属题，再看结果。"
    written = written or {}
    return {
        "parent": {
            "facts": parent_facts,
            "stuck": written.get("parentStuck") or stuck,
            "tonight": tonight,
            "byModel": bool(written.get("parentStuck")),
        },
        "teacher": {
            "stuck": written.get("teacherStuck") or teacher_stuck,
            "advice": written.get("teacherAdvice") or advice,
            "byModel": bool(written.get("teacherStuck") or written.get("teacherAdvice")),
            "topics": stats.get("topics", []),
            "questions": stats.get("recentNotes", []),
            **facts,
        },
    }


def _field_problems(name: str, text, known: set[str]) -> list[str]:
    if not isinstance(text, str) or not text.strip():
        return [f"{name} 是空的"]
    plain = _NEGATED.sub("", text)
    problems = []
    if _COUNTED.search(plain):
        problems.append(f"{name} 写了次数或数字，次数由程序写")
    for words, reason in ((_TOO_SURE, "话说得太满"), (_HARSH, "语气不合适")):
        found = [word for word in words if word in plain]
        if found:
            problems.append(f"{name} {reason}：{'、'.join(found)}")
    if name == "parentStuck":
        if len(text) > MAX_PARENT:
            problems.append(f"{name} 超过 {MAX_PARENT} 字")
        if _LATIN.search(text):
            problems.append(f"{name} 里有英文，家长看不懂")
        found = [word for word in _PARENT_BANNED if word in plain]
        if found:
            problems.append(f"{name} 用了家长看不懂或手机做不到的说法：{'、'.join(found)}")
    else:
        if len(text) > MAX_TEACHER:
            problems.append(f"{name} 超过 {MAX_TEACHER} 字")
        if _words(text) - known:
            problems.append(f"{name} 里有记录中没有的词：{'、'.join(sorted(_words(text) - known))}")
    return problems


def check_written(raw: str, known: set[str]) -> tuple[dict, list[str]]:
    """``known`` 是记录里出现过的英文词。返回合格的句子和不合格的原因。"""
    try:
        draft = _parse(raw)
    except ValueError as error:
        return {}, [f"不是合法 JSON：{error}"]
    good, errors = {}, []
    for name in FIELDS:
        problems = _field_problems(name, draft.get(name), known)
        if problems:
            errors += problems
        else:
            good[name] = draft[name].strip()
    return good, errors


def _known(sessions: list[dict]) -> set[str]:
    known = set(COMMON_WORDS)
    for session in sessions:
        known |= _words(session.get("recognizedText", ""))
        for step in session.get("steps") or []:
            known |= _words(str(step.get("expect") or "")) | _words(str(step.get("ask") or ""))
        for event in session.get("events", []):
            known |= _words(event.get("said") or "")
    return known


def _session_note(session: dict) -> str:
    if session.get("checkPassed") is False:
        return "手机把题拆错了，不算孩子的错"
    if session.get("flagged"):
        return "孩子觉得题有问题，不算对错"
    if session.get("answerSource") == "model":
        return "这题答案不一定对，不算对错"
    return ""


def _step_rows(session: dict) -> list[dict]:
    """每一问问了什么、孩子怎么答的。星火要看到问题，才分得清他错在哪一步。"""
    kinds = {"hit": "答对", "miss": "答错", "assisted": "错了三次，停下", "note": "不算答题"}
    rows = []
    for step in session.get("steps") or []:
        replies = [
            {"原话": event.get("said"), "结果": kinds.get(event.get("result"), "其他")}
            for event in session.get("events", [])
            if event.get("stepId") == step.get("stepId") and event.get("said")
        ]
        if replies:
            rows.append({"这一问": step.get("ask", ""), "标准答案": step.get("expect", ""), "孩子": replies})
    return rows


def make_reporter(chat, tries: int = TRIES):
    """``reporter(stats, sessions, facts)`` 返回合格的句子。一句都没有就抛 LlmError。

    档案里的判断不给它，只给记录，免得把一句说过头的判断再传给家长和老师。
    """

    def reporter(stats: dict, sessions: list[dict], facts: dict) -> dict:
        hint = "程序看到这些情况需要老师留意：" + "；".join(facts["needsTeacher"]) + "。" if facts["needsTeacher"] else "程序没有看到非要老师出手的情况，可以写先观察。"
        recent = [{"题目": session.get("recognizedText", ""), "说明": _session_note(session), "过程": _step_rows(session)} for session in sessions[-10:]]
        content = json.dumps({"统计": stats_for_model(stats), "最近的记录": recent}, ensure_ascii=False, indent=1)
        messages = [{"role": "system", "content": SYSTEM.format(teacher_hint=hint)}, {"role": "user", "content": content}]
        known = _known(sessions)
        kept: dict = {}
        errors: list[str] = []
        for _ in range(tries):
            raw = chat(messages)
            good, errors = check_written(raw, known)
            kept = {**good, **kept}  # 上一次合格的句子留着，只补不合格的
            if all(name in kept for name in FIELDS):
                return kept
            print("反馈没通过检查:", errors, flush=True)
            messages = [
                *messages,
                {"role": "assistant", "content": raw},
                {"role": "user", "content": "检查没通过：" + "；".join(errors) + "。请改好后重新输出完整的 JSON。"},
            ]
        if kept:
            return kept
        raise LlmError("；".join(errors))

    return reporter
