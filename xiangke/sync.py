"""手机这边的同步：把没传的辅导传上去，把回复里的新计划存下来。

云端确认收到的才标记已同步。没连上就什么都不改，下次再传。
新计划要过手机自己的检查，没过就继续用旧的，已传的记录不受影响。
"""

from __future__ import annotations

import http.client
import json
import urllib.parse
import urllib.request

from xiangke.store import PlanRefused, Store
from xiangke.questions import valid_question

DEFAULT_CLOUD = "http://127.0.0.1:8766"
TIMEOUT = 60  # 云端在后台写讲法和出题，同步本身不等大模型


class SyncFailed(Exception):
    """这次没同步成功。手机上的记录和计划都没动。"""


def post_json(url: str, body: dict, *, timeout: float = TIMEOUT) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data, {"Content-Type": "application/json; charset=utf-8"})
    # 电脑开了代理时，发给本机的请求不能走代理
    local = urllib.parse.urlsplit(url).hostname in ("127.0.0.1", "localhost")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if local else urllib.request.build_opener()
    try:
        with opener.open(request, timeout=timeout) as response:
            return json.load(response)
    except (OSError, ValueError, http.client.HTTPException) as error:
        raise SyncFailed(str(error)) from error


def acknowledge_plan(cloud_url: str, child_id: str, version: int) -> None:
    try:
        post_json(cloud_url.rstrip("/") + "/api/plan-received",
                  {"childId": child_id, "planVersion": version}, timeout=2)
    except SyncFailed:
        # 手机已经存好了。回执失败时，下次同步仍会带上本地版本。
        pass


def _plan_problems(plan, child_id: str) -> list[str]:
    """讲题时会用到的内容先看一遍，坏计划不能让讲题出错。"""
    if not isinstance(plan, dict) or plan.get("childId") != child_id:
        return ["wrong_child"]
    reminders = plan.get("reminders") or []
    examples = plan.get("examples") or []
    fine = (
        isinstance(plan.get("voice"), str)
        and isinstance(plan.get("forbid"), str)
        and bool(plan["forbid"].strip())
        and isinstance(reminders, list)
        and all(isinstance(item, str) for item in reminders)
        and isinstance(examples, list)
        and all(isinstance(item, dict) and "recognizedText" in item and "steps" in item for item in examples)
    )
    return [] if fine else ["bad_shape"]


def _drill_ok(item, child_id: str) -> bool:
    if not isinstance(item, dict) or item.get("childId") != child_id:
        return False
    if not isinstance(item.get("seq"), int) or isinstance(item.get("seq"), bool):
        return False
    return all(isinstance(item.get(key), str) and item[key].strip() for key in ("drillId", "text", "answer", "rule", "source"))


def sync_once(store: Store, child_id: str, cloud_url: str = DEFAULT_CLOUD, post=post_json) -> dict:
    """同步一次。连不上或回复看不懂时抛 SyncFailed。"""
    sessions = store.unsynced(child_id)
    have = store.latest_plan(child_id)
    reply = post(
        cloud_url.rstrip("/") + "/api/sync",
        {
            "childId": child_id,
            "planVersion": have["planVersion"] if have else 0,
            "sessions": sessions,
            "drillSeq": store.last_drill_seq(child_id),
            "questionSeq": store.last_question_seq(child_id),
        },
    )
    if not isinstance(reply, dict) or not isinstance(reply.get("accepted"), list):
        raise SyncFailed("云端的回复看不懂")

    sent = {record["sessionId"] for record in sessions}
    accepted = [item for item in reply["accepted"] if item in sent]
    store.mark_synced(accepted)

    result = {"sent": len(sessions), "accepted": len(accepted), "planUpdated": False, "planRefused": []}
    if reply.get("planFailed"):
        result["planFailed"] = True
    if reply.get("working"):
        result["working"] = True
    if reply.get("recorded"):
        result["recorded"] = True
    for key in ("replayPlanMissing", "replayQuestionMissing"):
        if reply.get(key):
            result[key] = True
    if reply.get("questionFailed"):
        result["questionFailed"] = True
    questions = reply.get("questions")
    good_questions = [item for item in questions if valid_question(item, child_id)] if isinstance(questions, list) else []
    # 有坏条目时整批留到下次再取，不能越过坏条目的序号而漏掉它。
    if isinstance(questions, list) and len(good_questions) != len(questions):
        result["questionsRefused"] = True
    else:
        result["questionsUpdated"] = store.save_questions(good_questions)
    drills = reply.get("drills")
    good = [item for item in drills if _drill_ok(item, child_id)] if isinstance(drills, list) else []
    added = store.save_drills(good)
    if added:
        result["drillsAdded"] = added
    plan = reply.get("plan")
    if plan is not None:
        problems = _plan_problems(plan, child_id)
        if problems:
            result["planRefused"] = problems
        else:
            try:
                store.save_plan(plan)
                result["planUpdated"] = True
            except PlanRefused as refused:
                result["planRefused"] = refused.reasons
    result["planVersion"] = store.latest_plan(child_id)["planVersion"] if store.latest_plan(child_id) else 0
    return result
