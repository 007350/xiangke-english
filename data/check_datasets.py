"""Run the kernel over the sample datasets and print what disagreed."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from synthetic_check import evaluate
from xiangke.kernel import check_plan, check_session, finish_session, judge, record_replies

DATA = Path(__file__).resolve().parent


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    failures = 0
    answer_rows = load(DATA / "answers.json")
    for index, row in enumerate(answer_rows, start=1):
        got = judge(row["said"], row["expect"])
        mark = "通过" if got == row["result"] else "不符"
        if got != row["result"]:
            failures += 1
        print(f"[判题 {index}] {mark}  said={row['said']!r}  expect={row['expect']!r}  期望 {row['result']}  实际 {got}")

    for path in sorted((DATA / "sessions").glob("*.json")):
        item = load(path)
        session = item["session"]
        got = check_session(session)
        same = got == {
            "checkPassed": session["checkPassed"],
            "checkReasons": session["checkReasons"],
        }
        if not same:
            failures += 1
        print(f"[辅导 {path.name}] {'通过' if same else '不符'}")
        print(f"    期望 {session['checkReasons'] or '通过'}  实际 {got['checkReasons'] or '通过'}")
        if not session["checkPassed"]:
            bad = [event for event in session["events"] if event["result"] != "note"]
            if bad:
                failures += 1
                print("    不符  检查失败时仍有答对或答错记录")
            try:
                record_replies(session, ["am"])
            except ValueError:
                print("    通过  检查失败时拒绝讲题")
            else:
                failures += 1
                print("    不符  检查失败时仍然讲题")
            note = next(
                (event["said"] for event in session["events"] if event["result"] == "note"),
                None,
            )
            try:
                built = finish_session(session, ["am"], note)
            except ValueError:
                failures += 1
                print("    不符  检查失败时没有收下兜底的话")
            else:
                if built != session:
                    failures += 1
                    print("    不符  整条辅导对不上")
                else:
                    print("    通过  整条辅导")
            continue
        steps = {step["stepId"]: step for step in session["steps"]}
        for event in session["events"]:
            if event["result"] not in {"hit", "miss"}:
                continue
            got_judge = judge(event["said"], steps[event["stepId"]]["expect"])
            if got_judge != event["result"]:
                failures += 1
                print(f"    不符  {event['said']!r} 记录为 {event['result']}  程序判为 {got_judge}")
        saids = [event["said"] for event in session["events"]]
        got_events = record_replies(session, saids)
        if got_events != session["events"]:
            failures += 1
            print("    不符  回放与文件里的记录不一致")
        else:
            print("    通过  回放")
        built = finish_session(session, saids)
        if built != session:
            failures += 1
            print("    不符  整条辅导对不上")
        else:
            print("    通过  整条辅导")

    for path in sorted((DATA / "plans").glob("*.json")):
        item = load(path)
        plan = item["plan"]
        got = check_plan(plan, item["previousVersion"])
        same = got == {
            "checkPassed": plan["checkPassed"],
            "checkReasons": plan["checkReasons"],
        }
        if not same:
            failures += 1
        print(f"[计划 {path.name}] {'通过' if same else '不符'}")
        print(f"    期望 {plan['checkReasons'] or '通过'}  实际 {got['checkReasons'] or '通过'}")

    report = evaluate(DATA / "synthetic.json")
    if report["failures"]:
        failures += len(report["failures"])
        for item in report["failures"][:20]:
            print(item)
        left = len(report["failures"]) - 20
        if left > 0:
            print(f"还有 {left} 处合成数据不符")
    else:
        print(
            "[合成] 通过"
            f"  判题 {report['answers']}"
            f"  回放 {report['traces']}"
            f"  拒绝 {report['refused']}"
            f"  组合 {report['combo']}"
            f"  成稿 {report['assemblies']}"
        )

    print("全部相符" if failures == 0 else f"有 {failures} 处不符")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
