"""独立检查 v6 数据：和 v5 逐条对比，只允许出现预期的改动。

用法：python 训练/v6/check_ds.py
"""

from __future__ import annotations

import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from xiangke.kernel import check_session, judge  # noqa: E402

V5 = HERE.parent / "v5" / "ds_v5"
V6 = HERE / "ds_v6"
HINT = "这道题的最后答案（程序已算出）："
EXK = "拆题样例（examples，只模仿拆法，答案与本题无关）：\n"
TAIL = "\n请按要求只输出 JSON。"


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    old = {r["id"]: r for r in load(V5 / "train.jsonl")}
    new = load(V6 / "train.jsonl")
    problems: list[tuple[str, str]] = []

    def bad(reason: str, rid: str) -> None:
        problems.append((reason, rid))

    if len(new) != len(old) or {r["id"] for r in new} != set(old):
        bad("条目和 v5 不同", "全部")

    systems = Counter()
    given = Counter()
    for row in new:
        rid = row["id"]
        before = old[rid]
        if row["recognizedText"] != before["recognizedText"] or row["meta"]["gold"] != before["meta"]["gold"]:
            bad("题或答案变了", rid)
        if row["type"] == "intent":
            if row["messages"] != before["messages"]:
                bad("分类数据不该变", rid)
            continue
        systems[row["messages"][0]["content"]] += 1
        user = row["messages"][1]["content"]
        lines = user.split("\n")
        if not lines[0].endswith(row["recognizedText"]):
            bad("第一行不是题目", rid)
        hint_lines = [line for line in lines if line.startswith(HINT)]
        if row["meta"]["answer_given"]:
            given["给"] += 1
            if lines[1] != HINT + row["meta"]["gold"] or len(hint_lines) != 1:
                bad("答案行不对", rid)
        else:
            given["不给"] += 1
            if hint_lines:
                bad("不该有答案行", rid)
        # 去掉答案行和删掉的句子后，用户消息要和 v5 相同
        restored = "\n".join(line for line in lines if not line.startswith(HINT))
        if restored != before["messages"][1]["content"].replace("句子里还有 now 表示现在，", ""):
            bad("用户消息多了别的改动", rid)
        # 训练脚本会处理的示例部分要能解析
        if EXK in user:
            start = user.index(EXK) + len(EXK)
            json.loads(user[start: user.rindex(TAIL)])
        steps = json.loads(row["messages"][2]["content"])["steps"]
        old_steps = json.loads(before["messages"][2]["content"])["steps"]
        if [s["ask"] for s in steps] != [s["ask"] for s in old_steps] or [s["expect"] for s in steps] != [s["expect"] for s in old_steps]:
            bad("问句或答案变了", rid)
        for a, b in zip(steps, old_steps):
            if a["explain"] != b["explain"].replace("句子里还有 now 表示现在，", ""):
                bad("解释有别的改动", rid)
        if not check_session({"steps": steps})["checkPassed"]:
            bad("check_session 不通过", rid)
        if judge(steps[-1]["expect"], row["meta"]["gold"]) != "hit":
            bad("最后一问不是答案", rid)
        joined = row["messages"][2]["content"]
        if "句子里还有 now" in joined:
            bad("还有 now 句子", rid)
        if "第三人称" in joined:
            bad("答案里有第三人称", rid)

    if len(systems) != 1:
        bad("系统提示词不止一种", str(len(systems)))
    system = next(iter(systems))
    print("系统提示词新增规则:", [line for line in system.splitlines() if line.startswith("7.")])
    print("给答案", given["给"], "不给答案", given["不给"], "比例", round(given["给"] / (given["给"] + given["不给"]), 3))
    by_template = Counter((r["template"].split("/")[0], r["meta"]["answer_given"]) for r in new if r["type"] != "intent")
    print("各来源给答案情况", sorted(by_template.items(), key=str)[:12])

    for name in ("heldout.jsonl", "external_test.jsonl"):
        rows = load(V6 / name)
        before = load(V5 / name)
        if [r["messages"][1:] for r in rows] != [r["messages"][1:] for r in before]:
            bad(f"{name} 除系统提示词外有变化", name)
        if {r["messages"][0]["content"] for r in rows} != {system}:
            bad(f"{name} 系统提示词不对", name)

    # 验收题不能混进训练
    eval_set = json.loads((HERE / "eval_set.json").read_text(encoding="utf-8"))
    texts = {r["recognizedText"] for r in new}
    for item in eval_set["items"]:
        if item["text"] in texts:
            bad("验收题混进了训练", item["text"])

    print("问题数", len(problems))
    for reason, count in Counter(p[0] for p in problems).most_common():
        print(" ", reason, count, [p[1] for p in problems if p[0] == reason][:3])

    rng = random.Random(3)
    print("\n===== 抽样 =====")
    picks = [
        next(r for r in rng.sample(new, len(new)) if r["type"] == "fix" and r["meta"].get("answer_given") and "now" in r["recognizedText"]),
        next(r for r in rng.sample(new, len(new)) if r["template"] == "v5/base_choose" and r["meta"].get("answer_given")),
        next(r for r in rng.sample(new, len(new)) if r["type"] == "spell" and not r["meta"].get("answer_given")),
    ]
    for row in picks:
        print("\n题:", row["recognizedText"], "| 给答案:", row["meta"]["answer_given"])
        print("用户消息开头:", " / ".join(row["messages"][1]["content"].split("\n")[:3])[:150])
        for step in json.loads(row["messages"][2]["content"])["steps"]:
            print("  问:", step["ask"], "| 答:", step["expect"], "| 解:", step["explain"])
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
