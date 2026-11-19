import json
import threading
import unittest

from xiangke.answers import compute_answer
from xiangke.app import BadRequest, PhoneApp
from xiangke.cloud import CloudApp, CloudStore, test_planner
from xiangke.drills import check_item, make_drill_maker, redo_items
from xiangke.planner import LlmError, build_messages, make_planner
from xiangke.portrait import build_stats, stats_for_model
from xiangke.store import Store
from xiangke.sync import sync_once
from xiangke.teach import teach

RULE = "主语是一个人时，动词要变。"
GOOD = {"text": "改错（只改一个词）：Tom eat apples.", "answer": "eats", "rule": RULE}


def session(session_id="s1", text="改错（只改一个词）：She like cats.", results=("miss", "hit"), **extra):
    return {
        "sessionId": session_id,
        "childId": "child_01",
        "recognizedText": text,
        "answerSource": "program",
        "checkPassed": True,
        "startedAt": "2026-11-05T20:00:00+08:00",
        "steps": [{"stepId": "s1", "ask": "改成什么？", "expect": "likes", "explain": "She 是一个人。"}],
        "events": [{"stepId": "s1", "said": "like", "result": result, "hintUsed": 0} for result in results],
        **extra,
    }


class ProgramAnswerTests(unittest.TestCase):
    def test_words_without_an_s_form_are_refused(self):
        for text in ("She ___ sing well. (can / cans)", "He ___ go. (must / musts)", "He ___ come. (will / wills)", "He ___ late. (may / mays)", "She ___ here. (be / bes)"):
            with self.subTest(text=text):
                self.assertIsNone(compute_answer(text))

    def test_ordinary_verbs_still_work(self):
        self.assertEqual(compute_answer("She ___ apples. (like / likes)")["answer"], "likes")
        self.assertEqual(compute_answer("She can ___. (swim / swims)")["answer"], "swim")


class CheckItemTests(unittest.TestCase):
    def reason(self, **change):
        return check_item({**GOOD, **change}, set())

    def test_good_items_pass(self):
        self.assertIsNone(self.reason())
        self.assertIsNone(self.reason(text="I have two ___. (foot / feet)", answer="feet", rule="两个以上要用复数。"))
        self.assertIsNone(self.reason(text="看中文写单词：书 b__k", answer="book", rule="书的英文。"))
        self.assertIsNone(self.reason(text="昨天我去了公园。I ___ to the park yesterday.", answer="went", rule="昨天的事要用过去的样子。"))

    def test_blank_count_must_match_the_answer(self):
        self.assertIn("对不上", self.reason(text="看中文写单词：孩子们 c__dren", answer="children", rule="孩子们的英文。"))
        self.assertIn("对不上", self.reason(text="看中文写单词：书 b__k", answer="bike", rule="书的英文。"))

    def test_spaced_blanks_count_too(self):
        self.assertIsNone(self.reason(text="看中文写单词：他喜欢。 _ _ _ _ _", answer="likes", rule="他喜欢的英文。"))
        self.assertIn("对不上", self.reason(text="看中文写单词：他喜欢。 _ _ _ _ _ _", answer="likes", rule="他喜欢的英文。"))

    def test_option_word_cannot_also_sit_in_the_sentence(self):
        self.assertIn("又出现", self.reason(text="选词：He ___ run. (run / runs)", answer="runs"))

    def test_item_needs_an_english_sentence(self):
        self.assertIn("英文句子", self.reason(text="填空：她___衣服。 (wash / washes)", answer="washes"))

    def test_answer_rules(self):
        self.assertIn("一个英文词", self.reason(answer="I am"))
        self.assertIn("一个英文词", self.reason(answer="是"))
        self.assertIn("选项", self.reason(text="She ___ apples. (like / likes)", answer="liked"))
        self.assertIn("已经写了", self.reason(text="She likes ___.", answer="likes"))

    def test_program_wins_when_it_can_compute(self):
        self.assertIn("程序算", self.reason(text="She ___ apples. (like / likes)", answer="like"))

    def test_rule_cannot_give_the_answer_or_use_terms(self):
        self.assertIn("规则", self.reason(rule="要写成 eats。"))
        self.assertIn("规则", self.reason(rule="这是第三人称。"))
        self.assertIn("规则", self.reason(rule="多" * 61))

    def test_missing_fields_and_unknown_shapes(self):
        self.assertIsNotNone(check_item("不是题", set()))
        self.assertIsNotNone(self.reason(rule=""))
        self.assertIn("题型", self.reason(text="说一说你喜欢什么。"))

    def test_duplicates_are_dropped(self):
        seen = set()
        self.assertIsNone(check_item(GOOD, seen))
        self.assertEqual(check_item({**GOOD, "text": GOOD["text"].upper()}, seen), "重复")


class RedoTests(unittest.TestCase):
    def test_wrong_items_come_back_newest_first(self):
        items = redo_items([session("a"), session("b", "She ___ apples. (like / likes)")], set())
        self.assertEqual([item["text"] for item in items], ["She ___ apples. (like / likes)", "改错（只改一个词）：She like cats."])
        self.assertEqual([item["answer"] for item in items], ["likes", "likes"])
        self.assertEqual(items[0]["source"], "redo")

    def test_only_judged_wrong_items_come_back(self):
        cases = [
            session(results=("hit",)),
            session(answerSource="model"),
            session(checkPassed=False),
            session(flagged=True),
            session(text="说不清的题 ___"),
        ]
        self.assertEqual(redo_items(cases, set()), [])

    def test_known_items_and_limit(self):
        self.assertEqual(redo_items([session()], {"改错（只改一个词）：she like cats."}), [])
        many = [session(str(i), f"改错（只改一个词）：He like {word}.") for i, word in enumerate(("apples", "rice", "bread"))]
        self.assertEqual(len(redo_items(many, set())), 2)

    def test_transfer_items_are_not_sent_back_as_redo(self):
        transfer = session("t1", "He ___ TV. (watch / watches)", source="transfer")
        self.assertEqual(redo_items([transfer], set()), [])


class MakerTests(unittest.TestCase):
    PORTRAIT = {"judgement": "会看主语，想不到动词要变。", "focus": ["she、he 后面的动词"]}

    def test_every_item_is_checked_again_with_the_check_model(self):
        asked, checked = [], []

        def chat(messages):
            asked.append(messages)
            return json.dumps({"items": [{"text": "He ___ apples. (like / likes)", "answer": "likes", "rule": RULE}]})

        def check_chat(messages):
            checked.append(messages)
            return json.dumps({"answers": [{"id": 1, "answer": "likes"}]})

        items = make_drill_maker(chat, check_chat)(self.PORTRAIT, [session()], set())
        self.assertEqual([item["source"] for item in items], ["cloud"])
        self.assertEqual((len(asked), len(checked)), (1, 1))
        self.assertIn("想不到动词要变", asked[0][0]["content"])
        self.assertIn("He ___ apples", checked[0][0]["content"])
        self.assertNotIn("likes\"", checked[0][0]["content"])

    def test_item_the_check_calls_bad_is_dropped_even_if_the_program_agrees(self):
        replies = [
            json.dumps({"items": [{"text": "He ___ the park. (go / goes)", "answer": "goes", "rule": RULE}]}),
            json.dumps({"answers": [{"id": 1, "answer": "有问题"}]}, ensure_ascii=False),
        ]
        self.assertEqual(make_drill_maker(lambda messages: replies.pop(0))(self.PORTRAIT, [session()], set()), [])

    def test_second_check_keeps_only_matching_answers(self):
        replies = [
            json.dumps({"items": [
                {"text": "I ___ to school yesterday. (go / went)", "answer": "went", "rule": "昨天的事。"},
                {"text": "她喜欢阅读。She ___ reading.", "answer": "likes", "rule": "看中文意思。"},
                {"text": "They ___ happy. (was / were)", "answer": "were", "rule": "多个人。"},
            ]}, ensure_ascii=False),
            json.dumps({"answers": [{"id": 1, "answer": "Went"}, {"id": 2, "answer": "有问题"}, {"id": 3, "answer": "was"}]}, ensure_ascii=False),
        ]
        items = make_drill_maker(lambda messages: replies.pop(0))(self.PORTRAIT, [session()], set())
        self.assertEqual([item["answer"] for item in items], ["went"])

    def test_second_check_failing_sends_nothing_new(self):
        answers = iter([json.dumps({"items": [
            {"text": "He ___ apples. (like / likes)", "answer": "likes", "rule": RULE},
            {"text": "I ___ to school yesterday. (go / went)", "answer": "went", "rule": "昨天的事。"},
        ]})])

        def chat(messages):
            try:
                return next(answers)
            except StopIteration:
                raise LlmError("连不上大模型") from None

        self.assertEqual(make_drill_maker(chat)(self.PORTRAIT, [session()], set()), [])

    def test_too_many_items_and_known_items(self):
        rows = [{"text": f"He ___ apples. ({word} / {word}s)", "answer": word + "s", "rule": RULE} for word in ("eat", "like", "want", "need", "see", "find", "take", "make", "get", "peel")]

        def chat(messages):
            if "别人出的" in messages[0]["content"]:
                return json.dumps({"answers": [{"id": index, "answer": row["answer"]} for index, row in enumerate(rows[1:], start=1)]})
            return json.dumps({"items": rows}, ensure_ascii=False)

        items = make_drill_maker(chat)(self.PORTRAIT, [session()], {"he ___ apples. (eat / eats)"})
        self.assertEqual([item["answer"] for item in items], ["likes", "wants", "needs"])

    def test_timeout_is_tried_again(self):
        answers = [
            LlmError("连不上大模型"),
            json.dumps({"items": [{"text": "He ___ apples. (like / likes)", "answer": "likes", "rule": RULE}]}),
            json.dumps({"answers": [{"id": 1, "answer": "likes"}]}),
        ]

        def chat(messages):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        self.assertEqual(len(make_drill_maker(chat)(self.PORTRAIT, [session()], set())), 1)

    def test_broken_output_is_an_error(self):
        with self.assertRaises(LlmError):
            make_drill_maker(lambda messages: "坏掉了")(self.PORTRAIT, [session()], set())


class StatsTests(unittest.TestCase):
    def test_counts(self):
        sessions = [
            session("a"),
            session("b", results=("hit",)),
            session("c", results=("hit",)),
            session("d", "They ___ my friends. (am / is / are)", results=("miss", "assisted")),
            session("e", answerSource="model"),
            session("f", flagged=True),
        ]
        sessions[1]["events"].append({"stepId": "s1", "said": "什么时候不加s", "result": "note"})
        stats = build_stats(sessions)
        self.assertEqual(stats["judgedSessions"], 4)
        self.assertEqual(stats["recentNotes"], ["什么时候不加s"])
        rows = {row["topic"]: row for row in stats["topics"]}
        self.assertEqual((rows["动词变不变"]["attempts"], rows["动词变不变"]["wrong"], rows["动词变不变"]["streak"]), (3, 1, 2))
        self.assertEqual(rows["am、is、are"]["wrong"], 1)
        self.assertEqual(rows["am、is、are"]["lastWrongAt"], "2026-11-05T20:00:00+08:00")

    def test_model_gets_no_dates(self):
        text = json.dumps(stats_for_model(build_stats([session()])), ensure_ascii=False)
        self.assertNotIn("2026", text)
        self.assertIn("做错几次", text)

    def test_profile_goes_into_the_prompt(self):
        current = {"voice": "讲法", "reminders": [], "examples": [], "forbid": "不说答案", "profile": {"上次的判断": "想不到动词要变"}}
        self.assertIn("想不到动词要变", build_messages(current, [session()])[1]["content"])


PLAN_REPLY = {
    "portrait": {"judgement": "会看主语，想不到 like 要变。", "focus": ["she 后面的动词"]},
    "voice": "每一问都很短。",
    "reminders": ["She 后面的动词他会忘记变。"],
    "examples": [],
    "forbid": "不要直接说出标准答案。",
}


class CloudJobTests(unittest.TestCase):
    def setUp(self):
        self.store = CloudStore(":memory:")
        self.replies = []

    def tearDown(self):
        self.store.connection.close()

    def chat(self, messages):
        return json.dumps(self.replies.pop(0), ensure_ascii=False)

    def cloud(self, **options):
        return CloudApp(self.store, make_planner(self.chat), make_drill_maker(self.chat), **options)

    def test_one_sync_updates_portrait_plan_and_drills(self):
        self.replies = [PLAN_REPLY, {"items": [{"text": "He ___ apples. (like / likes)", "answer": "likes", "rule": RULE}]}, {"answers": [{"id": 1, "answer": "likes"}]}]
        reply = self.cloud().sync("child_01", 2, [session()])
        self.assertEqual(reply["plan"]["planVersion"], 3)
        self.assertEqual([item["source"] for item in reply["drills"]], ["redo", "cloud"])
        self.assertEqual([item["seq"] for item in reply["drills"]], [1, 2])
        portrait = self.store.latest_portrait("child_01")
        self.assertEqual(portrait["judgement"], PLAN_REPLY["portrait"]["judgement"])
        self.assertEqual(portrait["stats"]["topics"][0]["wrong"], 1)
        self.assertEqual(portrait["version"], 1)

    def test_be_is_an_ordinary_word_in_the_judgement(self):
        written = {**PLAN_REPLY, "portrait": {"judgement": "be 动词会选，动词加 s 没形成。", "focus": []}}
        self.replies = [written, {"items": []}]
        self.cloud().sync("child_01", 2, [session()])
        self.assertIn("be 动词", self.store.latest_portrait("child_01")["judgement"])

    def test_examples_with_long_answers_are_dropped(self):
        example = {"recognizedText": "She ___ apples. (like / likes)", "steps": [{"ask": "She 是几个人？", "expect": "一个人"}, {"ask": "选哪个？", "expect": "likes"}]}
        self.replies = [{**PLAN_REPLY, "examples": [example]}, {"items": []}]
        reply = self.cloud().sync("child_01", 2, [session()])
        self.assertNotIn(example, reply["plan"]["examples"])

    def test_drills_are_sent_only_once(self):
        self.replies = [PLAN_REPLY, {"items": []}]
        cloud = self.cloud()
        first = cloud.sync("child_01", 2, [session()])
        last = first["drills"][-1]["seq"]
        self.assertNotIn("drills", cloud.sync("child_01", 3, [], last))
        self.assertEqual(len(cloud.sync("child_01", 3, [], 0)["drills"]), 1)

    def test_finishing_drills_updates_the_judgement_even_when_all_right(self):
        improved = {**PLAN_REPLY, "portrait": {"judgement": "现在 she 后面的动词会变了。", "focus": []}, "reminders": []}
        self.replies = [improved, {"items": []}]
        reply = self.cloud().sync("child_01", 2, [session(results=("hit",), drillId="d1")])
        self.assertEqual(reply["plan"]["planVersion"], 3)
        self.assertIn("会变了", self.store.latest_portrait("child_01")["judgement"])

    def test_bad_portrait_keeps_the_plan_and_old_judgement(self):
        self.replies = [{**PLAN_REPLY, "portrait": {"judgement": "他把 elephant 拼错了。", "focus": []}}, {"items": []}]
        reply = self.cloud().sync("child_01", 2, [session()])
        self.assertEqual(reply["plan"]["planVersion"], 3)
        self.assertIsNone(self.store.latest_portrait("child_01")["judgement"])

    def test_drill_failure_keeps_the_plan_and_original_items(self):
        self.replies = [PLAN_REPLY]

        def chat(messages):
            if self.replies:
                return self.chat(messages)
            raise LlmError("连不上大模型")

        reply = CloudApp(self.store, make_planner(chat), make_drill_maker(chat)).sync("child_01", 2, [session()])
        self.assertEqual(reply["plan"]["planVersion"], 3)
        self.assertEqual([item["source"] for item in reply["drills"]], ["redo"])
        self.assertNotIn("planFailed", reply)

    def test_model_down_still_sends_original_items(self):
        def chat(messages):
            raise LlmError("连不上大模型")

        cloud = CloudApp(self.store, make_planner(chat), make_drill_maker(chat))
        reply = cloud.sync("child_01", 2, [session()])
        self.assertIsNone(reply["plan"])
        self.assertTrue(reply["planFailed"])
        self.assertEqual([item["source"] for item in reply["drills"]], ["redo"])

    def test_all_right_writes_nothing_and_does_not_keep_working(self):
        calls = []

        def chat(messages):
            calls.append(messages)
            return "{}"

        cloud = CloudApp(self.store, make_planner(chat), make_drill_maker(chat))
        reply = cloud.sync("child_01", 2, [session(results=("hit",))])
        self.assertEqual(calls, [])
        self.assertIsNone(reply["plan"])
        self.assertFalse(cloud._should_start("child_01", 2))

    def test_enough_open_drills_means_no_new_ones(self):
        cloud = self.cloud()
        for index in range(6):
            self.store.add_drill("child_01", {"text": f"题{index} ___", "answer": "a", "rule": "规则", "source": "cloud"})
        self.replies = [PLAN_REPLY]
        reply = cloud.sync("child_01", 2, [session()])
        self.assertEqual(reply["plan"]["planVersion"], 3)
        self.assertEqual(self.replies, [])
        self.assertEqual(sum(item["source"] == "cloud" for item in reply["drills"]), 6)


class BackgroundTests(unittest.TestCase):
    def test_sync_returns_before_the_model_finishes(self):
        release = threading.Event()
        finished = threading.Event()

        def planner(current, new_sessions):
            release.wait(10)
            return test_planner(current, new_sessions)

        with CloudStore(":memory:") as store:
            cloud = CloudApp(store, planner, background=True)
            original = cloud._job

            def job(*args):
                original(*args)
                finished.set()

            cloud._job = job
            reply = cloud.sync("child_01", 2, [session()])
            self.assertTrue(reply["working"])
            self.assertIsNone(reply["plan"])
            self.assertEqual([item["source"] for item in reply["drills"]] if "drills" in reply else ["redo"], ["redo"])
            again = cloud.sync("child_01", 2, [])
            self.assertTrue(again["working"])
            release.set()
            self.assertTrue(finished.wait(10))
            done = cloud.sync("child_01", 2, [])
            self.assertEqual(done["plan"]["planVersion"], 3)
            self.assertNotIn("working", done)
            self.assertEqual(store.latest_version("child_01"), 3)

    def test_background_failure_is_reported_once_then_retried(self):
        finished = threading.Event()
        state = {"broken": True}

        def planner(current, new_sessions):
            if state["broken"]:
                raise RuntimeError("大模型连不上")
            return test_planner(current, new_sessions)

        with CloudStore(":memory:") as store:
            cloud = CloudApp(store, planner, background=True)
            original = cloud._job

            def job(*args):
                original(*args)
                finished.set()

            cloud._job = job
            cloud.sync("child_01", 2, [session()])
            self.assertTrue(finished.wait(10))
            reply = cloud.sync("child_01", 2, [])
            self.assertTrue(reply["planFailed"])
            self.assertNotIn("working", reply)
            finished.clear()
            state["broken"] = False
            cloud.sync("child_01", 2, [])
            self.assertTrue(finished.wait(10))
            self.assertEqual(cloud.sync("child_01", 2, [])["plan"]["planVersion"], 3)


LESSON = {"steps": [{"ask": "空里填哪个？", "expect": "went", "explain": "昨天的事，所以填 went。"}]}


def fake_write(system, user, grammar, limit=512):
    if "孩子说：" in user:
        return json.dumps({"label": "答题"}, ensure_ascii=False)
    return json.dumps(LESSON, ensure_ascii=False)


def drill(drill_id="d1", text="I ___ to school yesterday. (go / went)", answer="went", seq=1, source="cloud"):
    return {"drillId": drill_id, "childId": "child_01", "seq": seq, "text": text, "answer": answer, "rule": "昨天的事要用过去的样子。", "source": source}


class PhoneDrillTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.app = PhoneApp(fake_write, self.store)

    def tearDown(self):
        self.store.connection.close()

    def test_cloud_answer_is_judged_and_marked(self):
        self.store.save_drills([drill()])
        self.assertEqual([row["drillId"] for row in self.app.home()["drills"]], ["d1"])
        view = self.app.start_drill("d1")
        self.assertTrue(view["drill"])
        self.assertEqual(view["mode"], "judged")
        self.assertEqual(self.app.reply("go")["messages"], ["再试一次。昨天的事要用过去的样子。"])
        view = self.app.reply("went")
        self.assertTrue(view["done"])
        record = self.store.unsynced()[0]
        self.assertEqual(record["answerSource"], "cloud")
        self.assertEqual(record["drillId"], "d1")
        self.assertEqual(record["source"], "drill")
        self.assertEqual([event["result"] for event in record["events"]], ["miss", "hit"])
        self.assertEqual(self.app.home()["drills"], [])
        self.assertEqual(self.app.home()["history"][0]["answerSource"], "cloud")

    def test_wrong_rule_without_a_space_is_caught(self):
        def write(system, user, grammar, limit=512):
            return json.dumps({"steps": [
                {"ask": "主语是几个？", "expect": "一个", "explain": "She 是一个人。"},
                {"ask": "选哪个？", "expect": "likes", "explain": "动词要用一般样子（不加s）：like → likes。"},
            ]}, ensure_ascii=False)

        lesson = teach("She ___ apples. (like / likes)", write)
        self.assertEqual(lesson["source"], "program")
        self.assertIn("该变形却讲成不加 s", lesson["errors"])

    def test_item_the_program_can_compute_stays_program(self):
        self.store.save_drills([drill(text="She ___ apples. (like / likes)", answer="likes")])
        self.app.start_drill("d1")
        self.app.leave()
        self.assertEqual(self.store.unsynced()[0]["answerSource"], "program")

    def test_fill_item_without_options_falls_back_to_program_wording(self):
        lesson = teach("昨天我去了公园。I ___ to the park yesterday.", lambda *_: "坏掉了", None, {"kind": "cloud", "answer": "went", "ruleKey": "cloud", "rule": "昨天的事。"})
        self.assertEqual(lesson["source"], "program")
        self.assertTrue(lesson["judged"])
        self.assertEqual(lesson["steps"][0]["expect"], "went")
        self.assertIn("went", lesson["steps"][0]["explain"])

    def test_leaving_part_way_keeps_the_drill_open(self):
        self.store.save_drills([drill()])
        self.app.start_drill("d1")
        home = self.app.leave()
        self.assertEqual(len(home["drills"]), 1)
        self.app.start_drill("d1")
        self.app.reply("went")
        self.assertEqual(self.app.home()["drills"], [])
        self.assertEqual(len(self.store.unsynced()), 2)

    def test_flag_means_not_counted_and_closed(self):
        self.store.save_drills([drill()])
        self.app.start_drill("d1")
        self.app.reply("go")
        home = self.app.flag()
        self.assertEqual(home["drills"], [])
        self.assertTrue(home["history"][0]["flagged"])
        record = self.store.unsynced()[0]
        self.assertTrue(record["flagged"])
        self.assertEqual(build_stats([record])["judgedSessions"], 0)
        with self.assertRaises(BadRequest):
            self.app.flag()

    def test_flag_only_on_a_drill_in_progress(self):
        with self.assertRaises(BadRequest):
            self.app.flag()
        self.app.start("They ___ my friends. (am / is / are)", "pack")
        with self.assertRaises(BadRequest):
            self.app.flag()

    def test_unknown_or_finished_drill_is_refused(self):
        with self.assertRaises(BadRequest):
            self.app.start_drill("没有这道")

    def test_sync_saves_only_good_drills_once(self):
        good = drill()
        bad = [{**drill("d2", seq=2), "answer": " "}, {**drill("d3", seq=3), "childId": "别的孩子"}, {**drill("d4", seq=4), "seq": "4"}, "不是题"]
        seen = []

        def post(url, body):
            seen.append(body["drillSeq"])
            return {"accepted": [], "plan": None, "drills": [good, *bad], "working": True}

        result = sync_once(self.store, "child_01", post=post)
        self.assertEqual(result["drillsAdded"], 1)
        self.assertTrue(result["working"])
        result = sync_once(self.store, "child_01", post=post)
        self.assertNotIn("drillsAdded", result)
        self.assertEqual(seen, [0, 1])
        self.assertEqual(len(self.store.open_drills("child_01")), 1)

    def test_full_circle_from_wrong_answer_to_redo(self):
        with CloudStore(":memory:") as cloud_store:
            cloud = CloudApp(cloud_store)
            post = lambda url, body: cloud.sync(body["childId"], body["planVersion"], body["sessions"], body["drillSeq"])
            self.app.start("She ___ apples. (like / likes)", "pack")
            self.app.reply("like")
            self.app.leave()
            result = sync_once(self.store, "child_01", post=post)
            self.assertEqual(result["drillsAdded"], 1)
            drills = self.app.home()["drills"]
            self.assertEqual(drills[0]["source"], "redo")
            self.app.start_drill(drills[0]["drillId"])
            self.app.leave()
            sync_once(self.store, "child_01", post=post)
            self.assertEqual(cloud_store.sessions_after("child_01", None)[-1]["drillId"], drills[0]["drillId"])


if __name__ == "__main__":
    unittest.main()
