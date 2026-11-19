"""端侧提示、程序兜底讲法和云端答疑共用的规则。

每条规则都要讲清适用条件。v_3s 家族配一个 can 反例，免得孩子把
「动词要加 s」当成任何句子都成立的通则，碰上 can 又不知道怎么办。
"""

VERB_3S = {
    "v_3s": (
        "主语是 he、she、it，句子里又没有 can 这些帮忙的词，动词才要加 s。",
        "有 can 帮忙就不变：She can run fast，run 用原来的样子。",
    ),
    "v_es": (
        "主语是 he、she、it，句子里又没有 can 这些帮忙的词，这个动词才要加 es。",
        "有 can 帮忙就不变：She can watch TV，watch 用原来的样子。",
    ),
    "v_ies": (
        "主语是 he、she、it，句子里又没有 can 这些帮忙的词，这个动词才要把 y 改成 i，再加 es。",
        "有 can 帮忙就不变：She can fly kites，fly 用原来的样子。",
    ),
    "v_irregular": (
        "主语是 he、she、it，句子里又没有 can 这些帮忙的词，这个动词才有特殊变化。",
        "有 can 帮忙就不变：She can have lunch，have 用原来的样子。",
    ),
}

V_3S = "主语是一个别人，句子里又没有 can 这些帮忙的词，动词才要变。"
V_3S_COUNTER = VERB_3S["v_3s"][1]
V_BASE_AFTER_COUNTER = "如果说的是 he、she、it 平时做的事，又没有这些帮忙的词，动词就要变：He goes to school，go 变成了 goes。"
