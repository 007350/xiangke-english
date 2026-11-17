import subprocess
from pathlib import Path

root = Path(r"D:\competition")
cli = root / "tools" / "llama.cpp" / "llama-cli.exe"
model = root / "xiangke-v4-最终版-Q4_K_M.gguf"
sysf = root / "prompts" / "system_prompt.txt"
grammar = root / "prompts" / "steps.gbnf"
out_dir = root / "prompts" / "runs"
out_dir.mkdir(exist_ok=True)

examples = """[
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

items = [
    ("They ___ my friends. (am / is / are)", "先让他看主语，再选该用的那个词。"),
    ("This is an ___. (apple / apples)", "先看空前面的词，再选该用的那个词。"),
    ("改错（只改一个词）：She like cats.", "先问哪个词错了，再问它该改成什么。"),
    ("看中文写单词：书 b__k", "看着给出的字母，让他自己把缺的部分说出来。"),
]

for index, (question, voice) in enumerate(items, start=1):
    prompt = (
        f"题目（recognizedText，孩子已确认）：{question}\n"
        f"讲法（voice）：{voice}\n"
        "孩子犯过的错（reminders）：（无）\n"
        "禁令（forbid）：不要直接说出标准答案。\n"
        "拆题样例（examples，只模仿拆法，答案与本题无关）：\n"
        f"{examples}\n"
        "请按要求只输出 JSON。"
    )
    (out_dir / f"{index}.prompt.txt").write_text(prompt, encoding="utf-8")
    completed = subprocess.run(
        [
            str(cli),
            "-m",
            str(model),
            "-sysf",
            str(sysf),
            "-p",
            prompt,
            "--reasoning",
            "off",
            "--temp",
            "0",
            "-st",
            "-c",
            "4096",
            "-n",
            "512",
            "--no-display-prompt",
            "--grammar-file",
            str(grammar),
        ],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    (out_dir / f"{index}.out.txt").write_text(completed.stdout, encoding="utf-8")
    (out_dir / f"{index}.err.txt").write_text(completed.stderr, encoding="utf-8")
    print(f"===== {index} exit {completed.returncode} =====")
    print(question)
