#!/bin/bash
# ModelScope PAI-DSW launcher for xiangke LoRA v5. Resume-safe: just rerun the same one-liner.
#   export MODELSCOPE_API_TOKEN=<token>; modelscope login --token $MODELSCOPE_API_TOKEN && modelscope download --model xiaobang123/xiangke-lora-job-v5 run.sh --local_dir /mnt/workspace/job_v5 && bash /mnt/workspace/job_v5/run.sh
# Optional: SKIP_BASE=1 bash /mnt/workspace/job_v5/run.sh   (skip base-model eval)
REPO=xiaobang123/xiangke-lora-job-v5; J=/mnt/workspace/job_v5; O=/mnt/workspace/out_v5
mkdir -p $J $O /mnt/workspace/models
if [ -z "$MODELSCOPE_API_TOKEN" ]; then echo "ERROR: export MODELSCOPE_API_TOKEN first"; exit 1; fi
if [ -f $O/DONE ]; then echo "== already DONE =="; cat $O/DONE; exit 0; fi
if [ -f $O/train.pid ] && kill -0 "$(cat $O/train.pid)" 2>/dev/null; then echo "== already running (pid $(cat $O/train.pid)) =="; tail -n 5 $O/train.log; exit 0; fi
pipi(){ python -m pip install -q "$@" || python -m pip install -q -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com "$@" || python -m pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple "$@"; }
PIP=pipi
echo "[$(date)] setup" >> $O/setup.log
python -c "import modelscope;print('modelscope',modelscope.__version__)" >> $O/setup.log 2>&1 || $PIP modelscope >> $O/setup.log 2>&1 || { echo "pip modelscope failed, see $O/setup.log"; exit 1; }
python - <<'PY' >> $O/setup.log 2>&1 || { echo "repo download failed, see $O/setup.log"; exit 1; }
import os
from modelscope import snapshot_download
snapshot_download("xiaobang123/xiangke-lora-job-v5", local_dir="/mnt/workspace/job_v5", ignore_patterns=["outputs/*", "outputs/**"], token=os.environ["MODELSCOPE_API_TOKEN"])
print("repo downloaded")
PY
$PIP -r $J/requirements.txt >> $O/setup.log 2>&1 || { echo "pip requirements failed, see $O/setup.log"; exit 1; }
python -c "import torch,transformers,peft;print('torch',torch.__version__,'cuda',torch.cuda.is_available(),'transformers',transformers.__version__,'peft',peft.__version__)" | tee -a $O/setup.log
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONUNBUFFERED=1 TOKENIZERS_PARALLELISM=false
EXTRA=""; [ "$SKIP_BASE" = "1" ] && EXTRA="--skip-base-eval"
cd $J
nohup bash -c "for k in 1 2 3; do python -u $J/train_v5_ms.py $EXTRA; rc=\$?; [ \$rc -ne 1 ] && break; echo \"[run.sh] rc=1, retry \$k in 60s\"; sleep 60; done" >> $O/train.log 2>&1 &
echo $! > $O/train.pid
echo "== started (pid $(cat $O/train.pid)); log: tail -f $O/train.log ; status: cat $O/progress.json =="
sleep 20; tail -n 5 $O/train.log
