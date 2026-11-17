"""用本地模型跑几道完整的题：读计划、讲题、判题、存进手机库。

用法：python -m xiangke.try_one [模型文件]
"""

import json
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from xiangke.intent import make_label_of
from xiangke.local_model import DEFAULT_MODEL, LocalModel
from xiangke.one_item import run_item
from xiangke.store import Store

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "demo" / "phone.db"
CHILD = "child_01"
BEIJING = timezone(timedelta(hours=8))
ITEMS = [
    ("They ___ my friends. (am / is / are)", ["不知道", "They", "这是什么意思", "我要去喂鸡", "is", "是 are 吧", "是"]),
    ("改错（只改一个词）：She like cats.", ["like", "太难了", "还是不会", "一个", "liked", "likes"]),
    ("She can ___. (swim / swims)", ["can", "为什么不用变", "不要", "swims", "swim"]),
    ("看中文写单词：苹果 ap_le", ["p", "apple"]),
]


def first_plan(store: Store) -> dict:
    """第一次运行时放入样例计划。以后读手机上最新的一版。"""
    if store.latest_plan(CHILD) is None:
        for name in ("child01_v1.json", "child01_v2.json"):
            store.save_plan(json.loads((ROOT / "data" / "plans" / name).read_text(encoding="utf-8"))["plan"])
    return store.latest_plan(CHILD)


def facts(text: str, plan: dict) -> dict:
    now = datetime.now(BEIJING).isoformat(timespec="seconds")
    return {
        "sessionId": "s_" + uuid.uuid4().hex[:12],
        "childId": CHILD,
        "planVersion": plan["planVersion"],
        "source": "photo",
        "recognizedText": text,
        "confirmed": True,
        "offline": True,
        "startedAt": now,
        "endedAt": now,
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    model = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL
    with Store(DB) as store, LocalModel(model) as local:
        plan = first_plan(store)
        print("计划版本:", plan["planVersion"], "| 提醒:", plan["reminders"])
        for text, replies in ITEMS:
            result = run_item(text, replies, local.write, facts(text, plan), plan, make_label_of(local.write))
            lesson = result["lesson"]
            print("=" * 60)
            print("题目:", text)
            print("程序算出:", (lesson["computed"] or {}).get("answer"), "| 讲法来自:", lesson["source"], "| 不合格原因:", lesson["errors"])
            print("--- 对话 ---")
            for line in result["transcript"]:
                print(" ", line)
            record = result["record"]
            print("--- 记录 --- 答案来自", record["answerSource"])
            for event in record["events"]:
                print(" ", json.dumps(event, ensure_ascii=False))
            store.save_session(record)
        print("=" * 60)
        print("手机上待同步的辅导:", len(store.unsynced(CHILD)), "条")


if __name__ == "__main__":
    main()
