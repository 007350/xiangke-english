"""Run a few correction items through the local model and print the result."""

import json
import sys

from xiangke.llama_write import llama_write
from xiangke.verb_3s import run_correction

ITEMS = [
    "改错（只改一个词）：She like cats.",
    "改错（只改一个词）：He play football.",
    "改错（只改一个词）：It look nice.",
    "改错（只改一个词）：She watch TV.",
    "改错（只改一个词）：He study English.",
    "改错（只改一个词）：He go to school.",
]


def main() -> None:
    report = []
    for text in ITEMS:
        result = run_correction(text, llama_write)
        brief = {
            "item": text,
            "source": result["source"],
            "steps": result["steps"],
            "attempts": [
                {"ok": attempt["ok"], "errors": attempt["errors"], "raw": attempt["raw"]}
                for attempt in result["attempts"]
            ],
        }
        report.append(brief)
        print("=" * 60, flush=True)
        print(text, "->", result["source"], flush=True)
        for index, attempt in enumerate(brief["attempts"], start=1):
            print(f"  try{index} ok={attempt['ok']} errors={attempt['errors']}", flush=True)
            print("  raw:", attempt["raw"], flush=True)
        for step in result["steps"]:
            print(f"  {step['stepId']} expect={step['expect']}", flush=True)
            print("   ask:", step["ask"], flush=True)
            print("   explain:", step["explain"], flush=True)
    path = "prompts/runs/verb_3s_try.json"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)
    print("wrote", path, flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
