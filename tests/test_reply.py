import copy
import json
import unittest
from pathlib import Path

from xiangke.kernel import finish_session
from xiangke.reply import (
    classify_error,
    confirm_reply,
    find_candidate,
    finish_with_error_kinds,
    route_reply,
)

SESSIONS = Path(__file__).resolve().parents[1] / "data" / "sessions"


def _load(name):
    return json.loads((SESSIONS / name).read_text(encoding="utf-8"))["session"]


class RouteReplyTests(unittest.TestCase):
    def test_right_answer_is_a_hit_whatever_the_label(self):
        for label in (None, "答题", "不会", "提问", "其他"):
            with self.subTest(label=label):
                self.assertEqual(route_reply("likes", "likes", label)["action"], "hit")
        self.assertEqual(route_reply("是 likes", "likes", "答题")["action"], "hit")

    def test_label_can_never_turn_a_reply_into_a_hit(self):
        for label in (None, "答题", "不会", "提问", "其他"):
            with self.subTest(label=label):
                self.assertNotEqual(route_reply("liked", "likes", label)["action"], "hit")

    def test_not_knowing_or_asking_is_not_a_wrong_answer(self):
        cases = [
            ("不知道", "不会", "hint"),
            ("为啥要改", "提问", "show_rule"),
            ("我饿了", "其他", "redirect"),
        ]
        for said, label, action in cases:
            with self.subTest(said=said):
                result = route_reply(said, "likes", label)
                self.assertEqual(result["action"], action)
                self.assertFalse(result["countsAsMiss"])

    def test_question_never_shows_the_answer_action(self):
        self.assertNotEqual(route_reply("为什么", "likes", "提问")["action"], "explain")

    def test_wrapped_answer_is_confirmed_not_marked_wrong(self):
        for said in ("是 likes 吧", "likes吧", "我觉得是 likes", "likes 对吧"):
            with self.subTest(said=said):
                result = route_reply(said, "likes", "答题")
                self.assertEqual(result["action"], "confirm")
                self.assertEqual(result["candidate"], "likes")
                self.assertFalse(result["countsAsMiss"])

    def test_wrapped_wrong_answer_is_also_confirmed_first(self):
        result = route_reply("应该是 liked", "likes", "答题")
        self.assertEqual((result["action"], result["candidate"]), ("confirm", "liked"))

    def test_plain_wrong_answer_is_a_miss(self):
        for said in ("liked", "likeing", "She likes", "I am"):
            with self.subTest(said=said):
                result = route_reply(said, "likes", "答题")
                self.assertEqual(result["action"], "miss")
                self.assertTrue(result["countsAsMiss"])

    def test_without_a_label_the_program_still_works(self):
        self.assertEqual(route_reply("likes", "likes")["action"], "hit")
        self.assertEqual(route_reply("是 likes 吧", "likes")["action"], "confirm")
        self.assertEqual(route_reply("liked", "likes")["action"], "miss")

    def test_chinese_expect_has_no_candidate(self):
        self.assertEqual(route_reply("多个", "一个", "答题")["action"], "miss")
        self.assertIsNone(find_candidate("应该是 one", "一个"))

    def test_unknown_label_is_refused(self):
        with self.assertRaises(ValueError):
            route_reply("likes", "likes", "别的")


class ConfirmReplyTests(unittest.TestCase):
    def test_yes_then_program_judges_the_word(self):
        for said in ("是", "是的", "对", "嗯", "yes"):
            with self.subTest(said=said):
                self.assertEqual(confirm_reply(said, "likes", "likes")["result"], "hit")
        result = confirm_reply("是", "liked", "likes")
        self.assertEqual(result["result"], "miss")
        self.assertTrue(result["countsAsMiss"])

    def test_no_asks_again_without_a_miss(self):
        for said in ("不是", "不", "no"):
            with self.subTest(said=said):
                result = confirm_reply(said, "liked", "likes")
                self.assertEqual(result["action"], "ask_again")
                self.assertFalse(result["countsAsMiss"])

    def test_a_new_answer_is_handled_normally(self):
        self.assertEqual(confirm_reply("likes", "liked", "likes")["action"], "hit")
        self.assertEqual(confirm_reply("liked", "likex", "likes")["action"], "miss")


class ClassifyErrorTests(unittest.TestCase):
    def test_kinds(self):
        cases = [
            ("", "likes", {}, "empty"),
            ("喜欢", "likes", {}, "not_english"),
            ("like", "likes", {"wrong": "like"}, "no_change"),
            ("are", "is", {"options": ["am", "is", "are"]}, "wrong_option"),
            ("liked", "likes", {}, "wrong_form"),
            ("liking", "likes", {}, "wrong_form"),
            ("like", "likes", {}, "wrong_form"),
            ("studys", "studies", {}, "wrong_form"),
            ("wahes", "washes", {}, "spelling"),
            ("wachs", "watches", {}, "spelling"),
            ("She likes", "likes", {}, "extra_words"),
            ("I am", "is", {}, "other"),
            ("apple", "likes", {}, "other"),
        ]
        for said, expect, extra, want in cases:
            with self.subTest(said=said):
                self.assertEqual(classify_error(said, expect, **extra), want)

    def test_chinese_answer_is_not_called_spelling(self):
        self.assertEqual(classify_error("多个", "一个"), "other")
        self.assertEqual(classify_error("多个", "一个", options=["一个", "多个"]), "wrong_option")


class ErrorKindRecordTests(unittest.TestCase):
    def fix_session(self):
        session = copy.deepcopy(_load("s01_be_verb.json"))
        session["recognizedText"] = "改错（只改一个词）：She like cats."
        session["steps"] = [
            {"stepId": "s1", "ask": "哪个词用错了？", "expect": "like", "explain": "用错的是 like。"},
            {"stepId": "s2", "ask": "like 应该改成什么？", "expect": "likes", "explain": "改成 likes。"},
        ]
        return session

    def test_real_sample_session(self):
        session = _load("s01_be_verb.json")
        finished = finish_with_error_kinds(session, ["is", "I am", "是am"])
        self.assertEqual([e.get("errorKind") for e in finished["events"]], ["wrong_option", "extra_words", None])
        self.assertNotIn("errorKind", finished["events"][2])

    def test_results_are_the_same_as_the_kernel(self):
        session = _load("s01_be_verb.json")
        plain = finish_session(session, ["is", "I am", "是am"])
        finished = finish_with_error_kinds(session, ["is", "I am", "是am"])
        for event in finished["events"]:
            event.pop("errorKind", None)
        self.assertEqual(finished, plain)

    def test_input_is_not_changed(self):
        session = _load("s01_be_verb.json")
        before = copy.deepcopy(session)
        finish_with_error_kinds(session, ["is"])
        self.assertEqual(session, before)

    def test_assisted_also_has_a_kind(self):
        finished = finish_with_error_kinds(_load("s01_be_verb.json"), ["is", "are", "is"])
        self.assertEqual([e["result"] for e in finished["events"]], ["miss", "miss", "assisted"])
        self.assertEqual(finished["events"][2]["errorKind"], "wrong_option")

    def test_failed_split_only_has_the_note(self):
        finished = finish_with_error_kinds(_load("s03_split_failed.json"), ["is"], note="先问问老师")
        self.assertEqual([e["result"] for e in finished["events"]], ["note"])
        self.assertNotIn("errorKind", finished["events"][0])

    def test_fix_item_kinds(self):
        finished = finish_with_error_kinds(self.fix_session(), ["cats", "like", "like", "liked", "likes"])
        kinds = [(e["stepId"], e["result"], e.get("errorKind")) for e in finished["events"]]
        self.assertEqual(
            kinds,
            [
                ("s1", "miss", "other"),
                ("s1", "hit", None),
                ("s2", "miss", "no_change"),
                ("s2", "miss", "wrong_form"),
                ("s2", "hit", None),
            ],
        )


if __name__ == "__main__":
    unittest.main()
