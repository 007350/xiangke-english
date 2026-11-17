"""把 adapter 合并进基座模型：先用 fp32 合并，再存成 bf16。做法和 v4 相同。

用法：python merge_lora.py <基座目录> <adapter目录> <输出目录>
"""

import glob
import os
import shutil
import sys

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

base_dir, adapter_dir, out_dir = sys.argv[1:4]
os.makedirs(out_dir, exist_ok=True)
model = AutoModelForCausalLM.from_pretrained(base_dir, trust_remote_code=True, dtype=torch.float32)
model = PeftModel.from_pretrained(model, adapter_dir)
model = model.merge_and_unload()
model = model.to(torch.bfloat16)
model.save_pretrained(out_dir, safe_serialization=True, max_shard_size="2GB")
AutoTokenizer.from_pretrained(base_dir, trust_remote_code=True).save_pretrained(out_dir)
# 模型自带的代码和模板也要带上，转换时要读
for pattern in ("*.py", "*.jinja", "generation_config.json"):
    for path in glob.glob(os.path.join(base_dir, pattern)):
        shutil.copy(path, out_dir)
print("merged to", out_dir)
