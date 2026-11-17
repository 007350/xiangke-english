import json
import sqlite3
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from xiangke import cloud as cloud_module
from xiangke.app import BadRequest, PhoneApp, make_handler as phone_handler
from xiangke.cloud import TEST_REMINDER, BadUpload, CloudApp, CloudStore, make_handler
from xiangke.store import Store
from xiangke.sync import SyncFailed, post_json, sync_once

TEXT = "They ___ my friends. (am / is / are)"
LESSON = {
    "steps": [
        {"ask": "这句话的主语是哪个词？", "expect": "They", "explain": "主语是 They。"},
        {"ask": "那空里填 am、is 还是 are？", "expect": "are", "explain": "They 是两个或更多，所以填 are。"},
    ]
}


def fake_write(system, user, grammar, limit=512):
    if "孩子说：" in user:
        return json.dumps({"label": "答题"}, ensure_ascii=False)
    fake_write.seen.append(user)
    return json.dumps(LESSON, ensure_ascii=False)


fake_write.seen = []


def session(session_id, child="child_01"):
    return {"sessionId": session_id, "childId": child, "answerSource": "program", "events": [], "steps": []}


class SyncCase(unittest.TestCase):
    """一个真的云端服务，一个手机。"""

    def setUp(self):
        fake_write.seen = []
        self.cloud_store = CloudStore(":memory:")
        self.cloud = CloudApp(self.cloud_store)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.cloud))
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.store = Store(":memory:")
        self.phone = PhoneApp(fake_write, self.store, cloud_url=self.url)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.store.connection.close()
        self.cloud_store.connection.close()

    def do_item(self):
        self.phone.start(TEXT)
        self.phone.reply("They")
        self.phone.reply("are")


class WholeLoopTests(SyncCase):
    def test_offline_then_online_updates_the_plan_used_for_teaching(self):
        self.do_item()
        self.assertEqual(self.phone.home()["waitingSync"], 1)
        self.assertTrue(self.store.unsynced()[0]["offline"])
        self.assertNotIn(TEST_REMINDER, fake_write.seen[0])

        self.phone.set_network(True)
        home = self.phone.sync()
        self.assertEqual(home["syncResult"], {"sent": 1, "accepted": 1, "planUpdated": True, "planRefused": [], "planVersion": 3})
        self.assertEqual(home["waitingSync"], 0)
        self.assertEqual(home["planVersion"], 3)

        plan = self.store.latest_plan("child_01")
        self.assertIn(TEST_REMINDER, plan["reminders"])
        self.assertTrue(plan["checkPassed"])
        self.assertEqual(self.cloud_store.sessions_after("child_01", None)[0]["recognizedText"], TEXT)

        self.phone.set_network(False)
        self.do_item()
        self.assertIn(TEST_REMINDER, fake_write.seen[-1])
        self.assertEqual(self.store.unsynced()[0]["planVersion"], 3)

    def test_second_sync_with_nothing_new_changes_nothing(self):
        self.do_item()
        self.phone.set_network(True)
        self.phone.sync()
        result = self.phone.sync()["syncResult"]
        self.assertEqual(result, {"sent": 0, "accepted": 0, "planUpdated": False, "planRefused": [], "planVersion": 3})
        self.assertEqual(self.cloud_store.latest_version("child_01"), 3)

    def test_no_sessions_yet_gives_no_new_plan(self):
        self.phone.set_network(True)
        self.assertEqual(self.phone.sync()["syncResult"]["planVersion"], 2)

    def test_start_records_the_switch_state(self):
        self.phone.set_network(True)
        self.do_item()
        self.assertFalse(self.store.unsynced()[0]["offline"])


class PhoneSafetyTests(SyncCase):
    def test_sync_is_refused_while_offline(self):
        self.do_item()
        with self.assertRaises(BadRequest):
            self.phone.sync()
        self.assertEqual(len(self.store.unsynced()), 1)
        self.assertEqual(self.cloud_store.sessions_after("child_01", None), [])

    def test_sync_is_refused_in_the_middle_of_an_item(self):
        self.phone.set_network(True)
        self.phone.start(TEXT)
        with self.assertRaises(BadRequest):
            self.phone.sync()

    def test_cloud_down_keeps_everything(self):
        self.do_item()
        self.phone.set_network(True)
        self.phone.cloud_url = "http://127.0.0.1:1"
        with self.assertRaises(BadRequest):
            self.phone.sync()
        self.assertEqual(len(self.store.unsynced()), 1)
        self.assertEqual(self.store.latest_plan("child_01")["planVersion"], 2)

    def test_only_what_the_cloud_accepted_is_marked(self):
        self.store.save_session(session("a"))
        self.store.save_session(session("b"))
        result = sync_once(self.store, "child_01", post=lambda url, body: {"accepted": ["a", "没传过的"], "plan": None})
        self.assertEqual((result["sent"], result["accepted"]), (2, 1))
        self.assertEqual([item["sessionId"] for item in self.store.unsynced()], ["b"])

    def test_unreadable_reply_changes_nothing(self):
        self.store.save_session(session("a"))
        with self.assertRaises(SyncFailed):
            sync_once(self.store, "child_01", post=lambda url, body: ["不是对象"])
        self.assertEqual(len(self.store.unsynced()), 1)

    def test_bad_plans_keep_the_old_one_but_sessions_stay_synced(self):
        good = {"childId": "child_01", "planVersion": 3, "voice": "讲法", "reminders": [], "examples": [], "forbid": "不说答案"}
        cases = [
            ({**good, "planVersion": 9}, "version_gap"),
            ({**good, "voice": " "}, "empty_voice"),
            ({**good, "reminders": ["1", "2", "3", "4"]}, "too_many_reminders"),
            ({**good, "childId": "别的孩子"}, "wrong_child"),
            ({**good, "forbid": None}, "bad_shape"),
            ({**good, "reminders": "一句话"}, "bad_shape"),
            ({**good, "examples": [{"steps": []}]}, "bad_shape"),
            ("不是计划", "wrong_child"),
        ]
        for index, (plan, reason) in enumerate(cases):
            with self.subTest(reason=reason, index=index):
                self.store.save_session(session(f"s{index}"))
                result = sync_once(self.store, "child_01", post=lambda url, body: {"accepted": [f"s{index}"], "plan": plan})
                self.assertIn(reason, result["planRefused"])
                self.assertFalse(result["planUpdated"])
                self.assertEqual(result["planVersion"], 2)
                self.assertEqual(self.store.unsynced(), [])


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.store = CloudStore(":memory:")
        self.cloud = CloudApp(self.store)

    def tearDown(self):
        self.store.connection.close()

    def test_lost_reply_gets_the_same_plan_again(self):
        first = self.cloud.sync("child_01", 2, [session("a")])
        again = self.cloud.sync("child_01", 2, [session("a")])
        self.assertEqual(first["plan"], again["plan"])
        self.assertEqual(again["accepted"], ["a"])
        self.assertEqual(self.store.latest_version("child_01"), 3)
        self.assertEqual(len(self.store.sessions_after("child_01", None)), 1)

    def test_phone_behind_gets_exactly_its_next_version(self):
        self.cloud.sync("child_01", 2, [session("a")])
        self.cloud.sync("child_01", 3, [session("b")])
        self.assertEqual(self.store.latest_version("child_01"), 4)
        self.assertEqual(self.cloud.sync("child_01", 2, [])["plan"]["planVersion"], 3)

    def test_new_sessions_wait_while_the_phone_is_behind(self):
        self.cloud.sync("child_01", 2, [session("a")])
        reply = self.cloud.sync("child_01", 2, [session("b")])
        self.assertEqual(reply["plan"]["basedOnSessionId"], "a")
        self.assertEqual(reply["accepted"], ["b"])
        self.assertEqual(self.cloud.sync("child_01", 3, [])["plan"]["basedOnSessionId"], "b")

    def test_planner_failure_keeps_records_and_retries_next_time(self):
        def broken(current, new_sessions):
            raise RuntimeError("大模型连不上")

        self.cloud.planner = broken
        reply = self.cloud.sync("child_01", 2, [session("a")])
        self.assertEqual((reply["accepted"], reply["plan"]), (["a"], None))
        self.cloud.planner = cloud_module.test_planner
        self.assertEqual(self.cloud.sync("child_01", 2, [])["plan"]["basedOnSessionId"], "a")

    def test_planner_cannot_write_the_programs_fields(self):
        def writer(current, new_sessions):
            return {"voice": "讲法", "reminders": [], "examples": [], "forbid": "不说答案", "planVersion": 99, "childId": "别人", "basedOnSessionId": "假的"}

        self.cloud.planner = writer
        plan = self.cloud.sync("child_01", 2, [session("a")])["plan"]
        self.assertEqual((plan["planVersion"], plan["childId"], plan["basedOnSessionId"]), (3, "child_01", "a"))

    def test_planner_with_too_many_reminders_makes_no_plan(self):
        self.cloud.planner = lambda current, new_sessions: {"voice": "讲法", "reminders": ["1", "2", "3", "4"], "forbid": "不说答案"}
        self.assertIsNone(self.cloud.sync("child_01", 2, [session("a")])["plan"])
        self.assertEqual(self.store.latest_version("child_01"), 2)

    def test_test_planner_does_not_pile_up_reminders(self):
        self.cloud.sync("child_01", 2, [session("a")])
        plan = self.cloud.sync("child_01", 3, [session("b")])["plan"]
        self.assertEqual(plan["reminders"].count(TEST_REMINDER), 1)
        self.assertLessEqual(len(plan["reminders"]), 3)

    def test_same_id_with_different_content_is_not_accepted(self):
        self.cloud.sync("child_01", 2, [session("a")])
        changed = {**session("a"), "answerSource": "model"}
        self.assertEqual(self.cloud.sync("child_01", 3, [changed])["accepted"], [])
        self.assertEqual(self.store.sessions_after("child_01", None)[0]["answerSource"], "program")

    def test_bad_requests_store_nothing(self):
        cases = [
            ("", 2, []),
            ("child_01", "2", []),
            ("child_01", True, []),
            ("child_01", -1, []),
            ("child_01", 2, "不是列表"),
            ("child_01", 2, [session("a"), {"sessionId": "b"}]),
            ("child_01", 2, [session("a"), session("b", child="child_02")]),
        ]
        for child, version, sessions in cases:
            with self.subTest(child=child, version=version):
                with self.assertRaises(BadUpload):
                    self.cloud.sync(child, version, sessions)
                self.assertEqual(self.store.sessions_after("child_01", None), [])

    def test_children_are_kept_apart(self):
        self.cloud.sync("child_01", 2, [session("a")])
        self.assertEqual(self.store.sessions_after("child_02", None), [])
        self.assertIsNone(self.cloud.sync("child_02", 2, [])["plan"])
        self.assertEqual(self.store.latest_version("child_02"), 2)

    def test_received_records_cannot_be_changed_or_deleted(self):
        self.cloud.sync("child_01", 2, [session("a")])
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.connection.execute("UPDATE sessions SET record = '{}'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.connection.execute("DELETE FROM sessions")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.connection.execute("UPDATE plans SET plan = '{}'")


class HttpTests(SyncCase):
    def phone_post(self, url, path, body):
        request = urllib.request.Request(url + path, json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"})
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def test_phone_routes_for_switch_and_sync(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), phone_handler(self.phone))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.phone_post(url, "/api/sync", {})
            self.assertEqual(caught.exception.code, 400)
            self.assertIn("断网", json.load(caught.exception)["error"])
            caught.exception.close()
            self.assertTrue(self.phone_post(url, "/api/network", {"online": True})["online"])
            self.assertEqual(self.phone_post(url, "/api/sync", {})["syncResult"]["sent"], 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_cloud_refuses_bad_bodies_with_a_message(self):
        for body in (b"not json", b"[1]", json.dumps({"childId": "child_01", "planVersion": 2, "sessions": "x"}).encode("utf-8")):
            with self.subTest(body=body):
                request = urllib.request.Request(self.url + "/api/sync", body)
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request)
                self.assertEqual(caught.exception.code, 400)
                caught.exception.close()

    def test_error_from_the_cloud_counts_as_failed(self):
        with self.assertRaises(SyncFailed):
            post_json(self.url + "/api/sync", {"childId": "", "planVersion": 2, "sessions": []})

    def test_unknown_cloud_paths_are_refused(self):
        with self.assertRaises(SyncFailed):
            post_json(self.url + "/data/pack.json", {})


if __name__ == "__main__":
    unittest.main()
