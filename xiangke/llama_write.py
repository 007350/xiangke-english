"""Ask the local 1.7B to explain a correction item. One process per call."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "tools" / "llama.cpp" / "llama-cli.exe"
MODEL = ROOT / "xiangke-v4-最终版-Q4_K_M.gguf"
SYSTEM = (
    "你是乡村小学英语辅导里的“小模型”。程序已经写好每一问的问句和答案。"
    "你只为每一问写 explain：孩子答不出时看的一句温暖的中文解释。"
    "只讲题目给的线索和规则，不要讲别的口诀，不要用语法术语，不要拿别的主语举例。"
    "只输出一个 JSON 对象 {\"steps\":[{\"explain\":\"...\"},{\"explain\":\"...\"}]}，不要输出其他文字。"
)


def llama_write(message: str, grammar: str) -> str:
    """Return the model's raw text. The JSON object is what the checker reads."""
    with tempfile.TemporaryDirectory() as folder:
        folder_path = Path(folder)
        grammar_path = folder_path / "step.gbnf"
        system_path = folder_path / "system.txt"
        grammar_path.write_text(grammar, encoding="utf-8")
        system_path.write_text(SYSTEM, encoding="utf-8")
        completed = subprocess.run(
            [
                str(CLI),
                "-m",
                str(MODEL),
                "-sysf",
                str(system_path),
                "-p",
                message,
                "--reasoning",
                "off",
                "--temp",
                "0",
                "-st",
                "--simple-io",
                "-c",
                "4096",
                "-n",
                "512",
                "--no-display-prompt",
                "--grammar-file",
                str(grammar_path),
            ],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-2000:] or f"llama-cli exited {completed.returncode}")
    return _json_object(completed.stdout)


def _json_object(text: str) -> str:
    start = text.rfind('{"steps"')
    if start < 0:
        return text.strip()
    depth = 0
    for index, char in enumerate(text[start:], start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return text[start:].strip()
