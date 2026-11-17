"""独立检查 v5 训练数据。不复用 build_ds 的词表和模板，发现问题就列出来。

用法：python 训练/v5/check_ds.py
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

DS = HERE / "ds_v5"
V4 = HERE.parent / "v4" / "ds_v4"
TERMS = ("第三人称", "第一人称", "宾格", "比较级", "物主代词", "谓语", "原形")
LABELS = {"答题", "不会", "提问", "其他"}
# 独立的动词变化表：不从 build_ds 导入
SPECIAL = {"go": "goes", "do": "does", "have": "has", "fly": "flies", "study": "studies", "carry": "carries", "try": "tries"}


def third(base: str) -> str:
    if base in SPECIAL:
        return SPECIAL[base]
    if re.search(r"(?:s|x|ch|sh)$", base):
        return base + "es"
    return base + "s"


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def check_new(row: dict, problems: list[tuple[str, str]]) -> None:
    rid, text, gold = row["id"], row["recognizedText"], row["meta"]["gold"]

    def bad(reason: str) -> None:
        problems.append((reason, f"{rid} {text}"))

    if [m["role"] for m in row["messages"]] != ["system", "user", "assistant"]:
        bad("消息结构不对")
        return
    try:
        steps = json.loads(row["messages"][2]["content"])["steps"]
    except (json.JSONDecodeError, KeyError):
        bad("答案不是合法 JSON")
        return
    if not 2 <= len(steps) <= 4:
        bad("问数不对")
    if any(set(s) != {"ask", "expect", "explain"} or not all(str(s[k]).strip() for k in s) for s in steps):
        bad("字段缺失或为空")
        return
    if not check_session({"steps": steps})["checkPassed"]:
        bad("check_session 不通过")
    if judge(steps[-1]["expect"], gold) != "hit":
        bad("最后一问不是标准答案")
    words = re.findall(r"[A-Za-z']+", text)
    joined = row["messages"][2]["content"]
    if any(term in joined for term in TERMS):
        bad("答案里有术语")
    if "主语 There" in joined:
        bad("把 There 当主语")
    for index, step in enumerate(steps[:-1]):
        if judge(step["expect"], gold) == "hit":
            bad("中间一问已经是答案")
        if re.search(r"(?<![A-Za-z])" + re.escape(gold) + r"(?![A-Za-z])", step["explain"], re.I) and row["type"] != "spell":
            bad("前面的解释说出了最终答案")

    if row["type"] == "fix":
        wrong = row["meta"].get("wrong")
        if wrong not in words:
            bad("错词不在句子里")
        if steps[0]["expect"] != wrong:
            bad("第一问不是错词")
        if wrong == gold:
            bad("改了等于没改")
        if " " in gold:
            bad("答案不止一个词")
        if "主语 She 是" in joined and '"expect": "我"' in joined:
            bad("She 被说成我")
    if row["type"] == "choose":
        found = re.search(r"\(([^)]*)\)\s*$", text)
        options = [o.strip() for o in found.group(1).split("/")] if found else []
        if gold not in options:
            bad("答案不在选项里")
    if row["type"] == "spell":
        match = re.search(r"([a-zA-Z_]+)$", text)
        mask = match.group(1) if match else ""
        if len(mask) != len(gold) or any(m != "_" and m != g for m, g in zip(mask, gold)):
            bad("字母模板和答案对不上")
        letters = [g for m, g in zip(mask, gold) if m == "_"]
        if [s["expect"] for s in steps[:-1]] != letters:
            bad("中间字母不对")
        if any(re.search(r"(?<![A-Za-z])" + gold + r"(?![A-Za-z])", s["ask"]) for s in steps):
            bad("问句写出了整个单词")

    # 语法再核对：按句型独立算一遍答案
    template = row["template"]
    if template == "v5/fix_be_pron":
        subject = words[0]
        want = "is" if subject in ("She", "He", "It") else "are"
        if gold != want:
            bad("be 动词答案不对")
    if template == "v5/fix_3s_pron":
        subject = words[0]
        if subject in ("She", "He", "It"):
            if gold != third(row["meta"]["wrong"]):
                bad("加 s 答案不对")
        elif third(gold) != row["meta"]["wrong"]:
            bad("原形答案不对")
    if template == "v5/fix_i" and third(gold) != row["meta"]["wrong"]:
        bad("I 句答案不对")
    if template in ("v5/base_fix",) and third(gold) != row["meta"]["wrong"]:
        bad("原形答案不对")
    if template == "v5/base_choose":
        found = re.search(r"\((\w+) / (\w+)\)", text)
        pair = {found.group(1), found.group(2)}
        if pair != {gold, third(gold)}:
            bad("选项不是原形和加 s 的一对")
    if template == "v5/animal_choose":
        found = re.search(r"\((\w+) / (\w+)\)", text)
        pair = sorted([found.group(1), found.group(2)], key=len)
        if gold != third(pair[0]):
            bad("动物主语答案不对")
    if template == "v5/fix_dont":
        subject = text.split("：")[1].split(" don't")[0].split(" doesn't")[0]
        want = "doesn't" if subject in ("She", "He", "It", "Tom", "Lily", "My sister", "The boy") else "don't"
        if gold != want:
            bad("don't 答案不对")
    if template == "v5/fix_was":
        want = "was" if words[0] in ("I", "She", "He", "It") else "were"
        if gold != want:
            bad("was 答案不对")
    if template in ("v5/fix_there", "v5/choose_there"):
        sentence = text.split("：")[-1]
        many = re.search(r"\b(?:two|three|four|five|six|many|some)\b", sentence) is not None
        if gold != ("are" if many else "is"):
            bad("There 答案不对")


def main() -> None:
    train = load(DS / "train.jsonl")
    old = load(V4 / "train.jsonl")
    held = load(V4 / "heldout.jsonl")
    ext = load(V4 / "external_test.jsonl")
    problems: list[tuple[str, str]] = []
    print("总条数", len(train), "旧", len(old))

    ids = Counter(r["id"] for r in train)
    if ids.most_common(1)[0][1] > 1:
        problems.append(("id 重复", str(ids.most_common(1))))

    # 旧数据：除了术语，答案和标准都不能变
    by_id = {r["id"]: r for r in train}
    for row in old:
        new = by_id.get(row["id"])
        if not new:
            problems.append(("旧数据丢失", row["id"]))
            continue
        if new["recognizedText"] != row["recognizedText"] or new["meta"]["gold"] != row["meta"]["gold"]:
            problems.append(("旧数据的题或答案变了", row["id"]))
        a = json.loads(new["messages"][2]["content"])["steps"]
        b = json.loads(row["messages"][2]["content"])["steps"]
        if [s["expect"] for s in a] != [s["expect"] for s in b] or [s["ask"] for s in a] != [s["ask"] for s in b]:
            problems.append(("旧数据的问或答变了", row["id"]))
        if new["messages"][0]["content"] != row["messages"][0]["content"]:
            problems.append(("旧数据系统提示词变了", row["id"]))

    old_ids = {r["id"] for r in old}
    new_rows = [r for r in train if r["id"] not in old_ids]
    steps_rows = [r for r in new_rows if r["type"] != "intent"]
    intent_rows = [r for r in new_rows if r["type"] == "intent"]
    system = old[0]["messages"][0]["content"]
    seen_text = Counter(r["recognizedText"] for r in steps_rows)
    for text, count in seen_text.items():
        if count > 1:
            problems.append(("新题重复", text))
    forbidden = {r["recognizedText"] for r in old + held + ext}
    for row in steps_rows:
        if row["recognizedText"] in forbidden:
            problems.append(("新题和旧数据重复", row["recognizedText"]))
        if row["messages"][0]["content"] != system:
            problems.append(("系统提示词不一致", row["id"]))
        if f"题目（recognizedText，孩子已确认）：{row['recognizedText']}" not in row["messages"][1]["content"]:
            problems.append(("用户消息里没有题目", row["id"]))
        check_new(row, problems)
    for row in intent_rows:
        label = json.loads(row["messages"][2]["content"]).get("label")
        if label not in LABELS or label != row["meta"]["gold"]:
            problems.append(("分类标签不对", row["id"]))

    # 验收题不能出现在训练里
    eval_set = json.loads((HERE / "eval_set_v5.json").read_text(encoding="utf-8"))
    texts = {r["recognizedText"] for r in train}
    for item in eval_set["items"]:
        if item["text"] in texts:
            problems.append(("验收题混进了训练", item["text"]))
    replies = {r["meta"]["reply"] for r in intent_rows}
    for item in eval_set["intent"]:
        if item["reply"] in replies:
            problems.append(("验收回复混进了训练", item["reply"]))
    print("验收题", len(eval_set["items"]), "验收回复", len(eval_set["intent"]))

    print("新拆题", len(steps_rows), Counter(r["template"] for r in steps_rows).most_common())
    print("新分类", len(intent_rows), Counter(r["meta"]["gold"] for r in intent_rows).most_common())
    print("回复种类", len({r["meta"]["reply"] for r in intent_rows}))
    print("改错错词分布前十", Counter(r["meta"].get("wrong") for r in steps_rows if r["type"] == "fix").most_common(10))
    print("问题数", len(problems))
    for reason, count in Counter(p[0] for p in problems).most_common():
        print(" ", reason, count, [p[1] for p in problems if p[0] == reason][:3])

    still = [r for r in train if r["id"] in old_ids and "第三人称" in r["messages"][2]["content"]]
    print("旧数据答案里还带第三人称", len(still))

    rng = random.Random(7)
    print("\n===== 抽样 =====")
    for template in sorted({r["template"] for r in steps_rows}):
        row = rng.choice([r for r in steps_rows if r["template"] == template])
        print(f"\n[{template}] {row['recognizedText']}")
        for step in json.loads(row["messages"][2]["content"])["steps"]:
            print("  问:", step["ask"], "| 答:", step["expect"], "| 解:", step["explain"])
    print("\n[分类抽样]")
    for row in rng.sample(intent_rows, 16):
        print(" ", row["meta"]["gold"], "|", row["meta"]["reply"], "|", row["meta"]["question"])
    print("\n[旧数据术语替换抽样]")
    old_map = {r["id"]: r for r in old}
    changed = [r for r in train if r["id"] in old_map and r["messages"][2]["content"] != old_map[r["id"]]["messages"][2]["content"]]
    print("答案被改的旧数据", len(changed))
    for row in rng.sample(changed, min(4, len(changed))):
        a = json.loads(old_map[row["id"]]["messages"][2]["content"])["steps"]
        b = json.loads(row["messages"][2]["content"])["steps"]
        for x, y in zip(a, b):
            if x["explain"] != y["explain"]:
                print("  旧:", x["explain"])
                print("  新:", y["explain"])
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
