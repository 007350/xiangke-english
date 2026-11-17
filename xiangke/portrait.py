"""孩子的长期档案里由程序数出来的部分。次数不让大模型写，免得被编出来。"""

from __future__ import annotations

from xiangke.answers import compute_answer

_TOPICS = {
    "be_verb": "am、is、are",
    "plural_noun": "单数和复数",
    "verb_choice": "动词变不变",
    "verb_3s": "动词变不变",
}
OTHER = "其他"
RECENT_NOTES = 5


def counts_as_judged(session: dict) -> bool:
    """手机拆错、模型试做、孩子觉得题有问题的，都不算孩子的对错。"""
    return session.get("answerSource") in ("program", "cloud") and session.get("checkPassed") is not False and not session.get("flagged")


def topic_of(session: dict) -> str:
    computed = compute_answer(session.get("recognizedText", ""))
    return _TOPICS.get(computed["kind"], OTHER) if computed else OTHER


def build_stats(sessions: list[dict]) -> dict:
    """``sessions`` 是这个孩子的全部辅导，旧的在前。"""
    topics: dict[str, dict] = {}
    notes: list[str] = []
    judged = 0
    for session in sessions:
        events = session.get("events", [])
        notes += [event["said"] for event in events if event.get("result") == "note" and event.get("stepId") and event.get("said")]
        if not counts_as_judged(session) or not any(event.get("result") in ("hit", "miss", "assisted") for event in events):
            continue
        judged += 1
        row = topics.setdefault(topic_of(session), {"topic": topic_of(session), "attempts": 0, "wrong": 0, "streak": 0, "lastWrongAt": None})
        row["attempts"] += 1
        if any(event.get("result") in ("miss", "assisted") for event in events):
            row["wrong"] += 1
            row["streak"] = 0
            row["lastWrongAt"] = session.get("startedAt")
        else:
            row["streak"] += 1
    ordered = sorted(topics.values(), key=lambda row: (-row["wrong"], row["topic"]))
    return {"judgedSessions": judged, "topics": ordered, "recentNotes": notes[-RECENT_NOTES:]}


def stats_for_model(stats: dict) -> list[dict]:
    """发给大模型的统计，不带时间。"""
    return [
        {"哪一类": row["topic"], "做过几次": row["attempts"], "做错几次": row["wrong"], "最近连对几次": row["streak"]}
        for row in stats["topics"]
    ]
