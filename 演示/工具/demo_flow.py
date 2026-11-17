"""按演示步骤走完两轮。第一个参数 record 用真实星火录制，replay 用录好的文件回放。

用法：demo_flow.py record 输出文件 | demo_flow.py replay 录制文件
只用内存数据库，不写项目里的库。
"""
import json
import sys
import threading
import time
from http.server import ThreadingHTTPServer

sys.path.insert(0, r"D:\competition")
sys.stdout.reconfigure(encoding="utf-8")

from xiangke.app import PhoneApp
from xiangke.cloud import CloudApp, CloudStore, make_handler, pick_drill_maker, pick_planner, pick_reporter
from xiangke.local_model import LocalModel
from xiangke.replay import Recorder, Replayer
from xiangke.store import Store
from xiangke.teach import teach

mode, path = sys.argv[1], sys.argv[2]
ROUND_ONE = [
    ("She ___ apples. (like / likes)", {"likes": ["like"]}, {}),
    ("改错（只改一个词）：She like cats.", {"like": ["cats"], "likes": ["like"]}, {"likes": "为什么要加s"}),
    ("They ___ my friends. (am / is / are)", {}, {}),
]


def play(app, opener, wrong, ask):
    view = opener()
    item = app.item
    print(f"\n题目: {view['question']} | 小模型写的: {item.lesson['source'] == 'model'} | 被拦: {item.lesson['errors']}")
    print("  手机:", view["messages"])
    for step in list(item.steps):
        for said in wrong.get(step["expect"], []):
            view = app.reply(said)
            print(f"  孩子: {said}\n  手机: {view['messages']}")
        if step["expect"] in ask:
            view = app.reply(ask[step["expect"]])
            print(f"  孩子: {ask[step['expect']]}\n  手机: {view['messages']}")
        view = app.reply(step["expect"])
        print(f"  孩子: {step['expect']}\n  手机: {view['messages']}")
        if view["done"]:
            break
    app.leave()


def wait_sync(app, label):
    started = time.time()
    while True:
        result = app.sync()["syncResult"]
        if result.get("planUpdated") or result.get("drillsAdded") or not result.get("working"):
            print(f"[{label} {time.time() - started:5.1f} 秒] {result}", flush=True)
        if not result.get("working"):
            return
        time.sleep(4)


def show_report(cloud_app):
    report = cloud_app.report("child_01")
    print("\n给家长:", report["parent"]["facts"], report["parent"]["stuck"], report["parent"]["tonight"], "| 星火写的:", report["parent"]["byModel"])
    teacher = report["teacher"]
    print("给老师:", teacher["stuck"], "||", teacher["advice"], "| 星火写的:", teacher["byModel"])
    print("  留意:", teacher["needsTeacher"], "| 说过的话:", teacher["questions"], "| 专属题:", teacher["drills"])
    print("  分类:", [(row["topic"], row["attempts"], row["wrong"], row["streak"]) for row in teacher["topics"]])
    print("判断:", report["judgement"], report["focus"], "| 回放标记:", report["recorded"])


if mode == "record":
    recorder = Recorder(path)
    maker = pick_drill_maker()
    planner, reporter = recorder.wrap_planner(pick_planner()), recorder.wrap_reporter(pick_reporter())
    maker = recorder.wrap_maker(maker)
else:
    replayer = Replayer(path)
    planner, reporter, maker = replayer.planner, replayer.reporter, replayer.maker

with LocalModel(port=8096) as model, Store(":memory:") as phone, CloudStore(":memory:") as store:
    cloud_app = CloudApp(store, planner, maker, background=True, reporter=reporter, recorded=mode == "replay")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cloud_app))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    app = PhoneApp(model.write, phone, cloud_url=f"http://127.0.0.1:{server.server_address[1]}")
    old_plan = phone.latest_plan("child_01")

    print("===== 第一轮：断网做题 =====")
    for text, wrong, ask in ROUND_ONE:
        play(app, lambda: app.start(text, "photo"), wrong, ask)
    print("\n===== 联网同步 =====")
    app.set_network(True)
    wait_sync(app, "第一次")
    plan = phone.latest_plan("child_01")
    print("新讲法:", json.dumps({key: plan.get(key) for key in ("planVersion", "voice", "reminders", "examples", "forbid")}, ensure_ascii=False, indent=1))
    show_report(cloud_app)
    print("专属题:", json.dumps([{key: row[key] for key in ("text", "answer", "rule", "source")} for row in phone.open_drills("child_01")], ensure_ascii=False, indent=1))

    print("\n===== 同一道题的新旧讲法 =====")
    for text in ("She ___ apples. (like / likes)", "改错（只改一个词）：She like cats.", "He ___ a book. (read / reads)"):
        for label, used in (("旧", old_plan), ("新", plan)):
            lesson = teach(text, model.write, used)
            steps = lesson["steps"] or []
            print(f"{label} [{lesson['source']}] {text}: {[step['ask'] for step in steps]} 被拦: {lesson['errors']}")
            print(f"   解释: {[step['explain'] for step in steps]}")

    print("\n===== 第二轮：断网做专属题，全部做对 =====")
    app.set_network(False)
    for row in phone.open_drills("child_01"):
        play(app, lambda drill_id=row["drillId"]: app.start_drill(drill_id), {}, {})
    print("\n===== 再同步 =====")
    app.set_network(True)
    wait_sync(app, "第二次")
    plan = phone.latest_plan("child_01")
    print("讲法:", json.dumps({key: plan.get(key) for key in ("planVersion", "voice", "reminders")}, ensure_ascii=False, indent=1))
    show_report(cloud_app)
    print("还没做的专属题:", [row["text"] for row in phone.open_drills("child_01")])
    server.shutdown()
    server.server_close()
