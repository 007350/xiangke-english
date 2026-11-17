"""从 v4 的任务文件做出 v5 的任务文件夹。只替换路径和名字，训练配置不动。

用法：python 训练/v5/make_job.py
"""

from __future__ import annotations

import json
import py_compile
import shutil
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
V4 = HERE.parent / "v4"
JOB = HERE / "job"
REPO = "xiaobang123/xiangke-lora-job-v5"


def replace(data: bytes, old: str, new: str, count: int) -> bytes:
    raw_old, raw_new = old.encode("utf-8"), new.encode("utf-8")
    found = data.count(raw_old)
    if found != count:
        raise RuntimeError(f"「{old}」应出现 {count} 次，实际 {found} 次")
    return data.replace(raw_old, raw_new)


def main() -> None:
    if JOB.exists():
        shutil.rmtree(JOB)
    JOB.mkdir()

    # 不改内容的文件
    for name in ("eval_core.py", "kernel.py", "eval_set.json", "requirements.txt"):
        shutil.copyfile(V4 / name, JOB / name)
    shutil.copytree(HERE / "ds_v5", JOB / "ds_v5")

    # 训练脚本
    train = (V4 / "train_v4_ms.py").read_bytes()
    train = replace(train, "# xiangke LoRA v4 -- ModelScope PAI-DSW", "# xiangke LoRA v5 (same hparams as v4, data ds_v5) -- ModelScope PAI-DSW", 1)
    train = replace(train, '"/mnt/workspace/out"', '"/mnt/workspace/out_v5"', 1)
    train = replace(train, '"xiaobang123/xiangke-lora-job"', f'"{REPO}"', 1)
    train = replace(train, 'os.path.join(A.job, "ds_v4")', 'os.path.join(A.job, "ds_v5")', 1)
    (JOB / "train_v5_ms.py").write_bytes(train)

    # 启动脚本：工作目录和输出目录都换新，不碰 v4 留下的结果
    run = (V4 / "run.sh").read_bytes()
    run = replace(run, "xiaobang123/xiangke-lora-job", REPO, 3)
    run = replace(run, "/mnt/workspace/job", "/mnt/workspace/job_v5", 5)
    run = replace(run, "O=/mnt/workspace/out", "O=/mnt/workspace/out_v5", 1)
    run = replace(run, "train_v4_ms.py", "train_v5_ms.py", 1)
    run = replace(run, "xiangke LoRA v4", "xiangke LoRA v5", 1)
    if b"\r\n" in run:
        raise RuntimeError("run.sh 不能有 Windows 换行")
    (JOB / "run.sh").write_bytes(run)

    (JOB / "README.md").write_text(
        "# xiangke-lora-job-v5 (private)\n\n"
        "xiangke LoRA v5 training job for ModelScope PAI-DSW (A10 24GB). Same hparams as v4, data is ds_v5.\n\n"
        "One-liner in the DSW terminal (resume-safe, rerun the same line after a session ends):\n\n"
        f"    export MODELSCOPE_API_TOKEN=<token>; modelscope login --token $MODELSCOPE_API_TOKEN && modelscope download --model {REPO} run.sh --local_dir /mnt/workspace/job_v5 && SKIP_BASE=1 bash /mnt/workspace/job_v5/run.sh\n\n"
        "Outputs: /mnt/workspace/out_v5, uploaded to `outputs/` in this repo every ~25 min and at each epoch / phase end.\n",
        encoding="utf-8",
    )

    # 静态检查
    with tempfile.TemporaryDirectory() as folder:
        py_compile.compile(str(JOB / "train_v5_ms.py"), cfile=str(Path(folder) / "t.pyc"), doraise=True)
    text = (JOB / "train_v5_ms.py").read_text(encoding="utf-8")
    assert "ds_v4" not in text and "train_v4" not in text
    assert "/mnt/workspace/job/" not in (JOB / "run.sh").read_text(encoding="utf-8")

    # 按训练脚本的做法走一遍每条数据：读取、去掉示例解释、取答案
    EXK = "拆题样例（examples，只模仿拆法，答案与本题无关）：\n"
    tail = "\n请按要求只输出 JSON。"
    rows = [json.loads(line) for line in (JOB / "ds_v5" / "train.jsonl").read_text(encoding="utf-8").splitlines()]
    with_examples = 0
    for row in rows:
        assert [m["role"] for m in row["messages"]] == ["system", "user", "assistant"]
        user = row["messages"][1]["content"]
        if EXK in user:
            with_examples += 1
            start = user.index(EXK) + len(EXK)
            end = user.rindex(tail)
            for example in json.loads(user[start:end]):
                for step in example["steps"]:
                    step.pop("explain", None)
        json.loads(row["messages"][2]["content"])
    print("训练条数", len(rows), "带示例", with_examples, "估计优化步数", len(rows) * 3 // 16)
    print("任务文件:")
    for path in sorted(JOB.rglob("*")):
        if path.is_file():
            print(" ", path.relative_to(JOB).as_posix(), path.stat().st_size)


if __name__ == "__main__":
    main()
