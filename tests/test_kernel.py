import json
import unittest
from pathlib import Path

from xiangke.kernel import check_plan, check_session, finish_session, judge, record_replies

ROOT = Path(__file__).resolve().parents[1]


class JudgeTests(unittest.TestCase):
    def test_case_and_space(self):
        self.assertEqual(judge(" Apple ", "apple"), "hit")
        self.assertEqual(judge("apples", "apple"), "miss")

    def test_surrounding_punctuation(self):
        self.assertEqual(judge("am.", "am"), "hit")
        self.assertEqual(judge("（am）", "am"), "hit")

    def test_leading_chinese_prefix(self):
        self.assertEqual(judge("是am", "am"), "hit")
        self.assertEqual(judge("选 am", "am"), "hit")
        self.assertEqual(judge("用：apple", "apple"), "hit")

    def test_extra_words_stay_wrong(self):
        self.assertEqual(judge("I am", "am"), "miss")
        self.assertEqual(judge("a m", "am"), "miss")

    def test_fullwidth_letters(self):
        self.assertEqual(judge("ａｍ", "am"), "hit")

    def test_curly_apostrophe_matches_straight(self):
        self.assertEqual(judge("don\u2019t", "don't"), "hit")
        self.assertEqual(judge("can\u2019t", "can't"), "hit")
        self.assertEqual(judge("it\u2019s", "it's"), "hit")
        self.assertEqual(judge("don\u2018t", "don't"), "hit")

    def test_internal_punctuation_stays(self):
        self.assertEqual(judge("I\u2019m", "I'm"), "hit")
        self.assertEqual(judge("I'm", "am"), "miss")
        self.assertEqual(judge("a.m", "am"), "miss")


class SessionCheckTests(unittest.TestCase):
    def test_empty_steps(self):
        result = check_session({"steps": []})
        self.assertFalse(result["checkPassed"])
        self.assertEqual(result["checkReasons"], ["empty_steps"])

    def test_missing_expect(self):
        result = check_session({"steps": [{"ask": "怎么拼？", "expect": "  "}]})
        self.assertEqual(result["checkReasons"], ["missing_expect"])

    def test_options_in_the_same_question_are_allowed(self):
        result = check_session(
            {
                "steps": [
                    {
                        "ask": "I 后面用 am、is 还是 are？",
                        "expect": "am",
                    }
                ]
            }
        )
        self.assertTrue(result["checkPassed"])
        self.assertEqual(result["checkReasons"], [])

    def test_later_answer_in_an_earlier_ask_leaks(self):
        result = check_session(
            {
                "steps": [
                    {"ask": "先看单词 apple", "expect": "cat"},
                    {"ask": "怎么拼？", "expect": "apple"},
                ]
            }
        )
        self.assertEqual(result["checkReasons"], ["answer_leaked"])

    def test_curly_apostrophe_in_an_earlier_ask_leaks(self):
        result = check_session(
            {
                "steps": [
                    {"ask": "先看 don\u2019t", "expect": "cat"},
                    {"ask": "这个缩写怎么拼？", "expect": "don't"},
                ]
            }
        )
        self.assertEqual(result["checkReasons"], ["answer_leaked"])

    def test_answer_inside_another_word_does_not_leak(self):
        result = check_session(
            {
                "steps": [
                    {"ask": "name 里面有几个字母？", "expect": "4"},
                    {"ask": "I 后面用哪个词？", "expect": "am"},
                ]
            }
        )
        self.assertTrue(result["checkPassed"])


class PlanCheckTests(unittest.TestCase):
    def _plan(self, **overrides):
        plan = {
            "planVersion": 1,
            "voice": "先看主语。",
            "reminders": [],
            "examples": [],
        }
        plan.update(overrides)
        return plan

    def test_first_plan(self):
        result = check_plan(self._plan(), None)
        self.assertTrue(result["checkPassed"])

    def test_version_must_increase_by_one(self):
        result = check_plan(self._plan(planVersion=3), 1)
        self.assertEqual(result["checkReasons"], ["version_gap"])

    def test_next_version_passes(self):
        result = check_plan(self._plan(planVersion=2), 1)
        self.assertTrue(result["checkPassed"])

    def test_empty_voice_and_limits(self):
        result = check_plan(
            self._plan(
                planVersion=2,
                voice="  ",
                reminders=["一", "二", "三", "四"],
                examples=[{}, {}, {}, {}],
            ),
            1,
        )
        self.assertEqual(
            result["checkReasons"],
            ["empty_voice", "too_many_reminders", "too_many_examples"],
        )


class RecordRepliesTests(unittest.TestCase):
    def _session(self, name: str) -> dict:
        path = ROOT / "data" / "sessions" / name
        return json.loads(path.read_text(encoding="utf-8"))["session"]

    def test_s01_misses_then_hit(self):
        session = self._session("s01_be_verb.json")
        saids = [event["said"] for event in session["events"]]
        self.assertEqual(record_replies(session, saids), session["events"])

    def test_s02_one_miss_then_hit(self):
        session = self._session("s02_spelling.json")
        saids = [event["said"] for event in session["events"]]
        self.assertEqual(record_replies(session, saids), session["events"])

    def test_failed_split_is_refused(self):
        for name in ("s03_split_failed.json", "s04_answer_leaked.json"):
            with self.assertRaises(ValueError):
                record_replies(self._session(name), ["am"])

    def test_third_miss_assists_and_stops(self):
        session = {
            "steps": [
                {
                    "stepId": "s1",
                    "ask": "I 后面用 am、is 还是 are？",
                    "expect": "am",
                    "explain": "I 和 am 连用。",
                },
                {
                    "stepId": "s2",
                    "ask": "apple 怎么拼？",
                    "expect": "apple",
                    "explain": "a-p-p-l-e。",
                },
            ]
        }
        events = record_replies(session, ["is", "I am", "are", "apple"])
        self.assertEqual(
            events,
            [
                {"stepId": "s1", "said": "is", "result": "miss", "hintUsed": 1},
                {"stepId": "s1", "said": "I am", "result": "miss", "hintUsed": 2},
                {"stepId": "s1", "said": "are", "result": "assisted", "hintUsed": 2},
            ],
        )
        self.assertEqual(session.get("events", []), [])

    def test_hint_count_resets_on_the_next_question(self):
        session = {
            "steps": [
                {
                    "stepId": "s1",
                    "ask": "I 后面用 am、is 还是 are？",
                    "expect": "am",
                    "explain": "I 和 am 连用。",
                },
                {
                    "stepId": "s2",
                    "ask": "apple 怎么拼？",
                    "expect": "apple",
                    "explain": "a-p-p-l-e。",
                },
            ]
        }
        events = record_replies(session, ["is", "am", "apples", "apple"])
        self.assertEqual(
            events,
            [
                {"stepId": "s1", "said": "is", "result": "miss", "hintUsed": 1},
                {"stepId": "s1", "said": "am", "result": "hit", "hintUsed": 1},
                {"stepId": "s2", "said": "apples", "result": "miss", "hintUsed": 1},
                {"stepId": "s2", "said": "apple", "result": "hit", "hintUsed": 1},
            ],
        )

    def test_reply_after_the_last_question_is_not_recorded(self):
        session = {
            "steps": [
                {
                    "stepId": "s1",
                    "ask": "I 后面用哪个？",
                    "expect": "am",
                    "explain": "I 和 am 连用。",
                }
            ]
        }
        events = record_replies(session, ["am", "is"])
        self.assertEqual(
            events,
            [{"stepId": "s1", "said": "am", "result": "hit", "hintUsed": 0}],
        )


class FinishSessionTests(unittest.TestCase):
    def _session(self, name: str) -> dict:
        path = ROOT / "data" / "sessions" / name
        return json.loads(path.read_text(encoding="utf-8"))["session"]

    def test_s01_is_the_whole_record(self):
        session = self._session("s01_be_verb.json")
        saids = [event["said"] for event in session["events"]]
        self.assertEqual(finish_session(session, saids), session)

    def test_s02_is_the_whole_record(self):
        session = self._session("s02_spelling.json")
        saids = [event["said"] for event in session["events"]]
        self.assertEqual(finish_session(session, saids), session)

    def test_s03_keeps_the_draft_and_only_the_note(self):
        session = self._session("s03_split_failed.json")
        note = session["events"][0]["said"]
        before = json.loads(json.dumps(session))
        got = finish_session(session, ["am", "is"], note)
        self.assertEqual(got, session)
        self.assertEqual(session, before)
        self.assertEqual(
            got["events"],
            [{"stepId": "", "said": note, "result": "note", "hintUsed": 0}],
        )

    def test_s04_keeps_the_draft_and_only_the_note(self):
        session = self._session("s04_answer_leaked.json")
        note = session["events"][0]["said"]
        got = finish_session(session, ["cat", "apple"], note)
        self.assertEqual(got, session)
        self.assertTrue(all(event["result"] == "note" for event in got["events"]))

    def test_failed_split_without_a_note_is_refused(self):
        session = self._session("s03_split_failed.json")
        with self.assertRaises(ValueError):
            finish_session(session, ["am"])


if __name__ == "__main__":
    unittest.main()
