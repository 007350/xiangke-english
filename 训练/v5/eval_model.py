"""用本地 GGUF 考模型。同一份题考 v4 和 v5，结果写进 训练/v5/eval_results/。

用法：python 训练/v5/eval_model.py <模型文件> <名字> [new] [given] [intent] [ext]
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from xiangke.kernel import check_session, judge  # noqa: E402

SERVER = ROOT / "tools" / "llama.cpp" / "llama-server.exe"
GRAMMAR = (ROOT / "prompts" / "steps.gbnf").read_text(encoding="utf-8")
TRAIN = HERE.parent / "v4" / "ds_v4" / "train.jsonl"
EXT = HERE.parent / "v4" / "ds_v4" / "external_test.jsonl"
EVSET = HERE.parent / "v4" / "eval_set.json"
OUT = HERE / "eval_results"
PORT = 8093
URL = f"http://127.0.0.1:{PORT}"
BOS, EOS = "<｜start▁of▁sentence｜>", "<｜end▁of▁sentence｜>"
VOICE = "一步一步问，让他自己说出答案。"
EXAMPLES = """[
 {
  "recognizedText": "She ___ my sister. (am / is / are)",
  "steps": [
   {"ask": "先找主语：这句话里是谁是我的姐姐？说出那个英文词。", "expect": "She"},
   {"ask": "She 是一个人，不是 I 也不是 you。从 am / is / are 里选一个填空。", "expect": "is"}
  ]
 },
 {
  "recognizedText": "改错：We is good friends.",
  "steps": [
   {"ask": "这句话的主语是哪个词？", "expect": "We"},
   {"ask": "We 是好几个人。句子里哪个词用错了？", "expect": "is"},
   {"ask": "那它应该改成哪个词？", "expect": "are"}
  ]
 }
]"""
INTENT_SYSTEM = (
    "你负责判断小学生对这一问的回复属于哪一类，只选一类：\n"
    "答题：孩子说出了一个答案，不管对错。\n"
    "不会：孩子说不知道、不会、想不出来。\n"
    "提问：孩子在问为什么、是什么意思、怎么做。\n"
    "其他：和题目无关的话。\n"
    '只输出一个 JSON 对象，格式：{"label":"类别"}，不要输出任何其他文字。'
)
INTENT_GRAMMAR = 'root ::= "{\\"label\\": \\"" ("答题" | "不会" | "提问" | "其他") "\\"}"'


def train_system() -> tuple[str, str]:
    rows = [json.loads(line) for line in TRAIN.read_text(encoding="utf-8").splitlines() if line.strip()]
    empty = next(r for r in rows if not r["plan"]["reminders"])["messages"][1]["content"]
    line = next(item for item in empty.splitlines() if item.startswith("孩子犯过的错"))
    return rows[0]["messages"][0]["content"], line


def generate(system: str, user: str, grammar: str, limit: int) -> tuple[str, float]:
    # 与训练时的模板相同：<|Bot|> 后带 </think>
    text = f"{BOS}<|System|>\nyou are a helpful assistant.\n\n{system}{EOS}{BOS}<|User|>{user}{EOS}{BOS}<|Bot|></think>"
    body = {"prompt": text, "n_predict": limit, "temperature": 0, "top_k": 1, "seed": 0, "cache_prompt": False, "grammar": grammar}
    request = urllib.request.Request(URL + "/completion", json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"})
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=300) as response:
        data = json.load(response)
    return data["content"], time.perf_counter() - started


def score_steps(raw: str, answer: str) -> dict:
    try:
        steps = json.loads(raw)["steps"]
        final = str(steps[-1].get("expect", ""))
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return {"valid": False, "final": None, "finalOk": False, "checkPassed": False, "steps": []}
    return {
        "valid": True,
        "steps": steps,
        "final": final,
        "finalOk": judge(final, answer) == "hit",
        "checkPassed": check_session({"steps": steps})["checkPassed"],
    }


def run_new(system: str, empty_line: str, items: list[dict]) -> dict:
    rows = []
    for item in items:
        user = "\n".join(
            [
                f"题目（recognizedText，孩子已确认）：{item['text']}",
                f"讲法（voice）：{VOICE}",
                empty_line,
                "禁令（forbid）：不要直接说出标准答案。",
                "拆题样例（examples，只模仿拆法，答案与本题无关）：",
                EXAMPLES,
                "请按要求只输出 JSON。",
            ]
        )
        raw, seconds = generate(system, user, GRAMMAR, 512)
        row = {**item, "raw": raw, "seconds": round(seconds, 2), **score_steps(raw, item["answer"])}
        joined = json.dumps(row["steps"], ensure_ascii=False)
        sentence = item["text"].split("：")[-1]
        row["calledMe"] = bool(re.match(r"(She|He|It|We|They|You)\b", sentence)) and '"expect": "我"' in joined
        row["jargon"] = "第三人称" in joined
        row["sameWord"] = (
            item["text"].startswith("改错") and len(row["steps"]) >= 2 and row["final"] == row["steps"][0].get("expect")
        )
        if item["kind"] == "spell":
            mask = re.search(r"([A-Za-z_]+)$", item["text"]).group(1)
            letters = [a for m, a in zip(mask, item["answer"]) if m == "_"]
            middle = [str(s.get("expect", "")).casefold() for s in row["steps"][:-1]]
            row["middleOk"] = middle == letters
        rows.append(row)
        print("对" if row["finalOk"] else "错", item["kind"], "|", item["text"], "| 标准", item["answer"], "| 模型", row["final"], f"| {seconds:.1f}s", flush=True)
    groups = defaultdict(lambda: [0, 0])
    for row in rows:
        groups[row["kind"]][1] += 1
        groups[row["kind"]][0] += row["finalOk"]
    spell = [r for r in rows if r["kind"] == "spell"]
    summary = {
        "right": sum(r["finalOk"] for r in rows),
        "total": len(rows),
        "byKind": dict(groups),
        "calledMe": sum(r["calledMe"] for r in rows),
        "jargon": sum(r["jargon"] for r in rows),
        "sameWord": sum(r["sameWord"] for r in rows),
        "spellMiddleOk": f"{sum(r.get('middleOk', False) for r in spell)}/{len(spell)}",
        "avgSeconds": round(sum(r["seconds"] for r in rows) / len(rows), 2),
    }
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return {"summary": summary, "rows": rows}


def answer_grammar(answer: str) -> str:
    """只把最后一问的 expect 锁成程序给的答案，前面最多三问由模型自己写。"""
    literal = json.dumps(json.dumps(answer, ensure_ascii=False), ensure_ascii=False)
    return "\n".join(
        [
            'root ::= "{" space "\\"steps\\"" space ":" space "[" space (step "," space)? (step "," space)? (step "," space)? last space "]" space "}"',
            'step ::= "{" space "\\"ask\\"" space ":" space string "," space "\\"expect\\"" space ":" space string "," space "\\"explain\\"" space ":" space string space "}"',
            f'last ::= "{{" space "\\"ask\\"" space ":" space string "," space "\\"expect\\"" space ":" space {literal} "," space "\\"explain\\"" space ":" space string space "}}"',
            'string ::= "\\"" char* "\\""',
            'char ::= [^"\\\\\\x7F\\x00-\\x1F] | "\\\\" (["\\\\bfnrt] | "u" [0-9a-fA-F]{4})',
            'space ::= | " " | "\\n" [ \\t]{0,20}',
        ]
    )


def run_given(system: str, empty_line: str, items: list[dict]) -> dict:
    """程序给答案、模型来讲。结果还要人逐题读，这里的标记只拦得住看得出的错。"""
    rows = []
    for item in items:
        user = "\n".join(
            [
                f"题目（recognizedText，孩子已确认）：{item['text']}",
                f"这道题的最后答案（程序已算出）：{item['answer']}",
                f"讲法（voice）：{VOICE}",
                empty_line,
                "禁令（forbid）：不要直接说出标准答案。",
                "拆题样例（examples，只模仿拆法，答案与本题无关）：",
                EXAMPLES,
                "请按要求只输出 JSON。",
            ]
        )
        raw, seconds = generate(system, user, answer_grammar(item["answer"]), 512)
        row = {**item, "raw": raw, "seconds": round(seconds, 2), **score_steps(raw, item["answer"])}
        joined = json.dumps(row["steps"], ensure_ascii=False)
        sentence = item["text"].split("：")[-1]
        flags = []
        if not row["checkPassed"]:
            flags.append("检查不过")
        if row["steps"] and re.search("[A-Za-z]", item["answer"]) and not re.search(
            r"(?<![A-Za-z])" + re.escape(item["answer"]) + r"(?![A-Za-z])", row["steps"][-1].get("explain", ""), re.I
        ):
            flags.append("解释没说答案")
        if re.match(r"(She|He|It|We|They|You)\b", sentence) and '"expect": "我"' in joined:
            flags.append("说成我")
        if "第三人称" in joined:
            flags.append("术语")
        if len(row["steps"]) > 3:
            flags.append("问得太多")
        if item["kind"] == "spell":
            mask = re.search(r"([A-Za-z_]+)$", item["text"]).group(1)
            letters = [a for m, a in zip(mask, item["answer"]) if m == "_"]
            if [str(s.get("expect", "")).casefold() for s in row["steps"][:-1]] != letters:
                flags.append("中间字母错")
        row["flags"] = flags
        rows.append(row)
        print(f"{item['kind']} | {item['text']} | 答案 {item['answer']} | 标记 {flags} | {seconds:.1f}s", flush=True)
        for step in row["steps"]:
            print("   问:", step.get("ask"), "| 答:", step.get("expect"), "| 解:", step.get("explain"), flush=True)
    groups = defaultdict(lambda: [0, 0])
    for row in rows:
        groups[row["kind"]][1] += 1
        groups[row["kind"]][0] += not row["flags"]
    summary = {
        "noFlags": sum(not r["flags"] for r in rows),
        "total": len(rows),
        "byKind": dict(groups),
        "avgSteps": round(sum(len(r["steps"]) for r in rows) / len(rows), 2),
        "avgSeconds": round(sum(r["seconds"] for r in rows) / len(rows), 2),
    }
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return {"summary": summary, "rows": rows}


def run_intent(items: list[dict]) -> dict:
    rows = []
    for item in items:
        raw, seconds = generate(INTENT_SYSTEM, f"这一问：{item['question']}\n孩子说：{item['reply']}", INTENT_GRAMMAR, 32)
        label = json.loads(raw)["label"]
        rows.append({**item, "got": label, "ok": label == item["label"], "seconds": round(seconds, 2)})
        print("对" if label == item["label"] else "错", item["label"], "→", label, "|", item["reply"], flush=True)
    groups = defaultdict(lambda: [0, 0])
    for row in rows:
        groups[row["label"]][1] += 1
        groups[row["label"]][0] += row["ok"]
    summary = {
        "right": sum(r["ok"] for r in rows),
        "total": len(rows),
        "byLabel": dict(groups),
        "avgSeconds": round(sum(r["seconds"] for r in rows) / len(rows), 2),
    }
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return {"summary": summary, "rows": rows}


def run_ext() -> dict:
    rows_in = [json.loads(line) for line in EXT.read_text(encoding="utf-8").splitlines() if line.strip()]
    questions = {q["id"]: q for q in json.loads(EVSET.read_text(encoding="utf-8"))["questions"]}
    rows = []
    for row in rows_in:
        answer = questions[row["id"].replace("ext-", "")]["gold"]
        raw, seconds = generate(row["messages"][0]["content"], row["messages"][1]["content"], GRAMMAR, 512)
        scored = score_steps(raw, answer)
        rows.append({"id": row["id"], "text": row["recognizedText"], "answer": answer, "raw": raw, "seconds": round(seconds, 2), **scored})
        print("对" if scored["finalOk"] else "错", row["recognizedText"], "| 标准", answer, "| 模型", scored["final"], flush=True)
    summary = {
        "right": sum(r["finalOk"] for r in rows),
        "checkPassed": sum(r["checkPassed"] for r in rows),
        "total": len(rows),
        "avgSeconds": round(sum(r["seconds"] for r in rows) / len(rows), 2),
    }
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return {"summary": summary, "rows": rows}


def main() -> None:
    model, name, *parts = sys.argv[1:]
    parts = parts or ["new", "intent", "ext"]
    OUT.mkdir(exist_ok=True)
    data = json.loads((HERE / "eval_set_v5.json").read_text(encoding="utf-8"))
    system, empty_line = train_system()
    # 考 v6 时用 v6 的提示词：先设置环境变量 XK_SYSTEM_FILE
    if os.environ.get("XK_SYSTEM_FILE"):
        system = Path(os.environ["XK_SYSTEM_FILE"]).read_text(encoding="utf-8")
    log = open(OUT / f"{name}_server.log", "w", encoding="utf-8")
    server = subprocess.Popen(
        [str(SERVER), "-m", model, "--host", "127.0.0.1", "--port", str(PORT), "-c", "4096", "--reasoning", "off"],
        cwd=ROOT, stdout=log, stderr=log,
    )
    try:
        for _ in range(120):
            try:
                with urllib.request.urlopen(URL + "/health", timeout=2) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(1)
        for part in parts:
            print("=====", name, part, flush=True)
            if part == "new":
                result = run_new(system, empty_line, data["items"])
            elif part == "given":
                result = run_given(system, empty_line, data["items"])
            elif part == "intent":
                result = run_intent(data["intent"])
            else:
                result = run_ext()
            (OUT / f"{name}_{part}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    finally:
        server.terminate()
        try:
            server.wait(timeout=30)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close()
    print("done", flush=True)


if __name__ == "__main__":
    main()
