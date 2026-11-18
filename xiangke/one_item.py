"""Tutor one item, one reply at a time, from confirmed text to a finished record.

Replies that are not answers (asking, not knowing, other talk) do not count as
wrong. Only items whose answer the program computed are marked right or wrong.
"""

from __future__ import annotations

import re

from xiangke.kernel import finish_session, judge
from xiangke.reply import confirm_reply, finish_with_error_kinds, route_reply
from xiangke.teach import teach

CANNOT_TEACH = "这道题我现在还不会讲，不是你的问题。可以检查一下题目文字，或者换一道题。"
NOT_SURE = "这道题我不一定对，可以问问老师。"
BE_RULE = "我（I）用 am，你（you）用 are，一个人或一样东西用 is，两个或更多用 are。先看主语是谁。"
SAFE_RULE = "先看看句子里的线索，再想一想。"


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
    if any(word in said for word in ("为什么", "为啥", "为何")):
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


def rule_without_answer(lesson: dict, remaining: list[dict]) -> str:
    """The rule to show when the child asks why. It must not give away an answer."""
    computed = lesson["computed"] or {}
    if computed.get("kind") == "be_verb":
        rule = BE_RULE
    else:
        rule = computed.get("rule", SAFE_RULE)
    answers = {step["expect"] for step in remaining if re.fullmatch(r"[A-Za-z']+", step["expect"])}
    # 三选一的口诀把选项都说了，不算说出答案；其他规则里出现答案就不能用
    if rule != BE_RULE and any(re.search(r"(?<![A-Za-z])" + re.escape(word) + r"(?![A-Za-z])", rule, re.I) for word in answers):
        return SAFE_RULE
    return rule


class ItemSession:
    """One item in progress. ``reply`` takes one line from the child.

    ``mode`` is ``judged``, ``try`` or ``none``. ``outcome`` is ``None`` until
    the item ends, then ``finished``, ``assisted`` or ``cannot``.
    """

    def __init__(self, text: str, write, facts: dict, plan: dict | None = None, label_of=None, known: dict | None = None):
        self.facts = facts
        self.label_of = label_of
        self.lesson = teach(text, write, plan, known)
        self.steps = self.lesson["steps"] or []
        self.transcript: list[str] = []
        self.index = self.misses = self.stuck = self.questions = 0
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
        record["answerSource"] = "cloud" if self.lesson["computed"].get("kind") == "cloud" else "program"
        return record

    def _say(self, text: str) -> None:
        self.transcript.append(f"手机：{text}")
        self._new.append(text)

    def _reply_judged(self, said: str) -> None:
        step = self.steps[self.index]
        if self.pending is not None:
            outcome = confirm_reply(said, self.pending, step["expect"])
            action = outcome["action"]
            # 孩子答“是”才判刚才那个词；直接说了新答案就判新答案
            answer = self.pending if outcome.get("result") else said
            self.pending = None
            if action == "ask_again":
                self._note(said, step)
                self._say(f"好的，再想想。{step['ask']}")
                return
            if action == "confirm":
                self._note(said, step)
                self.pending = outcome["candidate"]
                self._say(f"你是想说 {self.pending} 吗？")
                return
            if action not in ("hit", "miss"):
                return
        else:
            label = self.label_of(step["ask"], said) if self.label_of and judge(said, step["expect"]) != "hit" else None
            route = route_reply(said, step["expect"], label)
            action = route["action"]
            if action in ("confirm", "hint", "show_rule", "redirect"):
                self._note(said, step, {"hint": "stuck", "show_rule": "question"}.get(action))
            if action == "confirm":
                self.pending = route["candidate"]
                self._say(f"你是想说 {self.pending} 吗？")
                return
            if action == "hint":
                self.stuck += 1
                # 说第二次不会，就把解释给他看，不让他卡在这一问
                if self.stuck >= 2:
                    self._say(step["explain"])
                else:
                    self._say(f"没关系，慢慢想。{step['ask']}")
                return
            if action == "show_rule":
                rule = rule_without_answer(self.lesson, self.steps[self.index:])
                self._say(question_reply(said, step["ask"], rule, self.questions))
                self.questions += 1
                return
            if action == "redirect":
                self._say(f"我们先把这一问做完。{step['ask']}")
                return
            answer = said

        self.answers.append(answer)
        self.log.append(("answer",))
        if judge(answer, step["expect"]) == "hit":
            self._say("答对了！")
            self.index += 1
            self.misses = self.stuck = 0
            if self.index < len(self.steps):
                self._say(self.steps[self.index]["ask"])
            else:
                self.outcome = "finished"
            return
        self.misses += 1
        if self.misses == 1:
            self._say("还不对，再想想。")
        elif self.misses == 2:
            self._say(step["explain"])
        else:
            self._say("这一问先到这里，我们请老师再讲讲。")
            self.outcome = "assisted"

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
        label = self.label_of(step["ask"], said) if self.label_of else None
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
