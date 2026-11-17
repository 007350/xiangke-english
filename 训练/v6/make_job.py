"""从 v5 任务文件夹做出 v6 任务文件夹。只换路径和数据，训练配置不动。

用法：python 训练/v6/make_job.py
"""

from __future__ import annotations

import json
import py_compile
import shutil
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
V5 = HERE.parent / "v5" / "job"
JOB = HERE / "job"
REPO = "xiaobang123/xiangke-lora-job-v6"


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
    for name in ("eval_core.py", "kernel.py", "eval_set.json", "requirements.txt"):
        shutil.copyfile(V5 / name, JOB / name)
    shutil.copytree(HERE / "ds_v6", JOB / "ds_v6")
    for name in ("export_gguf.sh", "merge_lora.py"):
        data = (HERE / name).read_bytes().replace(b"\r\n", b"\n")
        (JOB / name).write_bytes(data)

    train = (V5 / "train_v5_ms.py").read_bytes()
    train = replace(train, "xiangke LoRA v5 (same hparams as v4, data ds_v5)", "xiangke LoRA v6 (same hparams as v4, data ds_v6)", 1)
    train = replace(train, '"/mnt/workspace/out_v5"', '"/mnt/workspace/out_v6"', 1)
    train = replace(train, '"xiaobang123/xiangke-lora-job-v5"', f'"{REPO}"', 1)
    train = replace(train, 'os.path.join(A.job, "ds_v5")', 'os.path.join(A.job, "ds_v6")', 1)
    (JOB / "train_v6_ms.py").write_bytes(train)

    run = (V5 / "run.sh").read_bytes()
    run = replace(run, "xiaobang123/xiangke-lora-job-v5", REPO, 3)
    run = replace(run, "/mnt/workspace/job_v5", "/mnt/workspace/job_v6", 5)
    run = replace(run, "O=/mnt/workspace/out_v5", "O=/mnt/workspace/out_v6", 1)
    run = replace(run, "train_v5_ms.py", "train_v6_ms.py", 1)
    run = replace(run, "xiangke LoRA v5", "xiangke LoRA v6", 1)
    # 训练后只抽样评测，省时间。真正的验收在本地用同一份题做
    run = replace(run, 'EXTRA=""; [ "$SKIP_BASE" = "1" ]', 'EXTRA="--limit-eval 20"; [ "$SKIP_BASE" = "1" ]', 1)
    run = replace(run, 'EXTRA="--skip-base-eval"', 'EXTRA="$EXTRA --skip-base-eval"', 1)
    if b"\r\n" in run:
        raise RuntimeError("run.sh 不能有 Windows 换行")
    (JOB / "run.sh").write_bytes(run)

    (JOB / "README.md").write_text(
        "# xiangke-lora-job-v6 (private)\n\n"
        "xiangke LoRA v6: same hparams as v4, data ds_v6 (the program gives the answer, the model talks).\n\n"
        "One-liner in the DSW terminal (resume-safe):\n\n"
        f"    modelscope download --model {REPO} run.sh --local_dir /mnt/workspace/job_v6 && SKIP_BASE=1 bash /mnt/workspace/job_v6/run.sh\n\n"
        "Export after training: bash /mnt/workspace/job_v6/export_gguf.sh /mnt/workspace/out_v6 " + REPO + " v6\n",
        encoding="utf-8",
    )

    with tempfile.TemporaryDirectory() as folder:
        py_compile.compile(str(JOB / "train_v6_ms.py"), cfile=str(Path(folder) / "t.pyc"), doraise=True)
        py_compile.compile(str(JOB / "merge_lora.py"), cfile=str(Path(folder) / "m.pyc"), doraise=True)
    text = (JOB / "train_v6_ms.py").read_text(encoding="utf-8")
    assert "ds_v5" not in text.replace("data ds_v5", "") and "out_v5" not in text and "job-v5" not in text
    rows = [json.loads(line) for line in (JOB / "ds_v6" / "train.jsonl").read_text(encoding="utf-8").splitlines()]
    print("训练条数", len(rows), "估计优化步数", len(rows) * 3 // 16)
    for path in sorted(JOB.rglob("*")):
        if path.is_file():
            print(" ", path.relative_to(JOB).as_posix(), path.stat().st_size)


if __name__ == "__main__":
    main()
