"""下载模型。只用标准库。断了可以接着下，下完核对校验值。

用法：python -m xiangke.download_model
"""

from __future__ import annotations

import hashlib
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "models" / "v5" / "gguf" / "xiangke-v5-Q4_K_M.gguf"
URL = "https://www.modelscope.cn/models/xiaobang123/xiangke-lora-job-v5/resolve/master/gguf/xiangke-v5-Q4_K_M.gguf"
SIZE = 1107457952
SHA256 = "97239079404d0a2159f891cf34d90ab615844cb1309dfa8d0f78262af9f6b168"
CHUNK = 1024 * 1024


class DownloadFailed(RuntimeError):
    """The message tells the person what to do."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def ensure_model(url: str = URL, target: Path = TARGET, size: int = SIZE, sha256: str = SHA256, say=print) -> Path:
    """Return the model path, downloading it if needed. Only a verified file gets the final name."""
    if target.is_file() and target.stat().st_size == size:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    if have > size:
        part.unlink()
        have = 0
    if have < size:
        say(f"正在下载模型（约 {size / 1024 / 1024 / 1024:.2f} GB），断了再运行一次可以接着下……")
        _fetch(url, part, have, size, say)
    say("下载完成，正在核对文件……")
    if file_sha256(part) != sha256:
        part.unlink()
        raise DownloadFailed("下载的文件不完整或被改动，已删除，请重新运行一次。")
    part.replace(target)
    say("模型准备好了。")
    return target


def _fetch(url: str, part: Path, have: int, size: int, say) -> None:
    headers = {"User-Agent": "xiangke-downloader"}
    if have:
        headers["Range"] = f"bytes={have}-"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            # 服务器不支持接着下时会从头发，这时要从头写
            if have and response.status != 206:
                have = 0
            done = have
            shown = -1
            with open(part, "ab" if have else "wb") as handle:
                while block := response.read(CHUNK):
                    handle.write(block)
                    done += len(block)
                    percent = done * 100 // size
                    if percent != shown and percent % 5 == 0:
                        say(f"  已下载 {percent}%")
                        shown = percent
    except urllib.error.HTTPError as error:
        raise DownloadFailed(f"模型地址打不开（{error.code}）。可以手动下载后放到 models/v5/gguf/：{url}") from error
    except OSError as error:
        raise DownloadFailed(f"下载中断了（{error}）。请检查网络后再运行一次，会接着下。") from error
    if part.stat().st_size != size:
        raise DownloadFailed("下载没有完成。请再运行一次，会接着下。")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        ensure_model()
    except DownloadFailed as error:
        print(error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
