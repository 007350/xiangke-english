# xiangke-lora-job-v6 (private)

xiangke LoRA v6: same hparams as v4, data ds_v6 (the program gives the answer, the model talks).

One-liner in the DSW terminal (resume-safe):

    modelscope download --model xiaobang123/xiangke-lora-job-v6 run.sh --local_dir /mnt/workspace/job_v6 && SKIP_BASE=1 bash /mnt/workspace/job_v6/run.sh

Export after training: bash /mnt/workspace/job_v6/export_gguf.sh /mnt/workspace/out_v6 xiaobang123/xiangke-lora-job-v6 v6
