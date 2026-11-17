import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from xiangke.reply import finish_with_error_kinds
from xiangke.store import PlanRefused, Store

# 表结构说明：计划和辅导都在同一个文件里，见 xiangke/store.py
DATA = Path(__file__).resolve().parents[1] / "data"


def sample(session_id="s_1", child="child_01"):
    session = json.loads((DATA / "sessions" / "s01_be_verb.json").read_text(encoding="utf-8"))["session"]
    record = finish_with_error_kinds(session, ["is", "是am"])
    record.update(sessionId=session_id, childId=child, answerSource="program")
    return record


def plan_file(name):
    return json.loads((DATA / "plans" / name).read_text(encoding="utf-8"))["plan"]


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.connection.close()

    def test_record_comes_back_unchanged(self):
        record = sample()
        self.store.save_session(record)
        self.assertEqual(self.store.load_session("s_1"), record)
        self.assertEqual(record["events"][0]["errorKind"], "wrong_option")

    def test_unknown_session_is_none(self):
        self.assertIsNone(self.store.load_session("nope"))

    def test_same_session_cannot_be_saved_twice(self):
        self.store.save_session(sample())
        changed = sample()
        changed["events"][0]["result"] = "hit"
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.save_session(changed)
        self.assertEqual(self.store.load_session("s_1")["events"][0]["result"], "miss")

    def test_saved_record_cannot_be_rewritten_even_with_sql(self):
        self.store.save_session(sample())
        with self.assertRaises(sqlite3.DatabaseError):
            with self.store.connection:
                self.store.connection.execute("UPDATE sessions SET record = '{}' WHERE sessionId = 's_1'")
        self.assertEqual(self.store.load_session("s_1")["sessionId"], "s_1")

    def test_unsynced_session_cannot_be_deleted(self):
        self.store.save_session(sample())
        with self.assertRaises(sqlite3.DatabaseError):
            with self.store.connection:
                self.store.connection.execute("DELETE FROM sessions")

    def test_incomplete_record_is_refused(self):
        record = sample()
        del record["answerSource"]
        with self.assertRaises(ValueError):
            self.store.save_session(record)

    def test_unsynced_oldest_first_then_marked(self):
        for name in ("b", "a", "c"):
            self.store.save_session(sample(name))
        self.store.save_session(sample("x", "child_02"))
        self.assertEqual([r["sessionId"] for r in self.store.unsynced("child_01")], ["b", "a", "c"])
        self.assertEqual(len(self.store.unsynced()), 4)
        self.store.mark_synced(["b", "c"])
        self.assertEqual([r["sessionId"] for r in self.store.unsynced("child_01")], ["a"])
        # 同步过的记录还在
        self.assertIsNotNone(self.store.load_session("b"))

    def test_survives_closing_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sub" / "phone.db"
            with Store(path) as store:
                store.save_session(sample())
            with Store(path) as store:
                self.assertEqual(store.load_session("s_1")["sessionId"], "s_1")
                self.assertEqual(len(store.unsynced()), 1)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.connection.close()

    def test_first_plan_and_next_version(self):
        self.assertIsNone(self.store.latest_plan("child_01"))
        self.store.save_plan(plan_file("child01_v1.json"))
        self.assertEqual(self.store.latest_plan("child_01")["planVersion"], 1)
        self.store.save_plan(plan_file("child01_v2.json"))
        latest = self.store.latest_plan("child_01")
        self.assertEqual(latest["planVersion"], 2)
        self.assertTrue(latest["reminders"])
        # 手机只留最新一版
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM plans").fetchone()[0], 1)

    def test_bad_plan_keeps_the_old_one(self):
        self.store.save_plan(plan_file("child01_v1.json"))
        with self.assertRaises(PlanRefused) as caught:
            self.store.save_plan(plan_file("child01_bad.json"))
        self.assertIn("version_gap", caught.exception.reasons)
        self.assertEqual(self.store.latest_plan("child_01")["planVersion"], 1)

    def test_skipping_a_version_is_refused(self):
        with self.assertRaises(PlanRefused):
            self.store.save_plan(plan_file("child01_v2.json"))
        self.assertIsNone(self.store.latest_plan("child_01"))

    def test_check_is_recomputed(self):
        plan = plan_file("child01_v1.json")
        plan["checkPassed"] = False
        plan["checkReasons"] = ["编的"]
        stored = self.store.save_plan(plan)
        self.assertTrue(stored["checkPassed"])
        self.assertEqual(stored["checkReasons"], [])


if __name__ == "__main__":
    unittest.main()
