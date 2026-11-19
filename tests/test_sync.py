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
from xiangke.planner import make_planner
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


def question_record(session_id="s1"):
    return {
        "sessionId": session_id,
        "childId": "child_01",
        "answerSource": "program",
        "source": "photo",
        "recognizedText": "改错（只改一个词）：She like cats.",
        "checkPassed": True,
        "startedAt": "2026-11-19T09:00:00+08:00",
        "endedAt": "2026-11-19T09:05:00+08:00",
        "steps": [{"stepId": "s1", "ask": "改成什么？", "expect": "likes", "explain": "She 是一个人。"}],
        "events": [{"stepId": "s1", "said": "为什么要加 s？", "result": "note", "hintUsed": 0, "noteKind": "question"}],
    }


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
    def test_scoped_plans_sync_and_teach_only_the_matching_new_topic(self):
        topics = {
            "be_i": {"strategy": "self_first", "voice": "先分清说的是自己"},
            "v_base_after:can": {"strategy": "cue_first", "voice": "先找表示能的词"},
            "be_there": {"strategy": "explain", "voice": "先看后面说的东西"},
        }
        requests = []

        def chat(messages):
            requests.append(messages)
            return json.dumps({"voice": "每一问都很短。", "reminders": [], "examples": [],
                               "forbid": "不要直接说出标准答案。", "topicPlans": topics}, ensure_ascii=False)

        self.cloud.planner = make_planner(chat)
        for text, wrong, correct in (
            ("I ___ at home. (am / is / are)", "is", "am"),
            ("She can ___ fast. (runs / run)", "runs", "run"),
        ):
            self.phone.start(text)
            self.phone.reply(wrong)
            self.assertEqual(self.phone.reply(correct)["summary"]["wrong"], 1)
        child = self.phone.child_id
        self.cloud.sync(child, 2, self.store.unsynced(child))
        pending = self.cloud.report(child)["planChanges"]
        self.assertEqual((pending["version"], pending["receivedVersion"]), (3, 2))
        self.assertEqual([item["evidence"]["said"] for item in pending["items"]], ["is", "runs"])
        self.assertEqual([item["evidence"]["expected"] for item in pending["items"]], ["am", "run"])
        self.assertIn("通用", pending["items"][0]["before"]["steps"])
        self.assertIn("自己还是别人", pending["items"][0]["after"]["steps"])

        self.phone.set_network(True)
        self.assertTrue(self.phone.sync()["syncResult"]["planUpdated"])
        received = self.cloud.report(child)["planChanges"]
        self.assertEqual((received["version"], received["receivedVersion"]), (3, 3))
        self.assertTrue(all("offlineUse" not in item for item in received["items"]))
        self.cloud.confirm_plan_received(child, 2)
        self.assertEqual(self.cloud.report(child)["planChanges"]["receivedVersion"], 3)
        self.assertIn('"题型": "be_i"', requests[0][-1]["content"])
        self.assertEqual(set(self.store.latest_plan("child_01")["topicPlans"]), {"be_i", "v_base_after:can"})

        self.phone.set_network(False)
        view = self.phone.start("I ___ ten years old. (are / am / is)")
        self.assertEqual(view["messages"], ["这句话说的是自己还是别人？用中文说。"])
        self.assertIn(topics["be_i"]["voice"], fake_write.seen[-1])
        self.assertNotIn(topics["v_base_after:can"]["voice"], fake_write.seen[-1])
        self.assertEqual(self.phone.reply("自己")["step"], 2)
        self.assertEqual(self.phone.reply("am")["summary"]["right"], 2)

        view = self.phone.start("My sister can ___ this song. (sings / sing)")
        self.assertEqual(view["messages"], ["题目里哪个英文词表示「能」？"])
        self.assertIn(topics["v_base_after:can"]["voice"], fake_write.seen[-1])
        self.assertNotIn(topics["be_i"]["voice"], fake_write.seen[-1])

        self.phone.start("My aunt doesn't ___ coffee. (drinks / drink)")
        prompt = fake_write.seen[-1]
        self.assertNotIn(topics["v_base_after:can"]["voice"], prompt)
        self.assertNotIn(topics["be_i"]["voice"], prompt)
        self.assertNotIn("他会写成 is", prompt)

        self.phone.leave()
        self.phone.set_network(True)
        self.phone.sync()
        used = self.cloud.report(child)["planChanges"]["items"]
        self.assertEqual(used[0]["offlineUse"], {
            "text": "I ___ ten years old. (are / am / is)",
            "firstQuestion": "这句话说的是自己还是别人？用中文说。",
            "version": 3,
        })
        self.assertNotIn("offlineUse", used[1])

    def test_offline_use_needs_a_judged_answer_and_survives_a_retry(self):
        topics = {"be_i": {"strategy": "self_first", "voice": "先分清说的是自己还是别人"}}
        self.cloud.planner = make_planner(
            lambda messages: json.dumps(
                {"voice": "每一问都很短。", "reminders": [], "examples": [],
                 "forbid": "不要直接说出标准答案。", "topicPlans": topics}, ensure_ascii=False))
        child = self.phone.child_id
        # 一道 be_i 错题，换来第 3 版讲法
        self.phone.start("I ___ at home. (am / is / are)")
        self.phone.reply("is")
        self.phone.reply("am")
        self.cloud.sync(child, 2, self.store.unsynced(child))
        self.phone.set_network(True)
        self.assertTrue(self.phone.sync()["syncResult"]["planUpdated"])

        def reported():
            items = self.cloud.report(child)["planChanges"]["items"]
            return next(item for item in items if item["title"] == "I 和 am 的搭配")

        # 先联网做过同一道题、再断网重做：重做要算数
        q = "I ___ ten years old. (are / am / is)"
        self.phone.start(q)
        self.phone.reply("自己")
        self.phone.reply("am")
        self.phone.sync()
        self.assertNotIn("offlineUse", reported())
        self.phone.set_network(False)
        self.phone.start(q)
        self.phone.reply("自己")
        self.phone.reply("am")
        self.phone.leave()
        self.phone.set_network(True)
        self.phone.sync()
        self.assertEqual(reported()["offlineUse"]["text"], q)

        # 第一次点开没答、第二次做完：也要算数
        q2 = "I ___ a student. (am / is / are)"
        self.phone.set_network(False)
        self.phone.start(q2)
        self.phone.leave()
        self.phone.start(q2)
        self.phone.reply("自己")
        self.phone.reply("am")
        self.phone.leave()
        self.phone.set_network(True)
        self.phone.sync()
        self.assertEqual(reported()["offlineUse"]["text"], q2)

        # 只记了疑问、一题没答：不能顶掉上面那条
        q3 = "I ___ from China. (am / is / are)"
        self.phone.set_network(False)
        self.phone.start(q3)
        self.phone.defer_question("为什么是 am？")
        self.phone.leave()
        self.phone.set_network(True)
        self.phone.sync()
        self.assertEqual(reported()["offlineUse"]["text"], q2)

    def test_offline_then_online_updates_the_plan_used_for_teaching(self):
        self.do_item()
        self.assertEqual(self.phone.home()["waitingSync"], 1)
        self.assertTrue(self.store.unsynced()[0]["offline"])
        self.assertNotIn(TEST_REMINDER, fake_write.seen[0])

        self.phone.set_network(True)
        home = self.phone.sync()
        self.assertEqual(home["syncResult"], {"sent": 1, "accepted": 1, "planUpdated": True, "planRefused": [], "questionsUpdated": 0, "planVersion": 3})
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
        self.assertEqual(result, {"sent": 0, "accepted": 0, "planUpdated": False, "planRefused": [], "questionsUpdated": 0, "planVersion": 3})
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

    def test_teacher_confirms_an_edited_explanation(self):
        self.cloud.sync("child_01", 2, [question_record()])
        question = self.store.questions_after("child_01")[0]
        self.store.answer_question(question["questionId"], "child_01", {"status": "needs_teacher", "answer": "这条需要老师确认。"})
        self.assertEqual(self.cloud.confirm_question("child_01", question["questionId"], "有 can 帮忙就不变，这道题没有 can，所以 like 要改成 likes。"), {"confirmed": True})
        updated = self.store.questions_after("child_01")[0]
        self.assertEqual(updated["status"], "teacher_confirmed")
        self.assertIn("likes", updated["answer"])
        self.assertGreater(updated["seq"], question["seq"])
        # 确认过的不能再改，别的孩子的疑惑也不能动
        with self.assertRaises(BadUpload):
            self.cloud.confirm_question("child_01", question["questionId"], "再改一次")
        with self.assertRaises(BadUpload):
            self.cloud.confirm_question("child_02", question["questionId"], "别的孩子")

    def test_confirm_refuses_empty_and_overlong_answers(self):
        self.cloud.sync("child_01", 2, [question_record()])
        question = self.store.questions_after("child_01")[0]
        self.store.answer_question(question["questionId"], "child_01", {"status": "answered", "answer": "云端的解释。"})
        for answer in ("", "   ", "多" * 401, None):
            with self.subTest(answer=repr(answer)[:20]):
                with self.assertRaises(BadUpload):
                    self.cloud.confirm_question("child_01", question["questionId"], answer)
        self.assertEqual(self.store.questions_after("child_01")[0]["status"], "answered")


class TeacherConfirmTests(SyncCase):
    def test_confirmed_letter_reaches_the_phone_with_a_badge_status(self):
        self.store.save_session(question_record())
        self.phone.set_network(True)
        self.phone.sync()
        question = self.cloud_store.questions_after("child_01")[0]
        self.cloud_store.answer_question(question["questionId"], "child_01", {"status": "needs_teacher", "answer": "这条需要老师确认。"})
        self.phone.sync()
        self.cloud.confirm_question("child_01", question["questionId"], "有 can 帮忙就不变，这道题没有 can，所以 like 要改成 likes。")
        home = self.phone.sync()
        letter = next(item for item in home["questions"] if item["questionId"] == question["questionId"])
        self.assertEqual(letter["status"], "teacher_confirmed")
        self.assertIn("likes", letter["answer"])


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

    def test_confirm_route_over_http(self):
        self.store.save_session(question_record())
        self.phone.set_network(True)
        self.phone.sync()
        question = self.cloud_store.questions_after("child_01")[0]
        self.cloud_store.answer_question(question["questionId"], "child_01", {"status": "answered", "answer": "云端的解释。"})
        result = self.phone_post(self.url, "/api/confirm", {"childId": "child_01", "questionId": question["questionId"], "answer": "老师改好的解释。"})
        self.assertTrue(result["confirmed"])
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.phone_post(self.url, "/api/confirm", {"childId": "child_01", "questionId": question["questionId"], "answer": " "})
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()


if __name__ == "__main__":
    unittest.main()
