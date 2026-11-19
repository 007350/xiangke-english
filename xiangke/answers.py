"""Compute the answer from the confirmed text. Return ``None`` when unsure.

No stored answer is read here. A wrong computed answer would mark a right child
wrong, so every rule refuses anything it cannot decide from the words on the page.
"""

from __future__ import annotations

import re

from xiangke.rules import V_3S, V_3S_COUNTER, V_BASE_AFTER_COUNTER
from xiangke.verb_3s import plan_correction

_BLANK_OPTIONS = re.compile(r"\b_{2,}\s*(?P<after>[^()（）]*?)\s*[(（](?P<options>[^)）]*/[^)）]*)[)）]\s*$")
_WORD = re.compile(r"[A-Za-z][A-Za-z']*")
_NUMBERS = {"two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "twenty"}
_MANY = _NUMBERS | {"many", "these", "those", "both", "several"}
_ONE = {"a", "an", "one", "this", "that", "each", "every"}
_DETERMINERS = {"a", "an", "the", "my", "your", "his", "her", "our", "their", "its", "this", "that"}
_TITLES = {"mr", "mrs", "miss", "ms"}
_BE_RULES = {
    "i": ("am", "be_i", "主语是 I，用 am。"),
    "you": ("are", "be_you", "主语是 you，用 are。"),
    "we": ("are", "be_we", "主语是 we，用 are。"),
    "they": ("are", "be_they", "主语是 they，用 are。"),
    "he": ("is", "be_he_she_it", "主语是 he、she、it，用 is。"),
    "she": ("is", "be_he_she_it", "主语是 he、she、it，用 is。"),
    "it": ("is", "be_he_she_it", "主语是 he、she、it，用 is。"),
    "this": ("is", "be_this_that", "this 和 that 用 is。"),
    "that": ("is", "be_this_that", "this 和 that 用 is。"),
    "these": ("are", "be_these_those", "these 和 those 用 are。"),
    "those": ("are", "be_these_those", "these 和 those 用 are。"),
}
# 复数和单数同形，或看不出单复数的词，一律不算
_UNSURE_NOUNS = {
    "fish", "sheep", "deer", "people", "police", "children", "men", "women", "feet", "teeth", "mice",
    "family", "class", "team", "news", "clothes", "pants", "shoes", "glasses", "trousers", "water", "milk", "rice",
}
_SINGLE_ENDING_S = {"bus", "class", "glass", "dress", "boss", "grass", "gas", "lens", "address"}


def compute_answer(text: str) -> dict | None:
    """Return ``{"kind", "answer", "ruleKey", "rule"}`` or ``None``."""
    return _be_verb(text) or _plural_noun(text) or _verb_choice(text) or _correction(text)


_VERB_CUES = {"can", "could", "must", "should", "will", "would", "may", "might", "to", "doesn't", "don't", "didn't", "does", "do", "did"}
_ASK_WORDS = {"does", "do", "did", "can", "will", "should", "must"}
_NO_S_FORM = {"can", "could", "must", "should", "shall", "will", "would", "may", "might", "be"}
_PAST_WORDS = {"yesterday", "ago", "last"}
_IRREGULAR_VERBS = {"go": "goes", "do": "does", "have": "has"}
_WANT_WORDS = {"want", "wants", "like", "likes", "need", "needs", "love", "loves", "have", "has", "try", "tries"}


def _verb_choice(text: str) -> dict | None:
    """Choosing between a base verb and its s form: (swim / swims)."""
    parts = _parts(text)
    if not parts:
        return None
    before, after, options = parts
    pair = _base_and_s(options)
    if pair is None:
        return None
    base, third = pair
    # can、must 这类词没有加 s 的样子，(can / cans) 里的 cans 是别的词，程序不算
    if base.casefold() in _NO_S_FORM:
        return None
    words = _WORD.findall(before)
    if not words:
        return None
    lowered = [w.casefold() for w in words]
    after_words = [w.casefold() for w in _WORD.findall(after)]
    if _PAST_WORDS & set(after_words) or _PAST_WORDS & set(lowered):
        return None
    # 空后面紧跟 is、are，说明空里是名词，不是动词
    if after_words and after_words[0] in {"am", "is", "are", "was", "were"}:
        return None
    if lowered[0] in _ASK_WORDS:
        # 问句：空要正好跟在主语后面。Can you see the ___ 不算
        if len(words) < 2 or _subject(words[1:]) is None:
            return None
        return {"kind": "verb_choice", "answer": base, "ruleKey": "v_base_after", "cue": words[0], "rule": f"句子里已经有 {words[0]} 了，后面的动词用原来的样子，不加 s。", "counter": V_BASE_AFTER_COUNTER}
    if lowered[-1] == "to":
        # go to ___ 后面可能是地点。只认 wants to、likes to 这种
        if len(lowered) < 2 or lowered[-2] not in _WANT_WORDS:
            return None
        return {"kind": "verb_choice", "answer": base, "ruleKey": "v_base_after", "cue": "to", "rule": "to 后面的动词用原来的样子，不加 s。", "counter": V_BASE_AFTER_COUNTER}
    if lowered[-1] in _VERB_CUES:
        return {"kind": "verb_choice", "answer": base, "ruleKey": "v_base_after", "cue": words[-1], "rule": f"{words[-1]} 后面的动词用原来的样子，不加 s。", "counter": V_BASE_AFTER_COUNTER}
    decided = _subject(words)
    if decided is None:
        return None
    be = decided[0]
    if be == "is":
        return {"kind": "verb_choice", "answer": third, "ruleKey": "v_3s", "rule": V_3S, "counter": V_3S_COUNTER}
    return {"kind": "verb_choice", "answer": base, "ruleKey": "v_base", "rule": "主语不是一个别人，动词用原来的样子。"}


def _base_and_s(options: list[str]) -> tuple[str, str] | None:
    """A verb pair. Nouns have the same shape, so plural rules run first."""
    if len(options) != 2:
        return None
    first, second = options
    for base, third in ((first, second), (second, first)):
        if _IRREGULAR_VERBS.get(base) == third or third in (base + "s", base + "es"):
            return base, third
        if base.endswith("y") and third == base[:-1] + "ies":
            return base, third
    return None


def _correction(text: str) -> dict | None:
    try:
        plan = plan_correction(text)
    except ValueError:
        return None
    return {
        "kind": "verb_3s",
        "answer": plan["right"],
        "ruleKey": plan["ruleKey"],
        "rule": plan["rule"],
        "counter": plan["counter"],
        "wrong": plan["wrong"],
    }


def _parts(text: str) -> tuple[str, str, list[str]] | None:
    found = _BLANK_OPTIONS.search(text.strip())
    if not found:
        return None
    before = text.strip()[: found.start()]
    options = [item.strip() for item in found.group("options").split("/")]
    if len(options) < 2 or not all(_WORD.fullmatch(item) for item in options):
        return None
    return before, found.group("after"), options


def _be_verb(text: str) -> dict | None:
    parts = _parts(text)
    if not parts:
        return None
    before, after, options = parts
    if not set(options) <= {"am", "is", "are"} or len(set(options)) < 2:
        return None
    words = _WORD.findall(before)
    if not words:
        return None

    if [w.casefold() for w in words] == ["there"]:
        decided = _there(after)
    else:
        decided = _subject(words)
    if decided is None:
        return None
    answer, key, rule = decided
    if answer not in options:
        return None
    return {"kind": "be_verb", "answer": answer, "ruleKey": key, "rule": rule}


def _there(after: str) -> tuple[str, str, str] | None:
    words = [w.casefold() for w in _WORD.findall(after)]
    if not words:
        return None
    if words[0] in {"a", "an", "one"}:
        return "is", "be_there", "There 开头的句子看后面的东西：一个用 is。"
    if words[0] in _NUMBERS or words[0] in {"many", "several"}:
        return "are", "be_there", "There 开头的句子看后面的东西：两个或更多用 are。"
    return None


def _subject(words: list[str]) -> tuple[str, str, str] | None:
    lowered = [w.casefold() for w in words]
    # Tom's ___ 的空里是名词，主语还没说完
    if any("'" in word for word in words):
        return None
    if len(words) == 1 and lowered[0] in _BE_RULES:
        return _BE_RULES[lowered[0]]
    if "and" in lowered:
        if lowered.count("and") == 1 and 0 < lowered.index("and") < len(lowered) - 1:
            return "are", "be_plural", "主语是两个或更多，用 are。"
        return None
    # 人名和称呼：Tom、Mr Li
    if len(words) == 1 and words[0][0].isupper() and lowered[0] not in _DETERMINERS and not _looks_plural(lowered[0]):
        return "is", "be_singular", "主语是一个，用 is。"
    if len(words) == 2 and lowered[0] in _TITLES and words[1][0].isupper():
        return "is", "be_singular", "主语是一个，用 is。"
    # 限定词加一个名词：My mother、The books
    if len(words) == 2 and lowered[0] in _DETERMINERS and lowered[1] not in _UNSURE_NOUNS:
        noun = lowered[1]
        if _looks_plural(noun):
            if lowered[0] in {"a", "an", "this", "that"}:
                return None
            return "are", "be_plural", "主语是两个或更多，用 are。"
        return "is", "be_singular", "主语是一个，用 is。"
    return None


def _looks_plural(noun: str) -> bool:
    return noun.endswith("s") and noun not in _SINGLE_ENDING_S and not noun.endswith("ss")


def _plural_noun(text: str) -> dict | None:
    parts = _parts(text)
    if not parts:
        return None
    before, after, options = parts
    if len(options) != 2 or set(options) & {"am", "is", "are"}:
        return None
    pair = _single_plural(options)
    if pair is None:
        return None
    single, plural = pair
    words = [w.casefold() for w in _WORD.findall(before)]
    if not words:
        return None
    last = words[-1]
    if last in {"a", "an", "one"}:
        return {"kind": "plural_noun", "answer": single, "ruleKey": "n_a_an", "cue": last, "rule": "a、an、one 后面用一个，名词不加 s。"}
    if last in _NUMBERS or last in {"many", "several", "these", "those"}:
        return {"kind": "plural_noun", "answer": plural, "ruleKey": "n_many", "rule": "数量是两个或更多，用复数。"}
    if last in _DETERMINERS and len(words) >= 3:
        head = words[:2]
        if head in (["those", "are"], ["these", "are"], ["they", "are"]):
            return {"kind": "plural_noun", "answer": plural, "ruleKey": "n_many", "rule": "前面说的是两个或更多，用复数。"}
        if head in (["this", "is"], ["that", "is"], ["it", "is"]):
            return {"kind": "plural_noun", "answer": single, "ruleKey": "n_one", "rule": "前面说的是一个，用单数。"}
    return None


def _single_plural(options: list[str]) -> tuple[str, str] | None:
    """Only regular pairs: s, es, y to ies. Irregular plurals are refused."""
    first, second = options
    for single, plural in ((first, second), (second, first)):
        if plural in (single + "s", single + "es"):
            return single, plural
        if single.endswith("y") and plural == single[:-1] + "ies":
            return single, plural
    return None
