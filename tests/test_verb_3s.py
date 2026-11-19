import json
import unittest

from xiangke.kernel import check_session, judge, record_replies
from xiangke.verb_3s import (
    _ASK_RIGHT,
    _ASK_WRONG,
    _verb_table,
    check_wording,
    lock_grammar,
    model_input,
    plan_correction,
    run_correction,
    template_steps,
)

ITEM = "改错（只改一个词）：She like cats."

GOOD = {
    "steps": [
        {"explain": "She 是她，一个人，后面的动词要变样子，所以 like 不对。"},
        {"explain": "主语是 he、she、it，句子里没有 can 这些帮忙的词，动词才要加 s，所以改成 likes。"},
    ]
}


def _dump(steps):
    return json.dumps({"steps": steps}, ensure_ascii=False)


def _with_explain(step_index, explain):
    changed = json.loads(json.dumps(GOOD))
    changed["steps"][step_index]["explain"] = explain
    return changed


class PlanCorrectionTests(unittest.TestCase):
    def test_she_like_cats(self):
        plan = plan_correction(ITEM)
        self.assertEqual(plan["type"], "verb_3s")
        self.assertEqual(plan["ruleKey"], "v_3s")
        self.assertEqual(plan["wrong"], "like")
        self.assertEqual(plan["right"], "likes")
        self.assertEqual(plan["corrected"], "She likes cats.")
        self.assertEqual(plan["clue"], "She 是她，一个人")
        self.assertEqual(
            [step["expect"] for step in plan["steps"]],
            ["like", "likes"],
        )

    def test_it_is_a_thing_not_a_person(self):
        plan = plan_correction("It look nice.")
        self.assertEqual(plan["clue"], "It 是它，一样东西")
        for step in template_steps(plan)["steps"]:
            self.assertNotIn("一个人", step["explain"])

    def test_model_input_gives_questions_and_answers(self):
        plan = plan_correction(ITEM)
        message = model_input(ITEM, plan)
        self.assertIn("题目：改错（只改一个词）：She like cats.", message)
        self.assertIn(plan["steps"][0]["ask"], message)
        self.assertIn(plan["steps"][1]["ask"], message)
        self.assertIn("答案=like", message)
        self.assertIn("答案=likes", message)
        self.assertIn("主语是 he、she、it，句子里又没有 can 这些帮忙的词，动词才要加 s。", message)
        self.assertIn("反例=", message)
        self.assertIn("线索=She 是她，一个人", message)

    def test_grammar_lets_the_model_write_only_explain(self):
        grammar = lock_grammar(plan_correction(ITEM))
        self.assertIn('\\"explain\\"', grammar)
        self.assertNotIn('\\"ask\\"', grammar)
        self.assertNotIn('\\"expect\\"', grammar)

    def test_chinese_opening_is_not_needed(self):
        for text in (
            "She like cats.",
            "改错：She like cats.",
            "改错（只改一个词）: She like cats.",
            "1. She like cats.",
            "she like cats",
        ):
            with self.subTest(text=text):
                self.assertEqual(plan_correction(text)["right"], "likes")

    def test_every_kind_of_change(self):
        cases = [
            ("He play football.", "play", "plays", "v_3s"),
            ("She watch TV.", "watch", "watches", "v_es"),
            ("It fix cars.", "fix", "fixes", "v_es"),
            ("He study English.", "study", "studies", "v_ies"),
            ("She stay at home.", "stay", "stays", "v_3s"),
            ("He go to school.", "go", "goes", "v_irregular"),
            ("She do homework.", "do", "does", "v_irregular"),
            ("It have a tail.", "have", "has", "v_irregular"),
        ]
        for text, wrong, right, rule in cases:
            with self.subTest(text=text):
                plan = plan_correction(text)
                self.assertEqual(plan["wrong"], wrong)
                self.assertEqual(plan["right"], right)
                self.assertEqual(plan["ruleKey"], rule)
                self.assertEqual(
                    [step["expect"] for step in plan["steps"]], [wrong, right]
                )

    def test_rule_wording_fits_the_change(self):
        self.assertIn("加 es", plan_correction("She watch TV.")["rule"])
        self.assertIn("y 改成 i", plan_correction("He study English.")["rule"])
        self.assertIn("特殊变化", plan_correction("He go to school.")["rule"])

    def test_verb_list_size(self):
        verbs, needs_word_after = _verb_table()
        self.assertEqual(len(verbs), 110)
        self.assertEqual(needs_word_after, {"open", "close", "clean"})

    def test_program_does_not_decide_these(self):
        for text in (
            "They like cats.",
            "I like cats.",
            "Tom like cats.",
            "She can swim.",
            "She went home.",
            "She very happy.",
            "She read books.",
            "She likes cats.",
            "She is a student.",
            "She don't like cats.",
            "She, like cats.",
            "It clean.",
            "She like cats and play games.",
            "She like cats. He play football.",
            "She ___ cats. (like / likes)",
            "She like cats（like / likes）",
            "看中文写单词：书 b__k",
            "这句话没有英文",
            "",
        ):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    plan_correction(text)

    def test_adjective_like_verb_needs_a_word_after_it(self):
        self.assertEqual(plan_correction("He clean the room.")["right"], "cleans")

    def test_and_with_a_noun_is_fine(self):
        self.assertEqual(plan_correction("She like cats and dogs.")["right"], "likes")


class QuestionShapeTests(unittest.TestCase):
    def test_no_wording_asks_for_a_yes_or_no(self):
        for ask in _ASK_WRONG + _ASK_RIGHT:
            with self.subTest(ask=ask):
                for bad in ("要不要", "对不对", "是不是", "「要」", "一个人"):
                    self.assertNotIn(bad, ask)

    def test_every_verb_and_subject_runs_clean(self):
        verbs, _ = _verb_table()
        for subject in ("He", "She", "It"):
            for base, (form, _) in verbs.items():
                text = f"{subject} {base} the book."
                with self.subTest(text=text):
                    plan = plan_correction(text)
                    template = template_steps(plan)
                    ok, errors, _ = check_wording(template, plan)
                    self.assertEqual(errors, [])
                    self.assertTrue(ok)
                    steps = [
                        {"stepId": f"s{index}", "ask": planned["ask"], "expect": planned["expect"]}
                        for index, planned in enumerate(plan["steps"], start=1)
                    ]
                    self.assertTrue(check_session({"steps": steps})["checkPassed"])
                    events = record_replies({"steps": steps}, [base, form])
                    self.assertEqual([event["result"] for event in events], ["hit", "hit"])


class WordingCheckTests(unittest.TestCase):
    def setUp(self):
        self.plan = plan_correction(ITEM)

    def assert_rejected(self, wording, plan, needle):
        ok, errors, _ = check_wording(wording, plan)
        self.assertFalse(ok)
        self.assertTrue(any(needle in error for error in errors), errors)

    def test_sample_wording_passes(self):
        ok, errors, _ = check_wording(GOOD, self.plan)
        self.assertTrue(ok)
        self.assertEqual(errors, [])

    def test_model_cannot_write_the_question_or_answer(self):
        step = {"ask": "要不要加 s？说「要」或「不要」。", "expect": "要", "explain": "like 要加 s。"}
        self.assert_rejected({"steps": [step, GOOD["steps"][1]]}, self.plan, "只能有 explain")

    def test_first_question_must_not_say_likes(self):
        self.assert_rejected(_with_explain(0, "like 要改成 likes。"), self.plan, "likes")

    def test_each_explanation_must_say_its_own_answer(self):
        self.assert_rejected(_with_explain(0, "She 是她，一个人。"), self.plan, "没有说出答案")
        self.assert_rejected(_with_explain(1, "动词要加 s。"), self.plan, "没有说出答案")

    def test_she_called_wo_fails(self):
        self.assert_rejected(_with_explain(1, "She 是我，所以改成 likes。"), self.plan, "「我」")

    def test_not_me_is_allowed(self):
        ok, errors, _ = check_wording(_with_explain(1, "She 不是我，是她，句子里没有 can 帮忙，所以改成 likes。"), self.plan)
        self.assertEqual(errors, [])
        self.assertTrue(ok)

    def test_rule_must_say_when_it_applies(self):
        self.assert_rejected(_with_explain(1, "主语是 he、she、it，动词要加 s，所以改成 likes。"), self.plan, "什么时候才变")

    def test_template_carries_the_condition_and_a_counter_example(self):
        explain = template_steps(self.plan)["steps"][1]["explain"]
        self.assertIn("帮忙", explain)
        self.assertIn("She can run fast", explain)

    def test_be_verb_and_jargon_fail(self):
        wording = _with_explain(1, "第三人称用 is，所以改成 likes。")
        self.assert_rejected(wording, self.plan, "is")
        self.assert_rejected(wording, self.plan, "第三人称")

    def test_real_bad_outputs_from_the_last_run(self):
        self.assert_rejected(
            _with_explain(1, "一个人做动词：I like，2 个或更多的人做动词：they like。所以 like 要改成 likes。"),
            self.plan,
            "别的主语",
        )
        it_plan = plan_correction("It look nice.")
        self.assert_rejected(
            {"steps": [{"explain": "这句话里用错的是 look。"}, {"explain": "主语 It 是一个人，动词要变：一般直接加 s。look → looks。"}]},
            it_plan,
            "说成了人",
        )
        study_plan = plan_correction("He study English.")
        self.assert_rejected(
            {"steps": [{"explain": "这句话里用错的是 study。"}, {"explain": "一个人（I、he、she、we、they）做主语时，一般直接加 s。study → studies。"}]},
            study_plan,
            "只加 s",
        )
        go_plan = plan_correction("He go to school.")
        self.assert_rejected(
            {"steps": [{"explain": "这句话里用错的是 go。"}, {"explain": "He 说的是一个人（不是我，也不是你）。"}]},
            go_plan,
            "没有说出答案",
        )

    def test_v_3s_must_not_say_add_es(self):
        self.assert_rejected(_with_explain(1, "动词要加 es，所以改成 likes。"), self.plan, "加 es")


class RunCorrectionTests(unittest.TestCase):
    def test_first_draft_can_go_to_the_child(self):
        plan = plan_correction(ITEM)

        def write(message, grammar):
            self.assertIn("答案=likes", message)
            return _dump(GOOD["steps"])

        result = run_correction(ITEM, write)
        self.assertEqual(result["source"], "model")
        self.assertEqual(
            [step["ask"] for step in result["steps"]],
            [step["ask"] for step in plan["steps"]],
        )
        self.assertEqual(result["steps"][0]["explain"], GOOD["steps"][0]["explain"])
        session = {"steps": result["steps"]}
        self.assertTrue(check_session(session)["checkPassed"])
        self.assertEqual(judge("like", result["steps"][0]["expect"]), "hit")
        self.assertEqual(judge("likes", result["steps"][1]["expect"]), "hit")
        events = record_replies(session, ["like", "likes"])
        self.assertEqual([event["result"] for event in events], ["hit", "hit"])

    def test_retry_after_she_is_wo(self):
        calls = {"n": 0}

        def write(message, grammar):
            calls["n"] += 1
            if calls["n"] == 1:
                return _dump(_with_explain(1, "She 是我，所以改成 likes。")["steps"])
            self.assertIn("说成了「我」", message)
            return _dump(GOOD["steps"])

        result = run_correction(ITEM, write)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(result["source"], "model_retry")
        self.assertNotIn("是我", result["steps"][1]["explain"])

    def test_template_replaces_a_second_failure(self):
        def write(message, grammar):
            return _dump(_with_explain(1, "第三人称单数，所以改成 likes。")["steps"])

        result = run_correction(ITEM, write)
        self.assertEqual(result["source"], "template_patch")
        self.assertEqual(result["steps"][0]["explain"], GOOD["steps"][0]["explain"])
        self.assertNotIn("第三人称", result["steps"][1]["explain"])
        self.assertIn("likes", result["steps"][1]["explain"])
        self.assertNotIn("likes", result["steps"][0]["ask"] + result["steps"][0]["explain"])
        self.assertTrue(check_session({"steps": result["steps"]})["checkPassed"])

    def test_template_says_the_right_change_for_es(self):
        def write(message, grammar):
            return "不是 JSON"

        result = run_correction("She watch TV.", write)
        self.assertEqual(result["source"], "template")
        self.assertIn("加 es", result["steps"][1]["explain"])
        self.assertIn("watches", result["steps"][1]["explain"])
        self.assertNotIn("加 s", result["steps"][1]["explain"])


if __name__ == "__main__":
    unittest.main()
