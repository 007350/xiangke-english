import json
import shutil
import tempfile
import unittest
from pathlib import Path

from xiangke.cloud import CloudApp, CloudStore
from xiangke.feedback import make_reporter
from xiangke.planner import LlmError
from xiangke.questions import collect_questions
from xiangke.replay import Recorder, Replayer, question_key, round_key
from xiangke.store import Store
from xiangke.sync import sync_once

PLAN = {"voice": "讲法", "reminders": ["他会忘记变。"], "examples": [], "forbid": "不说答案", "portrait": {"judgement": "判断", "focus": ["要练的"]}}
WRITTEN = {"parentStuck": "家长的话。", "teacherStuck": "老师的话。", "teacherAdvice": "老师的建议。"}
ITEM = {"text": "He ___ apples. (like / likes)", "answer": "likes", "rule": "规则", "source": "cloud"}
DRAFT = {"status": "answered", "answer": "因为主语 She 是一个人，动词 like 要加 s 变成 likes。"}


def session(session_id="s1", text="She ___ apples. (like / likes)", wrong=True):
    return {
        "sessionId": session_id,
        "childId": "child_01",
        "recognizedText": text,
        "answerSource": "program",
        "checkPassed": True,
        "steps": [{"stepId": "s1", "ask": "选哪个？", "expect": "likes"}],
        "events": [{"stepId": "s1", "said": "like", "result": "miss" if wrong else "hit", "hintUsed": 0}],
    }


def question_session(session_id="s1", question="为什么要加 s？"):
    row = session(session_id)
    row["events"] = [{"stepId": "s1", "said": question, "result": "note", "hintUsed": 0, "noteKind": "question"}]
    return row


class ReplayCase(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)
        self.path = self.folder / "录制" / "spark.json"

    def record(self):
        recorder = Recorder(self.path)
        planner = recorder.wrap_planner(lambda current, new: PLAN)
        reporter = recorder.wrap_reporter(lambda stats, sessions, facts: WRITTEN)
        maker = recorder.wrap_maker(lambda portrait, sessions, known: [ITEM])
        with CloudStore(":memory:") as store:
            CloudApp(store, planner, maker, reporter=reporter).sync("child_01", 2, [session()])
        return recorder

    def record_question(self):
        recorder = Recorder(self.path)
        planner = recorder.wrap_planner(lambda current, new: PLAN)
        answerer = recorder.wrap_answerer(lambda item: DRAFT)
        with CloudStore(":memory:") as store:
            CloudApp(store, planner, question_answerer=answerer).sync("child_01", 2, [question_session()])
        return recorder


class QuestionKeyTests(unittest.TestCase):
    def test_spacing_and_case_do_not_matter(self):
        one = collect_questions(question_session())[0]
        two = {**one, "text": "she  ___ APPLES. (like / likes)", "question": " 为什么要加 S？ "}
        self.assertEqual(question_key(one), question_key(two))

    def test_different_question_does_not_match(self):
        one = collect_questions(question_session())[0]
        two = collect_questions(question_session(question="为什么不能选 like？"))[0]
        self.assertNotEqual(question_key(one), question_key(two))

    def test_ids_do_not_change_the_key(self):
        one = collect_questions(question_session())[0]
        two = collect_questions(question_session(session_id="别的辅导"))[0]
        self.assertNotEqual(one["questionId"], two["questionId"])
        self.assertEqual(question_key(one), question_key(two))


class RoundKeyTests(unittest.TestCase):
    def test_same_items_in_any_order_match(self):
        one, two = session("a"), session("b", "They ___ here. (am / is / are)", wrong=False)
        self.assertEqual(round_key([one, two]), round_key([two, one]))

    def test_right_and_wrong_do_not_match(self):
        self.assertNotEqual(round_key([session()]), round_key([session(wrong=False)]))

    def test_spacing_and_case_do_not_matter(self):
        self.assertEqual(round_key([session()]), round_key([session(text="she  ___ APPLES. (like / likes)")]))


class RecordTests(ReplayCase):
    def test_everything_is_written_down(self):
        self.record()
        rounds = json.loads(self.path.read_text(encoding="utf-8"))["rounds"]
        self.assertEqual(len(rounds), 1)
        saved = next(iter(rounds.values()))
        self.assertEqual((saved["plan"], saved["written"], saved["drills"]), (PLAN, WRITTEN, [ITEM]))

    def test_nothing_identifying_is_saved(self):
        self.record()
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("child_01", text)
        self.assertNotIn("s1", text.replace("\"s1\"", "")[:0])

    def test_failed_calls_are_not_recorded(self):
        recorder = Recorder(self.path)

        def broken(current, new):
            raise LlmError("连不上大模型")

        with self.assertRaises(LlmError):
            recorder.wrap_planner(broken)({}, [session()])
        self.assertFalse(self.path.exists())

    def test_answers_are_written_down_without_ids(self):
        self.record_question()
        text = self.path.read_text(encoding="utf-8")
        answers = json.loads(text)["answers"]
        self.assertEqual(list(answers.values()), [DRAFT])
        self.assertNotIn("child_01", text)
        self.assertNotIn(collect_questions(question_session())[0]["questionId"], text)

    def test_failed_answer_is_not_recorded(self):
        recorder = Recorder(self.path)

        def broken(item):
            raise LlmError("连不上大模型")

        with self.assertRaises(LlmError):
            recorder.wrap_answerer(broken)(collect_questions(question_session())[0])
        self.assertFalse(self.path.exists())


class ReplayTests(ReplayCase):
    def cloud(self, store):
        replayer = Replayer(self.path)
        return CloudApp(store, replayer.planner, replayer.maker, reporter=replayer.reporter, recorded=True)

    def test_same_round_gets_the_recorded_content_and_is_marked(self):
        self.record()
        with CloudStore(":memory:") as store:
            cloud = self.cloud(store)
            reply = cloud.sync("child_01", 2, [session()])
            self.assertEqual(reply["plan"]["reminders"], PLAN["reminders"])
            self.assertTrue(reply["recorded"])
            self.assertIn(ITEM["text"], [item["text"] for item in reply["drills"]])
            report = cloud.report("child_01")
            self.assertTrue(report["recorded"])
            self.assertEqual(report["parent"]["stuck"], WRITTEN["parentStuck"])
            self.assertEqual(report["judgement"], "判断")
            self.assertIn("做了 1 道题", report["parent"]["facts"])

    def test_different_round_gets_nothing_recorded(self):
        self.record()
        with CloudStore(":memory:") as store:
            cloud = self.cloud(store)
            reply = cloud.sync("child_01", 2, [session(wrong=False)])
            self.assertIsNone(reply["plan"])
            self.assertNotIn("planFailed", reply)
            report = cloud.report("child_01")
            self.assertFalse(report["parent"]["byModel"])
            self.assertIn("做了 1 道题", report["parent"]["facts"])

    def test_off_script_wrong_answer_still_gets_the_original_item_back(self):
        self.record()
        with CloudStore(":memory:") as store:
            reply = self.cloud(store).sync("child_01", 2, [session(text="They ___ here. (am / is / are)")])
            self.assertIsNone(reply["plan"])
            self.assertEqual([item["source"] for item in reply["drills"]], ["redo"])

    def test_known_items_are_not_sent_again(self):
        self.record()
        replayer = Replayer(self.path)
        replayer.planner({}, [session()])
        self.assertEqual(replayer.maker({}, [], {"he ___ apples. (like / likes)"}), [])

    def test_missing_written_falls_back_to_program_words(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"rounds": {round_key([session()]): {"plan": PLAN}}}, ensure_ascii=False), encoding="utf-8")
        with CloudStore(":memory:") as store:
            cloud = self.cloud(store)
            self.assertEqual(cloud.sync("child_01", 2, [session()])["plan"]["reminders"], PLAN["reminders"])
            self.assertFalse(cloud.report("child_01")["parent"]["byModel"])

    def test_phone_is_told(self):
        self.record()
        with CloudStore(":memory:") as store, Store(":memory:") as phone:
            cloud = self.cloud(store)
            for version in (1, 2):
                phone.save_plan({"childId": "child_01", "planVersion": version, "voice": "讲法", "reminders": [], "examples": [], "forbid": "不说答案"})
            phone.save_session(session())
            result = sync_once(phone, "child_01", post=lambda url, body: cloud.sync(body["childId"], body["planVersion"], body["sessions"], body["drillSeq"]))
            self.assertTrue(result["recorded"])
            self.assertTrue(result["planUpdated"])

    def test_normal_cloud_is_not_marked(self):
        with CloudStore(":memory:") as store:
            cloud = CloudApp(store, lambda current, new: None)
            self.assertNotIn("recorded", cloud.sync("child_01", 2, []))
            self.assertFalse(cloud.report("child_01")["recorded"])

    def test_recorded_answer_comes_back_with_new_ids(self):
        self.record_question()
        with CloudStore(":memory:") as store:
            replayer = Replayer(self.path)
            cloud = CloudApp(store, replayer.planner, question_answerer=replayer.answerer, recorded=True)
            reply = cloud.sync("child_01", 2, [question_session(session_id="新辅导")])
            item = reply["questions"][0]
            self.assertEqual((item["status"], item["answer"]), (DRAFT["status"], DRAFT["answer"]))
            self.assertNotEqual(item["questionId"], collect_questions(question_session())[0]["questionId"])

    def test_unrecorded_question_is_left_for_the_teacher(self):
        self.record_question()
        with CloudStore(":memory:") as store:
            replayer = Replayer(self.path)
            cloud = CloudApp(store, replayer.planner, question_answerer=replayer.answerer, recorded=True)
            reply = cloud.sync("child_01", 2, [question_session(question="这道题是不是印错了？")])
            item = reply["questions"][0]
            self.assertEqual(item["status"], "needs_teacher")
            self.assertTrue(item["answer"].strip())
            self.assertNotIn("questionFailed", reply)

    def test_old_recording_keeps_answering_off(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"rounds": {}}, ensure_ascii=False), encoding="utf-8")
        self.assertIsNone(Replayer(self.path).answers)

    def test_phone_gets_the_answer_without_network(self):
        self.record_question()
        with CloudStore(":memory:") as store, Store(":memory:") as phone:
            replayer = Replayer(self.path)
            cloud = CloudApp(store, replayer.planner, question_answerer=replayer.answerer, recorded=True)
            for version in (1, 2):
                phone.save_plan({"childId": "child_01", "planVersion": version, "voice": "讲法", "reminders": [], "examples": [], "forbid": "不说答案"})
            phone.save_session(question_session())
            result = sync_once(phone, "child_01", post=lambda url, body: cloud.sync(body["childId"], body["planVersion"], body["sessions"], body["drillSeq"], body["questionSeq"]))
            self.assertEqual(result["questionsUpdated"], 1)
            saved = phone.questions("child_01")
            self.assertEqual((saved[0]["status"], saved[0]["answer"]), (DRAFT["status"], DRAFT["answer"]))


if __name__ == "__main__":
    unittest.main()
