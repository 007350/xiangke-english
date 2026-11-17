# xiangke-lora-job-v5 (private)

xiangke LoRA v5 training job for ModelScope PAI-DSW (A10 24GB). Same hparams as v4, data is ds_v5.

One-liner in the DSW terminal (resume-safe, rerun the same line after a session ends):

    export MODELSCOPE_API_TOKEN=<token>; modelscope login --token $MODELSCOPE_API_TOKEN && modelscope download --model xiaobang123/xiangke-lora-job-v5 run.sh --local_dir /mnt/workspace/job_v5 && SKIP_BASE=1 bash /mnt/workspace/job_v5/run.sh

Outputs: /mnt/workspace/out_v5, uploaded to `outputs/` in this repo every ~25 min and at each epoch / phase end.
