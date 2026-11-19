"""手机端界面的本地服务。在电脑上代替手机：网页是界面，这里接模型和手机库。

用法：python -m xiangke.app [--port 8765] [--model 模型文件] [--db 库文件]
只监听本机。一次只服务一个孩子，同一时间只处理一个请求。
"""

from __future__ import annotations

import argparse
import json
import threading
import uuid
import webbrowser
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from xiangke.intent import make_label_of
from xiangke.local_model import DEFAULT_MODEL, LocalModel
from xiangke.one_item import ItemSession
from xiangke.pack import spelling_answer, texts as pack_texts
from xiangke.store import Store
from xiangke.sync import DEFAULT_CLOUD, SyncFailed, acknowledge_plan, sync_once
from xiangke.transfer import item_for, letter_focus, text_key

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "app" / "index.html"
DEFAULT_DB = ROOT / "demo" / "phone.db"
DEFAULT_PACK = ROOT / "data" / "pack.json"
CHILD = "child_01"
BEIJING = timezone(timedelta(hours=8))
MAX_IMAGE = 15 * 1024 * 1024


class BadRequest(ValueError):
    """The request cannot be done. The message is shown to the child."""


def now() -> str:
    return datetime.now(BEIJING).isoformat(timespec="seconds")


class PhoneApp:
    """Everything the page can ask for, without any HTTP. Tests use this directly."""

    def __init__(self, write, store: Store, recognizer=None, child_id: str = CHILD, cloud_url: str = DEFAULT_CLOUD,
                 pack_path: str | Path | None = None, *, transition_write=None):
        self.write = write
        self.transition_write = transition_write
        self.store = store
        self.recognizer = recognizer
        self.child_id = child_id
        self.cloud_url = cloud_url
        self.pack_path = Path(pack_path) if pack_path else DEFAULT_PACK
        # 模拟的网络开关。云端也在这台电脑上，真断网断不开，所以用开关。启动时是断网
        self.online = False
        self.label_of = make_label_of(write)
        self.item: ItemSession | None = None
        self.saved = True
        self.drill: dict | None = None
        self.transfer_basis: dict | None = None
        self._seed_plan()

    def _seed_plan(self) -> None:
        if self.store.latest_plan(self.child_id) is None:
            for name in ("child01_v1.json", "child01_v2.json"):
                plan = json.loads((ROOT / "data" / "plans" / name).read_text(encoding="utf-8"))["plan"]
                self.store.save_plan({**plan, "childId": self.child_id})

    def home(self) -> dict:
        plan = self.store.latest_plan(self.child_id)
        pack = json.loads(self.pack_path.read_text(encoding="utf-8"))
        return {
            "planVersion": plan["planVersion"],
            "starterPlan": bool(plan.get("starter")),
            "online": self.online,
            "waitingSync": len(self.store.unsynced(self.child_id)),
            "pack": pack_texts(pack),
            "drills": [{"drillId": row["drillId"], "text": row["text"], "source": row["source"]} for row in self.store.open_drills(self.child_id)],
            "history": [_history_row(record) for record in self.store.recent(self.child_id)],
            "questions": [{**item, "transferReady": letter_focus(item) is not None} for item in self.store.questions(self.child_id)],
        }

    def recognize(self, image: bytes) -> dict:
        if not image:
            raise BadRequest("没有收到照片")
        if len(image) > MAX_IMAGE:
            raise BadRequest("照片太大了，请换一张")
        if self.recognizer is None:
            raise BadRequest("这里还不能认照片")
        try:
            text = self.recognizer(image)
        except Exception as error:  # OCR 对坏图片会抛各种错，孩子只需要知道没认出来
            raise BadRequest("这张照片没有认出来，可以再拍一张，或者直接输入题目") from error
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            raise BadRequest("这张照片没有认出文字，可以再拍一张，或者直接输入题目")
        return {"lines": lines}

    def set_network(self, online) -> dict:
        self.online = bool(online)
        return self.home()

    def sync(self) -> dict:
        """联网时才能同步。连不上云端时记录还留在手机上。"""
        if not self.online:
            raise BadRequest("现在是断网状态，联网后才能同步")
        if self.item is not None and not self.saved:
            raise BadRequest("先把这道题做完，再同步")
        try:
            result = sync_once(self.store, self.child_id, self.cloud_url)
        except SyncFailed as error:
            print("同步失败:", error, flush=True)
            raise BadRequest("连不上云端，记录还留在手机上，等一会再试") from error
        if result["planUpdated"]:
            acknowledge_plan(self.cloud_url, self.child_id, result["planVersion"])
        return {**self.home(), "syncResult": result}

    def start(self, text: str, source: str = "photo", offline: bool | None = None) -> dict:
        """The child pressed 是这道题. Only now is the text final."""
        offline = not self.online if offline is None else offline
        text = (text or "").strip()
        if not text:
            raise BadRequest("请先写好题目")
        if source not in ("photo", "pack"):
            raise BadRequest("题目来源不对")
        self.leave()
        known = spelling_answer(json.loads(self.pack_path.read_text(encoding="utf-8")), text) if source == "pack" else None
        return self._begin(text, source, offline, known)

    def start_drill(self, drill_id) -> dict:
        """做一道云端发来的专属题。题目已经定好，不用孩子确认。"""
        self.leave()
        drill = next((row for row in self.store.open_drills(self.child_id) if row["drillId"] == drill_id), None)
        if drill is None:
            raise BadRequest("这道题已经做过了")
        known = {"kind": "cloud", "answer": drill["answer"], "ruleKey": "cloud", "rule": drill["rule"]}
        view = self._begin(drill["text"], "drill", not self.online, known, drill)
        return view

    def transfer(self, question_id: str | None = None) -> dict:
        """每天最多两道。跨日打开同一封回信，用同一考点换题。"""
        if self.item is not None and not self.saved:
            raise BadRequest("先做完或退出正在做的题，再来试新题。")
        letters = self.store.questions(self.child_id)
        if question_id is None:
            letter = next((item for item in letters if letter_focus(item)), None)
        else:
            letter = next((item for item in letters if item["questionId"] == question_id), None)
        focus = letter_focus(letter) if letter else None
        if not focus:
            raise BadRequest("这封回信暂时没有可做的新题。")
        key, topic = focus
        started_at = now()
        today = started_at[:10]
        history = self.store.recent(self.child_id, limit=-1)
        transfers = [record for record in history if record.get("source") == "transfer"]
        if sum(record.get("startedAt", "")[:10] == today for record in transfers) >= 2:
            raise BadRequest("今天的两道新题都试过了，改天再来换题复测吧。")
        attempts = [record for record in transfers if (record.get("transferBasis") or {}).get("questionId") == letter["questionId"]]
        slot = sum(record.get("startedAt", "")[:10] == today for record in attempts)
        original = self.store.load_session(letter.get("sessionId", "")) or {}
        wrong = ""
        if not original.get("flagged") and original.get("checkPassed") is not False:
            wrong = next((event.get("said", "") for event in reversed(original.get("events", []))
                          if event.get("stepId") == letter.get("stepId") and event.get("result") in ("miss", "assisted")), "")
        seen = {text_key(record.get("recognizedText") or "") for record in history}
        seen.add(text_key(letter["text"]))
        text = item_for(key, slot, seen, wrong)
        if text is None:
            raise BadRequest("这个考点的备选新题已经做过了，可以先找老师聊聊。")
        basis = {"questionId": letter["questionId"], "focus": key, "topic": topic,
                 "originalText": letter["text"], "originalReply": wrong, "contrast": slot == 1}
        retest = any((record.get("transferBasis") or {}).get("focus") == key
                     and "" < record.get("startedAt", "")[:10] < today for record in attempts)
        return self._begin(text, "transfer", True, started_at=started_at, transfer_basis={**basis, "retest": retest})

    def flag(self) -> dict:
        """孩子觉得这道专属题不对。这一次不算对错，留给老师看。"""
        if self.item is None or self.drill is None or self.saved:
            raise BadRequest("现在没有在做专属题")
        self._save(flagged=True)
        self.item = None
        return self.home()

    def _begin(self, text: str, source: str, offline: bool, known: dict | None = None, drill: dict | None = None, *,
               started_at: str | None = None, transfer_basis: dict | None = None) -> dict:
        plan = self.store.latest_plan(self.child_id)
        facts = {
            "sessionId": "s_" + uuid.uuid4().hex[:12],
            "childId": self.child_id,
            "planVersion": plan["planVersion"],
            "source": source,
            "recognizedText": text,
            "confirmed": True,
            "offline": bool(offline),
            "startedAt": started_at or now(),
            "endedAt": now(),
        }
        self.item = ItemSession(text, self.write, facts, plan, self.label_of, known,
                                transition_write=self.transition_write)
        self.drill = drill
        self.transfer_basis = transfer_basis
        self.saved = False
        return self._view(self.item.take_messages())

    def reply(self, said: str) -> dict:
        said = (said or "").strip()
        if not said:
            raise BadRequest("先说点什么吧")
        if self.item is None:
            raise BadRequest("现在没有在做题")
        return self._view(self.item.reply(said))

    def defer_question(self, said: str) -> dict:
        said = (said or "").strip()
        if not said:
            raise BadRequest("先在输入框写下你的疑惑")
        if self.item is None or self.item.done:
            raise BadRequest("现在没有正在做的小问")
        return self._view(self.item.defer_question(said))

    def explain(self) -> dict:
        if self.item is None or not self.item.can_explain:
            raise BadRequest("这一步还没有可以打开的讲解")
        return self._view(self.item.reveal_explanation())

    def leave(self) -> dict:
        """The child left. Whatever happened so far is kept."""
        self._save()
        self.item = None
        return self.home()

    def _save(self, flagged: bool = False) -> None:
        if self.item is None or self.saved:
            return
        record = self.item.record(now())
        if self.drill is not None:
            record["drillId"] = self.drill["drillId"]
        if flagged:
            record["flagged"] = True
        if self.transfer_basis is not None:
            record["transferTier"] = _transfer_tier(record)
            record["transferRetest"] = self.transfer_basis["retest"]
            record["transferBasis"] = {key: value for key, value in self.transfer_basis.items() if key != "retest"}
        self.store.save_session(record)
        self.saved = True
        if self.drill is not None:
            # 中途退出的题还留着，下次可以接着做
            if flagged:
                self.store.set_drill_status(self.drill["drillId"], "flagged")
            elif self.item.done:
                self.store.set_drill_status(self.drill["drillId"], "done")

    def _view(self, messages: list[str]) -> dict:
        item = self.item
        view = {
            "messages": messages,
            "done": item.done,
            "outcome": item.outcome,
            "mode": item.mode,
            "question": item.facts["recognizedText"],
            "step": min(item.index + 1, len(item.steps)),
            "steps": len(item.steps),
            "confirming": item.pending,
            "canExplain": item.can_explain,
            "drill": self.drill is not None,
        }
        if item.done:
            self._save()
            view["summary"] = _summary(item.record())
            view["today"] = self._today()
        return view

    def _today(self) -> dict:
        """今天做了几道、还有几道专门练的题、几次辅导没交。结束页靠它判断该不该收工。"""
        return {
            "done": self.store.started_on(self.child_id, now()[:10]),
            "left": len(self.store.open_drills(self.child_id)),
            "waiting": len(self.store.unsynced(self.child_id)),
        }


def _transfer_tier(record: dict) -> str:
    """迁移检验分档：独立做对 / 提示后改对 / 停下来。中途退出也算停下来。"""
    events = record.get("events", [])
    steps = record.get("steps") or []
    if any(event.get("result") == "assisted" for event in events):
        return "stopped"
    finished = bool(steps) and any(event.get("result") == "hit" and event.get("stepId") == steps[-1].get("stepId") for event in events)
    if not finished:
        return "stopped"
    helped = any(
        event.get("result") == "miss" or event.get("hintUsed", 0) > 0 or event.get("noteKind") in ("stuck", "question")
        for event in events
    )
    return "assisted" if helped else "independent"


def _summary(record: dict) -> dict:
    events = record["events"]
    shown = sum(bool(event.get("answerShown")) for event in events)
    return {
        "right": sum(event["result"] == "hit" for event in events),
        "wrong": sum(event["result"] in ("miss", "assisted") for event in events),
        "questions": len(record["steps"]),
        "answerSource": record["answerSource"],
        **({"afterExplanation": shown} if shown else {}),
    }


def _history_row(record: dict) -> dict:
    summary = _summary(record)
    return {
        "question": record["recognizedText"],
        "at": record["startedAt"],
        "source": record["source"],
        "answerSource": record["answerSource"],
        "flagged": bool(record.get("flagged")),
        "right": summary["right"],
        "wrong": summary["wrong"],
        "questions": summary["questions"],
        "afterExplanation": summary.get("afterExplanation", 0),
        "finished": any(event["result"] == "hit" and event["stepId"] == record["steps"][-1]["stepId"] for event in record["events"]) if record["steps"] else False,
    }


def make_handler(app: PhoneApp):
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _json(self, status: int, body: dict) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_IMAGE:
                raise BadRequest("内容太大了")
            return self.rfile.read(length)

        def do_GET(self):
            if self.path == "/" or self.path.startswith("/?"):
                data = PAGE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            elif self.path == "/api/home":
                self._run(app.home)
            else:
                self._json(404, {"error": "没有这个页面"})

        def do_POST(self):
            try:
                body = self._body()
            except BadRequest as error:
                self._json(400, {"error": str(error)})
                return
            if self.path == "/api/recognize":
                self._run(lambda: app.recognize(body))
                return
            try:
                data = json.loads(body or b"{}")
            except json.JSONDecodeError:
                self._json(400, {"error": "内容看不懂"})
                return
            routes = {
                "/api/start": lambda: app.start(data.get("text"), data.get("source", "photo"), data.get("offline")),
                "/api/reply": lambda: app.reply(data.get("said")),
                "/api/question": lambda: app.defer_question(data.get("said")),
                "/api/explain": app.explain,
                "/api/leave": app.leave,
                "/api/drill": lambda: app.start_drill(data.get("drillId")),
                "/api/transfer": lambda: app.transfer(data.get("questionId")),
                "/api/flag": app.flag,
                "/api/network": lambda: app.set_network(data.get("online")),
                "/api/sync": app.sync,
            }
            action = routes.get(self.path)
            if action is None:
                self._json(404, {"error": "没有这个操作"})
                return
            self._run(action)

        def _run(self, action) -> None:
            with lock:
                try:
                    result = action()
                except BadRequest as error:
                    self._json(400, {"error": str(error)})
                    return
                except Exception as error:  # 界面要能显示出错，服务不能因此退出
                    print("处理请求出错:", repr(error), flush=True)
                    self._json(500, {"error": "手机这边出了点问题，请再试一次"})
                    return
            self._json(200, result)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--model", default=str(DEFAULT_MODEL))
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--cloud", default=DEFAULT_CLOUD, help="云端地址")
    parser.add_argument("--pack", default=str(DEFAULT_PACK), help="课包文件，老师用 python -m xiangke.pack 生成")
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    args = parser.parse_args()

    def recognizer(image: bytes) -> str:
        from xiangke.recognize import recognize

        return recognize(image)

    with Store(args.db) as store, LocalModel(args.model) as model:
        app = PhoneApp(model.write, store, recognizer, cloud_url=args.cloud, pack_path=args.pack,
                       transition_write=partial(model.write, timeout=2.5))
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app))
        print(f"手机界面已启动：http://127.0.0.1:{args.port}", flush=True)
        if args.open:
            webbrowser.open(f"http://127.0.0.1:{args.port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            with threading.Lock():
                app.leave()
            server.server_close()


if __name__ == "__main__":
    main()
