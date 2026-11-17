"""把要给别人看的内容打成压缩包，只放白名单里的文件。

放运行需要的 llama.cpp 文件。模型第一次运行时下载，Python 环境用 requirements.txt 安装。
不放：模型、虚拟环境、完整训练数据、测试记录、备份、内部开发记录。
令牌不放进任何文件。打包时如果环境变量里有 AI_API_KEY，会查包里有没有它。
用法：python 打包/make_package.py
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
NAME = "乡课英语-发布版"
BAT_FILES = ("启动.bat", "启动云端.bat", "启动演示-回放.bat")
MODEL_NOTE = """小模型没有放进这个包，约 1.03 GB。

下载地址：
https://www.modelscope.cn/models/xiaobang123/xiangke-lora-job-v5/resolve/master/gguf/xiangke-v5-Q4_K_M.gguf

下载后放到：models/v5/gguf/xiangke-v5-Q4_K_M.gguf
如果没有放，启动脚本第一次运行时会自动下载。

SHA256：97239079404d0a2159f891cf34d90ab615844cb1309dfa8d0f78262af9f6b168
"""
OUT = HERE / NAME
ZIP = HERE / f"{NAME}.zip"
sys.stdout.reconfigure(encoding="utf-8")

FILES = [
    "xiangke/*.py",
    "app/index.html",
    "app/report.html",
    "tests/*.py",
    "prompts/steps.gbnf",
    "prompts/system_prompt.txt",
    "models/rapidocr/en_PP-OCRv5_rec_mobile.onnx",
    "data/pack.json",
    "data/answers.json",
    "data/synthetic.json",
    "data/动词表.json",
    "data/规则表.md",
    "data/说明.md",
    "data/课包格式.md",
    "data/课包模板.xlsx",
    "data/课包样例.xlsx",
    "演示/演示步骤.md",
    "演示/星火录制.json",
    "演示/题目.png",
    "data/check_datasets.py",
    "data/synthetic_check.py",
    "data/plans/*.json",
    "data/sessions/*.json",
    "训练/v5/build_ds.py",
    "训练/v5/check_ds.py",
    "训练/v5/eval_model.py",
    "训练/v5/make_job.py",
    "训练/v5/eval_set_v5.json",
    "训练/v5/eval_results/*.json",
    "训练/v5/job/train_v5_ms.py",
    "训练/v5/job/run.sh",
    "训练/v5/job/requirements.txt",
    "训练/v5/job/eval_core.py",
    "训练/v5/ds_v5/stats.json",
    "训练/v6/build_ds.py",
    "训练/v6/check_ds.py",
    "训练/v6/eval_teach.py",
    "训练/v6/make_job.py",
    "训练/v6/merge_lora.py",
    "训练/v6/export_gguf.sh",
    "训练/v6/system_prompt.txt",
    "训练/v6/eval_set.json",
    "训练/v6/eval_results/*.json",
    "训练/v6/ds_v6/stats.json",
]
SECRET = re.compile(r"ms-[0-9a-f]{8}-[0-9a-f]{4}|C:\\Users\\[^\\\s]+", re.I)
TEXT = {".py", ".md", ".json", ".txt", ".sh", ".html", ".gbnf", ".jsonl", ".bat"}


def sample_rows() -> str:
    """每种新数据各取几条，让人能看到训练数据长什么样。"""
    rows = [json.loads(line) for line in (ROOT / "训练" / "v6" / "ds_v6" / "train.jsonl").read_text(encoding="utf-8").splitlines()]
    rng = random.Random(1)
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(row["template"].split("/")[0] + "/" + row["template"].split("/")[1], []).append(row)
    picked = []
    for key in sorted(groups):
        if key.startswith("v5/"):
            picked += rng.sample(groups[key], min(2, len(groups[key])))
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in picked) + "\n"


def add_runtime() -> None:
    """放进运行模型必需的 llama.cpp 文件。模型第一次运行时下载。"""
    tools = OUT / "tools" / "llama.cpp"
    tools.mkdir(parents=True)
    source = ROOT / "tools" / "llama.cpp"
    for path in [source / "llama-server.exe", *sorted(source.glob("*.dll"))]:
        shutil.copyfile(path, tools / path.name)
    # Windows 的批处理要用 CRLF 换行
    for name in BAT_FILES:
        bat = (HERE / name).read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        (OUT / name).write_bytes(bat)


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    count = 0
    for pattern in FILES:
        found = sorted(ROOT.glob(pattern))
        if not found:
            print("没有匹配:", pattern)
        for path in found:
            if not path.is_file():
                continue
            target = OUT / path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            count += 1
    shutil.copyfile(HERE / "项目说明.md", OUT / "项目说明.md")
    (OUT / "requirements.txt").write_text("rapidocr==3.10.0\nonnxruntime==1.31.1\n", encoding="utf-8")
    (OUT / "模型下载地址.txt").write_text(MODEL_NOTE, encoding="utf-8")
    add_runtime()
    (OUT / "训练" / "数据样例.jsonl").write_text(sample_rows(), encoding="utf-8")

    problems = []
    token = os.environ.get("AI_API_KEY", "").strip()
    print("查令牌:", "已对照 AI_API_KEY" if token else "环境变量里没有 AI_API_KEY，只按格式查")
    for path in OUT.rglob("*"):
        if path.is_file() and path.suffix in TEXT:
            content = path.read_text(encoding="utf-8", errors="ignore")
            hit = SECRET.search(content)
            if hit:
                problems.append((str(path.relative_to(OUT)), hit.group(0)[:12]))
            if token and token in content:
                problems.append((str(path.relative_to(OUT)), "AI_API_KEY"))
    if problems:
        raise SystemExit(f"发现不该打包的内容，已停止：{problems}")

    if ZIP.exists():
        ZIP.unlink()
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(OUT.rglob("*")):
            if path.is_file():
                # 模型基本压不动，直接存，省时间
                kind = zipfile.ZIP_STORED if path.suffix == ".gguf" else zipfile.ZIP_DEFLATED
                archive.write(path, Path(NAME) / path.relative_to(OUT), compress_type=kind)
    print(f"文件 {sum(1 for p in OUT.rglob('*') if p.is_file())} 个（白名单 {count} 个）")
    print(f"压缩包 {ZIP.stat().st_size / 1024 / 1024:.1f} MB: {ZIP}")


if __name__ == "__main__":
    main()
