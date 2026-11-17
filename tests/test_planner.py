import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from xiangke.cloud import CloudApp, CloudStore, make_handler, pick_planner, test_planner
from xiangke.planner import (
    LlmError,
    PlanFailed,
    build_messages,
    chat_from_env,
    check_draft,
    collect_facts,
    has_signal,
    make_chat,
    make_planner,
)
from xiangke.store import Store
from xiangke.sync import sync_once

CURRENT = {
    "voice": "先让他看主语，再选该用的那个词。",
    "reminders": ["主语是 I 的时候，他会写成 is。"],
    "examples": [{"recognizedText": "I ___ a student. (am / is / are)", "steps": [{"ask": "I 后面用哪个？", "expect": "am"}]}],
    "forbid": "不要直接说出标准答案。",
}
GOOD = {
    "voice": "每一问都很短，先让他说出主语是谁。",
    "reminders": ["She 后面的动词他会忘记变。先问主语是几个人。"],
    "examples": [],
    "forbid": "不要直接说出标准答案。",
}


def record(session_id="s1", events=None, **extra):
    return {
        "sessionId": session_id,
        "childId": "child_01",
        "planVersion": 2,
        "recognizedText": "改错（只改一个词）：She like cats.",
        "answerSource": "program",
        "checkPassed": True,
        "startedAt": "2026-11-05T20:00:00+08:00",
        "steps": [
            {"stepId": "s1", "ask": "哪个词用错了？", "expect": "like", "explain": "用错的是 like。"},
            {"stepId": "s2", "ask": "like 应该改成什么？", "expect": "likes", "explain": "She 是一个人。"},
        ],
        "events": events if events is not None else [
            {"stepId": "s1", "said": "cats", "result": "miss", "hintUsed": 1, "errorKind": "other"},
            {"stepId": "s1", "said": "什么时候不加s", "result": "note", "hintUsed": 1},
            {"stepId": "s1", "said": "like", "result": "hit", "hintUsed": 1},
        ],
        **extra,
    }


def dumps(draft):
    return json.dumps(draft, ensure_ascii=False)


class FactsTests(unittest.TestCase):
    def test_facts_hold_the_real_replies_and_the_program_answer(self):
        fact = collect_facts([record()])[0]
        self.assertEqual(fact["程序算的答案"], "likes")
        said = fact["过程"][0]["回答"]
        self.assertEqual(said[0], {"孩子说": "cats", "结果": "程序判错", "错的类型": "other"})
        self.assertEqual(said[1]["结果"], "不算答题")

    def test_nothing_that_identifies_the_child_goes_out(self):
        text = json.dumps(build_messages(CURRENT, [record(session_id="s_secret")]), ensure_ascii=False)
        for secret in ("child_01", "s_secret", "2026-11-05", "childId"):
            self.assertNotIn(secret, text)

    def test_phone_mistakes_are_not_the_childs_mistakes(self):
        broken = collect_facts([record(checkPassed=False, events=[{"stepId": None, "said": "手机没讲", "result": "note", "hintUsed": 0}])])[0]
        self.assertEqual(broken["说明"], "手机把题拆错了，孩子的话不算对错")
        self.assertEqual(broken["其他的话"], ["手机没讲"])
        self.assertNotIn("程序算的答案", broken)
        guess = collect_facts([record(answerSource="model")])[0]
        self.assertIn("不一定对", guess["说明"])

    def test_all_right_has_no_signal(self):
        self.assertTrue(has_signal([record()]))
        self.assertFalse(has_signal([record(events=[{"stepId": "s1", "said": "like", "result": "hit", "hintUsed": 0}])]))


class CheckDraftTests(unittest.TestCase):
    def errors(self, **change):
        return check_draft(dumps({**GOOD, **change}), CURRENT, [record()])[1]

    def test_good_draft_passes(self):
        draft, errors = check_draft(dumps(GOOD), CURRENT, [record()])
        self.assertEqual(errors, [])
        self.assertEqual(draft["reminders"], GOOD["reminders"])
        self.assertEqual(set(draft), {"voice", "reminders", "examples", "forbid"})

    def test_json_inside_thinking_or_fences_is_found(self):
        for raw in (f"<think>想一想 {{坏的}}</think>{dumps(GOOD)}", f"```json\n{dumps(GOOD)}\n```"):
            self.assertEqual(check_draft(raw, CURRENT, [record()])[1], [])

    def test_broken_output_is_refused(self):
        for raw in ("", "不是 JSON", "[1, 2]", "{坏的}"):
            with self.subTest(raw=raw):
                self.assertIsNone(check_draft(raw, CURRENT, [record()])[0])

    def test_voice_rules(self):
        self.assertTrue(self.errors(voice=" "))
        self.assertTrue(self.errors(voice=None))
        self.assertTrue(self.errors(voice="多" * 61))
        self.assertTrue(any("具体题目" in item for item in self.errors(voice="记得 likes 要加 s。")))

    def test_voice_may_use_ordinary_subject_words(self):
        self.assertEqual(self.errors(voice="先问 he、she、it 这样的主语。"), [])

    def test_reminder_rules(self):
        self.assertTrue(self.errors(reminders=["一", "二", "三", "四"]))
        self.assertTrue(self.errors(reminders="一句话"))
        self.assertTrue(self.errors(reminders=[1]))
        self.assertTrue(self.errors(reminders=["多" * 81]))
        self.assertTrue(any("空的" in item for item in self.errors(reminders=[])))

    def test_invented_english_words_are_refused(self):
        errors = self.errors(reminders=["他把 elephant 拼错了。"])
        self.assertTrue(any("elephant" in item for item in errors))

    def test_words_from_the_record_and_old_reminders_are_allowed(self):
        self.assertEqual(self.errors(reminders=["他说成 cats。", "主语是 I 的时候，他会写成 is。"]), [])

    def test_no_reminder_needed_when_only_notes(self):
        notes = record(events=[{"stepId": "s1", "said": "不知道", "result": "note", "hintUsed": 0}])
        self.assertEqual(check_draft(dumps({**GOOD, "reminders": []}), CURRENT, [notes])[1], [])

    def test_terms_are_refused(self):
        self.assertTrue(any("术语" in item for item in self.errors(reminders=["他不懂第三人称。"])))

    def test_bad_forbid_keeps_the_old_one(self):
        draft, errors = check_draft(dumps({**GOOD, "forbid": ""}), CURRENT, [record()])
        self.assertEqual(errors, [])
        self.assertEqual(draft["forbid"], CURRENT["forbid"])

    def test_forbid_about_one_item_keeps_the_old_one(self):
        draft, errors = check_draft(dumps({**GOOD, "forbid": "不要说 like 应该改成 likes。"}), CURRENT, [record()])
        self.assertEqual(errors, [])
        self.assertEqual(draft["forbid"], CURRENT["forbid"])

    def test_good_example_is_kept(self):
        example = {"recognizedText": "They ___ my friends. (am / is / are)", "steps": [{"ask": "主语是谁？", "expect": "They"}, {"ask": "空里填哪个？", "expect": "are"}]}
        draft, _ = check_draft(dumps({**GOOD, "examples": [example]}), CURRENT, [record()])
        self.assertEqual(draft["examples"], [example])

    def test_bad_examples_are_dropped_and_old_ones_stay(self):
        bad = [
            {"recognizedText": "They ___ my friends. (am / is / are)", "steps": [{"ask": "空里填哪个？", "expect": "is"}]},
            {"recognizedText": "They ___ my friends. (am / is / are)", "steps": [{"ask": "是 are 吗？", "expect": "They"}, {"ask": "填哪个？", "expect": "are"}]},
            {"recognizedText": "随便一句话", "steps": [{"ask": "问", "expect": "答"}]},
            {"recognizedText": "They ___ my friends. (am / is / are)", "steps": []},
            "不是样例",
        ]
        draft, errors = check_draft(dumps({**GOOD, "examples": bad}), CURRENT, [record()])
        self.assertEqual(errors, [])
        self.assertEqual(draft["examples"], CURRENT["examples"])


class PlannerTests(unittest.TestCase):
    def test_all_right_does_not_call_the_model(self):
        def chat(_):
            raise AssertionError("不该调用")

        planner = make_planner(chat)
        self.assertIsNone(planner(CURRENT, [record(events=[{"stepId": "s1", "said": "like", "result": "hit", "hintUsed": 0}])]))

    def test_second_try_gets_the_reasons(self):
        calls = []

        def chat(messages):
            calls.append(messages)
            return "坏掉了" if len(calls) == 1 else dumps(GOOD)

        self.assertEqual(make_planner(chat)(CURRENT, [record()])["voice"], GOOD["voice"])
        self.assertEqual(len(calls), 2)
        self.assertIn("检查没通过", calls[1][-1]["content"])
        self.assertEqual(calls[1][-2], {"role": "assistant", "content": "坏掉了"})

    def test_gives_up_after_two_tries(self):
        calls = []

        def chat(messages):
            calls.append(messages)
            return "坏掉了"

        with self.assertRaises(PlanFailed):
            make_planner(chat)(CURRENT, [record()])
        self.assertEqual(len(calls), 2)

    def test_only_recent_sessions_are_read(self):
        seen = []

        def chat(messages):
            seen.append(messages[1]["content"])
            return dumps(GOOD)

        many = [record(session_id=f"s{index}") for index in range(25)]
        make_planner(chat)(CURRENT, many)
        self.assertEqual(seen[0].count('"题目"'), 20)


class FailureThroughCloudTests(unittest.TestCase):
    def setUp(self):
        self.store = CloudStore(":memory:")
        self.chat = lambda messages: dumps(GOOD)
        self.cloud = CloudApp(self.store, make_planner(lambda messages: self.chat(messages)))

    def tearDown(self):
        self.store.connection.close()

    def test_new_plan_keeps_program_fields(self):
        plan = self.cloud.sync("child_01", 2, [record()])["plan"]
        self.assertEqual((plan["planVersion"], plan["childId"], plan["basedOnSessionId"]), (3, "child_01", "s1"))
        self.assertEqual(plan["reminders"], GOOD["reminders"])

    def test_model_down_keeps_records_and_retries(self):
        def down(_):
            raise LlmError("连不上大模型")

        self.chat = down
        reply = self.cloud.sync("child_01", 2, [record()])
        self.assertEqual((reply["accepted"], reply["plan"], reply["planFailed"]), (["s1"], None, True))
        self.assertEqual(len(self.store.sessions_after("child_01", None)), 1)
        self.chat = lambda messages: dumps(GOOD)
        reply = self.cloud.sync("child_01", 2, [])
        self.assertEqual(reply["plan"]["planVersion"], 3)
        self.assertNotIn("planFailed", reply)

    def test_all_right_is_not_a_failure(self):
        reply = self.cloud.sync("child_01", 2, [record(events=[{"stepId": "s1", "said": "like", "result": "hit", "hintUsed": 0}])])
        self.assertIsNone(reply["plan"])
        self.assertNotIn("planFailed", reply)

    def test_phone_reports_the_failure_and_keeps_its_plan(self):
        def down(_):
            raise LlmError("连不上大模型")

        self.chat = down
        with Store(":memory:") as store:
            store.save_plan({"childId": "child_01", "planVersion": 1, "voice": "讲法", "reminders": [], "examples": [], "forbid": "不说答案"})
            store.save_plan({"childId": "child_01", "planVersion": 2, "voice": "讲法", "reminders": [], "examples": [], "forbid": "不说答案"})
            store.save_session(record())
            result = sync_once(store, "child_01", post=lambda url, body: self.cloud.sync(body["childId"], body["planVersion"], body["sessions"]))
            self.assertTrue(result["planFailed"])
            self.assertEqual(result["planVersion"], 2)
            self.assertEqual(store.unsynced(), [])


class FakeLlm(BaseHTTPRequestHandler):
    """假的大模型接口，形状和通用聊天接口一样。"""

    reply = {"choices": [{"message": {"content": dumps(GOOD)}}]}
    status = 200
    seen: list = []

    def log_message(self, *_):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        FakeLlm.seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": json.loads(self.rfile.read(length))})
        data = json.dumps(FakeLlm.reply, ensure_ascii=False).encode("utf-8")
        self.send_response(FakeLlm.status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class ChatClientTests(unittest.TestCase):
    def setUp(self):
        FakeLlm.seen = []
        FakeLlm.status = 200
        FakeLlm.reply = {"choices": [{"message": {"content": dumps(GOOD)}}]}
        self.llm = ThreadingHTTPServer(("127.0.0.1", 0), FakeLlm)
        self.base = f"http://127.0.0.1:{self.llm.server_address[1]}/v1"
        self.thread = threading.Thread(target=self.llm.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.llm.shutdown()
        self.llm.server_close()
        self.thread.join(timeout=5)

    def test_request_shape(self):
        reply = make_chat(self.base + "/", "令牌".encode("utf-8").hex(), "some-model")([{"role": "user", "content": "你好"}])
        self.assertEqual(json.loads(reply)["voice"], GOOD["voice"])
        seen = FakeLlm.seen[0]
        self.assertEqual(seen["path"], "/v1/chat/completions")
        self.assertTrue(seen["auth"].startswith("Bearer "))
        self.assertEqual(seen["body"]["model"], "some-model")
        self.assertFalse(seen["body"]["stream"])

    def test_thinking_can_be_turned_off(self):
        make_chat(self.base, "key", "m")([])
        make_chat(self.base, "key", "m", think=False)([])
        self.assertNotIn("thinking", FakeLlm.seen[0]["body"])
        self.assertEqual(FakeLlm.seen[1]["body"]["thinking"], {"type": "disabled"})

    def test_errors_do_not_show_the_key(self):
        chat = make_chat(self.base, "SECRET-KEY", "some-model")
        cases = [(401, {"error": "bad key SECRET-KEY"}), (200, {"code": 10007, "message": "SECRET-KEY"}), (200, {"choices": []}), (200, {"choices": [{"message": {"content": " "}}]})]
        for status, reply in cases:
            with self.subTest(status=status, reply=reply):
                FakeLlm.status, FakeLlm.reply = status, reply
                with self.assertRaises(LlmError) as caught:
                    chat([])
                self.assertNotIn("SECRET-KEY", str(caught.exception))

    def test_cut_off_reply_says_why(self):
        FakeLlm.reply = {"choices": [{"message": {"content": "", "reasoning_content": "想了很久"}, "finish_reason": "length"}]}
        with self.assertRaises(LlmError) as caught:
            make_chat(self.base, "key", "m")([])
        self.assertIn("length", str(caught.exception))

    def test_unreachable_model_is_an_llm_error(self):
        with self.assertRaises(LlmError):
            make_chat("http://127.0.0.1:1/v1", "key", "m", timeout=2)([])

    def test_settings_come_from_the_environment(self):
        with mock.patch.dict(os.environ, {"AI_BASE_URL": self.base, "AI_API_KEY": "key", "AI_MODEL": "m"}):
            self.assertIn("voice", chat_from_env()([]))
        self.assertEqual(FakeLlm.seen[0]["body"]["model"], "m")
        with mock.patch.dict(os.environ, {"AI_API_KEY": " "}):
            self.assertIsNone(chat_from_env())

    def test_address_and_model_have_defaults(self):
        with mock.patch.dict(os.environ, {"AI_BASE_URL": "", "AI_API_KEY": "key", "AI_MODEL": ""}), mock.patch("xiangke.planner.make_chat") as maker:
            chat_from_env()
        maker.assert_called_once_with("https://maas-api.cn-huabei-1.xf-yun.com/v2", "key", "spark-x2.6")

    def test_pick_planner(self):
        self.assertIs(pick_planner(True), test_planner)
        with mock.patch.dict(os.environ, {"AI_API_KEY": ""}):
            self.assertIsNone(pick_planner()(CURRENT, [record()]))

    def test_whole_chain_over_http(self):
        """手机 → 云端 → 假大模型，全部走真的 HTTP。"""
        with mock.patch.dict(os.environ, {"AI_BASE_URL": self.base, "AI_API_KEY": "key", "AI_MODEL": "m"}):
            planner = pick_planner()
        with CloudStore(":memory:") as cloud_store, Store(":memory:") as phone:
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(CloudApp(cloud_store, planner)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for version in (1, 2):
                    phone.save_plan({"childId": "child_01", "planVersion": version, "voice": "讲法", "reminders": [], "examples": [], "forbid": "不说答案"})
                phone.save_session(record())
                result = sync_once(phone, "child_01", f"http://127.0.0.1:{server.server_address[1]}")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
            self.assertTrue(result["planUpdated"])
            self.assertEqual(phone.latest_plan("child_01")["reminders"], GOOD["reminders"])
            self.assertIn("She like cats", FakeLlm.seen[0]["body"]["messages"][1]["content"])


if __name__ == "__main__":
    unittest.main()
