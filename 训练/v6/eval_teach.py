"""把验收题走一遍完整讲题流程，统计模型讲的内容有多少能给孩子看。

用法：python 训练/v6/eval_teach.py <模型文件> <名字>
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from xiangke.kernel import judge  # noqa: E402
from xiangke.local_model import LocalModel  # noqa: E402
from xiangke.teach import teach  # noqa: E402


def main() -> None:
    model, name = sys.argv[1:3]
    items = json.loads((HERE / "eval_set.json").read_text(encoding="utf-8"))["items"]
    rows = []
    with LocalModel(model, port=8096) as local:
        for item in items:
            started = time.perf_counter()
            lesson = teach(item["text"], local.write)
            seconds = time.perf_counter() - started
            final = lesson["steps"][-1]["expect"] if lesson["steps"] else None
            right = bool(final) and judge(final, item["answer"]) == "hit"
            rows.append({**item, "source": lesson["source"], "judged": lesson["judged"], "errors": lesson["errors"],
                         "steps": lesson["steps"], "finalRight": right, "seconds": round(seconds, 1)})
            print(f"{lesson['source']} | {item['kind']} | {item['text']} | 标准 {item['answer']} | 最后 {final} | {'对' if right else '错'} | {lesson['errors']} | {seconds:.1f}s", flush=True)
            for step in lesson["steps"] or []:
                print("   问:", step["ask"], "| 答:", step["expect"], "| 解:", step["explain"], flush=True)
    summary = {
        "total": len(rows),
        "source": dict(Counter(r["source"] for r in rows)),
        "byKind": {k: dict(Counter(r["source"] for r in rows if r["kind"] == k)) for k in sorted({r["kind"] for r in rows})},
        "judgedWrong": [r["text"] for r in rows if r["judged"] and not r["finalRight"]],
        "tryRight": sum(r["finalRight"] for r in rows if r["source"] == "model_try"),
        "tryTotal": sum(r["source"] == "model_try" for r in rows),
        "refusedReasons": Counter(e.split(" ")[0] for r in rows for e in r["errors"]).most_common(),
        "avgSeconds": round(sum(r["seconds"] for r in rows) / len(rows), 1),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1), flush=True)
    out = HERE / "eval_results"
    out.mkdir(exist_ok=True)
    (out / f"{name}_teach.json").write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
