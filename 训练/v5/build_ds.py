"""生成 v5 训练数据：旧数据（只改术语）+ 约 1450 条新数据。

所有答案和讲解都由这里的表和模板写出，不用模型生成。固定随机种子，重跑结果相同。
用法：python 训练/v5/build_ds.py
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import re
import shutil
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
V4 = HERE.parent / "v4" / "ds_v4"
OUT = HERE / "ds_v5"
EXK = "拆题样例（examples，只模仿拆法，答案与本题无关）：\n"
rng = random.Random(20261104)

# ---------- 不能进训练的题和回复（已经用来测过模型，留作对照） ----------
TESTED_ITEMS = {
    "Tom ___ my friend. (am / is / are)", "My mother ___ a teacher. (am / is / are)",
    "The books ___ on the desk. (am / is / are)", "Tom and Lily ___ students. (am / is / are)",
    "The cat ___ under the chair. (am / is / are)", "My brother and I ___ at home. (am / is / are)",
    "There ___ a bird in the tree. (is / are)", "There ___ two apples on the table. (is / are)",
    "I have two ___. (cat / cats)", "There are three ___ in the box. (pen / pens)",
    "Those are my ___. (book / books)", "I can see many ___. (bird / birds)",
    "We have five ___. (box / boxes)", "There are two ___ in the room. (baby / babies)",
    "Look at those ___. (child / children)", "I have one ___. (foot / feet)",
    "She can ___. (swim / swims)", "He wants to ___ a book. (read / reads)",
    "Tom ___ football every day. (play / plays)", "My sister ___ TV at night. (watch / watches)",
    "The dog ___ fast. (run / runs)", "They ___ to school every day. (go / goes)",
    "Does she ___ milk? (like / likes)", "Yesterday I ___ to the park. (go / went)",
    "改错（只改一个词）：Tom like apples.", "改错（只改一个词）：My father read books every day.",
    "改错（只改一个词）：They likes cats.", "改错（只改一个词）：I has a pen.",
    "改错（只改一个词）：She can swims.", "改错（只改一个词）：He don't like milk.",
    "改错（只改一个词）：I is a student.", "改错（只改一个词）：We was at home yesterday.",
    "改错（只改一个词）：There are a book on the desk.", "改错（只改一个词）：Yesterday she go to school.",
    "看中文写单词：书 b__k", "看中文写单词：苹果 a__le", "看中文写单词：老师 t__cher",
    "看中文写单词：学校 sch__l", "看中文写单词：朋友 fr__nd", "看中文写单词：水 w_ter",
    "改错（只改一个词）：She like cats.", "改错（只改一个词）：He play football.",
    "改错（只改一个词）：It look nice.", "改错（只改一个词）：She watch TV.",
    "改错（只改一个词）：He study English.", "改错（只改一个词）：He go to school.",
}
TESTED_REPLIES = {
    "likes", "是 likes 吧", "likes吧", "我觉得是 likes", "应该是 liked", "like", "改成 likeing", "likes?", "选 likes", "She likes",
    "不知道", "不会", "我不会", "想不出来", "太难了", "忘了", "不懂", "不会写",
    "为啥要改", "为什么要加 s", "为什么是这个词", "什么意思", "这是啥意思", "为什么不能用 like", "怎么改啊",
    "我饿了", "等一下", "哈哈哈", "妈妈叫我了", "今天下雨了",
    "likess", "我选 likes", "liking", "是 liked", "likes 对吧",
    "我不知道", "不清楚", "做不出来", "好难啊", "没学过",
    "为什么呀", "为啥不是 like", "s 是什么", "啥叫主语", "这题啥意思",
    "我想玩游戏", "你是谁", "吃饭了", "好累", "嗯嗯",
}

# ---------- 词表 ----------
# 原形、加 s 后的样子、讲法种类、宾语或后续
VERBS = [
    ("like", "likes", "s", ["cats", "apples", "music", "dogs", "bananas"]),
    ("love", "loves", "s", ["music", "my school", "apples"]),
    ("play", "plays", "s", ["football", "basketball", "games"]),
    ("eat", "eats", "s", ["rice", "apples", "bread"]),
    ("drink", "drinks", "s", ["water", "tea", "milk"]),
    ("want", "wants", "s", ["a book", "a new bag", "some water"]),
    ("need", "needs", "s", ["a pen", "some help", "a new bag"]),
    ("make", "makes", "s", ["cakes", "a kite", "dinner"]),
    ("clean", "cleans", "s", ["the room", "the desk"]),
    ("walk", "walks", "s", ["to school", "in the park"]),
    ("run", "runs", "s", ["in the park", "every morning"]),
    ("swim", "swims", "s", ["in the river", "every day"]),
    ("sing", "sings", "s", ["a song", "every morning"]),
    ("dance", "dances", "s", ["every day", "at school"]),
    ("write", "writes", "s", ["letters", "a story"]),
    ("cook", "cooks", "s", ["dinner", "rice"]),
    ("ride", "rides", "s", ["a bike", "a horse"]),
    ("draw", "draws", "s", ["a cat", "pictures"]),
    ("speak", "speaks", "s", ["English", "Chinese"]),
    ("wash", "washes", "es", ["clothes", "the dishes", "the car"]),
    ("watch", "watches", "es", ["a film", "TV at home"]),
    ("teach", "teaches", "es", ["English", "music"]),
    ("catch", "catches", "es", ["a ball", "fish"]),
    ("finish", "finishes", "es", ["the work", "homework"]),
    ("fix", "fixes", "es", ["cars", "the bike"]),
    ("miss", "misses", "es", ["home", "my school"]),
    ("study", "studies", "ies", ["math", "hard", "at night"]),
    ("carry", "carries", "ies", ["bags", "water"]),
    ("fly", "flies", "ies", ["kites", "a plane"]),
    ("try", "tries", "ies", ["again", "hard"]),
    ("go", "goes", "o", ["home", "to the park", "to work"]),
    ("do", "does", "o", ["homework", "the work"]),
    ("have", "has", "have", ["a dog", "a new bag", "lunch"]),
]
IT_PHRASES = [
    ("like", "likes", "s", "fish"), ("like", "likes", "s", "milk"), ("eat", "eats", "s", "fish"),
    ("eat", "eats", "s", "rice"), ("run", "runs", "s", "fast"), ("swim", "swims", "s", "well"),
    ("fly", "flies", "ies", "high"), ("sleep", "sleeps", "s", "on the bed"), ("look", "looks", "s", "good"),
    ("have", "has", "have", "a long tail"), ("go", "goes", "o", "fast"), ("drink", "drinks", "s", "water"),
    ("catch", "catches", "es", "mice"), ("carry", "carries", "ies", "food"), ("sing", "sings", "s", "well"),
    ("eat", "eats", "s", "meat"), ("eat", "eats", "s", "grass"), ("drink", "drinks", "s", "milk"), ("like", "likes", "s", "rice"),
    ("like", "likes", "s", "meat"), ("run", "runs", "s", "every day"), ("swim", "swims", "s", "in the river"), ("fly", "flies", "ies", "in the sky"),
    ("sleep", "sleeps", "s", "at night"), ("sleep", "sleeps", "s", "all day"), ("look", "looks", "s", "big"), ("look", "looks", "s", "very happy"),
    ("have", "has", "have", "big eyes"), ("have", "has", "have", "four legs"), ("go", "goes", "o", "home"), ("catch", "catches", "es", "fish"),
    ("carry", "carries", "ies", "water"), ("sing", "sings", "s", "in the tree"), ("play", "plays", "s", "with me"), ("wash", "washes", "es", "its face"),
]
ANIMAL_PHRASES = {
    "The cat": [("eat", "eats", "s", "fish"), ("sleep", "sleeps", "s", "on the bed"), ("catch", "catches", "es", "mice")],
    "The bird": [("sing", "sings", "s", "well"), ("fly", "flies", "ies", "high"), ("eat", "eats", "s", "rice")],
    "The horse": [("run", "runs", "s", "fast"), ("drink", "drinks", "s", "water"), ("eat", "eats", "s", "grass")],
    "The rabbit": [("eat", "eats", "s", "carrots"), ("run", "runs", "s", "fast"), ("sleep", "sleeps", "s", "at night")],
    "My dog": [("like", "likes", "s", "meat"), ("run", "runs", "s", "fast"), ("have", "has", "have", "a long tail")],
    "The duck": [("swim", "swims", "s", "well"), ("live", "lives", "s", "near the river"), ("eat", "eats", "s", "food")],
}
ONE_SUBJECTS = ["She", "He", "Tom", "Lily", "My sister", "My brother", "The boy", "The girl"]
MANY_SUBJECTS = ["They", "We", "The students", "Tom and Lily", "My friends"]
ASK_SUBJECTS = {
    "one": ["she", "he", "Tom", "Lily", "your sister", "the boy"],
    "many": ["they", "you", "the students", "your friends"],
}
PRON_INFO = {
    "She": ("一个", "She 说的是「她」，是一个人，不是「我」，也不是好几个人。", "She 是一个人"),
    "He": ("一个", "He 说的是「他」，是一个人，不是「我」，也不是好几个人。", "He 是一个人"),
    "It": ("一个", "It 说的是「它」，是一样东西，不是「我」，也不是好几个人。", "It 是一样东西"),
    "We": ("多个", "We 说的是「我们」，是两个或更多的人，不是只有「我」一个。", "We 是两个或更多的人"),
    "They": ("多个", "They 说的是「他们」，是两个或更多的人。", "They 是两个或更多的人"),
    "You": ("你", "You 说的是「你」。", "You 说的是「你」"),
    "I": ("我", "I 就是「我」自己。", "I 说的是「我」"),
}
BE_FRAMES = {
    "She": ["a teacher", "a student", "a doctor", "my friend", "my sister", "my mother", "happy", "very busy", "at school", "in the classroom", "at home now", "tall", "a nurse", "very kind", "in the park", "at work today"],
    "He": ["a teacher", "a student", "a doctor", "my friend", "my brother", "my father", "happy", "very busy", "at school", "in the classroom", "at home now", "tall", "a farmer", "very kind", "in the park", "at work today"],
    "It": ["a cat", "my pen", "a big dog", "on the desk", "under the chair", "very big", "in the box", "a nice bag", "my book", "on the bed", "very small", "my new toy", "a red ball", "near the door", "very old", "a good book"],
    "We": ["students", "good friends", "in the park", "at home", "happy", "very busy", "teachers", "in the classroom", "tired today", "at school now", "very kind", "farmers"],
    "They": ["students", "good friends", "in the park", "at home", "happy", "very busy", "teachers", "in the classroom", "tired today", "at school now", "very kind", "farmers"],
    "You": ["my friend", "a good student", "very kind", "at school", "happy", "my teacher", "tall", "in the classroom", "very busy", "at home now", "a good boy", "in the park"],
}
PAST_FRAMES = ["at home", "at school", "in the park", "happy", "very busy", "in the classroom", "tired"]
PAST_TIMES = ["yesterday", "last night", "last week", "last Sunday", "this morning"]
THERE_THINGS_ONE = ["a book", "a pen", "a cat", "a bird", "an apple", "a boy", "a map", "a chair", "an egg", "a ball"]
THERE_THINGS_MANY = ["two books", "three pens", "two cats", "four birds", "five eggs", "three boys", "two maps", "many chairs", "some apples", "six balls"]
THERE_PLACES = ["on the desk", "in the box", "under the bed", "in the room", "on the table", "near the door", "in the yard", "at the school"]
DONT_PHRASES = [("like", "milk"), ("like", "fish"), ("play", "football"), ("eat", "rice"), ("want", "a pen"), ("need", "a bag"),
                ("go", "to school"), ("have", "a dog"), ("watch", "a film"), ("know", "her name"), ("drink", "tea"), ("swim", "in the river")]

SPELL_WORDS = [
    ("书", "book", ["bo_k", "b_ok"]), ("苹果", "apple", ["ap_le", "app__"]), ("好的", "good", ["g__d", "go_d"]),
    ("脚", "foot", ["fo_t", "f_ot"]), ("月亮", "moon", ["m__n", "mo_n"]), ("勺子", "spoon", ["sp__n"]),
    ("树", "tree", ["tr__", "t_ee"]), ("绿色", "green", ["gr__n"]), ("睡觉", "sleep", ["sl__p", "sle_p"]),
    ("羊", "sheep", ["sh__p"]), ("房间", "room", ["r__m", "ro_m"]), ("食物", "food", ["f__d", "fo_d"]),
    ("看", "look", ["l__k", "lo_k"]), ("做饭", "cook", ["c__k", "co_k"]), ("高兴的", "happy", ["ha__y", "hap_y"]),
    ("兔子", "rabbit", ["ra__it", "rab_it"]), ("夏天", "summer", ["su__er", "sum_er"]), ("小猫", "kitten", ["ki__en", "kit_en"]),
    ("晚餐", "dinner", ["di__er", "din_er"]), ("信", "letter", ["le__er", "let_er"]), ("铃", "bell", ["be__", "bel_"]),
    ("球", "ball", ["ba__", "bal_"]), ("墙", "wall", ["wa__"]), ("高的", "tall", ["ta__"]), ("小的", "small", ["sma__"]),
    ("蛋", "egg", ["e__", "eg_"]), ("蜜蜂", "bee", ["b__", "be_"]), ("老师", "teacher", ["te_cher", "tea_h_r"]),
    ("学校", "school", ["sc__ol", "schoo_"]), ("朋友", "friend", ["f_ie_d", "frie_d"]), ("水", "water", ["wa_er", "wat_r"]),
    ("姐妹", "sister", ["sis_er", "s_ster"]), ("兄弟", "brother", ["br_ther", "bro_her"]), ("母亲", "mother", ["mo_her", "m_ther"]),
    ("父亲", "father", ["fa_her", "f_ther"]), ("黄色", "yellow", ["ye__ow", "yel_ow"]), ("铅笔", "pencil", ["pe__il", "pen_il"]),
    ("鱼", "fish", ["f__h", "fi_h"]), ("鸟", "bird", ["b__d", "bi_d"]), ("牛奶", "milk", ["m__k", "mi_k"]),
    ("面包", "bread", ["br__d", "bre_d"]), ("星期", "week", ["w__k", "we_k"]), ("街道", "street", ["str__t", "stre_t"]),
    ("教室", "classroom", ["cla__room", "class_oom"]), ("再见", "goodbye", ["g__dbye", "goodb_e"]), ("早上", "morning", ["m_rning", "mor_ing"]),
    ("蓝色", "blue", ["bl__", "b_ue"]), ("红色", "red", ["r_d"]), ("钢笔", "pen", ["p_n"]), ("猫", "cat", ["c_t"]), ("狗", "dog", ["d_g"]),
    ("米饭", "rice", ["r__e", "ri_e"]), ("黑色", "black", ["bl__k"]), ("白色", "white", ["wh_te"]), ("手", "hand", ["h_nd"]), ("头", "head", ["h__d"]),
    ("眼睛", "eye", ["e_e"]), ("耳朵", "ear", ["e_r"]), ("鼻子", "nose", ["n_se"]), ("嘴巴", "mouth", ["m__th"]), ("门", "door", ["d__r", "do_r"]),
    ("窗户", "window", ["w_ndow"]), ("桌子", "desk", ["d_sk"]), ("椅子", "chair", ["ch__r"]), ("老虎", "tiger", ["t_ger"]), ("熊猫", "panda", ["p_nda"]),
]
ORDINAL = "一二三四五六七八九十"


def ordinal(number: int) -> str:
    if number <= 10:
        return "第" + ORDINAL[number - 1]
    return "第十" + ORDINAL[number - 11]


# ---------- 旧数据格式 ----------
def load(name: str) -> list[dict]:
    return [json.loads(line) for line in (V4 / name).read_text(encoding="utf-8").splitlines() if line.strip()]


def user_message(text: str, plan: dict, empty_line: str) -> str:
    reminders = " ".join(plan["reminders"])
    lines = [
        f"题目（recognizedText，孩子已确认）：{text}",
        f"讲法（voice）：{plan['voice']}",
        f"孩子犯过的错（reminders）：{reminders}" if plan["reminders"] else empty_line,
        f"禁令（forbid）：{plan['forbid']}",
    ]
    if plan["examples"]:
        lines.append(EXK + json.dumps(plan["examples"], ensure_ascii=False, indent=1))
    lines.append("请按要求只输出 JSON。")
    return "\n".join(lines)


# ---------- 术语替换（只改解释，不改禁令和提醒） ----------
JARGON_RULES = [
    ("这叫第三人称单数。", ""),
    ("（第一人称单数）", ""),
    ("（第三人称单数）", ""),
    ("不是第三人称单数", "不是「一个别人」"),
    ("是第三人称单数（一个别人）", "是「一个别人」"),
    ("是第三人称单数", "是「一个别人」"),
    ("动词要用第三人称单数", "动词要变"),
    ("动词用第三人称单数", "动词要变"),
    ("第三人称单数", "「一个别人」"),
]


def clean_text(text: str) -> str:
    for old, new in JARGON_RULES:
        text = text.replace(old, new)
    return text


def clean_row(row: dict, empty_line: str) -> tuple[dict, bool]:
    new = copy.deepcopy(row)
    for example in new["plan"]["examples"]:
        for step in example["steps"]:
            if "explain" in step:
                step["explain"] = clean_text(step["explain"])
    bot = clean_text(new["messages"][2]["content"])
    new["messages"][2]["content"] = bot
    user = user_message(new["recognizedText"], new["plan"], empty_line)
    changed = bot != row["messages"][2]["content"] or user != row["messages"][1]["content"]
    new["messages"][1]["content"] = user
    return new, changed


# ---------- 新记录 ----------
class Builder:
    def __init__(self, old_rows: list[dict], blocked: set[str], empty_line: str):
        self.blocked = set(blocked)
        self.empty_line = empty_line
        self.pool = {
            kind: [r for r in old_rows if r["type"] == kind]
            for kind in ("choose", "fix", "spell")
        }
        self.system = old_rows[0]["messages"][0]["content"]
        self.rows: list[dict] = []
        self.counter = 0

    def add(self, template: str, kind: str, text: str, steps: list[dict], gold: str, lemmas: list[str], **meta) -> bool:
        if text in self.blocked:
            return False
        self.blocked.add(text)
        source = rng.choice(self.pool[kind])
        plan = copy.deepcopy(source["plan"])
        # 示例里的术语同样换掉，避免模型继续学到
        for example in plan["examples"]:
            for step in example["steps"]:
                if "explain" in step:
                    step["explain"] = clean_text(step["explain"])
        self.counter += 1
        number = f"{self.counter:05d}"
        plan["planId"] = "PL5" + number
        plan["planVersion"] = 1
        style = source["meta"]["voice_style"]
        worded = decorate(steps, style)
        bot = json.dumps({"steps": worded}, ensure_ascii=False)
        self.rows.append(
            {
                "id": f"xk5-{template}-{number}",
                "type": kind,
                "template": f"v5/{template}",
                "split": "train",
                "meta": {
                    "gold": gold,
                    "lemmas": lemmas,
                    "voice_style": style,
                    "hook": None,
                    "hook_dropped": False,
                    "forbid_tags": source["meta"]["forbid_tags"],
                    "n_examples": len(plan["examples"]),
                    **meta,
                },
                "recognizedText": text,
                "plan": plan,
                "messages": [
                    {"role": "system", "content": self.system},
                    {"role": "user", "content": user_message(text, plan, self.empty_line)},
                    {"role": "assistant", "content": bot},
                ],
            }
        )
        return True


def decorate(steps: list[dict], style: str) -> list[dict]:
    """按讲法给问句加上和旧数据一样的开头。"""
    worded = copy.deepcopy(steps)
    if style == "calm":
        worded[0]["ask"] = "别着急，" + worded[0]["ask"]
    elif style == "detective":
        worded[0]["ask"] = "小侦探，" + worded[0]["ask"]
    elif style == "praise":
        for step in worded[1:]:
            if not step["ask"].startswith("很好"):
                step["ask"] = "很好！" + step["ask"]
    return worded


def step(ask: str, expect: str, explain: str) -> dict:
    return {"ask": ask, "expect": expect, "explain": explain}


FIND_WRONG = ["这句话里哪个词用错了？说出那个英文词。", "仔细读一读，哪个英文词写错了？"]
FIX_ASK = ["那 {w} 应该改成什么？", "{w} 要改成哪个词？请说出来。"]


def wrong_step(word: str) -> dict:
    return step(rng.choice(FIND_WRONG), word, f"这句话里用错的是 {word}，其他词都没问题。")


def kind_step(subject: str) -> dict:
    expect, explain, _ = PRON_INFO[subject]
    return step(f"主语 {subject} 是哪一种：说「我」「你」「一个」还是「多个」？", expect, explain)


def fix_step(wrong: str, right: str, explain: str) -> dict:
    return step(rng.choice(FIX_ASK).format(w=wrong), right, explain)


def rule_3s(subject_note: str, base: str, form: str, rule: str) -> str:
    if rule == "s":
        return f"{subject_note}，现在常做的事，动词要变，一般直接加 s：{base} → {form}。"
    if rule == "es":
        return f"{subject_note}，现在常做的事，动词要变。以 s、x、ch、sh 结尾的加 es：{base} → {form}。"
    if rule == "ies":
        return f"{subject_note}，现在常做的事，动词要变。辅音字母加 y 结尾的，把 y 改成 i 再加 es：{base} → {form}。"
    if rule == "o":
        return f"{subject_note}，现在常做的事，动词要变。以 o 结尾的加 es：{base} → {form}。"
    return f"{subject_note}，现在常做的事，have 要变成 has：{base} → {form}。"


KOUJUE = "口诀：「我」用 am，「你」用 are，一个人或一样东西用 is，两个或更多用 are。"


def fill(builder: Builder, count: int, make) -> None:
    done = 0
    tries = 0
    while done < count:
        tries += 1
        if tries > count * 200:
            raise RuntimeError(f"{make.__name__} 只凑出 {done}/{count} 条，词表不够")
        if make(builder):
            done += 1


# ---------- 代词主语（300 条） ----------
def make_fix_be_pron(b: Builder, subject: str) -> bool:
    frame = rng.choice(BE_FRAMES[subject])
    right = "is" if subject in ("She", "He", "It") else "are"
    wrong = rng.choice([w for w in ("am", "is", "are") if w != right])
    text = f"改错（只改一个词）：{subject} {wrong} {frame}."
    steps = [
        wrong_step(wrong),
        kind_step(subject),
        fix_step(wrong, right, f"{KOUJUE}{PRON_INFO[subject][2]}，所以 {wrong} 要改成 {right}。"),
    ]
    return b.add("fix_be_pron", "fix", text, steps, right, [subject], wrong=wrong)


def make_fix_3s_one(b: Builder, subject: str) -> bool:
    if subject == "It":
        base, form, rule, tail = rng.choice(IT_PHRASES)
    else:
        base, form, rule, tails = rng.choice(VERBS)
        tail = rng.choice(tails)
    text = f"改错（只改一个词）：{subject} {base} {tail}."
    steps = [
        wrong_step(base),
        kind_step(subject),
        fix_step(base, form, rule_3s(PRON_INFO[subject][2], base, form, rule)),
    ]
    return b.add("fix_3s_pron", "fix", text, steps, form, [base], wrong=base)


def make_fix_3s_many(b: Builder, subject: str) -> bool:
    base, form, _, tails = rng.choice(VERBS)
    tail = rng.choice(tails)
    text = f"改错（只改一个词）：{subject} {form} {tail}."
    note = PRON_INFO[subject][2]
    steps = [
        wrong_step(form),
        kind_step(subject),
        fix_step(form, base, f"{note}，动词用原来的样子，不用变：{form} → {base}。"),
    ]
    return b.add("fix_3s_pron", "fix", text, steps, base, [base], wrong=form)


# ---------- can、to、does 后面用原形（250 条） ----------
def pick_verb():
    base, form, rule, tails = rng.choice(VERBS)
    return base, form, rule, rng.choice(tails)


def options(a: str, b: str) -> str:
    pair = [a, b]
    rng.shuffle(pair)
    return f"({pair[0]} / {pair[1]})"


def which(a: str, b: str) -> str:
    pair = [a, b]
    rng.shuffle(pair)
    return f"{pair[0]} 还是 {pair[1]}"


CUE_NOTE = {
    "can": "can 后面的动词总是用原来的样子，不管主语是谁，都不加 s。",
    "must": "must 后面的动词总是用原来的样子，不管主语是谁，都不加 s。",
    "should": "should 后面的动词总是用原来的样子，不管主语是谁，都不加 s。",
    "will": "will 后面的动词总是用原来的样子，不管主语是谁，都不加 s。",
    "to": "to 后面的动词用原来的样子，不加 s。",
    "doesn't": "doesn't 后面的动词用原来的样子，不加 s。",
    "don't": "don't 后面的动词用原来的样子，不加 s。",
    "didn't": "didn't 后面的动词用原来的样子，不加 s。",
    "Does": "句子里已经有 Does 了，后面的动词就用原来的样子，不加 s。",
    "Do": "句子里已经有 Do 了，后面的动词就用原来的样子，不加 s。",
    "Did": "句子里已经有 Did 了，后面的动词就用原来的样子，不加 s。",
}


def base_frame(cue_form: str | None = None):
    """返回 (题干模板, 提示词)。模板里的 {v} 放动词。"""
    pattern = cue_form or rng.choice(["modal", "modal", "to", "to", "ask", "ask", "neg"])
    one = rng.random() < 0.7
    subject = rng.choice(ONE_SUBJECTS if one else MANY_SUBJECTS)
    if pattern == "modal":
        cue = rng.choice(["can", "must", "should", "will"])
        return f"{subject} {cue} {{v}} {{t}}.", cue
    if pattern == "to":
        verb = rng.choice(["wants", "likes", "needs"]) if one else rng.choice(["want", "like", "need"])
        return f"{subject} {verb} to {{v}} {{t}}.", "to"
    if pattern == "ask":
        if rng.random() < 0.6:
            cue = "Does"
            who = rng.choice(ASK_SUBJECTS["one"])
        elif rng.random() < 0.5:
            cue = "Do"
            who = rng.choice(ASK_SUBJECTS["many"])
        else:
            cue = "Did"
            who = rng.choice(ASK_SUBJECTS["one"] + ASK_SUBJECTS["many"])
        return f"{cue} {who} {{v}} {{t}}?", cue
    cue = "doesn't" if one else "don't"
    if rng.random() < 0.25:
        cue = "didn't"
    return f"{subject} {cue} {{v}} {{t}}.", cue


def make_base_choose(b: Builder) -> bool:
    base, form, _, tail = pick_verb()
    frame, cue = base_frame()
    if cue.casefold() == base:
        return False
    text = frame.format(v="___", t=tail) + " " + options(base, form)
    if cue in ("Does", "Do", "Did"):
        first = step("句子开头的那个英文词是什么？", cue, f"句子开头的词是 {cue}。")
        second_ask = f"句子里有 {cue}，空里的动词要不要变形？说「要」或「不要」。"
    else:
        first = step("空格前面紧挨着的那个英文词是什么？", cue, f"空格前面紧挨着的是 {cue}。")
        second_ask = f"{cue} 后面的动词要不要变形？说「要」或「不要」。"
    steps = [
        first,
        step(second_ask, "不要", CUE_NOTE[cue]),
        step(f"那空里选 {which(base, form)}？", base, f"{CUE_NOTE[cue]}所以填 {base}。"),
    ]
    return b.add("base_choose", "choose", text, steps, base, [base], clue=cue)


def make_animal_choose(b: Builder) -> bool:
    subject = rng.choice(list(ANIMAL_PHRASES))
    base, form, rule, tail = rng.choice(ANIMAL_PHRASES[subject])
    every = rng.choice(["", "", " every day"])
    if tail in ("fast", "well", "high") and not every:
        every = ""
    text = f"{subject} ___ {tail}{every}. {options(base, form)}"
    note = f"{subject} 是一只动物，只有一只"
    steps = [
        step("这句话的主语是什么？请用英文把主语整个说出来。", subject, f"主语一般在句子开头，这句话的主语是 {subject}。"),
        step(f"{subject} 后面的动词要不要变形？说「要」或「不要」。", "要", f"{note}，现在常做的事，动词要变。"),
        step(f"那空里选 {which(base, form)}？", form, rule_3s(note, base, form, rule)),
    ]
    return b.add("animal_choose", "choose", text, steps, form, [base])


def make_base_fix(b: Builder) -> bool:
    base, form, _, tail = pick_verb()
    frame, cue = base_frame()
    if cue.casefold() == base:
        return False
    text = "改错（只改一个词）：" + frame.format(v=form, t=tail)
    if cue in ("Does", "Do", "Did"):
        second = step("句子开头的那个英文词是什么？", cue, f"句子开头的词是 {cue}。")
    else:
        second = step(f"{form} 前面紧挨着的那个英文词是什么？", cue, f"{form} 前面紧挨着的是 {cue}。")
    steps = [
        wrong_step(form),
        second,
        fix_step(form, base, f"{CUE_NOTE[cue]}所以 {form} 要改成 {base}。"),
    ]
    return b.add("base_fix", "fix", text, steps, base, [base], wrong=form, clue=cue)


# ---------- 改错缺口（300 条） ----------
def make_fix_i(b: Builder) -> bool:
    base, form, _, tail = pick_verb()
    text = f"改错（只改一个词）：I {form} {tail}."
    if base == "have":
        last = f"主语是 I，要用 have，不用 has：{form} → {base}。"
    else:
        last = f"主语 I 说的是「我」，动词用原来的样子，不加 s：{form} → {base}。"
    steps = [wrong_step(form), kind_step("I"), fix_step(form, base, last)]
    return b.add("fix_i", "fix", text, steps, base, [base], wrong=form)


ONE_DONT = ["She", "He", "It", "Tom", "Lily", "My sister", "The boy"]
MANY_DONT = ["We", "They", "You", "I", "My friends", "The students"]
SUBJECT_ONE_NOTE = {"She": "She 是一个人", "He": "He 是一个人", "It": "It 是一样东西"}


def subject_kind_step(subject: str) -> dict:
    if subject in PRON_INFO:
        return kind_step(subject)
    many = subject in ("My friends", "The students")
    expect = "多个" if many else "一个"
    explain = f"{subject} 是两个或更多的人。" if many else f"{subject} 是一个人，不是「我」，也不是好几个人。"
    return step(f"主语 {subject} 是哪一种：说「我」「你」「一个」还是「多个」？", expect, explain)


def make_fix_dont(b: Builder) -> bool:
    base, tail = rng.choice(DONT_PHRASES)
    if rng.random() < 0.6:
        subject = rng.choice(ONE_DONT)
        wrong, right = "don't", "doesn't"
    else:
        subject = rng.choice(MANY_DONT)
        wrong, right = "doesn't", "don't"
    text = f"改错（只改一个词）：{subject} {wrong} {base} {tail}."
    if right == "doesn't":
        last = f"主语 {subject} 是一个，否定要用 doesn't，不用 don't。后面的 {base} 不用动，只改这一个词。"
    else:
        last = f"主语 {subject} 不是一个别人，否定要用 don't，不用 doesn't。后面的 {base} 不用动，只改这一个词。"
    steps = [
        wrong_step(wrong),
        subject_kind_step(subject),
        step(f"那 {wrong} 应该改成什么？只说改好的这一个词。", right, last),
    ]
    return b.add("fix_dont", "fix", text, steps, right, [base], wrong=wrong)


def make_fix_was(b: Builder) -> bool:
    subject = rng.choice(["I", "She", "He", "It", "We", "They", "You"])
    frame = rng.choice(PAST_FRAMES)
    time = rng.choice(PAST_TIMES)
    right = "was" if subject in ("I", "She", "He", "It") else "were"
    wrong = "were" if right == "was" else "was"
    text = f"改错（只改一个词）：{subject} {wrong} {frame} {time}."
    last = f"说过去的事：「我」、一个人或一样东西用 was，「你」和两个或更多用 were。{PRON_INFO[subject][2]}，所以 {wrong} 要改成 {right}。"
    steps = [wrong_step(wrong), kind_step(subject), fix_step(wrong, right, last)]
    return b.add("fix_was", "fix", text, steps, right, [subject], wrong=wrong)


def make_fix_there(b: Builder) -> bool:
    one = rng.random() < 0.5
    thing = rng.choice(THERE_THINGS_ONE if one else THERE_THINGS_MANY)
    place = rng.choice(THERE_PLACES)
    right, wrong = ("is", "are") if one else ("are", "is")
    text = f"改错（只改一个词）：There {wrong} {thing} {place}."
    count = "一个" if one else "多个"
    second = step(f"句子里说的 {thing} 是一个还是多个？说「一个」或「多个」。", count, f"{thing} 说的是{'一个' if one else '两个或更多'}。")
    last = f"There 开头的句子要看后面的东西：一个用 is，两个或更多用 are。{thing} 是{count}，所以 {wrong} 要改成 {right}。"
    return b.add("fix_there", "fix", text, [wrong_step(wrong), second, fix_step(wrong, right, last)], right, [thing], wrong=wrong)


def make_choose_there(b: Builder) -> bool:
    one = rng.random() < 0.5
    thing = rng.choice(THERE_THINGS_ONE if one else THERE_THINGS_MANY)
    place = rng.choice(THERE_PLACES)
    right = "is" if one else "are"
    text = f"There ___ {thing} {place}. {options('is', 'are')}"
    count = "一个" if one else "多个"
    steps = [
        step("空格后面说的是什么东西？用英文整个说出来。", thing, f"There 开头的句子，要看空格后面的东西，这里是 {thing}。"),
        step(f"{thing} 是一个还是多个？说「一个」或「多个」。", count, f"{thing} 说的是{'一个' if one else '两个或更多'}。"),
        step(f"那空里填 {which('is', 'are')}？", right, f"There 开头的句子要看后面的东西：一个用 is，两个或更多用 are。{thing} 是{count}，所以填 {right}。"),
    ]
    return b.add("choose_there", "choose", text, steps, right, [thing])


# ---------- 拼写（100 条） ----------
def spell_steps(zh: str, word: str, mask: str) -> list[dict] | None:
    if len(mask) != len(word):
        raise ValueError(f"{mask} 和 {word} 长度不同")
    for letter, mark in zip(word, mask):
        if mark != "_" and mark != letter:
            raise ValueError(f"{mask} 和 {word} 对不上")
    blanks = [index for index, mark in enumerate(mask) if mark == "_"]
    steps = []
    for order, index in enumerate(blanks, start=1):
        if len(blanks) == 1:
            ask = f"「{zh}」这个词里，空格缺的是什么字母？"
        else:
            ask = f"「{zh}」这个词里，{ordinal(order)}个空格缺的是什么字母？"
        steps.append(step(ask, word[index], f"这个空是整个词的{ordinal(index + 1)}个字母，这里要写 {word[index]}。"))
    letters = " ".join(word)
    steps.append(step(f"很好！现在把「{zh}」这个词完整地拼出来。", word, f"「{zh}」的英文是 {word}，一共 {len(word)} 个字母：{letters}。"))
    return steps


def build_spell(b: Builder, count: int) -> None:
    items = [(zh, word, mask) for zh, word, masks in SPELL_WORDS for mask in masks]
    rng.shuffle(items)
    done = 0
    for zh, word, mask in items:
        if done >= count:
            break
        if b.add("spell_double", "spell", f"看中文写单词：{zh} {mask}", spell_steps(zh, word, mask), word, [word]):
            done += 1
    if done < count:
        raise RuntimeError(f"拼写只有 {done}/{count} 条")


# ---------- 听懂孩子的话（500 条） ----------
INTENT_SYSTEM = (
    "你负责判断小学生对这一问的回复属于哪一类，只选一类：\n"
    "答题：孩子说出了一个答案，不管对错。\n"
    "不会：孩子说不知道、不会、想不出来。\n"
    "提问：孩子在问为什么、是什么意思、怎么做。\n"
    "其他：和题目无关的话。\n"
    '只输出一个 JSON 对象，格式：{"label":"类别"}，不要输出任何其他文字。'
)
# 问句、可能的回答、问句里能被追问的词
INTENT_QUESTIONS = [
    ("这句话里有一个词用错了，是哪个词？说出那个英文词。", ["go", "goes", "want", "wants", "has"], ["动词", "主语"]),
    ("读一读这句话，哪个英文词用得不对？", ["eat", "eats", "are", "is"], ["动词", "主语"]),
    ("主语是 He，play 应该改成什么？写出改好的词。", ["plays", "played", "playing", "play"], ["play", "主语"]),
    ("He 后面的 study 要变个样子，应该怎么写？", ["studies", "studys", "studied"], ["study", "主语"]),
    ("把 wash 改对，应该写成什么？", ["washes", "washs", "washed"], ["wash", "动词"]),
    ("主语 Tom 是哪一种：说「我」「你」「一个」还是「多个」？", ["一个", "多个", "我", "你"], ["主语", "Tom"]),
    ("那空里选 swim 还是 swims？", ["swim", "swims"], ["swim", "空格"]),
    ("那空里填 am、is 还是 are？", ["am", "is", "are"], ["am", "空格"]),
    ("这句话的主语是哪个词？请用英文说。", ["Lily", "She", "They", "The boy"], ["主语", "英文"]),
    ("空格前面紧挨着的那个英文词是什么？", ["two", "an", "can", "my"], ["空格", "英文"]),
    ("two 表示一个还是多个？", ["一个", "多个", "两个"], ["two", "多个"]),
    ("can 后面的动词要不要变形？说「要」或「不要」。", ["要", "不要"], ["can", "变形"]),
    ("「书」这个词里，第一个空格缺的是什么字母？", ["o", "e", "O", "a"], ["字母", "空格"]),
    ("很好！现在把「书」这个词完整地拼出来。", ["book", "boook", "bok", "b o o k"], ["拼", "字母"]),
    ("所以空里选 apple 还是 apples？", ["apple", "apples"], ["apple", "空格"]),
    ("Yesterday 说的是过去、现在，还是将来？", ["过去", "现在", "将来"], ["Yesterday", "过去"]),
]
ANSWER_WRAPS = ["{}", "{}", "{}", "是 {}", "{} 吧", "我觉得是 {}", "应该是 {}", "选 {}", "答案是 {}", "{}？", "嗯，{}", "就是 {}", "我猜 {}", "填 {}", "写 {}", "{} 对吗", "是不是 {}", "{}。", "好像是 {}", "大概是 {}"]
DONT_KNOW = [
    "不晓得", "我也不知道", "不太会", "这个不会", "这题不会", "我不懂", "不明白", "看不懂", "听不懂", "想不起来", "记不住", "记不得了", "答不上来",
    "写不出来", "真不会", "完全不会", "一点都不会", "好难", "这题好难", "太难啦", "我做不来", "没学会", "老师没教", "还没学", "没听过", "不认识这个词",
    "不会做", "不知道选哪个", "不知道填什么", "不知道怎么写", "我放弃", "算了不会", "我想不到", "脑子转不过来", "我答不了", "这个我没学", "不确定", "搞不懂",
]
QUESTIONS_GENERAL = [
    "为什么啊", "为啥呀", "为什么要这样", "为什么要变", "为啥要变", "为什么不对", "为啥错了", "为什么错了", "这为什么是错的", "啥意思", "什么意思呀", "这句话啥意思",
    "这句话是什么意思", "题目什么意思", "这题问的是什么", "问的是啥", "这是什么意思", "怎么做", "怎么做啊", "怎么写", "这个怎么写", "怎么读", "这个词怎么读", "要怎么改", "该怎么改",
    "怎么才能知道", "要写中文吗", "要写英文吗", "要说英文还是中文", "写几个词", "只写一个词吗", "能再说一遍吗", "可以再讲一遍吗", "能讲讲吗", "讲一下行吗", "告诉我为什么", "可以提示一下吗", "有什么提示吗", "什么叫主语", "主语是什么", "动词是什么", "啥是动词", "什么是变形", "变形是啥",
]
QUESTIONS_WORD = ["{} 是什么", "{} 是什么意思", "{} 是啥", "{} 什么意思", "啥叫 {}", "什么是 {}", "{} 怎么读", "为什么是 {}", "为啥要用 {}"]
OTHER_TALK = [
    "我要吃饭了", "我肚子饿", "我想喝水", "我要去厕所", "我想睡觉", "太困了", "我想看电视", "我想出去玩", "我要去放牛", "外面在下雪", "天黑了", "今天好热", "爷爷回来了", "奶奶喊我",
    "弟弟在哭", "狗在叫", "同学来找我了", "我明天再做", "明天再说", "我不想做了", "不想学了", "你好", "你好呀", "你叫什么名字", "你几岁了", "你是机器人吗", "你会唱歌吗", "讲个故事吧", "给我讲笑话",
    "嘿嘿", "呵呵", "好的", "好吧", "嗯", "哦", "知道了", "谢谢", "谢谢老师", "再见", "拜拜", "手机快没电了", "我妈回来了", "等会儿", "等等我", "稍等", "我去拿笔", "笔没水了", "本子找不到了", "作业好多", "今天星期几", "我喜欢你",
]


def intent_row(number: int, question: str, reply: str, label: str, template: str) -> dict:
    user = f"这一问：{question}\n孩子说：{reply}"
    return {
        "id": f"xk5-intent-{number:05d}",
        "type": "intent",
        "template": f"v5/intent/{template}",
        "split": "train",
        "meta": {"gold": label, "reply": reply, "question": question},
        "recognizedText": reply,
        "plan": {},
        "messages": [
            {"role": "system", "content": INTENT_SYSTEM},
            {"role": "user", "content": user},
            {"role": "assistant", "content": json.dumps({"label": label}, ensure_ascii=False)},
        ],
    }


def varied(text: str) -> str:
    return rng.choice([text, text, f"{text}啊", f"{text}呀", f"老师，{text}", f"嗯，{text}"]) if not text.endswith(("吗", "啊", "呀")) else text


def build_intent(counts: dict[str, int]) -> list[dict]:
    rows, seen = [], set()
    number = 0

    def emit(question: str, reply: str, label: str) -> bool:
        nonlocal number
        key = (question, reply)
        if key in seen or reply in TESTED_REPLIES:
            return False
        seen.add(key)
        number += 1
        rows.append(intent_row(number, question, reply, label, label))
        return True

    for label, want in counts.items():
        done = tries = 0
        while done < want:
            tries += 1
            if tries > want * 500:
                raise RuntimeError(f"{label} 只凑出 {done}/{want}")
            question, answers, words = rng.choice(INTENT_QUESTIONS)
            if label == "答题":
                reply = rng.choice(ANSWER_WRAPS).format(rng.choice(answers))
            elif label == "不会":
                reply = varied(rng.choice(DONT_KNOW))
            elif label == "提问":
                if rng.random() < 0.65:
                    reply = varied(rng.choice(QUESTIONS_GENERAL))
                else:
                    reply = varied(rng.choice(QUESTIONS_WORD).format(rng.choice(words)))
            else:
                reply = varied(rng.choice(OTHER_TALK))
            done += emit(question, reply, label)
    return rows


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    train, held, ext = load("train.jsonl"), load("heldout.jsonl"), load("external_test.jsonl")
    empty = next(r for r in train if not r["plan"]["reminders"])["messages"][1]["content"]
    empty_line = next(line for line in empty.splitlines() if line.startswith("孩子犯过的错"))

    # 先确认重建的格式和旧数据逐字一致
    mismatch = [r["id"] for r in train if user_message(r["recognizedText"], r["plan"], empty_line) != r["messages"][1]["content"]]
    if mismatch:
        raise RuntimeError(f"格式重建和旧数据不一致 {len(mismatch)} 条，例如 {mismatch[:3]}")
    print("格式重建：5123 条旧数据逐字一致")

    cleaned, changed = [], 0
    for row in train:
        new, did = clean_row(row, empty_line)
        cleaned.append(new)
        changed += did
    left = sum("第三人称" in r["messages"][2]["content"] for r in cleaned)
    print(f"旧数据改掉术语 {changed} 条，答案里还剩 {left} 条")

    blocked = {r["recognizedText"] for r in train + held + ext} | TESTED_ITEMS
    b = Builder(cleaned, blocked, empty_line)

    # 代词主语 300
    for subject, count in (("She", 30), ("He", 30), ("It", 30), ("We", 20), ("They", 20), ("You", 20)):
        fill(b, count, lambda bb, s=subject: make_fix_be_pron(bb, s))
    for subject, count in (("She", 30), ("He", 30), ("It", 30)):
        fill(b, count, lambda bb, s=subject: make_fix_3s_one(bb, s))
    for subject, count in (("We", 20), ("They", 20), ("You", 20)):
        fill(b, count, lambda bb, s=subject: make_fix_3s_many(bb, s))
    # 原形 250
    fill(b, 110, make_base_choose)
    fill(b, 30, make_animal_choose)
    fill(b, 110, make_base_fix)
    # 改错缺口 300
    fill(b, 70, make_fix_i)
    fill(b, 70, make_fix_dont)
    fill(b, 60, make_fix_was)
    fill(b, 50, make_fix_there)
    fill(b, 50, make_choose_there)
    # 拼写 100
    build_spell(b, 100)
    # 听懂孩子的话 500
    intent = build_intent({"答题": 150, "不会": 110, "提问": 140, "其他": 100})

    final = cleaned + b.rows + intent
    with open(OUT / "train.jsonl", "w", encoding="utf-8", newline="\n") as handle:
        for row in final:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    for name in ("heldout.jsonl", "external_test.jsonl"):
        shutil.copyfile(V4 / name, OUT / name)

    stats = {
        "train": len(final),
        "train_old": len(cleaned),
        "train_old_jargon_changed": changed,
        "train_new_steps": len(b.rows),
        "train_new_intent": len(intent),
        "new_by_template": dict(Counter(r["template"] for r in b.rows + intent)),
        "heldout_unchanged_from_v4": True,
        "external_test_unchanged_from_v4": True,
        "md5": {
            name: hashlib.md5((OUT / name).read_bytes()).hexdigest()
            for name in ("train.jsonl", "heldout.jsonl", "external_test.jsonl")
        },
    }
    (OUT / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in stats.items() if k != "md5"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
