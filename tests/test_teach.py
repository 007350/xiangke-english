import json
import unittest

from xiangke.answers import compute_answer
from xiangke.intent import classify, make_label_of
from xiangke.one_item import SAFE_RULE, rule_without_answer, run_item
from xiangke.teach import answer_grammar, check_talk, program_steps, repair_terms, teach, user_message

FACTS = {
    "sessionId": "t1",
    "childId": "c1",
    "planVersion": 1,
    "source": "photo",
    "recognizedText": "They ___ my friends. (am / is / are)",
    "confirmed": True,
    "offline": True,
    "startedAt": "2026-11-04T19:00:00+08:00",
    "endedAt": "2026-11-04T19:01:00+08:00",
}
TEXT = FACTS["recognizedText"]
GOOD = {
    "steps": [
        {"ask": "这句话的主语是哪个词？", "expect": "They", "explain": "这句话的主语是 They。"},
        {"ask": "那空里填 am、is 还是 are？", "expect": "are", "explain": "They 是两个或更多，所以填 are。"},
    ]
}


def writer(result):
    def write(system, user, grammar):
        return json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else result

    return write


class ComputeTests(unittest.TestCase):
    def test_be_verb(self):
        cases = {
            "They ___ my friends. (am / is / are)": "are",
            "I ___ a student. (am / is / are)": "am",
            "This ___ my book. (am / is / are)": "is",
            "Tom and Lily ___ students. (am / is / are)": "are",
            "My mother ___ a teacher. (am / is / are)": "is",
            "The books ___ on the desk. (am / is / are)": "are",
            "Mr Li ___ here. (am / is / are)": "is",
            "There ___ a bird in the tree. (is / are)": "is",
            "There ___ two apples here. (is / are)": "are",
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(compute_answer(text)["answer"], want)

    def test_plural(self):
        cases = {
            "This is an ___. (apple / apples)": "apple",
            "I have two ___. (cat / cats)": "cats",
            "We have five ___. (box / boxes)": "boxes",
            "There are two ___ here. (baby / babies)": "babies",
            "Those are my ___. (shoe / shoes)": "shoes",
            "This is my ___. (shoe / shoes)": "shoe",
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(compute_answer(text)["answer"], want)

    def test_verb_choice(self):
        cases = {
            "She can ___. (swim / swims)": "swim",
            "He wants to ___ a book. (read / reads)": "read",
            "Does she ___ milk? (like / likes)": "like",
            "My aunt doesn't ___ coffee. (drink / drinks)": "drink",
            "The dog ___ fast. (run / runs)": "runs",
            "My uncle ___ a truck. (drive / drives)": "drives",
            "The baby ___ at night. (cry / cries)": "cries",
            "They ___ to school. (go / goes)": "go",
            "My parents ___ in a village. (live / lives)": "live",
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(compute_answer(text)["answer"], want)

    def test_refuses_what_it_cannot_decide(self):
        for text in (
            "The fish ___ in the water. (is / are)",
            "The people ___ here. (am / is / are)",
            "There ___ some water. (is / are)",
            "Look at those ___. (child / children)",
            "The fish ___ fast. (swim / swims)",
            "Can you see the ___? They are red. (flowers / flower)",
            "Tom's ___ is big. (book / books)",
            "I go to ___ every day. (school / schools)",
            "She ___ to school yesterday. (go / goes)",
            "The ___ are under the chair. (egg / eggs)",
            "看中文写单词：书 b__k",
            "随便一句话",
        ):
            with self.subTest(text=text):
                self.assertIsNone(compute_answer(text))


class CheckTalkTests(unittest.TestCase):
    def setUp(self):
        self.computed = compute_answer(TEXT)

    def errors(self, steps, text=TEXT, computed=None):
        return check_talk(json.dumps({"steps": steps}, ensure_ascii=False), text, computed or self.computed)[1]

    def test_good_wording_passes(self):
        self.assertEqual(self.errors(GOOD["steps"]), [])

    def test_too_many_steps(self):
        extra = {"ask": "再想想？", "expect": "x", "explain": "想想。"}
        steps = [extra, extra, extra, GOOD["steps"][-1]]
        self.assertIn("问得太多", self.errors(steps))

    def test_explanation_that_picks_the_wrong_option(self):
        steps = [{"ask": "空里选什么？", "expect": "are", "explain": "所以填 is，are。"}]
        self.assertTrue(any("讲成了 is" in e for e in self.errors(steps)))

    def test_middle_step_with_wrong_option(self):
        steps = [
            {"ask": "先选一个？", "expect": "is", "explain": "这里用 is。"},
            GOOD["steps"][-1],
        ]
        self.assertTrue(any("中间一问" in e for e in self.errors(steps)))

    def test_first_question_of_a_fix_item_may_ask_for_the_wrong_word(self):
        text = "改错（只改一个词）：She like cats."
        steps = [
            {"ask": "哪个词错了？", "expect": "like", "explain": "错的是 like。"},
            {"ask": "改成什么？", "expect": "likes", "explain": "She 是一个人，动词要加 s，所以改成 likes。"},
        ]
        self.assertEqual(self.errors(steps, text, compute_answer(text)), [])
        steps[1:1] = [{"ask": "再说一遍错词？", "expect": "like", "explain": "还是 like。"}]
        self.assertTrue(any("中间一问" in e for e in self.errors(steps, text, compute_answer(text))))

    def test_she_called_me(self):
        text = "改错（只改一个词）：She like cats."
        computed = compute_answer(text)
        steps = [
            {"ask": "哪个词错了？", "expect": "like", "explain": "错的是 like。"},
            {"ask": "主语是哪一种？", "expect": "我", "explain": "She 说的是「我」。"},
            {"ask": "改成什么？", "expect": "likes", "explain": "所以改成 likes。"},
        ]
        self.assertIn("把主语说成了我", self.errors(steps, text, computed))

    def test_saying_she_is_not_me_is_fine(self):
        text = "改错（只改一个词）：She eat noodles."
        steps = [
            {"ask": "哪个词错了？", "expect": "eat", "explain": "错的是 eat。"},
            {"ask": "主语是哪一种？", "expect": "一个", "explain": "She 说的是「她」，是一个人，不是「我」，也不是好几个人。"},
            {"ask": "改成什么？", "expect": "eats", "explain": "She 是一个人，动词要变，一般直接加 s：eat → eats。"},
        ]
        self.assertEqual(self.errors(steps, text, compute_answer(text)), [])

    def test_i_may_be_called_me(self):
        text = "I ___ a student. (am / is / are)"
        steps = [
            {"ask": "主语是哪一种？", "expect": "我", "explain": "I 就是「我」自己。"},
            {"ask": "填什么？", "expect": "am", "explain": "「我」用 am，所以填 am。"},
        ]
        self.assertEqual(self.errors(steps, text, compute_answer(text)), [])

    def test_real_bad_reason_is_refused(self):
        steps = [{"ask": "空里选什么？", "expect": "are", "explain": "句子里还有 cousins，所以填 are。"}]
        self.assertTrue(any("理由不可靠" in e for e in self.errors(steps)))

    def test_real_slipped_reasons_are_caught(self):
        step = lambda ask, expect, explain: {"ask": ask, "expect": expect, "explain": explain}
        cases = [
            ("Mary will ___ a picture. (paint / paints)", [
                step("Mary 后面的动词要不要变形？", "要", "要加 s 或 es。"),
                step("选哪个？", "paint", "动词要变：一般直接加 s。paint → paint，所以填 paint。"),
            ], "paint"),
            ("Lucy can ___ the song. (sing / sings)", [
                step("Lucy 后面的动词要不要变形？", "不要", "Lucy 是一个别人，动词用原形。"),
                step("选哪个？", "sing", "主语 Lucy 是一个别人，动词用原形，不加 s，所以填 sing。"),
            ], "没有讲到 can"),
            ("The baby ___ at night. (cry / cries)", [
                step("The baby 后面的动词要不要变形？", "不要", "动词用原形。"),
                step("选哪个？", "cries", "动词用原形，不加 s：cry → cries。"),
            ], "该变形"),
            ("Mr Li ___ science. (teach / teaches)", [
                step("选哪个？", "teaches", "主语是一个，动词要变：一般直接加 s。teach → teaches。"),
            ], "直接加 s"),
            ("I have three ___. (hat / hats)", [
                step("选哪个？", "hats", "名词要用复数。以 s、x、ch、sh 结尾的名词加 es。hat → hats，所以填 hats。"),
            ], "加 es"),
        ]
        for text, steps, needle in cases:
            with self.subTest(text=text):
                errors = self.errors(steps, text, compute_answer(text))
                self.assertTrue(any(needle in e for e in errors), errors)

    def test_good_reasons_still_pass(self):
        step = lambda ask, expect, explain: {"ask": ask, "expect": expect, "explain": explain}
        cases = [
            ("Lucy can ___ the song. (sing / sings)", [
                step("空格前面的词是什么？", "can", "空格前面的词是 can。"),
                step("选哪个？", "sing", "can 后面的动词用原来的样子，所以填 sing。"),
            ]),
            ("My aunt ___ the windows. (wash / washes)", [
                step("选哪个？", "washes", "主语是一个，动词要变。以 sh 结尾的加 es：wash → washes。"),
            ]),
            ("My uncle ___ a truck. (drive / drives)", [
                step("选哪个？", "drives", "主语是一个，动词要变：一般直接加 s。drive → drives。"),
            ]),
        ]
        for text, steps in cases:
            with self.subTest(text=text):
                self.assertEqual(self.errors(steps, text, compute_answer(text)), [])

    def test_term_is_repaired_not_refused(self):
        steps = repair_terms([{"ask": "?", "expect": "a", "explain": "Mary 是第三人称单数，动词要变。"}])
        self.assertEqual(steps[0]["explain"], "Mary 是「一个别人」，动词要变。")


class MessageTests(unittest.TestCase):
    def test_answer_line_and_grammar(self):
        message = user_message(TEXT, {"voice": "讲法", "reminders": ["错过一次"], "forbid": "禁令", "examples": []}, "are")
        lines = message.split("\n")
        self.assertEqual(lines[1], "这道题的最后答案（程序已算出）：are")
        self.assertIn("孩子犯过的错（reminders）：错过一次", message)
        self.assertNotIn("拆题样例", message)
        self.assertIn('\\"are\\"', answer_grammar("are"))
        self.assertIn('\\"doesn\'t\\"', answer_grammar("doesn't"))

    def test_no_answer_line_when_model_tries_alone(self):
        message = user_message("She can ___. (swim / swims)", {"voice": "v", "reminders": [], "forbid": "f"}, None)
        self.assertNotIn("最后答案", message)
        self.assertIn("孩子犯过的错（reminders）：（无）", message)


class TeachTests(unittest.TestCase):
    def test_model_wording_is_used_when_it_passes(self):
        lesson = teach(TEXT, writer(GOOD))
        self.assertEqual(lesson["source"], "model")
        self.assertTrue(lesson["judged"])
        self.assertEqual(lesson["steps"][0]["stepId"], "s1")

    def test_program_wording_replaces_a_refused_lesson(self):
        bad = {"steps": [{"ask": "选什么？", "expect": "are", "explain": "句子里还有 friends，所以填 are。"}]}
        lesson = teach(TEXT, writer(bad))
        self.assertEqual(lesson["source"], "program")
        self.assertEqual(lesson["steps"][-1]["expect"], "are")
        self.assertTrue(lesson["errors"])

    def test_program_wording_for_broken_output(self):
        self.assertEqual(teach(TEXT, writer("坏掉了"))["source"], "program")

    def test_model_try_calling_mary_me_is_not_shown(self):
        text = "改错（只改一个词）：Mary don't eat meat."
        answer = {"steps": [
            {"ask": "主语是哪一种？", "expect": "我", "explain": "Mary 是「我」。"},
            {"ask": "改成什么？", "expect": "don't", "explain": "不用改。"},
        ]}
        lesson = teach(text, writer(answer))
        self.assertEqual(lesson["source"], "none")
        self.assertIsNone(lesson["steps"])

    def test_every_kind_has_program_wording(self):
        for text in (TEXT, "This is an ___. (apple / apples)", "改错（只改一个词）：She like cats."):
            with self.subTest(text=text):
                computed = compute_answer(text)
                steps = program_steps(text, computed)
                self.assertEqual(steps[-1]["expect"], computed["answer"])

    def test_text_that_is_not_an_item_never_reaches_the_model(self):
        def write(*_):
            raise AssertionError("不该问模型")

        for text in ("今天天气很好，大家一起去玩。", "My name is Tom.", "Hello", "老师说明天考试 (真的)"):
            with self.subTest(text=text):
                lesson = teach(text, write)
                self.assertEqual(lesson["source"], "none")
                self.assertIsNone(lesson["steps"])

    def test_items_with_a_mark_may_reach_the_model(self):
        for text in ("看中文写单词：书 bo_k", "改错：Tom play games.", "My brother ___ (like) bananas."):
            with self.subTest(text=text):
                self.assertEqual(teach(text, writer("坏掉了"))["errors"], ["不是合法 JSON"])

    def test_model_only_item_is_not_judged(self):
        text = "看中文写单词：书 bo_k"
        answer = {"steps": [{"ask": "缺什么字母？", "expect": "o", "explain": "这里写 o。"}]}
        lesson = teach(text, writer(answer))
        self.assertEqual(lesson["source"], "model_try")
        self.assertFalse(lesson["judged"])


class IntentTests(unittest.TestCase):
    def test_one_english_word_does_not_ask_the_model(self):
        def write(*_):
            raise AssertionError("不该问模型")

        for said in ("likes", "liked.", "Doesn't", "  are？"):
            with self.subTest(said=said):
                self.assertIsNone(classify("问", said, write))

    def test_model_label_is_used(self):
        def write(system, user, grammar, limit):
            self.assertIn("这一问：问", user)
            self.assertIn("孩子说：为啥要改", user)
            return '{"label": "提问"}'

        self.assertEqual(classify("问", "为啥要改", write), "提问")

    def test_bad_model_reply_falls_back_to_answer(self):
        def broken(*_):
            raise OSError("server down")

        self.assertIsNone(classify("问", "不知道", broken))
        self.assertIsNone(classify("问", "不知道", lambda *_: "坏了"))
        self.assertIsNone(classify("问", "不知道", lambda *_: '{"label": "别的"}'))
        self.assertIsNone(classify("问", "不知道", lambda *_: "[1]"))


class RunItemTests(unittest.TestCase):
    def test_model_intent_flows_through(self):
        labels = {"不知道": "不会", "为啥": "提问", "我饿了": "其他"}

        def write(system, user, grammar, limit=512):
            if "孩子说：" in user:
                return json.dumps({"label": labels[user.split("孩子说：")[1]]}, ensure_ascii=False)
            return json.dumps(GOOD, ensure_ascii=False)

        result = run_item(TEXT, ["They", "不知道", "为啥", "我饿了", "are"], write, FACTS, label_of=make_label_of(write))
        events = result["record"]["events"]
        self.assertEqual([e["result"] for e in events], ["hit", "note", "note", "note", "hit"])
        self.assertEqual([e["said"] for e in events], ["They", "不知道", "为啥", "我饿了", "are"])
        self.assertEqual({e["stepId"] for e in events[1:]}, {"s2"})
        joined = "\n".join(result["transcript"])
        self.assertIn("没关系，慢慢想", joined)
        self.assertIn("先看主语是谁", joined)
        self.assertIn("我们先把这一问做完", joined)

    def test_rule_shown_for_a_question_never_gives_the_answer(self):
        cases = [
            ("改错（只改一个词）：He go to school.", "goes"),
            ("This is an ___. (apple / apples)", "apple"),
            ("She can ___. (swim / swims)", "swim"),
            ("My uncle ___ a truck. (drive / drives)", "drives"),
            ("There ___ two apples here. (is / are)", None),
        ]
        for text, answer in cases:
            with self.subTest(text=text):
                computed = compute_answer(text)
                remaining = [{"expect": computed["answer"]}]
                rule = rule_without_answer({"computed": computed}, remaining)
                if answer:
                    self.assertNotRegex(rule, rf"(?<![A-Za-z]){answer}(?![A-Za-z])")

    def test_answer_inside_a_rule_uses_the_safe_rule(self):
        computed = {"kind": "x", "rule": "所以这里写 goes。"}
        self.assertEqual(rule_without_answer({"computed": computed}, [{"expect": "goes"}]), SAFE_RULE)

    def test_saying_i_dont_know_twice_shows_the_explanation(self):
        result = run_item(TEXT, ["They", "不知道", "还是不会"], writer(GOOD), FACTS, label_of=lambda q, s: "不会")
        self.assertEqual(result["transcript"][-1], "手机：" + GOOD["steps"][1]["explain"])
        self.assertEqual([e["result"] for e in result["record"]["events"]], ["hit", "note", "note"])

    def test_notes_never_change_the_judged_results(self):
        result = run_item(TEXT, ["为什么", "They", "为什么", "is", "为什么", "are"], writer(GOOD), FACTS,
                          label_of=lambda q, s: "提问" if "为什么" in s else "答题")
        events = result["record"]["events"]
        self.assertEqual([e["result"] for e in events], ["note", "hit", "note", "miss", "note", "hit"])
        self.assertEqual([e["hintUsed"] for e in events], [0, 0, 0, 1, 1, 1])
        self.assertNotIn("errorKind", events[0])
        self.assertEqual(events[3]["errorKind"], "wrong_option")

    def test_new_answer_during_confirm_is_judged_not_the_old_word(self):
        result = run_item(TEXT, ["They", "应该是 is", "are"], writer(GOOD), FACTS)
        events = result["record"]["events"]
        self.assertEqual([e["said"] for e in events], ["They", "应该是 is", "are"])
        self.assertEqual([e["result"] for e in events], ["hit", "note", "hit"])

    def test_yes_during_confirm_judges_the_word(self):
        result = run_item(TEXT, ["They", "应该是 is", "是", "are"], writer(GOOD), FACTS)
        self.assertEqual([e["result"] for e in result["record"]["events"]], ["hit", "note", "miss", "hit"])

    def test_no_during_confirm_is_a_note(self):
        result = run_item(TEXT, ["They", "应该是 is", "不是", "are"], writer(GOOD), FACTS)
        self.assertEqual([e["result"] for e in result["record"]["events"]], ["hit", "note", "note", "hit"])


    def test_full_item_with_a_wrapped_answer(self):
        result = run_item(TEXT, ["They", "是 are 吧", "是"], writer(GOOD), FACTS)
        record = result["record"]
        self.assertEqual([e["result"] for e in record["events"]], ["hit", "note", "hit"])
        self.assertEqual(record["events"][1]["said"], "是 are 吧")
        self.assertTrue(record["checkPassed"])
        self.assertIn("手机：你是想说 are 吗？", result["transcript"])

    def test_wrong_answers_are_recorded_with_a_kind(self):
        result = run_item(TEXT, ["They", "is", "are"], writer(GOOD), FACTS)
        events = result["record"]["events"]
        self.assertEqual([e["result"] for e in events], ["hit", "miss", "hit"])
        self.assertEqual(events[1]["errorKind"], "wrong_option")

    def test_asking_does_not_count_as_wrong(self):
        result = run_item(TEXT, ["They", "为什么", "are"], writer(GOOD), FACTS, label_of=lambda q, s: "提问")
        self.assertEqual([e["result"] for e in result["record"]["events"]], ["hit", "note", "hit"])
        joined = "\n".join(result["transcript"])
        self.assertIn("先看主语是谁", joined)
        self.assertNotIn("主语是 they，用 are", joined)

    def test_third_miss_stops_with_assisted(self):
        result = run_item(TEXT, ["They", "is", "am", "is", "are"], writer(GOOD), FACTS)
        self.assertEqual([e["result"] for e in result["record"]["events"]], ["hit", "miss", "miss", "assisted"])

    def try_lesson(self):
        return {"steps": [
            {"ask": "缺什么字母？", "expect": "o", "explain": "理由可能讲错。"},
            {"ask": "完整拼出来？", "expect": "book", "explain": "理由可能讲错。"},
        ]}

    def test_model_try_is_recorded_but_never_judged(self):
        text = "看中文写单词：书 bo_k"
        result = run_item(text, ["a", "book"], writer(self.try_lesson()), {**FACTS, "recognizedText": text})
        record = result["record"]
        self.assertEqual(record["answerSource"], "model")
        self.assertEqual([e["result"] for e in record["events"]], ["note", "note"])
        self.assertEqual([e["said"] for e in record["events"]], ["a", "book"])
        self.assertIn("不一定对", result["transcript"][0])

    def test_model_try_shows_the_answer_without_reasons(self):
        text = "看中文写单词：书 bo_k"
        result = run_item(text, ["o", "book"], writer(self.try_lesson()), {**FACTS, "recognizedText": text})
        joined = "\n".join(result["transcript"])
        self.assertIn("我觉得答案可能是 book，但我不一定对", joined)
        self.assertNotIn("理由可能讲错", joined)

    def test_model_try_understands_the_child(self):
        text = "看中文写单词：书 bo_k"
        labels = {"为啥": "提问", "我饿了": "其他", "不知道": "不会", "还是不会": "不会"}
        result = run_item(text, ["为啥", "我饿了", "不知道", "还是不会", "book"], writer(self.try_lesson()),
                          {**FACTS, "recognizedText": text}, label_of=lambda q, s: labels.get(s))
        steps = [e["stepId"] for e in result["record"]["events"]]
        self.assertEqual(steps, ["s1", "s1", "s1", "s1", "s2"])
        self.assertIn("手机：这一问先放一放。", result["transcript"])

    def test_program_items_say_program(self):
        result = run_item(TEXT, ["They", "are"], writer(GOOD), FACTS)
        self.assertEqual(result["record"]["answerSource"], "program")

    def test_item_that_cannot_be_taught_still_has_a_record(self):
        text = "随便一句话"
        result = run_item(text, ["不知道"], writer("坏掉了"), {**FACTS, "recognizedText": text})
        record = result["record"]
        self.assertEqual(record["answerSource"], "none")
        self.assertFalse(record["checkPassed"])
        self.assertEqual(record["checkReasons"], ["empty_steps"])
        self.assertEqual([e["result"] for e in record["events"]], ["note"])
        self.assertIn("不是你的问题", result["transcript"][0])


if __name__ == "__main__":
    unittest.main()
