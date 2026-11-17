import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from xiangke.cloud import CloudApp, CloudStore, make_handler, pick_reporter
from xiangke.feedback import build_facts, build_report, check_written, make_reporter
from xiangke.planner import LlmError
from xiangke.portrait import build_stats

GOOD = {
    "parentStuck": "这几次看到，孩子遇到说一个人的句子，后面的词常忘了变。",
    "teacherStuck": "这几次看到 She 后面的 like 没有变。",
    "teacherAdvice": "先让他做专属题，还错再讲主语和动词。",
}
KNOWN = {"she", "like", "likes"}


def session(session_id="s1", text="改错（只改一个词）：She like cats.", results=("miss", "hit"), **extra):
    return {
        "sessionId": session_id,
        "childId": "child_01",
        "recognizedText": text,
        "answerSource": "program",
        "checkPassed": True,
        "steps": [{"stepId": "s1", "ask": "改成什么？", "expect": "likes"}],
        "events": [{"stepId": "s1", "said": "like" if result != "hit" else "likes", "result": result, "hintUsed": 0} for result in results],
        **extra,
    }


def drill(drill_id="d1", answer="went"):
    return {"drillId": drill_id, "text": "I ___ home. (go / went)", "answer": answer, "rule": "规则", "source": "cloud", "seq": 1, "childId": "child_01"}


class FactsTests(unittest.TestCase):
    def test_counts_mistakes_and_finished_drills(self):
        sessions = [session("a"), session("b", results=("hit",)), session("c", results=("hit",), drillId="d1"), session("d", results=("miss",), drillId="d2")]
        facts = build_facts(sessions, [drill("d1"), drill("d2"), drill("d3")])
        self.assertEqual((facts["judged"], facts["withMistakes"]), (4, 2))
        self.assertEqual(facts["drills"], {"given": 3, "done": 1})
        self.assertEqual(facts["needsTeacher"], ["专属题做的时候还有错"])

    def test_child_mistakes_and_phone_problems_are_kept_apart(self):
        sessions = [
            session("a", checkPassed=False, results=()),
            session("b", answerSource="model", text="We ___ here. (was / were)", results=("note",)),
        ]
        facts = build_facts(sessions, [])
        self.assertEqual(facts["judged"], 0)
        self.assertEqual(facts["withMistakes"], 0)
        self.assertEqual(facts["phoneProblems"], ["改错（只改一个词）：She like cats."])
        self.assertEqual(facts["notSure"], ["We ___ here. (was / were)"])
        self.assertEqual(facts["needsTeacher"], [])

    def test_flagged_item_goes_to_the_teacher_with_its_answer(self):
        facts = build_facts([session("a", flagged=True, drillId="d1", results=("miss",))], [drill("d1")])
        self.assertEqual(facts["flagged"], [{"text": "改错（只改一个词）：She like cats.", "answer": "went"}])
        self.assertEqual(facts["judged"], 0)
        self.assertEqual(facts["drills"]["done"], 1)
        self.assertIn("孩子觉得有专属题不对，请您核对", facts["needsTeacher"])

    def test_stopped_item_goes_to_the_teacher(self):
        facts = build_facts([session("a", results=("miss", "miss", "assisted"))], [])
        self.assertEqual(facts["stopped"], ["改错（只改一个词）：She like cats."])
        self.assertIn("有一问错到第三次停下了", facts["needsTeacher"])


class ReportTests(unittest.TestCase):
    def report(self, sessions, drills=(), written=None):
        return build_report(build_stats(sessions), build_facts(sessions, list(drills)), written)

    def test_program_numbers_stay_when_the_model_writes(self):
        report = self.report([session()], [drill()], GOOD)
        self.assertIn("做了 1 道题，其中 1 道中途做错过。手机上还有 1 道专属题", report["parent"]["facts"])
        self.assertEqual(report["parent"]["stuck"], GOOD["parentStuck"])
        self.assertTrue(report["parent"]["byModel"])
        self.assertEqual(report["teacher"]["advice"], GOOD["teacherAdvice"])

    def test_program_writes_everything_when_the_model_did_not(self):
        report = self.report([session()], [drill()])
        self.assertFalse(report["parent"]["byModel"])
        self.assertIn("动词变不变", report["parent"]["stuck"])
        self.assertIn("专属题做完", report["parent"]["tonight"])
        self.assertIn("做了 1 次，错 1 次", report["teacher"]["stuck"])

    def test_tonight_does_not_mention_drills_when_none_are_left(self):
        self.assertNotIn("专属题", self.report([session(results=("hit",))])["parent"]["tonight"])

    def test_no_records(self):
        report = self.report([])
        self.assertEqual(report["parent"]["facts"], "还没有做题记录。")
        self.assertEqual(report["teacher"]["topics"], [])

    def test_questions_reach_the_teacher(self):
        record = session()
        record["events"].append({"stepId": "s1", "said": "什么时候不加s", "result": "note"})
        self.assertEqual(self.report([record])["teacher"]["questions"], ["什么时候不加s"])


class CheckWrittenTests(unittest.TestCase):
    def errors(self, **change):
        return check_written(json.dumps({**GOOD, **change}, ensure_ascii=False), KNOWN)[1]

    def test_good_text_passes(self):
        written, errors = check_written(json.dumps(GOOD, ensure_ascii=False), KNOWN)
        self.assertEqual(errors, [])
        self.assertEqual(set(written), set(GOOD))

    def test_broken_output(self):
        self.assertTrue(check_written("坏掉了", KNOWN)[1])

    def test_parent_text_has_no_english_and_no_reading_aloud(self):
        self.assertTrue(any("英文" in item for item in self.errors(parentStuck="他的 like 没有变。")))
        for text in ("让孩子念给您听。", "跟着录音学。", "这个词少了小尾巴。", "他分不清单数。"):
            with self.subTest(text=text):
                self.assertTrue(any("手机做不到" in item for item in self.errors(parentStuck=text)))

    def test_saying_not_to_is_allowed(self):
        self.assertEqual(self.errors(parentStuck="这几次看到动词容易选错，不用朗读，也不用批评他。"), [])

    def test_teacher_may_use_grammar_words(self):
        self.assertEqual(self.errors(teacherStuck="she 是单数，like 要变成 likes。"), [])

    def test_length_and_empty(self):
        self.assertTrue(self.errors(parentStuck="多" * 61))
        self.assertTrue(self.errors(teacherAdvice="多" * 121))
        self.assertTrue(self.errors(teacherStuck=" "))
        self.assertTrue(self.errors(parentStuck=None))

    def test_good_sentences_survive_a_bad_one(self):
        written, errors = check_written(json.dumps({**GOOD, "parentStuck": "他的 like 没有变。"}, ensure_ascii=False), KNOWN)
        self.assertEqual(set(written), {"teacherStuck", "teacherAdvice"})
        self.assertTrue(errors)

    def test_counts_are_left_to_the_program(self):
        for text in ("他错了 3 道。", "他错了三次。", "他做了两道题。"):
            with self.subTest(text=text):
                self.assertTrue(any("次数" in item for item in self.errors(teacherStuck=text)))

    def test_saying_once_more_is_not_a_count(self):
        self.assertEqual(self.errors(teacherAdvice="可以再讲一遍 she 后面的动词。"), [])

    def test_one_person_is_not_a_count(self):
        self.assertEqual(self.errors(parentStuck="这几次看到，说一个人的句子，后面的词常忘了变。"), [])

    def test_overclaiming_and_harsh_words(self):
        self.assertTrue(any("太满" in item for item in self.errors(teacherStuck="动词变化完全靠猜。")))
        self.assertTrue(any("语气" in item for item in self.errors(parentStuck="孩子学习不认真。")))
        self.assertTrue(any("手机做不到" in item for item in self.errors(parentStuck="他不懂第三人称。")))

    def test_invented_words(self):
        self.assertTrue(any("elephant" in item for item in self.errors(teacherStuck="他把 elephant 写错了。")))


class ReporterTests(unittest.TestCase):
    def run_reporter(self, chat, sessions=None):
        sessions = sessions or [session()]
        stats = build_stats(sessions)
        return make_reporter(chat)(stats, sessions, build_facts(sessions, []))

    def test_retries_with_reasons(self):
        calls = []

        def chat(messages):
            calls.append(messages)
            return "坏掉了" if len(calls) == 1 else json.dumps(GOOD, ensure_ascii=False)

        self.assertEqual(self.run_reporter(chat)["teacherAdvice"], GOOD["teacherAdvice"])
        self.assertIn("检查没通过", calls[1][-1]["content"])

    def test_gives_up_with_an_error(self):
        with self.assertRaises(LlmError):
            self.run_reporter(lambda messages: "坏掉了")

    def test_nothing_identifying_goes_out_and_phone_mistakes_are_marked(self):
        seen = []

        def chat(messages):
            seen.append(json.dumps(messages, ensure_ascii=False))
            return json.dumps(GOOD, ensure_ascii=False)

        self.run_reporter(chat, [session("s_secret"), session("b", checkPassed=False)])
        self.assertNotIn("s_secret", seen[0])
        self.assertNotIn("child_01", seen[0])
        self.assertIn("手机把题拆错了", seen[0])

    def test_model_sees_each_question_and_answer_but_no_judgement(self):
        seen = []

        def chat(messages):
            seen.append(messages[1]["content"])
            return json.dumps(GOOD, ensure_ascii=False)

        self.run_reporter(chat)
        self.assertIn("改成什么？", seen[0])
        self.assertIn("答错", seen[0])
        self.assertNotIn("判断", seen[0])

    def test_keeps_what_passed_and_fills_in_the_rest(self):
        replies = [
            json.dumps({**GOOD, "parentStuck": "跟着录音学。"}, ensure_ascii=False),
            json.dumps({"parentStuck": GOOD["parentStuck"], "teacherStuck": "不合格的 elephant。", "teacherAdvice": "不合格的 elephant。"}, ensure_ascii=False),
        ]
        written = self.run_reporter(lambda messages: replies.pop(0))
        self.assertEqual(written, GOOD)

    def test_partial_result_is_used_after_the_last_try(self):
        reply = json.dumps({**GOOD, "parentStuck": "跟着录音学。"}, ensure_ascii=False)
        written = self.run_reporter(lambda messages: reply)
        self.assertEqual(set(written), {"teacherStuck", "teacherAdvice"})
        sessions = [session()]
        report = build_report(build_stats(sessions), build_facts(sessions, []), written)
        self.assertFalse(report["parent"]["byModel"])
        self.assertTrue(report["teacher"]["byModel"])
        self.assertIn("动词变不变", report["parent"]["stuck"])

    def test_teacher_hint_follows_the_facts(self):
        seen = []

        def chat(messages):
            seen.append(messages[0]["content"])
            return json.dumps(GOOD, ensure_ascii=False)

        self.run_reporter(chat, [session("a", results=("miss", "miss", "assisted"))])
        self.assertIn("有一问错到第三次停下了", seen[0])


class CloudReportTests(unittest.TestCase):
    PLAN = {"voice": "讲法", "reminders": ["他会忘记变。"], "examples": [], "forbid": "不说答案"}

    def setUp(self):
        self.store = CloudStore(":memory:")

    def tearDown(self):
        self.store.connection.close()

    def test_sync_writes_feedback_and_numbers_stay_fresh(self):
        cloud = CloudApp(self.store, lambda current, new: self.PLAN, reporter=make_reporter(lambda messages: json.dumps(GOOD, ensure_ascii=False)))
        cloud.sync("child_01", 2, [session()])
        report = cloud.report("child_01")
        self.assertEqual(report["parent"]["stuck"], GOOD["parentStuck"])
        self.assertIn("做了 1 道题", report["parent"]["facts"])
        cloud.sync("child_01", 3, [session("b", results=("hit",))])
        self.assertIn("做了 2 道题", cloud.report("child_01")["parent"]["facts"])
        self.assertEqual(cloud.report("child_01")["parent"]["stuck"], GOOD["parentStuck"])

    def test_feedback_failure_falls_back_and_keeps_the_plan(self):
        def chat(messages):
            raise LlmError("连不上大模型")

        cloud = CloudApp(self.store, lambda current, new: self.PLAN, reporter=make_reporter(chat))
        reply = cloud.sync("child_01", 2, [session()])
        self.assertEqual(reply["plan"]["planVersion"], 3)
        self.assertNotIn("planFailed", reply)
        self.assertFalse(cloud.report("child_01")["parent"]["byModel"])

    def test_all_right_does_not_call_the_reporter(self):
        calls = []
        cloud = CloudApp(self.store, lambda current, new: None, reporter=lambda *args: calls.append(args))
        cloud.sync("child_01", 2, [session(results=("hit",))])
        self.assertEqual(calls, [])
        self.assertEqual(cloud.report("child_01")["teacher"]["judged"], 1)

    def test_children_are_listed_and_kept_apart(self):
        cloud = CloudApp(self.store, lambda current, new: None)
        cloud.sync("child_01", 2, [session()])
        cloud.sync("child_02", 2, [{**session("b", results=("hit",)), "childId": "child_02"}])
        self.assertEqual(cloud.children(), ["child_01", "child_02"])
        self.assertEqual(cloud.report("child_02")["teacher"]["withMistakes"], 0)
        self.assertEqual(cloud.report("child_01")["teacher"]["withMistakes"], 1)

    def test_page_and_routes_over_http(self):
        cloud = CloudApp(self.store, lambda current, new: None)
        cloud.sync("child_01", 2, [session()])
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cloud))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urllib.request.urlopen(url + "/") as response:
                self.assertIn("学情反馈", response.read().decode("utf-8"))
            with urllib.request.urlopen(url + "/api/children") as response:
                self.assertEqual(json.load(response), {"children": ["child_01"]})
            with urllib.request.urlopen(url + "/api/report/child_01") as response:
                self.assertEqual(json.load(response)["teacher"]["judged"], 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_pick_reporter_for_test_mode(self):
        self.assertIsNone(pick_reporter(True))


if __name__ == "__main__":
    unittest.main()
