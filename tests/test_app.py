import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from xiangke.app import BadRequest, PhoneApp, make_handler
from xiangke.store import Store

LESSON = {
    "steps": [
        {"ask": "这句话的主语是哪个词？", "expect": "They", "explain": "主语是 They。"},
        {"ask": "那空里填 am、is 还是 are？", "expect": "are", "explain": "They 是两个或更多，所以填 are。"},
    ]
}
TEXT = "They ___ my friends. (am / is / are)"
LABELS = {"不知道": "不会", "为什么？": "提问", "我饿了": "其他"}


def fake_write(system, user, grammar, limit=512):
    if "孩子说：" in user:
        return json.dumps({"label": LABELS.get(user.split("孩子说：")[1], "答题")}, ensure_ascii=False)
    if "题目（recognizedText" in user and "bo_k" in user:
        return json.dumps({"steps": [{"ask": "缺什么字母？", "expect": "o", "explain": "写 o。"}]}, ensure_ascii=False)
    if "随便" in user:
        return "坏掉了"
    return json.dumps(LESSON, ensure_ascii=False)


class AppCase(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.app = PhoneApp(fake_write, self.store, recognizer=lambda image: "One line\n\n  Two line ")

    def tearDown(self):
        self.store.connection.close()


class HomeTests(AppCase):
    def test_home_has_plan_pack_and_empty_history(self):
        home = self.app.home()
        self.assertEqual(home["planVersion"], 2)
        self.assertTrue(home["starterPlan"])
        plan = self.store.latest_plan(self.app.child_id)
        self.assertEqual(plan["reminders"], [])
        self.assertIsNone(plan["basedOnSessionId"])
        self.assertEqual(len(home["pack"]), 4)
        self.assertEqual(home["history"], [])
        self.assertEqual(home["waitingSync"], 0)

    def test_home_keeps_more_than_the_page_fold(self):
        """折起来是页面的事，接口不能替页面把记录截掉。"""
        for index in range(25):
            self.store.save_session(
                {
                    "sessionId": f"s_{index:02d}",
                    "childId": "child_01",
                    "answerSource": "program",
                    "source": "photo",
                    "events": [],
                    "steps": [],
                    "recognizedText": f"第 {index} 题",
                    "startedAt": "2026-11-19T09:00:00+08:00",
                }
            )
        home = self.app.home()
        self.assertEqual(len(home["history"]), 25)
        self.assertEqual(home["history"][0]["question"], "第 24 题")


class RecognizeTests(AppCase):
    def test_lines_are_cleaned(self):
        self.assertEqual(self.app.recognize(b"x")["lines"], ["One line", "Two line"])

    def test_bad_photo_gives_a_message_not_a_crash(self):
        def broken(_):
            raise RuntimeError("不是图片")

        self.app.recognizer = broken
        with self.assertRaises(BadRequest):
            self.app.recognize(b"x")
        self.app.recognizer = lambda image: "   "
        with self.assertRaises(BadRequest):
            self.app.recognize(b"x")
        with self.assertRaises(BadRequest):
            self.app.recognize(b"")


class FlowTests(AppCase):
    def test_english_word_request_accepts_the_meaning_without_counting_a_miss(self):
        self.app.start(TEXT)
        view = self.app.reply("他们")
        self.assertEqual((view["step"], view["messages"]), (1, ["意思对了，试着写出那个英文词。"]))
        self.app.reply("They")
        self.assertEqual(self.app.reply("are")["summary"]["wrong"], 0)

    def test_first_mistake_gives_a_hint_and_answer_reveal_is_marked(self):
        self.app.start(TEXT)
        first = self.app.reply("friends")
        self.assertIn("第一个词", first["messages"][0])
        self.assertNotIn("They", first["messages"][0])
        second = self.app.reply("my")
        self.assertTrue(second["canExplain"])
        self.assertNotIn("They", second["messages"][0])
        revealed = self.app.explain()
        self.assertIn("They", revealed["messages"][0])
        self.assertFalse(revealed["canExplain"])
        solved = self.app.reply("They")
        self.assertEqual(solved["messages"][0], "跟着讲解完成了这一问。")
        self.assertEqual(self.app.reply("are")["summary"]["afterExplanation"], 1)
        self.assertTrue(self.store.unsynced()[0]["events"][3]["answerShown"])

    def test_second_mistake_in_be_question_gives_a_clue_without_the_word(self):
        self.app.start(TEXT)
        self.app.reply("They")
        self.app.reply("is")
        view = self.app.reply("am")
        self.assertTrue(view["canExplain"])
        self.assertIn("两个或更多", view["messages"][0])
        self.assertNotIn("are", view["messages"][0])
        done = self.app.reply("are")
        self.assertEqual(done["messages"], ["根据提示改对了！"])
        self.assertNotIn("afterExplanation", done["summary"])

    def test_spelling_pack_checks_letters_without_asking_the_model(self):
        self.app = PhoneApp(lambda *_: self.fail("课包拼写不需要模型"), self.store)
        text = "看中文写单词：书 b__k"
        view = self.app.start(text, "pack")
        self.assertEqual(view["mode"], "judged")
        self.assertIn("第 2 个字母", view["messages"][0])
        view = self.app.reply("o")
        self.assertEqual(view["step"], 2)
        self.assertIn("第 3 个字母", view["messages"][-1])
        wrong = self.app.reply("x")
        self.assertEqual(wrong["step"], 2)
        self.assertIn("这一格的字母还不对", wrong["messages"][0])
        self.assertIn("只补这一格", wrong["messages"][0])
        self.assertEqual(self.app.reply("o")["step"], 3)
        self.assertEqual(self.app.reply("book")["summary"],
                         {"right": 3, "wrong": 1, "questions": 3, "answerSource": "program"})

    def test_explicit_replies_skip_intent_model_and_ambiguous_help_uses_it(self):
        inputs = []

        def write(system, user, grammar, limit=512):
            if "孩子说：" in user:
                inputs.append(user.split("孩子说：")[1])
                return '{"label":"不会"}'
            return fake_write(system, user, grammar, limit)

        self.app = PhoneApp(write, self.store)
        self.app.start(TEXT)
        reason = self.app.reply("我知道选 are，但不知道为什么")
        self.assertIn("先看主语是谁", reason["messages"][0])
        self.assertEqual(inputs, [])

        self.assertEqual(self.app.reply("我觉得是 They")["confirming"], "They")
        hint = self.app.reply("有他们和朋友，我分不清该看谁")
        self.assertEqual(hint["step"], 1)
        self.assertIsNone(hint["confirming"])
        self.assertIn("第一个词", hint["messages"][0])
        self.assertNotIn("They", hint["messages"][0])
        self.assertNotIn(LESSON["steps"][0]["ask"], hint["messages"][0])
        self.assertEqual(inputs, ["有他们和朋友，我分不清该看谁"])

        self.assertEqual(self.app.reply("我觉得是 They")["confirming"], "They")
        self.assertEqual(self.app.reply("是")["step"], 2)
        self.assertEqual(self.app.reply("能给个提示吗")["step"], 2)
        self.assertEqual(self.app.reply("are")["summary"]["wrong"], 0)
        self.assertEqual(inputs, ["有他们和朋友，我分不清该看谁"])

    def test_question_during_answer_confirmation_is_not_counted_wrong(self):
        self.app.start(TEXT)
        self.app.reply("我觉得是 They")
        reason = self.app.reply("我知道选 are，但不知道为什么")
        self.assertIsNone(reason["confirming"])
        self.assertIn("先看主语是谁", reason["messages"][0])
        self.app.reply("They")
        self.assertEqual(self.app.reply("are")["summary"]["wrong"], 0)

    def test_early_final_answer_does_not_count_against_current_step(self):
        self.app.start(TEXT)
        for said in ("所以这道题是are", "are", "我选 are"):
            view = self.app.reply(said)
            self.assertEqual(view["step"], 1)
            self.assertFalse(view["done"])
            self.assertIn("最后一空", view["messages"][0])
            self.assertEqual(view["messages"][-1], LESSON["steps"][0]["ask"])

        self.assertEqual(self.app.reply("They")["step"], 2)
        self.assertEqual(self.app.reply("are")["summary"]["wrong"], 0)
        self.assertEqual([e["result"] for e in self.store.unsynced()[0]["events"]],
                         ["note", "note", "note", "hit", "hit"])

    def test_early_answer_questions_and_negations_do_not_get_credit(self):
        for said in ("are？", "不是are", "所以这道题不是 are"):
            with self.subTest(said=said):
                self.app.start(TEXT)
                view = self.app.reply(said)
                self.assertNotIn("最后一空", "".join(view["messages"]))
                self.assertEqual(view["step"], 1)

    def test_model_transition_preserves_next_question_and_falls_back(self):
        for raw, expected in (("这一步想对了，接着看下一问。", "这一步想对了，接着看下一问。"),
                              ("应该填 is。", "答对了！"),
                              (TimeoutError(), "答对了！")):
            with self.subTest(raw=raw):
                def transition_write(*args):
                    if isinstance(raw, Exception):
                        raise raw
                    return raw

                self.app = PhoneApp(fake_write, self.store, transition_write=transition_write)
                self.app.start(TEXT)
                view = self.app.reply("They")
                self.assertEqual(view["messages"], [expected, LESSON["steps"][1]["ask"]])
                self.assertEqual(view["step"], 2)
                self.assertEqual(self.app.reply("are")["summary"]["right"], 2)

    def test_whole_item_saves_the_record(self):
        view = self.app.start(TEXT, "pack", offline=True)
        self.assertEqual(view["messages"], [LESSON["steps"][0]["ask"]])
        self.assertEqual((view["mode"], view["done"], view["step"], view["steps"]), ("judged", False, 1, 2))
        self.assertEqual(len(self.store.unsynced()), 0)

        view = self.app.reply("They")
        self.assertEqual(view["messages"][0], "答对了！")
        self.assertEqual(view["step"], 2)
        view = self.app.reply("is")
        self.assertIn("先看主语是谁", view["messages"][0])
        view = self.app.reply("是 are 吧")
        self.assertEqual(view["confirming"], "are")
        view = self.app.reply("是")
        self.assertTrue(view["done"])
        self.assertEqual(view["outcome"], "finished")
        self.assertEqual(view["summary"], {"right": 2, "wrong": 1, "questions": 2, "answerSource": "program"})

        saved = self.store.unsynced()
        self.assertEqual(len(saved), 1)
        record = saved[0]
        self.assertEqual(record["source"], "pack")
        self.assertTrue(record["confirmed"])
        self.assertTrue(record["offline"])
        self.assertEqual(record["planVersion"], 2)
        self.assertEqual([e["result"] for e in record["events"]], ["hit", "miss", "note", "hit"])

    def test_reply_after_done_changes_nothing(self):
        self.app.start(TEXT)
        for said in ("They", "are"):
            view = self.app.reply(said)
        self.assertTrue(view["done"])
        again = self.app.reply("再说一句")
        self.assertEqual(again["messages"], [])
        self.assertEqual(len(self.store.unsynced()), 1)

    def test_leaving_part_way_keeps_what_happened(self):
        self.app.start(TEXT)
        self.app.reply("They")
        home = self.app.leave()
        self.assertEqual(home["waitingSync"], 1)
        self.assertEqual([e["result"] for e in self.store.unsynced()[0]["events"]], ["hit"])
        self.assertFalse(home["history"][0]["finished"])

    def test_leaving_twice_saves_once(self):
        self.app.start(TEXT)
        self.app.leave()
        self.app.leave()
        self.assertEqual(len(self.store.unsynced()), 1)

    def test_starting_a_new_item_saves_the_old_one(self):
        self.app.start(TEXT)
        self.app.reply("They")
        self.app.start("This is an ___. (apple / apples)")
        self.assertEqual(len(self.store.unsynced()), 1)

    def test_model_try_is_marked_and_saved(self):
        view = self.app.start("看中文写单词：书 bo_k")
        self.assertEqual(view["mode"], "try")
        view = self.app.reply("o")
        self.assertTrue(view["done"])
        self.assertEqual(view["summary"]["answerSource"], "model")
        self.assertEqual(self.store.unsynced()[0]["answerSource"], "model")

    def test_item_that_cannot_be_taught_ends_at_once(self):
        view = self.app.start("随便一句话")
        self.assertTrue(view["done"])
        self.assertEqual(view["outcome"], "cannot")
        self.assertEqual(self.store.unsynced()[0]["answerSource"], "none")

    def test_blank_inputs_are_refused(self):
        with self.assertRaises(BadRequest):
            self.app.start("   ")
        with self.assertRaises(BadRequest):
            self.app.reply("hi")
        self.app.start(TEXT)
        with self.assertRaises(BadRequest):
            self.app.reply("   ")
        with self.assertRaises(BadRequest):
            self.app.start(TEXT, "别的来源")


class HttpTests(AppCase):
    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        super().tearDown()

    def post(self, path, body):
        request = urllib.request.Request(self.url + path, json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"})
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def test_page_and_flow_over_http(self):
        with urllib.request.urlopen(self.url + "/") as response:
            self.assertIn("乡课英语", response.read().decode("utf-8"))
        self.assertEqual(self.post("/api/start", {"text": TEXT})["step"], 1)
        self.assertEqual(self.post("/api/reply", {"said": "They"})["messages"][0], "答对了！")

    def test_errors_are_json_with_a_message(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/reply", {"said": "hi"})
        self.assertEqual(caught.exception.code, 400)
        self.assertIn("没有在做题", json.load(caught.exception)["error"])

    def test_unknown_paths_are_refused(self):
        for path in ("/../README.md", "/data/pack.json", "/xiangke/app.py"):
            with self.subTest(path=path):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(self.url + path)
                self.assertEqual(caught.exception.code, 404)


class PackFileTests(unittest.TestCase):
    """老师用 xlsx 读出来的课包，手机首页要读得出来。"""

    def test_home_reads_new_format_pack(self):
        pack = {
            "packId": "pack_demo_01",
            "packVersion": 1,
            "grade": "三年级",
            "unit": "第一单元",
            "words": [],
            "items": [{"itemId": "1", "kind": "choose", "recognizedText": "He ___ tall. (is / are)", "answer": "is"}],
            "rules": ["be_verb"],
            "teacherWords": [],
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pack.json"
            path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
            store = Store(":memory:")
            try:
                app = PhoneApp(fake_write, store, pack_path=path)
                self.assertEqual(app.home()["pack"], ["He ___ tall. (is / are)"])
            finally:
                store.connection.close()

    def test_missing_pack_file_fails_loudly(self):
        store = Store(":memory:")
        try:
            app = PhoneApp(fake_write, store, pack_path="没有这个文件.json")
            with self.assertRaises(FileNotFoundError):
                app.home()
        finally:
            store.connection.close()


class TodayTests(AppCase):
    def test_finished_item_reports_today_and_left(self):
        self.app.start(TEXT, "pack", offline=True)
        self.app.reply("They")
        view = self.app.reply("are")
        self.assertTrue(view["done"])
        self.assertEqual(view["today"], {"done": 1, "left": 0, "waiting": 1})

    def test_unfinished_item_has_no_today(self):
        view = self.app.start(TEXT, "pack", offline=True)
        self.assertNotIn("today", view)


class TransferTests(AppCase):
    """回信关联的两道验收题离线做，跨日换题。"""

    def letter(self, status="answered", *, text="改错（只改一个词）：She like cats.", question_id="q1", seq=1, session_id="original"):
        self.store.save_questions([{
            "questionId": question_id, "childId": "child_01", "seq": seq, "status": status,
            "text": text, "sessionId": session_id, "stepId": "s1", "answer": "这题要看句子的线索。",
        }])

    def test_no_letter_no_transfer(self):
        with self.assertRaises(BadRequest):
            self.app.transfer()

    def test_pending_letter_is_not_enough(self):
        self.letter("pending")
        with self.assertRaises(BadRequest):
            self.app.transfer()

    def test_unsupported_or_unconfirmed_letter_has_no_transfer(self):
        self.letter("needs_teacher")
        self.letter(text="Look!", question_id="q2", seq=2)
        for question_id in ("q1", "q2", "missing"):
            with self.subTest(question_id=question_id), self.assertRaises(BadRequest):
                self.app.transfer(question_id)
        self.assertFalse(any(item["transferReady"] for item in self.app.home()["questions"]))

    def test_teacher_confirmed_letter_still_opens_transfer(self):
        self.letter("teacher_confirmed")
        view = self.app.transfer()
        self.assertEqual(view["question"], "He ___ TV. (watch / watches)")

    def test_first_item_answered_first_try_is_independent(self):
        self.letter()
        view = self.app.transfer()
        self.assertEqual(view["question"], "He ___ TV. (watch / watches)")
        self.assertEqual(view["mode"], "judged")
        self.assertEqual(view["steps"], 1)
        view = self.app.reply("watches")
        self.assertTrue(view["done"])
        record = self.store.unsynced()[0]
        self.assertEqual(record["source"], "transfer")
        self.assertTrue(record["offline"])
        self.assertEqual(record["transferTier"], "independent")
        self.assertFalse(record["transferRetest"])
        self.assertEqual(record["transferBasis"]["questionId"], "q1")
        self.assertFalse(record["transferBasis"]["contrast"])

    def test_selected_letter_and_its_wrong_choice_set_the_focus_and_contrast(self):
        self.letter(text=TEXT)
        self.letter(question_id="q2", seq=2)
        self.store.save_session({
            "sessionId": "original", "childId": "child_01", "answerSource": "program",
            "recognizedText": TEXT, "source": "pack", "steps": [],
            "events": [{"stepId": "s1", "result": "miss", "said": "am"}],
        })
        with patch("xiangke.app.now", return_value="2026-11-19T09:00:00+08:00"):
            view = self.app.transfer("q1")
            self.assertEqual(view["question"], "They ___ ready. (am / is / are)")
            self.app.reply("are")
            view = self.app.transfer("q1")
            self.assertEqual(view["question"], "I ___ hungry. (am / is / are)")
            self.app.reply("am")
            with self.assertRaisesRegex(BadRequest, "今天的两道新题"):
                self.app.transfer("q2")
        basis = self.store.unsynced()[-1]["transferBasis"]
        self.assertEqual((basis["focus"], basis["originalReply"], basis["contrast"]), ("are", "am", True))
        with patch("xiangke.app.now", return_value="2026-11-20T09:00:00+08:00"):
            self.assertEqual(self.app.transfer("q2")["question"], "He ___ TV. (watch / watches)")
            self.app.reply("watches")
            self.assertFalse(self.store.unsynced()[-1]["transferRetest"])

    def test_repeated_start_keeps_the_open_attempt(self):
        self.letter()
        self.app.transfer("q1")
        with self.assertRaises(BadRequest):
            self.app.transfer("q1")
        self.assertTrue(self.app.reply("watches")["done"])
        self.assertEqual(len(self.store.unsynced()), 1)

    def test_miss_then_hit_is_assisted(self):
        self.letter()
        self.app.transfer()
        self.app.reply("watch")
        view = self.app.reply("watches")
        self.assertTrue(view["done"])
        self.assertEqual(self.store.unsynced()[0]["transferTier"], "assisted")

    def test_asking_why_means_not_independent(self):
        self.letter()
        self.app.transfer()
        self.app.reply("为什么？")
        view = self.app.reply("watches")
        self.assertTrue(view["done"])
        self.assertEqual(self.store.unsynced()[0]["transferTier"], "assisted")

    def test_three_misses_is_stopped(self):
        self.letter()
        self.app.transfer()
        for _ in range(3):
            view = self.app.reply("watch")
        self.assertEqual(view["outcome"], "assisted")
        self.assertEqual(self.store.unsynced()[0]["transferTier"], "stopped")

    def test_leaving_part_way_is_stopped(self):
        self.letter()
        self.app.transfer()
        self.app.leave()
        self.assertEqual(self.store.unsynced()[0]["transferTier"], "stopped")

    def test_second_item_is_the_can_trap_then_done_for_today(self):
        self.letter()
        self.app.transfer()
        self.app.reply("watches")
        view = self.app.transfer()
        self.assertEqual(view["question"], "She can ___ TV. (watch / watches)")
        view = self.app.reply("watch")
        self.assertTrue(view["done"])
        self.assertEqual(self.store.unsynced()[-1]["transferTier"], "independent")
        with self.assertRaises(BadRequest):
            self.app.transfer()

    def test_new_day_retests_the_same_focus_with_two_unseen_questions(self):
        self.letter()
        with patch("xiangke.app.now", return_value="2026-11-19T09:00:00+08:00"):
            first = self.app.transfer("q1")["question"]
            self.app.reply("watches")
            contrast = self.app.transfer("q1")["question"]
            self.app.reply("watch")
        with patch("xiangke.app.now", return_value="2026-11-20T09:00:00+08:00"):
            later = self.app.transfer("q1")["question"]
            self.assertNotEqual(first, later)
            self.assertTrue(self.app.reply("walks")["done"])
            later_contrast = self.app.transfer("q1")["question"]
            self.assertNotEqual(contrast, later_contrast)
            self.assertTrue(self.app.reply("fly")["done"])
        records = self.store.unsynced()
        self.assertEqual(len({row["recognizedText"] for row in records}), 4)
        self.assertEqual([row["transferRetest"] for row in records], [False, False, True, True])
        self.assertEqual({row["transferBasis"]["focus"] for row in records}, {"change"})

    def test_older_format_and_reordered_options_are_skipped(self):
        self.store.save_session({
            "sessionId": "s_old", "childId": "child_01", "answerSource": "program",
            "source": "transfer", "recognizedText": "HE ___ TV.（watches / watch）",
            "startedAt": "2026-11-18T09:00:00+08:00", "events": [], "steps": [],
        })
        self.letter()
        view = self.app.transfer()
        self.assertEqual(view["question"], "Amy ___ to school. (walks / walk)")
        view = self.app.reply("walks")
        self.assertTrue(view["done"])
        self.assertFalse(self.store.unsynced()[-1]["transferRetest"])


if __name__ == "__main__":
    unittest.main()
