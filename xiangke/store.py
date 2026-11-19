"""The phone's SQLite store. Standard library only.

A finished session is stored once and never changed, so a recorded ``hit`` or
``miss`` cannot be rewritten later. The phone keeps only the latest plan, and a
plan must pass ``check_plan`` before it replaces the old one.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from xiangke.kernel import check_plan
from xiangke.questions import collect_questions

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    sessionId TEXT PRIMARY KEY,
    childId   TEXT NOT NULL,
    record    TEXT NOT NULL,
    synced    INTEGER NOT NULL DEFAULT 0,
    savedOrder INTEGER
);
CREATE TABLE IF NOT EXISTS plans (
    childId     TEXT PRIMARY KEY,
    planVersion INTEGER NOT NULL,
    plan        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS drills (
    drillId TEXT PRIMARY KEY,
    childId TEXT NOT NULL,
    seq     INTEGER NOT NULL,
    item    TEXT NOT NULL,
    status  TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS questions (
    questionId TEXT PRIMARY KEY,
    childId TEXT NOT NULL,
    seq INTEGER NOT NULL,
    item TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS sessions_keep_record
BEFORE UPDATE OF record, sessionId, childId ON sessions
BEGIN
    SELECT RAISE(ABORT, 'a finished session cannot be changed');
END;
CREATE TRIGGER IF NOT EXISTS sessions_keep_row
BEFORE DELETE ON sessions
WHEN OLD.synced = 0
BEGIN
    SELECT RAISE(ABORT, 'an unsynced session cannot be deleted');
END;
"""
_REQUIRED = ("sessionId", "childId", "answerSource", "events", "steps")


class PlanRefused(ValueError):
    """The plan did not pass ``check_plan``. ``reasons`` says why."""

    def __init__(self, reasons: list[str]):
        super().__init__(", ".join(reasons))
        self.reasons = reasons


class Store:
    """Use as a context manager. ``path`` may be ``:memory:`` in tests."""

    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # 网页服务用多个线程。调用方要自己保证同一时间只有一个线程在用
        self.connection = sqlite3.connect(str(path), check_same_thread=False)
        self.connection.executescript(_SCHEMA)

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *_) -> None:
        self.connection.close()

    def save_session(self, record: dict) -> None:
        """Store a finished session. Saving the same ``sessionId`` twice is refused."""
        missing = [key for key in _REQUIRED if key not in record]
        if missing:
            raise ValueError(f"record is missing {missing}")
        text = json.dumps(record, ensure_ascii=False)
        with self.connection:
            order = self.connection.execute("SELECT COALESCE(MAX(savedOrder), 0) + 1 FROM sessions").fetchone()[0]
            self.connection.execute(
                "INSERT INTO sessions (sessionId, childId, record, savedOrder) VALUES (?, ?, ?, ?)",
                (record["sessionId"], record["childId"], text, order),
            )
            # seq 为 0 表示尚未同步。和辅导一起提交，断网也能查看已留下的疑惑。
            for item in collect_questions(record):
                self.connection.execute(
                    "INSERT OR IGNORE INTO questions (questionId, childId, seq, item) VALUES (?, ?, 0, ?)",
                    (item["questionId"], item["childId"], json.dumps({**item, "seq": 0}, ensure_ascii=False)),
                )

    def load_session(self, session_id: str) -> dict | None:
        row = self.connection.execute("SELECT record FROM sessions WHERE sessionId = ?", (session_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def unsynced(self, child_id: str | None = None) -> list[dict]:
        """Sessions waiting to go up, oldest first."""
        query = "SELECT record FROM sessions WHERE synced = 0"
        values: tuple = ()
        if child_id is not None:
            query += " AND childId = ?"
            values = (child_id,)
        rows = self.connection.execute(query + " ORDER BY savedOrder", values).fetchall()
        return [json.loads(row[0]) for row in rows]

    def recent(self, child_id: str, limit: int = 200) -> list[dict]:
        """Newest first, for showing past sessions.

        200 covers a term of daily practice; the page fold is a display
        concern and must not silently drop records the store already has.
        """
        rows = self.connection.execute(
            "SELECT record FROM sessions WHERE childId = ? ORDER BY savedOrder DESC LIMIT ?",
            (child_id, limit),
        ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def started_on(self, child_id: str, day: str) -> int:
        """这一天开始做了几道题。``day`` 形如 ``2026-11-18``。

        一个孩子一天只有几条，扫全部记录比依赖 JSON 文本格式更稳。
        """
        rows = self.connection.execute("SELECT record FROM sessions WHERE childId = ?", (child_id,)).fetchall()
        return sum(json.loads(row[0]).get("startedAt", "").startswith(day) for row in rows)

    def mark_synced(self, session_ids: list[str]) -> None:
        with self.connection:
            self.connection.executemany("UPDATE sessions SET synced = 1 WHERE sessionId = ?", [(item,) for item in session_ids])

    def save_drills(self, items: list[dict]) -> int:
        """存下云端发来的专属题。已经有的不动。返回新加了几道。"""
        added = 0
        with self.connection:
            for item in items:
                cursor = self.connection.execute(
                    "INSERT OR IGNORE INTO drills (drillId, childId, seq, item) VALUES (?, ?, ?, ?)",
                    (item["drillId"], item["childId"], item["seq"], json.dumps(item, ensure_ascii=False)),
                )
                added += cursor.rowcount
        return added

    def last_drill_seq(self, child_id: str) -> int:
        row = self.connection.execute("SELECT MAX(seq) FROM drills WHERE childId = ?", (child_id,)).fetchone()
        return row[0] or 0

    def save_questions(self, items: list[dict]) -> int:
        """按云端更新序号覆盖疑惑状态；重复或较旧的同步结果不覆盖新答复。"""
        changed = 0
        with self.connection:
            for item in items:
                cursor = self.connection.execute(
                    "INSERT INTO questions (questionId, childId, seq, item) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(questionId) DO UPDATE SET seq = excluded.seq, item = excluded.item "
                    "WHERE questions.childId = excluded.childId AND questions.seq < excluded.seq",
                    (item["questionId"], item["childId"], item["seq"], json.dumps(item, ensure_ascii=False)),
                )
                changed += cursor.rowcount
        return changed

    def last_question_seq(self, child_id: str) -> int:
        row = self.connection.execute("SELECT MAX(seq) FROM questions WHERE childId = ?", (child_id,)).fetchone()
        return row[0] or 0

    def questions(self, child_id: str) -> list[dict]:
        rows = self.connection.execute(
            "SELECT item FROM questions WHERE childId = ? ORDER BY CASE WHEN seq = 0 THEN 0 ELSE 1 END, seq DESC", (child_id,),
        ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def open_drills(self, child_id: str) -> list[dict]:
        rows = self.connection.execute("SELECT item FROM drills WHERE childId = ? AND status = 'open' ORDER BY seq", (child_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def set_drill_status(self, drill_id: str, status: str) -> None:
        with self.connection:
            self.connection.execute("UPDATE drills SET status = ? WHERE drillId = ?", (status, drill_id))

    def latest_plan(self, child_id: str) -> dict | None:
        row = self.connection.execute("SELECT plan FROM plans WHERE childId = ?", (child_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_plan(self, plan: dict) -> dict:
        """Check the plan against the phone's version, then replace the old one.

        The check is run here. A ``checkPassed`` written in the plan is ignored.
        A refused plan leaves the old plan in place.
        """
        child_id = plan["childId"]
        current = self.latest_plan(child_id)
        previous = current["planVersion"] if current else None
        check = check_plan(plan, previous)
        if not check["checkPassed"]:
            raise PlanRefused(check["checkReasons"])
        stored = {**plan, **check}
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO plans (childId, planVersion, plan) VALUES (?, ?, ?)",
                (child_id, plan["planVersion"], json.dumps(stored, ensure_ascii=False)),
            )
        return stored
