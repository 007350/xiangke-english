"""Tutor one item, one reply at a time, from confirmed text to a finished record.

Replies that are not answers (asking, not knowing, other talk) do not count as
wrong. Only items whose answer the program computed are marked right or wrong.
"""

from __future__ import annotations

import re

from xiangke.intent import asks_why, explicit_intent
from xiangke.kernel import finish_session, judge, normalize_answer
from xiangke.reply import confirm_reply, finish_with_error_kinds, matches_final_answer, route_reply
from xiangke.teach import teach
from xiangke.transitions import transition

CANNOT_TEACH = "这道题我现在还不会讲，不是你的问题。可以检查一下题目文字，或者换一道题。"
NOT_SURE = "这道题我不一定对，可以问问老师。"
BE_RULE = "我（I）用 am，你（you）用 are，一个人或一样东西用 is，两个或更多用 are。先看主语是谁。"
SAFE_RULE = "先看看句子里的线索，再想一想。"
_ENGLISH_MEANINGS = {
    "i": {"我"}, "you": {"你", "你们"}, "he": {"他"}, "she": {"她"},
    "it": {"它"}, "we": {"我们"}, "they": {"他们", "她们", "它们"}, "like": {"喜欢"},
}


_ACK = {
    "why": ("嗯，这个值得弄明白。", "你问到点子上了。", "嗯，咱们把道理弄明白。"),
    "meaning": ("你想知道它的意思呀。", "嗯，先弄明白意思再做，是对的。"),
    "other": ("嗯，这个问题有意思。", "这个问题问得细。"),
}
_DEFER = (
    "这个我先记下来，去问问老师，联网了他写回信给你。",
    "我先记在小本子上，去问问老师，联网了他写回信给你。",
    "好，先记下来，我去问问老师，等联网了他写回信给你。",
)
_LEAD = ("那你说，", "来，", "回到这一问，")


def question_reply(said: str, ask: str, rule: str | None, count: int) -> str:
    """三拍：接住孩子的话，能答就答、答不了诚实推迟，再温和接回当前小问。"""
    said = said.strip()
    if asks_why(said):
        shape = "why"
    elif "意思" in said:
        shape = "meaning"
    else:
        shape = "other"
    acks = _ACK[shape]
    ack = acks[count % len(acks)]
    if said and len(said) <= 14:
        ack = f"「{said.rstrip('？?。！!')}」，{ack}"
    if shape == "why" and rule and rule != SAFE_RULE:
        body = rule
    else:
        body = _DEFER[count % len(_DEFER)]
    lead = _LEAD[count % len(_LEAD)]
    return f"{ack}{body}{lead}{ask.removeprefix('想一想，')}"


def rule_without_answer(lesson: dict, remaining: list[dict], said: str = "") -> str:
    """The rule to show when the child asks why. It must not give away an answer."""
    computed = lesson["computed"] or {}
    if computed.get("kind") == "be_verb":
        return BE_RULE
    rule = computed.get("rule", SAFE_RULE)
    counter = computed.get("counter", "")
    answers = {
        normalize_answer(step["expect"]) for step in remaining
        if re.fullmatch(r"[A-Za-z][A-Za-z'’]*", step["expect"].strip())
    }
    if computed.get("ruleKey") == "v_base_after" and asks_why(said):
        cue = normalize_answer(computed.get("cue", ""))
        if cue and cue != normalize_answer(computed["answer"]) and _has_english_word(normalize_answer(said), cue):
            answers.discard(cue)

    def leaks(text: str) -> bool:
        return any(_has_english_word(normalize_answer(text), word) for word in answers)

    # 三选一的口诀把选项都说了，不算说出答案；其他规则里出现答案就不能用
    if rule != BE_RULE and leaks(rule):
        return SAFE_RULE
    # 反例里碰巧有这道题的答案时，只去掉反例，规则还能讲
    if counter and leaks(counter):
        counter = ""
    return rule + counter


def _has_english_word(text: str, word: str) -> bool:
    return re.search(rf"(?<![A-Za-z']){re.escape(word)}(?![A-Za-z'])", text) is not None


def _first_hint(text: str, step: dict, lesson: dict, remaining: list[dict]) -> str:
    if lesson["computed"]["kind"] == "pack_spelling":
        return "从左往右数，只补这一格的一个字母。" if len(remaining) > 1 else "按顺序把原题和你补的字母连起来。"
    first_word = re.match(r"[A-Za-z][A-Za-z']*", text.lstrip())
    asks_for_subject = re.search(r"主语|说的是(?:谁|哪个人|哪个词)", step["ask"])
    if asks_for_subject and first_word and judge(first_word[0], step["expect"]) == "hit":
        return "从左往右看，先找句子开头的第一个词。"
    if "哪个词用错" in step["ask"] and (lesson["computed"] or {}).get("kind") == "verb_3s":
        return "先圈出表示动作的词，再看看这个词要不要变。"
    return rule_without_answer(lesson, remaining)


def _second_hint(text: str, step: dict, lesson: dict, remaining: list[dict], previous: dict | None) -> str:
    computed = lesson["computed"]
    if computed["kind"] == "pack_spelling":
        if len(remaining) > 1 and previous and previous["expect"] == step["expect"]:
            return "这一格和刚刚填对的那一格是同一个字母。"
        return f"这个词有 {len(computed['answer'])} 个字母。先数清已给的字母，再补缺的。"
    if computed["kind"] == "be_verb" and judge(step["expect"], computed["answer"]) == "hit":
        key = computed["ruleKey"]
        if key == "be_there":
            return "句首 There 表示「有」。先数一数空格后面说的东西。"
        if key == "be_i":
            return "I 说的是自己。回想三个选项里，哪个专门和 I 搭配。"
        if key == "be_you":
            return "you 指正在跟你说话的那个「你」。想想它通常和哪个选项搭配。"
        quantity = "两个或更多" if computed["answer"].casefold() == "are" else "一个"
        return f"这句话说的是{quantity}。再想想哪个选项和这个数量搭配。"
    if "哪个词用错" in step["ask"] and computed["kind"] == "verb_3s":
        return "先找句子里说的动作，再检查那个动作词和句子开头的人是否搭配。"
    return _first_hint(text, step, lesson, remaining)


class ItemSession:
    """One item in progress. ``reply`` takes one line from the child.

    ``mode`` is ``judged``, ``try`` or ``none``. ``outcome`` is ``None`` until
    the item ends, then ``finished``, ``assisted`` or ``cannot``.
    """

    def __init__(self, text: str, write, facts: dict, plan: dict | None = None, label_of=None,
                 known: dict | None = None, *, transition_write=None):
        self.facts = facts
        self.label_of = label_of
        self.transition_write = transition_write
        self.lesson = teach(text, write, plan, known, assessment=facts.get("source") == "transfer")
        self.steps = self.lesson["steps"] or []
        self.transcript: list[str] = []
        self.index = self.misses = self.stuck = self.questions = 0
        self.answer_shown = False
        self.shown_answers: set[int] = set()
        self.pending: str | None = None
        self.answers: list[str] = []
        self.log: list[tuple] = []
        self.note_kinds: dict[int, str] = {}
        self.events: list[dict] = []
        self.outcome: str | None = None
        self._new: list[str] = []
        if self.lesson["steps"] is None:
            self.mode = "none"
            self.outcome = "cannot"
            self._say(CANNOT_TEACH)
        elif not self.lesson["judged"]:
            self.mode = "try"
            self._say(NOT_SURE)
            self._say(self.steps[0]["ask"])
        else:
            self.mode = "judged"
            self._say(self.steps[0]["ask"])

    @property
    def done(self) -> bool:
        return self.outcome is not None

    @property
    def can_explain(self) -> bool:
        return self.mode == "judged" and not self.done and not self.answer_shown and (self.misses >= 2 or self.stuck >= 2)

    def reveal_explanation(self) -> list[str]:
        if not self.can_explain:
            return []
        self.pending = None
        self.answer_shown = True
        step = self.steps[self.index]
        self._note("看讲解", step, "explanation")
        self._say(f"先看这一步的讲解，再试着填一次。{step['explain']}")
        return self.take_messages()

    def take_messages(self) -> list[str]:
        """What the phone said since the last call."""
        new, self._new = self._new, []
        return new

    def reply(self, said: str) -> list[str]:
        if self.done:
            return []
        self.transcript.append(f"孩子：{said}")
        if self.mode == "try":
            self._reply_try(said)
        else:
            self._reply_judged(said)
        return self.take_messages()

    def defer_question(self, said: str) -> list[str]:
        """孩子明确点了记下疑惑，不依赖意图分类，也不把它当作答案。"""
        if self.done:
            return []
        self.transcript.append(f"孩子：{said}")
        step = self.steps[self.index]
        if self.mode == "try":
            self.events.append({"stepId": step["stepId"], "said": said, "result": "note", "hintUsed": 0, "noteKind": "question"})
        else:
            self._note(said, step, "question")
        self._say(question_reply(said, step["ask"], None, self.questions))
        self.questions += 1
        return self.take_messages()

    def record(self, ended_at: str | None = None) -> dict:
        """The record so far. Safe to call when the child leaves part way."""
        facts = {**self.facts}
        if ended_at:
            facts["endedAt"] = ended_at
        if self.mode == "none":
            record = finish_session({**facts, "steps": []}, [], CANNOT_TEACH)
            record["answerSource"] = "none"
            return record
        if self.mode == "try":
            record = finish_session({**facts, "steps": self.steps}, [])
            record["events"] = [dict(event) for event in self.events]
            record["answerSource"] = "model"
            return record
        record = finish_with_error_kinds({**facts, "steps": self.steps}, self.answers)
        record["events"] = _merge_notes(record["events"], self.log, self.note_kinds)
        for index in self.shown_answers:
            record["events"][index]["answerShown"] = True
        if "appliedTopicPlan" in self.lesson:
            record["appliedTopicPlan"] = self.lesson["appliedTopicPlan"]
        record["answerSource"] = "cloud" if self.lesson["computed"].get("kind") == "cloud" else "program"
        return record

    def _say(self, text: str) -> None:
        self.transcript.append(f"手机：{text}")
        self._new.append(text)

    def _reply_judged(self, said: str) -> None:
        step = self.steps[self.index]
        hit = judge(said, step["expect"]) == "hit"
        computed = self.lesson["computed"]
        spelling_letter = computed["kind"] == "pack_spelling" and self.index < len(self.steps) - 1
        if spelling_letter and judge(said, computed["answer"]) == "hit":
            self._note(said, step)
            self._say("你写的是完整单词。这一步只写缺的一个字母。")
            return
        asks_for_word = any(marker in step["ask"] for marker in ("英文", "哪个词", "哪个单词"))
        if not hit and asks_for_word and normalize_answer(said) in _ENGLISH_MEANINGS.get(
            normalize_answer(step["expect"]), ()
        ):
            self.pending = None
            self._note(said, step)
            self._say("意思对了，试着写出那个英文词。")
            return
        if (
            not spelling_letter
            and self.index < len(self.steps) - 1
            and not hit
            and matches_final_answer(said, self.lesson["computed"]["answer"])
        ):
            self.pending = None
            self._note(said, step)
            self._say(transition("early", said, step["ask"], self.transition_write,
                                 "最后一空你选对了。我们回头看看这一小问。"))
            self._say(step["ask"])
            return
        pending, self.pending = self.pending, None
        route = confirm_reply(said, pending, step["expect"]) if pending is not None else None
        confirmed = route is not None and route.get("result") is not None
        # 等确认时也可以求助。只有明确的肯定或否定才按确认处理。
        if route is None or (not confirmed and route["action"] in ("confirm", "miss")):
            label = None if hit else explicit_intent(said)
            if label is None and self.label_of and not hit:
                label = self.label_of(step["ask"], said)
            route = route_reply(said, step["expect"], label)

        action = route["action"]
        if action not in ("hit", "miss"):
            self._note(said, step, {"hint": "stuck", "show_rule": "question"}.get(action))
        if action == "ask_again":
            self._say(f"好的，再想想。{step['ask']}")
            return
        if action == "confirm":
            self.pending = route["candidate"]
            self._say(f"你是想说 {self.pending} 吗？")
            return
        if action == "hint":
            self.stuck += 1
            if self.stuck >= 2:
                self._say(self._deeper_hint(step))
            else:
                hint = _first_hint(self.facts["recognizedText"], step, self.lesson, self.steps[self.index:])
                self._say(f"没关系，慢慢想。{hint}")
            return
        if action == "show_rule":
            rule = rule_without_answer(self.lesson, self.steps[self.index:], said)
            self._say(question_reply(said, step["ask"], rule, self.questions))
            self.questions += 1
            return
        if action == "redirect":
            self._say(f"我们先把这一问做完。{step['ask']}")
            return

        answer = pending if confirmed else said
        self.answers.append(answer)
        self.log.append(("answer",))
        if judge(answer, step["expect"]) == "hit":
            shown = self.answer_shown
            if shown:
                self.shown_answers.add(len(self.log) - 1)
            helped = self.misses > 0 or self.stuck > 0
            self.index += 1
            self.misses = self.stuck = 0
            self.answer_shown = False
            feedback = "跟着讲解完成了这一问。" if shown else "根据提示改对了！" if helped else "答对了！"
            if self.index < len(self.steps):
                ask = self.steps[self.index]["ask"]
                self._say(feedback if helped else transition("next", said, ask, self.transition_write, feedback))
                self._say(ask)
            else:
                self._say(feedback)
                self.outcome = "finished"
            return
        self.misses += 1
        if self.misses == 1:
            hint = _first_hint(self.facts["recognizedText"], step, self.lesson, self.steps[self.index:])
            lead = "这一格的字母还不对。" if spelling_letter else "再试一次。"
            self._say(lead + hint)
        elif self.misses == 2:
            self._say(self._deeper_hint(step))
        else:
            self._say("这一问先到这里，我们请老师再讲讲。")
            self.outcome = "assisted"

    def _deeper_hint(self, step: dict) -> str:
        previous = self.steps[self.index - 1] if self.index else None
        hint = _second_hint(self.facts["recognizedText"], step, self.lesson, self.steps[self.index:], previous)
        return f"{hint}还想看看这一步怎么做，可以点「看讲解」。" if self.can_explain else hint

    def _note(self, said: str, step: dict, kind: str | None = None) -> None:
        if kind:
            self.note_kinds[len(self.log)] = kind
        self.log.append(("note", said, step["stepId"], self.misses))

    def _reply_try(self, said: str) -> None:
        """Nothing is judged and every reply becomes a note.

        At the end the child hears the model's answer without its reasons, since
        the reasons are what it most often gets wrong.
        """
        step = self.steps[self.index]
        self.events.append({"stepId": step["stepId"], "said": said, "result": "note", "hintUsed": 0})
        label = explicit_intent(said)
        if label is None and self.label_of:
            label = self.label_of(step["ask"], said)
        if label in ("提问", "不会"):
            self.events[-1]["noteKind"] = "question" if label == "提问" else "stuck"
        if label == "提问":
            self._say(question_reply(said, step["ask"], None, self.questions))
            self.questions += 1
            return
        if label == "其他":
            self._say(f"我们先把这一问做完。{step['ask']}")
            return
        if label == "不会" and self.stuck == 0:
            self.stuck = 1
            self._say(f"没关系，慢慢想。{step['ask']}")
            return
        self._say("这一问先放一放。" if label == "不会" else "记下了。")
        self.index += 1
        self.stuck = 0
        if self.index < len(self.steps):
            self._say(self.steps[self.index]["ask"])
        else:
            self._say(f"我觉得答案可能是 {self.steps[-1]['expect']}，但我不一定对，明天可以问问老师。")
            self.outcome = "finished"


def run_item(text: str, replies: list[str], write, facts: dict, plan: dict | None = None, label_of=None) -> dict:
    """Run a list of replies through ``ItemSession``. Used by tests and scripts."""
    item = ItemSession(text, write, facts, plan, label_of)
    for said in replies:
        if item.done:
            break
        item.reply(said)
    return {"lesson": item.lesson, "said": list(item.answers), "record": item.record(), "transcript": item.transcript}


def _merge_notes(events: list[dict], log: list[tuple], note_kinds: dict[int, str] | None = None) -> list[dict]:
    """Put replies that are not answers between the judged events, in the order they happened."""
    judged = iter(events)
    merged = []
    for index, entry in enumerate(log):
        if entry[0] == "answer":
            merged.append(next(judged))
        else:
            _, said, step_id, misses = entry
            note = {"stepId": step_id, "said": said, "result": "note", "hintUsed": misses}
            if note_kinds and index in note_kinds:
                note["noteKind"] = note_kinds[index]
            merged.append(note)
    merged.extend(judged)
    return merged
