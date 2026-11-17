# Prompt building + scoring. Scoring uses the user's REAL kernel.py (judge/check_session/_answer_in_text).
import json, re
import kernel

SYSTEM = """你是乡村小学英语辅导里的“小模型”。你只负责把一道孩子已确认的英语题拆成几个小问，给每一问写三样东西：
- ask：问孩子的那一句，用中文开头，一次只问一件事，孩子用一个词、一个字母或一个数字就能回答。
- expect：这一问唯一的简短标准答案（一个英文单词、一个字母、一个数字，或很短的中文）。程序会拿孩子的话和它比对，判对错。
- explain：孩子换了两种说法还答不出时展示的中文解释，讲清道理。
规则：
1. 只做答案唯一的语法和拼写题：选词、填空、改一个词、单词拼写。
2. 最后一问的 expect 必须是整道题的最终答案：填进空里的那个单词，或改正后的那个单词，或拼出的完整单词。
3. 后面某一问的 expect，绝不能出现在前面任何一问的 ask 里。卷面上的选项只能在要选它的那一问里列出。
4. 遵守计划里的禁令（forbid）和讲法（voice），参考孩子犯过的错（reminders）来提醒。
5. 一般拆成 2 到 3 问。
只输出一个 JSON 对象，格式：{"steps":[{"ask":"...","expect":"...","explain":"..."}]}，不要输出任何其他文字。"""

def user_msg(q, plan, with_examples):
    parts = [f"题目（recognizedText，孩子已确认）：{q['recognizedText']}",
             f"讲法（voice）：{plan['voice']}",
             "孩子犯过的错（reminders）：" + " ".join(plan["reminders"]),
             f"禁令（forbid）：{plan['forbid']}"]
    if with_examples:
        ex = [{"recognizedText": e["recognizedText"], "steps": e["steps"]} for e in plan["examples"]]
        parts.append("拆题样例（examples，只模仿拆法，答案与本题无关）：\n" + json.dumps(ex, ensure_ascii=False, indent=1))
    parts.append("请按要求只输出 JSON。")
    return "\n".join(parts)

def parse_output(text):
    """returns (steps|None, strict_ok, lenient_ok)"""
    t = text
    if "</think>" in t:
        t = t.split("</think>")[-1]
    t = t.strip()
    def as_steps(obj):
        if isinstance(obj, dict) and isinstance(obj.get("steps"), list): return obj["steps"]
        if isinstance(obj, list): return obj
        return None
    try:
        s = as_steps(json.loads(t))
        if s is not None: return s, True, True
    except Exception: pass
    cand = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    for c in [cand, cand[cand.find("{"):cand.rfind("}")+1] if "{" in cand else "",
              cand[cand.find("["):cand.rfind("]")+1] if "[" in cand else ""]:
        if not c: continue
        try:
            s = as_steps(json.loads(c))
            if s is not None: return s, False, True
        except Exception: pass
    return None, False, False

CJK = re.compile(r"[\u4e00-\u9fff]")
def _first_meaningful(s):
    for ch in s:
        if ch.isalnum() or CJK.match(ch): return ch
    return ""

def score(q, raw):
    steps, strict, lenient = parse_output(raw)
    r = {"json_strict": strict, "json_valid": lenient}
    schema_ok = bool(steps) and all(isinstance(s, dict) and all(isinstance(s.get(k), str) for k in ("ask","expect","explain")) for s in steps)
    r["schema_ok"] = schema_ok
    if not lenient or not isinstance(steps, list) or not all(isinstance(s, dict) for s in steps):
        r.update(check_pass=False, check_reasons=["unparseable"], final_correct=False, forbid_violation=None,
                 gold_in_any_ask=None, explain_ok=False, ask_chinese=False, n_steps=0, steps=None)
        return r
    steps = [{k: ("" if s.get(k) is None else str(s.get(k))) for k in ("ask","expect","explain")} for s in steps]
    r["steps"] = steps; r["n_steps"] = len(steps)
    c = kernel.check_session({"steps": steps})
    r["check_pass"] = c["checkPassed"]; r["check_reasons"] = c["checkReasons"]
    gold = q["gold"]
    r["final_correct"] = bool(steps) and kernel.judge(steps[-1]["expect"], gold) == "hit"
    gi = next((i for i, s in enumerate(steps) if kernel.judge(s["expect"], gold) == "hit"), None)
    choose = q["type"].startswith("choose_")
    viol = False
    upto = gi if gi is not None else len(steps) - 1
    for i in range(upto):
        if kernel._answer_in_text(steps[i]["ask"], gold): viol = True
    own = gi if gi is not None else len(steps) - 1
    own_has = bool(steps) and kernel._answer_in_text(steps[own]["ask"], gold)
    if own_has and not choose: viol = True   # fill/fix/spell: printing the answer in its own ask = reveal
    r["forbid_violation"] = viol
    r["gold_in_own_ask_choose"] = own_has and choose   # allowed option listing (info only)
    r["gold_in_any_ask"] = any(kernel._answer_in_text(s["ask"], gold) for s in steps)
    r["explain_ok"] = bool(steps) and all(s["explain"].strip() and CJK.search(s["explain"]) for s in steps)
    r["ask_chinese"] = bool(steps) and all(CJK.search(s["ask"]) for s in steps)
    r["strict_pass"] = r["check_pass"] and r["final_correct"] and not viol and r["explain_ok"]
    return r

def summarize(rows):
    n = len(rows)
    if n == 0: return {}
    pct = lambda k: round(100.0 * sum(1 for x in rows if x.get(k)) / n, 1)
    out = {"n": n}
    for k in ["json_valid","json_strict","schema_ok","check_pass","final_correct","forbid_violation",
              "gold_in_any_ask","explain_ok","ask_chinese","strict_pass"]:
        out[k + "_pct"] = pct(k)
    out["leak_pct"] = round(100.0 * sum(1 for x in rows if "answer_leaked" in (x.get("check_reasons") or [])) / n, 1)
    out["missing_expect_pct"] = round(100.0 * sum(1 for x in rows if "missing_expect" in (x.get("check_reasons") or [])) / n, 1)
    out["avg_latency_s"] = round(sum(x["latency_s"] for x in rows) / n, 2)
    out["avg_new_tokens"] = round(sum(x["new_tokens"] for x in rows) / n, 1)
    out["tok_per_s"] = round(sum(x["new_tokens"] for x in rows) / max(1e-9, sum(x["latency_s"] for x in rows)), 1)
    out["avg_steps"] = round(sum(x.get("n_steps", 0) for x in rows) / n, 2)
    return out
