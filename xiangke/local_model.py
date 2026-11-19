"""Keep the local model loaded and call it with the template used in training."""

from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "tools" / "llama.cpp" / "llama-server.exe"
DEFAULT_MODEL = ROOT / "models" / "v5" / "gguf" / "xiangke-v5-Q4_K_M.gguf"
BOS, EOS = "<｜start▁of▁sentence｜>", "<｜end▁of▁sentence｜>"


class LocalModel:
    """Use as a context manager. ``write(system, user, grammar)`` returns raw text."""

    def __init__(self, model: Path | str = DEFAULT_MODEL, port: int = 8095):
        self.model = str(model)
        self.url = f"http://127.0.0.1:{port}"
        self.port = port
        self.process = None

    def __enter__(self) -> "LocalModel":
        self.process = subprocess.Popen(
            [str(SERVER), "-m", self.model, "--host", "127.0.0.1", "--port", str(self.port), "-c", "4096", "--reasoning", "off"],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(180):
            if self.process.poll() is not None:
                raise RuntimeError(f"model server exited {self.process.returncode}")
            try:
                with urllib.request.urlopen(self.url + "/health", timeout=2) as response:
                    if response.status == 200:
                        return self
            except OSError:
                time.sleep(1)
        raise RuntimeError("model server not ready")

    def __exit__(self, *_) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.process.kill()

    def write(self, system: str, user: str, grammar: str, limit: int = 512, *, timeout: float = 300) -> str:
        prompt = f"{BOS}<|System|>\nyou are a helpful assistant.\n\n{system}{EOS}{BOS}<|User|>{user}{EOS}{BOS}<|Bot|></think>"
        body = {
            "prompt": prompt,
            "n_predict": limit,
            "temperature": 0,
            "top_k": 1,
            "seed": 0,
            "cache_prompt": False,
            "grammar": grammar,
        }
        request = urllib.request.Request(self.url + "/completion", json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)["content"]
