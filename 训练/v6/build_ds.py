"""在 v5 数据上做 v6：练“程序给答案，模型来讲”。

做三件事，题和答案都不变：
1. 系统提示词加一条规则。
2. 七成拆题数据在题目后面加一行程序算出的答案，三成不加（程序算不出时用）。
3. 删掉解释里的“句子里还有 now 表示现在，”，模型会把它学成乱找理由。

用法：python 训练/v6/build_ds.py
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import shutil
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
V5 = HERE.parent / "v5" / "ds_v5"
OUT = HERE / "ds_v6"
HINT_RATE = 0.7
QUESTION = "题目（recognizedText，孩子已确认）："
HINT = "这道题的最后答案（程序已算出）："
TAIL = "只输出一个 JSON 对象，格式："
NEW_RULE = "7. 如果题目后面给了“这道题的最后答案”，最后一问的 expect 必须就是它，每一问的讲解都要和它一致，不能讲成别的答案；前面几问仍然不能说出它。\n"
NOW_CLAUSE = "句子里还有 now 表示现在，"
rng = random.Random(20261105)


def load(name: str) -> list[dict]:
    return [json.loads(line) for line in (V5 / name).read_text(encoding="utf-8").splitlines() if line.strip()]


def new_system(old: str) -> str:
    if old.count(TAIL) != 1 or "\n7. " in old:
        raise RuntimeError("系统提示词和预期不符")
    return old.replace(TAIL, NEW_RULE + TAIL)


def add_hint(user: str, answer: str) -> str:
    first, rest = user.split("\n", 1)
    if not first.startswith(QUESTION):
        raise RuntimeError(f"第一行不是题目：{first[:30]}")
    return f"{first}\n{HINT}{answer}\n{rest}"


def clean_now(row: dict) -> bool:
    bot = row["messages"][2]["content"]
    if NOW_CLAUSE not in bot:
        return False
    row["messages"][2]["content"] = bot.replace(NOW_CLAUSE, "")
    return True


def clean_examples(row: dict) -> None:
    """示例里也去掉，用户消息里的文字同步替换。"""
    for example in row["plan"].get("examples", []):
        for step in example["steps"]:
            if "explain" in step:
                step["explain"] = step["explain"].replace(NOW_CLAUSE, "")
    row["messages"][1]["content"] = row["messages"][1]["content"].replace(NOW_CLAUSE, "")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    train = load("train.jsonl")
    held = load("heldout.jsonl")
    ext = load("external_test.jsonl")
    system_old = next(r for r in train if r["type"] != "intent")["messages"][0]["content"]
    system_new = new_system(system_old)

    final, hinted, cleaned = [], 0, 0
    for source in train:
        row = copy.deepcopy(source)
        if row["type"] != "intent":
            row["messages"][0]["content"] = system_new
            cleaned += clean_now(row)
            clean_examples(row)
            give = rng.random() < HINT_RATE
            row["meta"]["answer_given"] = give
            if give:
                row["messages"][1]["content"] = add_hint(row["messages"][1]["content"], row["meta"]["gold"])
                hinted += 1
        final.append(row)

    with open(OUT / "train.jsonl", "w", encoding="utf-8", newline="\n") as handle:
        for row in final:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    # 留出题不给答案，和 v4、v5 比的是模型自己做题的水平。只换系统提示词。
    for name, rows in (("heldout.jsonl", held), ("external_test.jsonl", ext)):
        with open(OUT / name, "w", encoding="utf-8", newline="\n") as handle:
            for source in rows:
                row = copy.deepcopy(source)
                row["messages"][0]["content"] = system_new
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    stats = {
        "train": len(final),
        "answer_given": hinted,
        "answer_not_given": sum(r["type"] != "intent" for r in final) - hinted,
        "intent": sum(r["type"] == "intent" for r in final),
        "now_clause_removed": cleaned,
        "by_type": dict(Counter(r["type"] for r in final)),
        "md5": {
            name: hashlib.md5((OUT / name).read_bytes()).hexdigest()
            for name in ("train.jsonl", "heldout.jsonl", "external_test.jsonl")
        },
    }
    (OUT / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    (HERE / "system_prompt.txt").write_text(system_new, encoding="utf-8")
    print(json.dumps({k: v for k, v in stats.items() if k != "md5"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
    shutil.copyfile(HERE.parent / "v5" / "eval_set_v5.json", HERE / "eval_set.json")
