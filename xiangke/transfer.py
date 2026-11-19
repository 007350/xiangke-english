"""回信后的离线验收。题库按题面能核实的规则组织，复测沿用规则、换题。"""

from __future__ import annotations

from xiangke.answers import compute_answer
from xiangke.kernel import normalize_answer


# 每组先测原题涉及的规则，再用一个容易混淆的条件做对照。
_TOPICS = {
    "change": ("动词什么时候要变", "change", "helper"),
    "base": ("主语变化时选动词原形", "base", "change"),
    "helper": ("can、will 等后面用动词原形", "helper", "change"),
    "do": ("do、does、did 后面用动词原形", "do", "change"),
    "to": ("want to 等搭配后面用动词原形", "to", "change"),
    "am": ("I 和 am 的搭配", "am", "is"),
    "is": ("一个人或一样东西用 is", "is", "are"),
    "are": ("主语和 are 的搭配", "are", "is"),
    "you": ("you 和 are 的搭配", "you", "is"),
    "there_is": ("There 开头，后面是一个", "there_is", "there_are"),
    "there_are": ("There 开头，后面是多个", "there_are", "there_is"),
    "one": ("一个东西用名词单数", "one", "many"),
    "many": ("多个东西用名词复数", "many", "one"),
}

_BE_ORDERS = ("am / is / are", "is / are / am", "are / am / is", "am / are / is", "is / am / are")

_ITEMS = {
    "change": (
        ("He ___ TV. (watch / watches)", "watches"),
        ("Amy ___ to school. (walks / walk)", "walks"),
        ("My uncle ___ comics. (read / reads)", "reads"),
        ("She ___ her hands. (washes / wash)", "washes"),
        ("Tom ___ his lunch at school. (has / have)", "has"),
    ),
    "base": (
        ("They ___ kites. (fly / flies)", "fly"),
        ("We ___ comics. (reads / read)", "read"),
        ("I ___ my hands. (wash / washes)", "wash"),
        ("You ___ your lunch at school. (has / have)", "have"),
    ),
    "helper": (
        ("She can ___ TV. (watch / watches)", "watch"),
        ("He can ___ a kite. (flies / fly)", "fly"),
        ("Amy will ___ a picture. (draw / draws)", "draw"),
        ("Tom must ___ his hands. (washes / wash)", "wash"),
    ),
    "do": (
        ("Does Amy ___ comics? (read / reads)", "read"),
        ("He doesn't ___ to school. (walks / walk)", "walk"),
        ("Does she ___ lunch at school? (have / has)", "have"),
        ("Did Tom ___ his hands? (washes / wash)", "wash"),
    ),
    "to": (
        ("Amy wants to ___ a picture. (draw / draws)", "draw"),
        ("He likes to ___ comics. (reads / read)", "read"),
        ("She needs to ___ her hands. (wash / washes)", "wash"),
        ("Tom loves to ___ kites. (flies / fly)", "fly"),
    ),
    "am": tuple((f"I ___ {rest}. ({choices})", "am") for rest, choices in zip(("hungry", "ready", "at school", "nine"), _BE_ORDERS)),
    "is": tuple((f"{sentence}. ({choices})", "is") for sentence, choices in zip((
        "Tom ___ hungry", "My bag ___ blue", "She ___ ready", "Mr Li ___ at school", "This ___ my pencil",
    ), _BE_ORDERS)),
    "are": tuple((f"{sentence}. ({choices})", "are") for sentence, choices in zip((
        "They ___ ready", "The books ___ on the desk", "We ___ happy", "Amy and Tom ___ at school",
    ), _BE_ORDERS)),
    "you": tuple((f"You ___ {rest}. ({choices})", "are") for rest, choices in zip(("ready", "at school", "my friend", "kind"), _BE_ORDERS)),
    "there_is": tuple((f"There ___ {rest}. (is / are)", "is") for rest in (
        "a kite in the box", "one cup on the table", "an apple in the bag", "a book on the desk",
    )),
    "there_are": tuple((f"There ___ {rest}. (are / is)", "are") for rest in (
        "two kites in the box", "three cups on the table", "five apples in the bag", "six books on the desk",
    )),
    "one": (
        ("This is a ___. (kite / kites)", "kite"),
        ("I have an ___. (oranges / orange)", "orange"),
        ("There is one ___. (cup / cups)", "cup"),
        ("That is a ___. (books / book)", "book"),
    ),
    "many": (
        ("I have two ___. (kite / kites)", "kites"),
        ("She has three ___. (oranges / orange)", "oranges"),
        ("There are five ___. (cup / cups)", "cups"),
        ("We have six ___. (books / book)", "books"),
    ),
}


def focus_for(text: str) -> tuple[str, str] | None:
    """用程序核实的规则确定考点，不根据模型回信猜测孩子的错因。"""
    if not isinstance(text, str) or not text.strip():
        return None
    computed = compute_answer(text)
    if not computed:
        return None
    kind, rule = computed["kind"], computed["ruleKey"]
    if kind == "be_verb":
        key = computed["answer"]
        if rule == "be_there":
            key = f"there_{key}"
        elif rule == "be_you":
            key = "you"
    elif kind == "plural_noun":
        key = "many" if rule == "n_many" else "one"
    elif kind == "verb_3s" or rule == "v_3s":
        key = "change"
    elif rule == "v_base":
        key = "base"
    elif rule == "v_base_after":
        # 用程序已经识别出的线索区分两类助动词和 to 搭配。
        cue = computed["rule"].removeprefix("句子里已经有 ").split()[0].casefold()
        if cue == "to":
            key = "to"
        else:
            key = "do" if cue in {"do", "does", "did", "don't", "doesn't", "didn't"} else "helper"
    else:
        return None
    return key, _TOPICS[key][0]


def letter_focus(question: dict) -> tuple[str, str] | None:
    status = question.get("status")
    if status not in {"answered", "teacher_confirmed"} or (question.get("contextWarning") and status != "teacher_confirmed"):
        return None
    return focus_for(question.get("text", ""))


def item_for(focus: str, slot: int, seen: set[str], original_reply: str = "") -> str | None:
    bank = _TOPICS[focus][slot + 1]
    if slot == 1 and focus in {"am", "is", "are", "you"}:
        mistaken = normalize_answer(original_reply)
        if mistaken in {"am", "is", "are"} - {"are" if focus == "you" else focus}:
            bank = mistaken
    for text, answer in _ITEMS[bank]:
        if text_key(text) not in seen and (compute_answer(text) or {}).get("answer") == answer:
            return text
    return None


def text_key(text: str) -> str:
    """同一句即使大小写、选项顺序或括号不同，也视为已经见过。"""
    return " ".join(text.split("(", 1)[0].split("（", 1)[0].casefold().split()).rstrip(".?!。？！")
