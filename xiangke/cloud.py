"""云端的最小版本：另开一个程序，收手机传来的辅导记录，发新计划和专属题。

用法：python -m xiangke.cloud [--port 8766] [--db demo/cloud.db]
现在只监听本机，也没有登录。搬到真服务器之前要补上身份校验。

步骤由程序定：答复已留下的疑惑 → 更新档案和讲法 → 写反馈 → 出专属题。
每一步的判断交给星火，分别见 questions.py、planner.py、feedback.py 和 drills.py。打开 http://127.0.0.1:8766
是老师和家长看的页面。星火慢，所以手机传完记录就回去，云端在
后台写，手机下次同步再取结果。没设置 AI_API_KEY 时只收记录和发回原题，不写新讲法。
--test-planner 用一句写死的话，只为验证计划能送到手机。写讲法出错不影响记录。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from xiangke.drills import _norm, make_drill_maker, redo_items
from xiangke.feedback import build_facts, build_report, make_reporter
from xiangke.kernel import check_plan
from xiangke.planner import chat_from_env, make_planner
from xiangke.portrait import build_stats, stats_for_model
from xiangke.questions import BATCH_SIZE, MAX_ANSWER, collect_questions, make_answerer
from xiangke.replay import Recorder, Replayer

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "demo" / "cloud.db"
REPORT_PAGE = ROOT / "app" / "report.html"
MAX_BODY = 32 * 1024 * 1024
MAX_OPEN_DRILLS = 6  # 手上没做完的题够多了，就先不出新的
_REQUIRED = ("sessionId", "childId", "answerSource", "events", "steps")
TEST_REMINDER = "（同步测试）先问主语是谁，再让他选词。"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    sessionId     TEXT PRIMARY KEY,
    childId       TEXT NOT NULL,
    record        TEXT NOT NULL,
    receivedOrder INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
    childId     TEXT NOT NULL,
    planVersion INTEGER NOT NULL,
    plan        TEXT NOT NULL,
    PRIMARY KEY (childId, planVersion)
);
CREATE TABLE IF NOT EXISTS portraits (
    childId  TEXT NOT NULL,
    version  INTEGER NOT NULL,
    portrait TEXT NOT NULL,
    PRIMARY KEY (childId, version)
);
CREATE TABLE IF NOT EXISTS reports (
    childId TEXT NOT NULL,
    version INTEGER NOT NULL,
    written TEXT NOT NULL,
    PRIMARY KEY (childId, version)
);
CREATE TABLE IF NOT EXISTS drills (
    seq     INTEGER PRIMARY KEY AUTOINCREMENT,
    drillId TEXT NOT NULL UNIQUE,
    childId TEXT NOT NULL,
    item    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS questions (
    questionId TEXT PRIMARY KEY,
    childId TEXT NOT NULL,
    seq INTEGER NOT NULL UNIQUE,
    status TEXT NOT NULL,
    item TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS questions_child_status ON questions(childId, status);
CREATE TRIGGER IF NOT EXISTS cloud_sessions_no_update
BEFORE UPDATE ON sessions
BEGIN
    SELECT RAISE(ABORT, 'a received session cannot be changed');
END;
CREATE TRIGGER IF NOT EXISTS cloud_sessions_no_delete
BEFORE DELETE ON sessions
BEGIN
    SELECT RAISE(ABORT, 'a received session cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS cloud_plans_no_update
BEFORE UPDATE ON plans
BEGIN
    SELECT RAISE(ABORT, 'a plan version cannot be changed');
END;
CREATE TRIGGER IF NOT EXISTS cloud_portraits_no_update
BEFORE UPDATE ON portraits
BEGIN
    SELECT RAISE(ABORT, 'a portrait version cannot be changed');
END;
"""


class BadUpload(ValueError):
    """手机传来的内容不对。整次不收，手机上的记录还在。"""


def _canonical(record: dict) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True)


def test_planner(current: dict, new_sessions: list[dict]) -> dict:
    """写死的讲法，只为验证计划能送到手机。"""
    reminders = [item for item in current.get("reminders") or [] if item != TEST_REMINDER]
    return {
        "voice": current["voice"],
        "reminders": [*reminders, TEST_REMINDER][-3:],
        "examples": current.get("examples") or [],
        "forbid": current.get("forbid") or "不要直接说出标准答案。",
    }


class CloudStore:
    """按孩子保存历次辅导、计划、档案和专属题。辅导收下后不能改，版本只加不改。"""

    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), check_same_thread=False)
        self.connection.executescript(_SCHEMA)

    def __enter__(self) -> "CloudStore":
        return self

    def __exit__(self, *_) -> None:
        self.connection.close()

    def keep_session(self, record: dict) -> bool:
        """收下一条辅导。已经有一模一样的也算收到，编号相同内容不同则不收。"""
        text = _canonical(record)
        row = self.connection.execute("SELECT record FROM sessions WHERE sessionId = ?", (record["sessionId"],)).fetchone()
        if row:
            return row[0] == text
        order = self.connection.execute("SELECT COALESCE(MAX(receivedOrder), 0) + 1 FROM sessions").fetchone()[0]
        self.connection.execute(
            "INSERT INTO sessions (sessionId, childId, record, receivedOrder) VALUES (?, ?, ?, ?)",
            (record["sessionId"], record["childId"], text, order),
        )
        return True

    def sessions_after(self, child_id: str, session_id: str | None) -> list[dict]:
        """这个孩子在 session_id 之后传上来的辅导，旧的在前。"""
        row = self.connection.execute(
            "SELECT receivedOrder FROM sessions WHERE sessionId = ? AND childId = ?", (session_id, child_id)
        ).fetchone()
        start = row[0] if row else 0
        rows = self.connection.execute(
            "SELECT record FROM sessions WHERE childId = ? AND receivedOrder > ? ORDER BY receivedOrder",
            (child_id, start),
        ).fetchall()
        return [json.loads(item[0]) for item in rows]

    def latest_order(self, child_id: str) -> int:
        row = self.connection.execute("SELECT MAX(receivedOrder) FROM sessions WHERE childId = ?", (child_id,)).fetchone()
        return row[0] or 0

    def latest_version(self, child_id: str) -> int:
        row = self.connection.execute("SELECT MAX(planVersion) FROM plans WHERE childId = ?", (child_id,)).fetchone()
        return row[0] or 0

    def plan_at(self, child_id: str, version: int) -> dict | None:
        row = self.connection.execute(
            "SELECT plan FROM plans WHERE childId = ? AND planVersion = ?", (child_id, version)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def add_plan(self, plan: dict) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO plans (childId, planVersion, plan) VALUES (?, ?, ?)",
                (plan["childId"], plan["planVersion"], json.dumps(plan, ensure_ascii=False)),
            )

    def latest_portrait(self, child_id: str) -> dict | None:
        row = self.connection.execute(
            "SELECT portrait FROM portraits WHERE childId = ? ORDER BY version DESC LIMIT 1", (child_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def add_portrait(self, child_id: str, portrait: dict) -> None:
        latest = self.latest_portrait(child_id)
        version = latest["version"] + 1 if latest else 1
        with self.connection:
            self.connection.execute(
                "INSERT INTO portraits (childId, version, portrait) VALUES (?, ?, ?)",
                (child_id, version, json.dumps({**portrait, "version": version}, ensure_ascii=False)),
            )

    def latest_written(self, child_id: str) -> dict | None:
        row = self.connection.execute("SELECT written FROM reports WHERE childId = ? ORDER BY version DESC LIMIT 1", (child_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def add_written(self, child_id: str, written: dict) -> None:
        version = self.connection.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM reports WHERE childId = ?", (child_id,)).fetchone()[0]
        with self.connection:
            self.connection.execute(
                "INSERT INTO reports (childId, version, written) VALUES (?, ?, ?)",
                (child_id, version, json.dumps(written, ensure_ascii=False)),
            )

    def children(self) -> list[str]:
        rows = self.connection.execute("SELECT DISTINCT childId FROM sessions ORDER BY childId").fetchall()
        return [row[0] for row in rows]

    def add_drill(self, child_id: str, item: dict) -> None:
        drill_id = "d_" + uuid.uuid4().hex[:12]
        with self.connection:
            self.connection.execute(
                "INSERT INTO drills (drillId, childId, item) VALUES (?, ?, ?)",
                (drill_id, child_id, json.dumps({**item, "drillId": drill_id, "childId": child_id}, ensure_ascii=False)),
            )

    def drills_after(self, child_id: str, seq: int) -> list[dict]:
        rows = self.connection.execute("SELECT seq, item FROM drills WHERE childId = ? AND seq > ? ORDER BY seq", (child_id, seq)).fetchall()
        return [{**json.loads(item), "seq": number} for number, item in rows]

    def keep_questions(self, record: dict) -> None:
        """和原始辅导一起落库；同一条记录再次同步不重复建疑惑。"""
        for item in collect_questions(record):
            seq = self.connection.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM questions").fetchone()[0]
            self.connection.execute(
                "INSERT OR IGNORE INTO questions (questionId, childId, seq, status, item) VALUES (?, ?, ?, ?, ?)",
                (item["questionId"], item["childId"], seq, "pending", json.dumps(item, ensure_ascii=False)),
            )

    def questions_after(self, child_id: str, seq: int = 0) -> list[dict]:
        rows = self.connection.execute(
            "SELECT seq, item FROM questions WHERE childId = ? AND seq > ? ORDER BY seq", (child_id, seq),
        ).fetchall()
        return [{**json.loads(item), "seq": number} for number, item in rows]

    def pending_questions(self, child_id: str) -> list[dict]:
        rows = self.connection.execute(
            "SELECT item FROM questions WHERE childId = ? AND status = 'pending' ORDER BY seq LIMIT ?",
            (child_id, BATCH_SIZE),
        ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def answer_question(self, question_id: str, child_id: str, draft: dict) -> None:
        row = self.connection.execute(
            "SELECT item FROM questions WHERE questionId = ? AND childId = ? AND status = 'pending'", (question_id, child_id),
        ).fetchone()
        if row is None:
            return
        item = {**json.loads(row[0]), "status": draft["status"], "answer": draft["answer"]}
        seq = self.connection.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM questions").fetchone()[0]
        with self.connection:
            self.connection.execute(
                "UPDATE questions SET seq = ?, status = ?, item = ? WHERE questionId = ? AND childId = ?",
                (seq, item["status"], json.dumps(item, ensure_ascii=False), question_id, child_id),
            )


class CloudApp:
    """云端要做的事，不含 HTTP。测试直接用它。

    ``background`` 为 False 时，写讲法和出题在同步请求里做完，测试用它。
    """

    def __init__(self, store: CloudStore, planner=test_planner, drill_maker=None, background: bool = False, reporter=None, recorded: bool = False, question_answerer=None):
        self.store = store
        self.recorded = recorded  # 演示备份：内容是提前写好的，要告诉看的人
        self.planner = planner
        self.drill_maker = drill_maker
        self.reporter = reporter
        self.question_answerer = question_answerer
        self.background = background
        self.lock = threading.RLock()
        self.active: set[str] = set()
        self.failed: dict[str, bool] = {}
        self.question_failed: dict[str, bool] = {}
        self.looked: dict[str, int] = {}  # 已经看过、不需要再写的记录位置

    def sync(self, child_id, have_version, sessions, drill_seq=0, question_seq=0) -> dict:
        self._check_request(child_id, have_version, sessions, drill_seq)
        if type(question_seq) is not int or question_seq < 0:
            raise BadUpload("疑惑同步位置不对")
        with self.lock:
            self._seed_plans(child_id)
            with self.store.connection:
                accepted = []
                for record in sessions:
                    if self.store.keep_session(record):
                        self.store.keep_questions(record)
                        accepted.append(record["sessionId"])
            start = self._should_start(child_id, have_version)
            if start:
                self.active.add(child_id)
        if start:
            if self.background:
                threading.Thread(target=self._job, args=(child_id, have_version), daemon=True).start()
            else:
                self._job(child_id, have_version)
        with self.lock:
            reply = {"accepted": accepted, "plan": self.store.plan_at(child_id, have_version + 1)}
            drills = self.store.drills_after(child_id, drill_seq)
            if drills:
                reply["drills"] = drills
            questions = self.store.questions_after(child_id, question_seq)
            if questions:
                reply["questions"] = questions
            question_failed = self.question_failed.pop(child_id, False)
            if question_failed:
                reply["questionFailed"] = True
            if self.failed.pop(child_id, False):
                reply["planFailed"] = True
            if child_id in self.active:
                reply["working"] = True
            elif self.question_answerer and not question_failed and self.store.pending_questions(child_id):
                # 一次只处理少量疑惑，余下的由下一次自动同步继续处理。
                reply["working"] = True
            if self.recorded:
                reply["recorded"] = True
            return reply

    def view(self, child_id: str) -> dict:
        """给人看的这个孩子的现状。"""
        with self.lock:
            latest = self.store.latest_version(child_id)
            return {
                "portrait": self.store.latest_portrait(child_id),
                "plan": self.store.plan_at(child_id, latest) if latest else None,
                "drills": self.store.drills_after(child_id, 0),
                "questions": self.store.questions_after(child_id),
                "working": child_id in self.active,
            }

    def report(self, child_id: str) -> dict:
        """家长和老师看的内容。次数每次现算，所以孩子刚做完就是新的。"""
        with self.lock:
            sessions = self.store.sessions_after(child_id, None)
            drills = self.store.drills_after(child_id, 0)
            written = self.store.latest_written(child_id)
            portrait = self.store.latest_portrait(child_id) or {}
            working = child_id in self.active
            questions = self.store.questions_after(child_id)
        stats = build_stats(sessions)
        return {
            "childId": child_id,
            "judgement": portrait.get("judgement"),
            "focus": portrait.get("focus") or [],
            "working": working,
            "recorded": self.recorded,
            "questions": questions,
            **build_report(stats, build_facts(sessions, drills), written),
        }

    def children(self) -> list[str]:
        with self.lock:
            return self.store.children()

    def _check_request(self, child_id, have_version, sessions, drill_seq) -> None:
        if not isinstance(child_id, str) or not child_id:
            raise BadUpload("没有写是哪个孩子")
        if not isinstance(have_version, int) or isinstance(have_version, bool) or have_version < 0:
            raise BadUpload("计划版本不对")
        if not isinstance(drill_seq, int) or isinstance(drill_seq, bool) or drill_seq < 0:
            raise BadUpload("专属题位置不对")
        if not isinstance(sessions, list):
            raise BadUpload("辅导记录的格式不对")
        for record in sessions:
            if not isinstance(record, dict) or any(key not in record for key in _REQUIRED):
                raise BadUpload("辅导记录缺内容")
            if not isinstance(record["sessionId"], str) or record["childId"] != child_id:
                raise BadUpload("辅导记录不属于这个孩子")

    def _seed_plans(self, child_id: str) -> None:
        # 手机一开始就有这两版，云端要从同样的地方接着往下编
        if self.store.latest_version(child_id):
            return
        for name in ("child01_v1.json", "child01_v2.json"):
            plan = json.loads((ROOT / "data" / "plans" / name).read_text(encoding="utf-8"))["plan"]
            self.store.add_plan({**plan, "childId": child_id})

    def _should_start(self, child_id: str, have: int) -> bool:
        """有没看过的新记录才开工。刚失败过的先让手机知道，下次同步再试。"""
        if child_id in self.active or self.failed.get(child_id) or self.question_failed.get(child_id):
            return False
        latest = self.store.latest_version(child_id)
        # 手机落后时先把现成的发完，版本必须一版一版加
        if latest > have:
            return False
        if self.question_answerer and self.store.pending_questions(child_id):
            return True
        current = self.store.plan_at(child_id, latest)
        if not self.store.sessions_after(child_id, current.get("basedOnSessionId")):
            return False
        return self.store.latest_order(child_id) > self.looked.get(child_id, 0)

    def _job(self, child_id: str, have: int) -> None:
        try:
            self._work(child_id, have)
        except Exception as error:  # 后台出错不能影响记录，下次同步再试
            print("后台工作出错:", repr(error), flush=True)
            with self.lock:
                self.failed[child_id] = True
        finally:
            with self.lock:
                self.active.discard(child_id)

    def _work(self, child_id: str, have: int) -> None:
        with self.lock:
            latest = self.store.latest_version(child_id)
            current = self.store.plan_at(child_id, latest)
            new_sessions = self.store.sessions_after(child_id, current.get("basedOnSessionId"))
            everything = self.store.sessions_after(child_id, None)
            order = self.store.latest_order(child_id)
            prior = self.store.latest_portrait(child_id) or {}
            given = self.store.drills_after(child_id, 0)
            known = {_norm(item["text"]) for item in given}
            # 做错的原题不用等大模型，先发回去
            for item in redo_items(new_sessions, known):
                self.store.add_drill(child_id, item)

        # 先发回原错题，再答疑；答疑不依赖新讲法是否成功。
        self._answer_questions(child_id)
        if not new_sessions:
            return

        stats = build_stats(everything)
        profile = {"统计": stats_for_model(stats), "上次的判断": prior.get("judgement"), "要练的地方": prior.get("focus")}
        draft = self._write_plan(child_id, have, {**current, "profile": profile}, new_sessions)
        wrote = draft if isinstance(draft, dict) else None

        written = (wrote or {}).get("portrait") or {}
        portrait = {
            "childId": child_id,
            "basedOnSessionId": new_sessions[-1]["sessionId"] if new_sessions else prior.get("basedOnSessionId"),
            "stats": stats,
            "judgement": written.get("judgement") or prior.get("judgement"),
            "focus": written.get("focus") or prior.get("focus") or [],
        }
        with self.lock:
            if {key: portrait[key] for key in ("stats", "judgement", "focus")} != {key: prior.get(key) for key in ("stats", "judgement", "focus")}:
                self.store.add_portrait(child_id, portrait)

        if wrote and self.reporter:
            self._write_feedback(child_id, stats, everything)
        if wrote and self.drill_maker:
            self._make_drills(child_id, portrait, new_sessions, everything)
        with self.lock:
            if not self.failed.get(child_id):
                self.looked[child_id] = order

    def _answer_questions(self, child_id: str) -> None:
        if self.question_answerer is None:
            return
        with self.lock:
            questions = self.store.pending_questions(child_id)
        for question in questions:
            try:
                draft = self.question_answerer(question)
                # 可注入的生成器也不能写入任意字段或状态。
                if not isinstance(draft, dict) or draft.get("status") not in ("answered", "needs_teacher"):
                    raise ValueError("答疑状态不对")
                answer = draft.get("answer")
                if not isinstance(answer, str) or not answer.strip() or len(answer) > MAX_ANSWER:
                    raise ValueError("答疑内容不对")
                with self.lock:
                    self.store.answer_question(question["questionId"], child_id, {"status": draft["status"], "answer": answer.strip()})
            except Exception as error:
                print("这条疑惑暂未解答:", type(error).__name__, flush=True)
                with self.lock:
                    self.question_failed[child_id] = True
                # 留在 pending，下次手动同步重试；其他任务仍可以继续。

    def _write_plan(self, child_id: str, have: int, current: dict, new_sessions: list[dict]) -> dict | None:
        try:
            draft = self.planner(current, new_sessions)
            if draft is None:
                return None
            plan = {
                "childId": child_id,
                "planVersion": have + 1,
                "basedOnSessionId": new_sessions[-1]["sessionId"],
                "voice": draft.get("voice"),
                "reminders": draft.get("reminders") or [],
                "examples": draft.get("examples") or [],
                "forbid": draft.get("forbid"),
            }
        except Exception as error:  # 写讲法失败不能影响记录，下次同步再试
            print("写计划出错:", repr(error), flush=True)
            with self.lock:
                self.failed[child_id] = True
            return None
        check = check_plan(plan, have)
        if not check["checkPassed"]:
            print("新计划没通过检查:", check["checkReasons"], flush=True)
            with self.lock:
                self.failed[child_id] = True
            return None
        with self.lock:
            self.store.add_plan({**plan, **check})
        return draft

    def _write_feedback(self, child_id: str, stats: dict, everything: list[dict]) -> None:
        with self.lock:
            drills = self.store.drills_after(child_id, 0)
        try:
            written = self.reporter(stats, everything, build_facts(everything, drills))
        except Exception as error:  # 写不出来时页面用程序的说法，不影响别的
            print("写反馈出错:", repr(error), flush=True)
            return
        with self.lock:
            self.store.add_written(child_id, written)

    def _make_drills(self, child_id: str, portrait: dict, new_sessions: list[dict], everything: list[dict]) -> None:
        with self.lock:
            given = self.store.drills_after(child_id, 0)
        done = {session.get("drillId") for session in everything}
        if sum(item["drillId"] not in done for item in given) >= MAX_OPEN_DRILLS:
            return
        known = {_norm(item["text"]) for item in given} | {_norm(session.get("recognizedText", "")) for session in everything}
        try:
            items = self.drill_maker(portrait, new_sessions, known)
        except Exception as error:  # 出题失败不影响讲法，原题已经发回去了
            print("出专属题出错:", repr(error), flush=True)
            return
        with self.lock:
            for item in items:
                self.store.add_drill(child_id, item)


def make_handler(app: CloudApp):
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

        def do_GET(self):
            if self.path == "/" or self.path.startswith("/?"):
                data = REPORT_PAGE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            elif self.path == "/api/children":
                self._json(200, {"children": app.children()})
            elif self.path.startswith("/api/report/") and self.path.count("/") == 3:
                self._json(200, app.report(self.path.rsplit("/", 1)[1]))
            elif self.path.startswith("/api/child/") and self.path.count("/") == 3:
                self._json(200, app.view(self.path.rsplit("/", 1)[1]))
            else:
                self._json(404, {"error": "没有这个页面"})

        def do_POST(self):
            if self.path != "/api/sync":
                self._json(404, {"error": "没有这个操作"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length < 0 or length > MAX_BODY:
                    raise BadUpload("内容太大了")
                data = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(data, dict):
                    raise BadUpload("内容看不懂")
                result = app.sync(data.get("childId"), data.get("planVersion"), data.get("sessions"), data.get("drillSeq", 0), data.get("questionSeq", 0))
            except (BadUpload, ValueError) as error:
                self._json(400, {"error": str(error)})
            except Exception as error:  # 服务不能因此退出
                print("处理同步出错:", repr(error), flush=True)
                self._json(500, {"error": "云端出了点问题"})
            else:
                self._json(200, result)

    return Handler


def pick_planner(use_test: bool = False):
    if use_test:
        print("写讲法：写死的测试讲法", flush=True)
        return test_planner
    # 关掉思考：实测写讲法从 26 到 63 秒降到 9 到 14 秒，内容没有变差
    chat = chat_from_env(think=False)
    if chat is None:
        print("写讲法：没有设置大模型，只收记录，不写新讲法", flush=True)
        return lambda current, new_sessions: None
    print("写讲法：大模型", flush=True)
    return make_planner(chat)


def pick_drill_maker(use_test: bool = False):
    chat = None if use_test else chat_from_env()
    return make_drill_maker(chat) if chat else None


def pick_reporter(use_test: bool = False):
    # 反馈就几句话，关掉思考，十几秒写完
    chat = None if use_test else chat_from_env(think=False)
    return make_reporter(chat) if chat else None


def pick_question_answerer(use_test: bool = False):
    chat = None if use_test else chat_from_env()
    return make_answerer(chat) if chat else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--test-planner", action="store_true", help="用写死的讲法，不调用大模型")
    parser.add_argument("--record", help="把星火写好的内容录到这个文件，做演示备份")
    parser.add_argument("--replay", help="用录好的文件代替星火，不联网。页面会标明是提前写好的")
    args = parser.parse_args()
    if args.record and args.replay:
        parser.error("--record 和 --replay 不能同时用")
    if args.replay:
        replayer = Replayer(args.replay)
        print("写讲法：回放演示备份，不联网", flush=True)
        planner, maker, reporter = replayer.planner, replayer.maker, replayer.reporter
        question_answerer = None  # 旧录制不含答疑，回放时不偷偷调用真实接口。
    else:
        planner, maker, reporter = pick_planner(args.test_planner), pick_drill_maker(args.test_planner), pick_reporter(args.test_planner)
        question_answerer = pick_question_answerer(args.test_planner)
    if args.record:
        recorder = Recorder(args.record)
        planner, reporter = recorder.wrap_planner(planner), recorder.wrap_reporter(reporter) if reporter else None
        maker = recorder.wrap_maker(maker) if maker else None
    with CloudStore(args.db) as store:
        app = CloudApp(store, planner, maker, background=True, reporter=reporter, recorded=bool(args.replay), question_answerer=question_answerer)
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app))
        print(f"云端已启动：http://127.0.0.1:{args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
