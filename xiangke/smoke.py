"""一条命令自证程序活着：起云端、起手机端、做一道题、通电同步、看反馈页。

用法：python -m xiangke.smoke

走的是真代码：真的 HTTP 服务、真的 SQLite、真的同步和判题。
只有「讲题」那一步用写死的两问代替模型（和测试里的做法一样），云端用程序兜底，不调星火。
所以它证明的是「流程通、数据通、页面打得开」，不证明讲解质量。
数据全在内存里，不碰 demo/ 下的文件。
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from xiangke.app import PhoneApp, make_handler as phone_handler
from xiangke.cloud import CloudApp, CloudStore, make_handler as cloud_handler
from xiangke.store import Store

TEXT = "They ___ my friends. (am / is / are)"
LESSON = {
    "steps": [
        {"ask": "这句话的主语是哪个词？", "expect": "They", "explain": "主语是 They。"},
        {"ask": "那空里填 am、is 还是 are？", "expect": "are", "explain": "They 是两个或更多，所以填 are。"},
    ]
}


class Failed(RuntimeError):
    """哪一步没走通。消息里说清楚看到的是什么。"""


def stub_write(system: str, user: str, grammar: str, limit: int = 512) -> str:
    """代替小模型：分类一律当答题，讲题给写死的两问。"""
    if "孩子说：" in user:
        return json.dumps({"label": "答题"}, ensure_ascii=False)
    return json.dumps(LESSON, ensure_ascii=False)


def serve(handler) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def request(url: str, body: dict | None = None) -> tuple[int, str]:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def call(base: str, path: str, body: dict | None = None) -> dict:
    status, text = request(base + path, body)
    if status != 200:
        raise Failed(f"{path} 返回 {status}：{text[:200]}")
    return json.loads(text)


def step(number: int, total: int, what: str) -> None:
    print(f"[{number}/{total}] {what} …… ", end="", flush=True)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cloud_store = CloudStore(":memory:")
    store = Store(":memory:")
    cloud_server = phone_server = None
    total = 6
    try:
        # 云端用默认的程序兜底，不读写讲法，也不调星火
        cloud = CloudApp(cloud_store)
        cloud_server, cloud_url = serve(cloud_handler(cloud))
        step(1, total, "起云端")
        status, page = request(cloud_url + "/")
        if status != 200 or "学情反馈" not in page:
            raise Failed(f"反馈页打不开（{status}）")
        print("ok")

        phone = PhoneApp(stub_write, store, cloud_url=cloud_url)
        phone_server, phone_url = serve(phone_handler(phone))
        step(2, total, "起手机端")
        status, page = request(phone_url + "/")
        if status != 200 or "乡课" not in page:
            raise Failed(f"手机页面打不开（{status}）")
        print("ok")

        step(3, total, "断网做题")
        call(phone_url, "/api/network", {"online": False})
        call(phone_url, "/api/start", {"text": TEXT})
        call(phone_url, "/api/reply", {"said": "They"})
        call(phone_url, "/api/reply", {"said": "are"})
        home = call(phone_url, "/api/home")
        if home["waitingSync"] != 1:
            raise Failed(f"做完一道题，待同步应该是 1，实际是 {home['waitingSync']}")
        print("ok（记下 1 条待同步）")

        step(4, total, "通电同步")
        call(phone_url, "/api/network", {"online": True})
        result = call(phone_url, "/api/sync", {})["syncResult"]
        if result.get("accepted") != 1:
            raise Failed(f"云端没有收下这次辅导：{result}")
        print(f"ok（收下 {result['accepted']} 条，讲法到第 {result.get('planVersion')} 版）")

        step(5, total, "手机端拿到新讲法")
        home = call(phone_url, "/api/home")
        if home["waitingSync"] != 0:
            raise Failed("同步之后还有待同步的记录")
        if home["planVersion"] < 3:
            raise Failed(f"讲法版本没往前走，现在是第 {home['planVersion']} 版")
        print(f"ok（第 {home['planVersion']} 版，待同步 0）")

        step(6, total, "云端有记录和反馈")
        children = json.loads(request(cloud_url + "/api/children")[1])["children"]
        if "child_01" not in children:
            raise Failed(f"云端的孩子列表里没有 child_01：{children}")
        report = call(cloud_url, "/api/report/child_01")
        if report.get("childId") != "child_01" or "judgement" not in report:
            raise Failed(f"反馈内容不对：{list(report)[:8]}")
        print("ok")

        print("\n全部通过。断网做题 → 通电同步 → 云端有记录和反馈，这条链是通的。")
        print("注意：讲题用的是写死的两问，没有调用真模型，所以这次不说明讲解好不好。")
        return 0
    except Failed as error:
        print("失败")
        print(f"\n{error}")
        return 1
    except OSError as error:
        print("失败")
        print(f"\n端口或被拦截了：{error}")
        return 1
    finally:
        for server in (phone_server, cloud_server):
            if server is not None:
                server.shutdown()
                server.server_close()
        store.connection.close()
        cloud_store.connection.close()


if __name__ == "__main__":
    sys.exit(main())
