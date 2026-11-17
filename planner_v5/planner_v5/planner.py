# -*- coding: utf-8 -*-
"""planner_v5: 程序定步骤/expect/规则，模型只润色 ask/explain。纯 CPU、无依赖。
所有"工具"函数都是 纯函数，输入字符串，输出 dict / list / None（None = 没把握）。"""
import re, json

# ---------------------------------------------------------------- 词表
PRON = {  # 主语代词 -> (中文, 类别)  类别: I / you / one / many
    'i': ('我', 'I'), 'you': ('你（或你们）', 'you'), 'he': ('他', 'one'), 'she': ('她', 'one'),
    'it': ('它', 'one'), 'we': ('我们', 'many'), 'they': ('他们（她们、它们）', 'many'),
}
DET_ONE = {'a', 'an', 'one', 'this', 'that', 'every', 'each'}
DET_MANY = {'these', 'those', 'many', 'some', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight',
            'nine', 'ten', 'eleven', 'twelve', 'twenty', 'lots', 'few', 'both', 'all'}
POSS = {'my', 'your', 'his', 'her', 'our', 'their', 'its', 'the'}
DETS = DET_ONE | DET_MANY | POSS
ADJ = {'little', 'big', 'small', 'old', 'new', 'best', 'good', 'young', 'nice', 'red', 'white', 'black'}
NOT_NAME = {'look', 'let', 'please', 'is', 'are', 'am', 'do', 'does', 'can', 'where', 'what', 'who', 'how',
            'when', 'which', 'there', 'here', 'change', 'yesterday', 'last', 'every', 'on', 'in', 'at', 'now',
            'today', 'tomorrow', 'once', 'then'}
TIME_START = {'yesterday', 'last', 'every', 'on', 'in', 'at', 'now', 'today', 'tomorrow', 'usually', 'often'}
IRR_PL = {'child': 'children', 'man': 'men', 'woman': 'women', 'mouse': 'mice', 'foot': 'feet',
          'tooth': 'teeth', 'goose': 'geese', 'person': 'people', 'policeman': 'policemen'}
SPECIAL_PL = {'knife': ('knives', 'plural_ves'), 'leaf': ('leaves', 'plural_ves'), 'wolf': ('wolves', 'plural_ves'),
              'tomato': ('tomatoes', 'plural_oes'), 'potato': ('potatoes', 'plural_oes'), 'hero': ('heroes', 'plural_oes')}
IRR_PL_INV = {v: k for k, v in IRR_PL.items()}
IRR_PL_INV.update({v[0]: k for k, v in SPECIAL_PL.items()})
AMBIG_PL = {'sheep', 'fish', 'deer', 'chinese', 'people'}  # people 无单数歧义，但不归单
NOT_PLURAL_S = {'bus', 'class', 'glass', 'grass', 'dress', 'is', 'his', 'this', 'us', 'yes', 'boss', 'chris', 'james', 'thomas'}
MODALS = {'can', 'will', 'must', 'should', 'may', 'could', 'would', 'did', "can't", "don't", "doesn't",
          'is', 'am', 'are', 'was', 'were'}
ADVS = {'often', 'always', 'usually', 'sometimes', 'never', 'also', 'really'}
NUM_CN = '一二三四五六七八九十'

def toks(s):
    return re.findall(r"_+|[A-Za-z][A-Za-z']*|[.,!?;]", s)

def strip_cn(s):
    """去掉中文括注 （...） 和全部中文，保留英文句子。"""
    s = re.sub(r'（[^）]*）', ' ', s)
    return s

# ---------------------------------------------------------------- 形态规则
def is_vowel(c): return c.lower() in 'aeiou'

def pluralize(w):
    lw = w.lower()
    if lw in IRR_PL: return IRR_PL[lw], 'plural_irregular'
    if lw in SPECIAL_PL: return SPECIAL_PL[lw]
    if re.search(r'(s|x|ch|sh)$', lw): return lw + 'es', 'plural_es'
    if lw.endswith('y') and len(lw) > 1 and not is_vowel(lw[-2]): return lw[:-1] + 'ies', 'plural_ies'
    return lw + 's', 'plural_s'

def third(w):
    """动词原形 -> (三单形式, 规则key)"""
    lw = w.lower()
    if lw == 'have': return 'has', 's3_has'
    if lw in ('be',) : return None, None
    if re.search(r'(s|x|ch|sh|o)$', lw): return lw + 'es', 's3_es'
    if lw.endswith('y') and len(lw) > 1 and not is_vowel(lw[-2]): return lw[:-1] + 'ies', 's3_ies'
    return lw + 's', 's3_add_s'

def noun_number(w):
    lw = w.lower()
    if lw in AMBIG_PL and lw != 'people': return None
    if lw in IRR_PL_INV or lw == 'people': return 'many'
    if lw in IRR_PL or lw in SPECIAL_PL: return 'one'
    if lw.endswith('s') and lw not in NOT_PLURAL_S and not lw.endswith('ss') and not lw.endswith('us'): return 'many'
    return 'one'

# ---------------------------------------------------------------- 工具函数
def find_subject(sentence, end=None):
    """在句子(英文) 中找主语。end=主语后第一个词的下标（空格或动词位置），None 时自动从句首找。
    返回 {'text','gloss','cat'(I/you/one/many),'start','end'} 或 None（没把握）。"""
    t = toks(strip_cn(sentence))
    words = [x for x in t]
    i = 0
    # 跳过句首时间状语，如 Yesterday evening / Last month / In spring ,
    while i < len(words) and words[i].lower() in TIME_START:
        lw = words[i].lower()
        if lw in ('on', 'in', 'at', 'last', 'every'):
            i += 2  # Last month / In spring / On Sundays
        else:
            i += 1  # Yesterday / Now
        if i < len(words) and words[i].lower() in ('morning', 'afternoon', 'evening', 'night'): i += 1
        if i < len(words) and words[i] == ',': i += 1
    if end is not None:
        seg = words[i:end]
        if not seg: return None
    else:
        seg = None
    def np_at(j):
        if j >= len(words): return None
        w = words[j]; lw = w.lower()
        if lw in PRON: return j + 1, PRON[lw][1], PRON[lw][0]
        if lw in DETS:
            k = j + 1
            while k < len(words) and words[k].lower() in ADJ: k += 1
            if k >= len(words) or not words[k][0].isalpha(): return None
            n = noun_number(words[k])
            if n is None: return None
            if lw in DET_MANY: n = 'many'
            return k + 1, n, None
        if w[0].isupper() and lw not in NOT_NAME and w.isalpha():
            k = j + 1
            while k < len(words) and words[k][:1].isupper() and words[k].isalpha() and words[k].lower() not in PRON: k += 1
            return k, 'one', None
        return None
    r = np_at(i)
    if not r: return None
    k, cat, gloss = r
    if k < len(words) and words[k].lower() == 'and':
        r2 = np_at(k + 1)
        if not r2: return None
        k = r2[0]; cat = 'many'; gloss = None
    if end is not None and k != end: return None  # 主语没正好接到空格/动词前 -> 没把握
    text = ' '.join(words[i:k])
    if gloss is None: gloss = None
    return {'text': text, 'gloss': gloss, 'cat': cat, 'start': i, 'end': k}

def diff_word(orig, corrected):
    """两句只差一个词时返回 {'index','wrong','right'}，否则 None。"""
    a, b = toks(orig), toks(corrected)
    if len(a) != len(b): return None
    d = [(i, x, y) for i, (x, y) in enumerate(zip(a, b)) if x != y]
    if len(d) != 1: return None
    i, x, y = d[0]
    return {'index': i, 'wrong': x, 'right': y}

def spell_slots(pattern, answer):
    """b__k + book -> [{'pos':1,'letter':'o','nth':1},{'pos':2,'letter':'o','nth':2}]；对不齐返回 None。"""
    if not pattern or not answer or len(pattern) != len(answer) or '_' not in pattern: return None
    out = []
    for i, (p, a) in enumerate(zip(pattern, answer)):
        if p == '_':
            if not a.isalpha(): return None
            out.append({'pos': i, 'letter': a.lower(), 'nth': len(out) + 1})
        elif p.lower() != a.lower():
            return None
    return out

def parse_options(text):
    m = re.search(r'\(([A-Za-z]+(?:\s*/\s*[A-Za-z]+)+)\)\s*$', text.strip())
    return [o.strip() for o in m.group(1).split('/')] if m else None

def cue_word(text):
    """空格前（或后）决定"一个/多个"的提示词。返回 {'word','where','cat'} 或 None。"""
    s = strip_cn(re.sub(r'\(([A-Za-z]+(?:\s*/\s*[A-Za-z]+)+)\)\s*$', '', text))
    t = toks(s)
    if '___' not in ''.join(t): return None
    bi = next(i for i, x in enumerate(t) if x.startswith('_'))
    if bi > 0:
        p = t[bi - 1].lower()
        if p == 'of' and bi >= 3 and t[bi - 2].lower() == 'lot': return {'word': 'a lot of', 'where': 'before', 'cat': 'many'}
        if p == 'of' and bi >= 2 and t[bi - 2].lower() == 'lots': return {'word': 'lots of', 'where': 'before', 'cat': 'many'}
        if p in DET_ONE: return {'word': t[bi - 1], 'where': 'before', 'cat': 'one'}
        if p in DET_MANY: return {'word': t[bi - 1], 'where': 'before', 'cat': 'many'}
        if p not in POSS: return None
    # 前面是 the/my 之类，看后面：___ are / ___ is / 下一句 It is / They are
    j = bi + 1
    if j < len(t) and t[j].startswith('('):
        j += 1
    rest = [x for x in t[j:] if not x.startswith('(')]
    # 去掉 fill 题的 (box)
    rest_s = re.sub(r'^\s*\([A-Za-z]+\)', '', s.split(t[bi], 1)[1])
    rt = toks(rest_s)
    if rt and rt[0].lower() == 'is': return {'word': 'is', 'where': 'after', 'cat': 'one'}
    if rt and rt[0].lower() == 'are': return {'word': 'are', 'where': 'after', 'cat': 'many'}
    if bi >= 2 and t[bi - 2].lower() in ('is', 'are') and t[bi - 1].lower() in POSS:  # Where is my ___?
        return {'word': t[bi - 2], 'where': 'before2', 'cat': 'one' if t[bi - 2].lower() == 'is' else 'many'}
    ends = [i for i, x in enumerate(rt) if x in '.!?']
    if ends:
        k = ends[0] + 1
        if k + 1 < len(rt):
            a, b = rt[k].lower(), rt[k + 1].lower()
            if a == 'it' and b == 'is': return {'word': 'It', 'where': 'next_sentence', 'cat': 'one'}
            if a == 'they' and b == 'are': return {'word': 'They', 'where': 'next_sentence', 'cat': 'many'}
    return None

FIX_PREFIX = re.compile(r'^\s*改错（只改一个词）[:：]\s*')
SPELL_RE = re.compile(r'^\s*看中文写(?:英文)?单词[:：]\s*([\u4e00-\u9fff]+)\s+([A-Za-z_]*_[A-Za-z_]*)\s*$')

def _fix_3s_candidate(sentence, gold=None):
    """改错句里找三单错误。返回 {'wrong','right','index','subject','dir'} 或 None。"""
    s = strip_cn(sentence)
    t = toks(s)
    subj = find_subject(s)
    if not subj or subj['cat'] is None: return None
    cands = []
    j = subj['end']
    while j < len(t) and t[j].lower() in ADVS: j += 1
    if j < len(t): cands.append(j)
    cands += [k + 1 for k in range(subj['end'], len(t) - 1) if t[k].lower() == 'and' and k + 1 > j]
    for k in cands:
        w = t[k]; lw = w.lower()
        if lw in MODALS or not w.isalpha(): continue
        if k > 0 and t[k - 1].lower() in DETS: continue  # 名词
        if gold:
            g = gold.lower()
            if third(lw)[0] == g and subj['cat'] == 'one':
                return {'wrong': w, 'right': gold, 'index': k, 'subject': subj, 'dir': 'to_3s'}
            if third(g)[0] == lw and subj['cat'] in ('many', 'I', 'you'):
                return {'wrong': w, 'right': gold, 'index': k, 'subject': subj, 'dir': 'to_base'}
        else:
            if subj['cat'] == 'one' and not lw.endswith('s') and not lw.endswith('ed') and k == j:
                return {'wrong': w, 'right': None, 'index': k, 'subject': subj, 'dir': 'to_3s'}
            if subj['cat'] in ('many', 'I', 'you') and (lw.endswith('es') or lw in ('has',) or (lw.endswith('s') and not lw.endswith('ss'))):
                return {'wrong': w, 'right': None, 'index': k, 'subject': subj, 'dir': 'to_base'}
    return None

def detect_type(item_text, gold=None):
    """-> be_verb | plural_noun | verb_3s | spelling | unknown（gold 可选，用于改错题确认）"""
    tx = item_text.strip()
    if SPELL_RE.match(tx): return 'spelling'
    if FIX_PREFIX.match(tx):
        return 'verb_3s' if _fix_3s_candidate(FIX_PREFIX.sub('', tx), gold) else 'unknown'
    if '___' not in tx: return 'unknown'
    opts = parse_options(tx)
    if opts and {o.lower() for o in opts} <= {'am', 'is', 'are'} and len(opts) >= 2: return 'be_verb'
    cw = cue_word(tx)
    if opts and len(opts) == 2 and cw:
        a, b = opts[0].lower(), opts[1].lower()
        if pluralize(a)[0] == b or pluralize(b)[0] == a: return 'plural_noun'
    m = re.search(r'___\s*\(([A-Za-z]+)\)', tx)
    if not opts and m and cw: return 'plural_noun'  # 填空：two ___ (box)
    return 'unknown'

# ---------------------------------------------------------------- 规则表
RULES = {
    # be 动词
    'be_I':        'I（我）后面的 be 动词只用 am：I am。',
    'be_you':      'you（你、你们）后面用 are：you are。',
    'be_one':      '说的是一个人或一样东西（he、she、it、Mike、this cat……）时，用 is。',
    'be_many':     '说的是两个或更多（we、they、Mike and Amy、these birds……）时，用 are。',
    'be_song':     '口诀：I 用 am，you 用 are，一个用 is，多个用 are。',
    'subj_who':    '{subj} 是“{gloss}”。',
    'subj_num':    '{subj} 说的是{num}。',
    # 名词单复数
    'cue_one':     '{cue} 后面说的是一个东西，名词用原来的样子，不加 s。',
    'cue_many':    '{cue} 后面说的是两个或更多，名词要变成“多个”的样子（复数）。',
    'cue_is':      '句子里和它搭配的是 {cue}，说明说的是一个，名词用原来的样子。',
    'cue_are':     '句子里和它搭配的是 {cue}，说明说的是多个，名词要用复数（“多个”的样子）。',
    'plural_s':    '大多数名词变多个，直接在后面加 s，比如 cat→cats。',
    'plural_es':   '以 s、x、ch、sh 结尾的名词变多个，加 es，比如 box→boxes。',
    'plural_ies':  '辅音字母加 y 结尾的名词，把 y 变成 i 再加 es，比如 baby→babies。',
    'plural_oes':  'tomato、potato 这几个以 o 结尾的词，变多个要加 es：tomato→tomatoes。',
    'plural_ves':  'knife、leaf、wolf 这类以 f 或 fe 结尾的词，变多个把 f 换成 v 再加 es：knife→knives。',
    'plural_irregular': '有些名词变多个很特别，要记住：mouse→mice，child→children，man→men，foot→feet。',
    # 动词加 s（改错）
    's3_find':     '改错题：从主语 {subj} 开始一个词一个词读，找和主语不搭配的那个词。',
    's3_add_s':    '主语是一个人或一样东西（he、she、it、Tom……），说平时经常做的事，动词后面加 s。',
    's3_es':       '动词以 s、x、ch、sh、o 结尾，加 es，比如 watch→watches，go→goes。',
    's3_ies':      '动词是辅音字母加 y 结尾，把 y 变成 i 再加 es，比如 cry→cries。',
    's3_has':      '主语是一个人或一样东西时，have 要变成 has。',
    's3_base':     '主语是 I、you、we、they 或两个以上的人和东西时，动词用原来的样子，不加 s。',
    # 拼写
    'spell_letter': '先想想“{cn}”的英文怎么读，再按读音一个字母一个字母地拼。',
    'spell_double': '这里有两个一样的字母 {L}{L} 挨在一起，拼的时候别漏掉一个。',
    'spell_whole':  '把填好的字母放回空格里，从头到尾拼一遍、读一遍。',
    'generic':      '仔细读题，按题目要求作答。',
}
NUM_TXT = {'one': '一个（一个人或一样东西）', 'many': '多个（两个或更多）'}

def get_rule(key, **kw):
    r = RULES[key]
    try: return r.format(**kw)
    except KeyError: return r

def _single(item_text, gold, why):
    return {'type': 'unknown', 'fallback': True, 'why': why,
            'steps': [{'intent': 'whole', 'expect': gold, 'rule': get_rule('generic'), 'rule_keys': ['generic'], 'cue': None}]}

def _subj_gloss(subj):
    if subj['gloss']: return subj['gloss']
    return None

# ---------------------------------------------------------------- 计划
def plan_steps(item_text, gold_answer):
    """-> {'type','fallback','why','meta',steps:[{intent,expect,rule,rule_keys,cue}]}"""
    tx = item_text.strip(); g = gold_answer.strip()
    ty = detect_type(tx, g)
    try:
        if ty == 'be_verb':   p = _plan_be(tx, g)
        elif ty == 'plural_noun': p = _plan_plural(tx, g)
        elif ty == 'verb_3s': p = _plan_3s(tx, g)
        elif ty == 'spelling': p = _plan_spell(tx, g)
        else: return _single(tx, g, 'type_unknown')
    except Exception as e:  # 任何意外都退回单步
        return _single(tx, g, f'error:{e!r}')
    if isinstance(p, str): return _single(tx, g, p)
    p['type'] = ty; p['fallback'] = False; p['why'] = None
    assert p['steps'][-1]['expect'] == g
    return p

def _plan_be(tx, g):
    s = strip_cn(re.sub(r'\([^)]*\)\s*$', '', tx))
    t = toks(s)
    bi = next(i for i, x in enumerate(t) if x.startswith('_'))
    subj = find_subject(s, end=bi)
    if not subj: return 'be:subject_not_found'
    cat = subj['cat']
    pred = {'I': 'am', 'you': 'are', 'one': 'is', 'many': 'are'}[cat]
    if pred != g.lower(): return f'be:rule_says_{pred}_gold_{g}'
    opts = parse_options(tx)
    st1 = {'intent': 'subject_who' if cat in ('I', 'you') else 'subject_number', 'cue': subj['text']}
    if cat in ('I', 'you'):
        st1['expect'] = subj['text']
        st1['rule'] = get_rule('subj_who', subj=subj['text'], gloss=subj['gloss']); st1['rule_keys'] = ['subj_who']
    else:
        st1['expect'] = '一个' if cat == 'one' else '多个'
        r = (get_rule('subj_who', subj=subj['text'], gloss=subj['gloss']) if subj['gloss'] else '') + get_rule('subj_num', subj=subj['text'], num=NUM_TXT[cat])
        st1['rule'] = r; st1['rule_keys'] = (['subj_who'] if subj['gloss'] else []) + ['subj_num']
    k = {'I': 'be_I', 'you': 'be_you', 'one': 'be_one', 'many': 'be_many'}[cat]
    st2 = {'intent': 'choose', 'expect': g, 'cue': ' / '.join(opts), 'rule': get_rule(k) + get_rule('be_song'), 'rule_keys': [k, 'be_song']}
    return {'meta': {'subject': subj['text'], 'subject_cat': cat, 'options': opts}, 'steps': [st1, st2]}

def _plan_plural(tx, g):
    cw = cue_word(tx)
    opts = parse_options(tx)
    m = re.search(r'___\s*\(([A-Za-z]+)\)', tx)
    base = None
    if opts:
        a, b = opts[0].lower(), opts[1].lower()
        if pluralize(a)[0] == b: base, pl = a, b
        elif pluralize(b)[0] == a: base, pl = b, a
        else: return 'plural:not_a_pair'
        mode = 'choose'
    else:
        base = m.group(1).lower(); pl = pluralize(base)[0]; mode = 'fill'
    pred = base if cw['cat'] == 'one' else pl
    if pred != g.lower(): return f'plural:rule_says_{pred}_gold_{g}'
    ck = {'before': {'one': 'cue_one', 'many': 'cue_many'}, 'before2': {'one': 'cue_is', 'many': 'cue_are'}, 'after': {'one': 'cue_is', 'many': 'cue_are'},
          'next_sentence': {'one': 'cue_is', 'many': 'cue_are'}}[cw['where']][cw['cat']]
    cue_disp = cw['word'] if cw['where'] != 'next_sentence' else cw['word'] + (' is' if cw['cat'] == 'one' else ' are')
    st1 = {'intent': 'cue_word_' + cw['where'], 'expect': cw['word'], 'cue': cw['word'],
           'rule': get_rule(ck, cue=cue_disp), 'rule_keys': [ck]}
    keys = [ck]
    if cw['cat'] == 'many': keys.append(pluralize(base)[1])
    st2 = {'intent': mode, 'expect': g, 'cue': ' / '.join(opts) if opts else base,
           'rule': ''.join(get_rule(k, cue=cue_disp) for k in keys), 'rule_keys': keys}
    return {'meta': {'cue': cw, 'base': base, 'plural': pl, 'mode': mode}, 'steps': [st1, st2]}

def _plan_3s(tx, g):
    sent = FIX_PREFIX.sub('', tx)
    c = _fix_3s_candidate(sent, g)
    if not c: return '3s:no_candidate'
    t = toks(strip_cn(sent)); t2 = list(t); t2[c['index']] = g
    corrected = ' '.join(t2).replace(' .', '.').replace(' ,', ',')
    d = diff_word(strip_cn(sent), corrected)
    if not d or d['right'] != g: return '3s:diff_not_single'
    subj = c['subject']
    if c['dir'] == 'to_3s':
        _, k = third(c['wrong'])
        keys = ['s3_add_s'] + ([k] if k != 's3_add_s' else [])
        if k == 's3_has': keys = ['s3_has']
    else:
        keys = ['s3_base']
    sg = (f"（{subj['gloss']}）" if subj['gloss'] else '')
    st1 = {'intent': 'find_wrong_word', 'expect': d['wrong'], 'cue': subj['text'],
           'rule': get_rule('s3_find', subj=subj['text'] + sg), 'rule_keys': ['s3_find']}
    st2 = {'intent': 'correct_word', 'expect': g, 'cue': d['wrong'],
           'rule': (f"主语 {subj['text']}{sg} 说的是{NUM_TXT[subj['cat']]}。" if subj['cat'] in ('one', 'many') else f"主语是 {subj['text']}{sg}。")
                   + ''.join(get_rule(k) for k in keys),
           'rule_keys': keys}
    return {'meta': {'subject': subj['text'], 'subject_cat': subj['cat'], 'corrected_sentence': corrected, 'dir': c['dir']},
            'steps': [st1, st2]}

def _plan_spell(tx, g):
    m = SPELL_RE.match(tx); cn, pat = m.group(1), m.group(2)
    slots = spell_slots(pat, g)
    if not slots: return 'spell:pattern_mismatch'
    steps = []; cur = list(pat)
    for s in slots:
        L = s['letter']
        neigh = ''.join(g[max(0, s['pos'] - 1): s['pos'] + 2]).lower()
        dbl = (s['pos'] > 0 and g[s['pos'] - 1].lower() == L) or (s['pos'] + 1 < len(g) and g[s['pos'] + 1].lower() == L)
        keys = ['spell_letter'] + (['spell_double'] if dbl else [])
        steps.append({'intent': 'spell_letter', 'expect': L, 'cue': ''.join(cur), 'slot': s['nth'], 'pos': s['pos'] + 1,
                      'rule': ''.join(get_rule(k, cn=cn, L=L) for k in keys), 'rule_keys': keys})
        cur[s['pos']] = L
    steps.append({'intent': 'spell_whole', 'expect': g, 'cue': pat, 'rule': get_rule('spell_whole'), 'rule_keys': ['spell_whole']})
    # 安全：任何 cue（会被展示）都不能是完整单词
    assert all(st['cue'].lower() != g.lower() for st in steps)
    return {'meta': {'cn': cn, 'pattern': pat, 'n_blanks': len(slots)}, 'steps': steps}

# ---------------------------------------------------------------- 检查
BE = {'am', 'is', 'are'}
JARGON = ['第三人称', '单数', '宾格', '比较级', '过去式', '物主代词', '谓语']
def _has(text, w):
    if not w: return False
    if re.fullmatch(r"[A-Za-z' ]+", w):
        # 单字母/单词按整词匹配；紧挨下划线的字母属于题面 b__k，不算说出答案
        return re.search(r"(?<![A-Za-z_])" + re.escape(w) + r"(?![A-Za-z_])", text, re.I) is not None
    return w in text

def _allowed_be(plan):
    return {w.lower() for p in plan['steps'] for w in toks((p.get('cue') or '') + ' ' + p['rule'])} & BE

def check_steps(model_json, plan):
    """返回 (ok, errors[list[str]], steps)。model_json 可为 str 或 dict。"""
    errs = []
    try:
        obj = json.loads(model_json) if isinstance(model_json, str) else model_json
    except Exception as e:
        return False, [f'JSON 解析失败：{e}'], None
    if not isinstance(obj, dict) or not isinstance(obj.get('steps'), list):
        return False, ['格式不对：必须是 {"steps":[...]}'], None
    st = obj['steps']; ps = plan['steps']
    for i, s in enumerate(st, 1):
        if not isinstance(s, dict) or set(s) != {'ask', 'expect', 'explain'} or not all(isinstance(s[k], str) and s[k].strip() for k in s):
            errs.append(f'第{i}步格式不对：必须且只能有非空的 ask、expect、explain')
    if errs: return False, errs, st
    if len(st) != len(ps): errs.append(f'步数不对：应该 {len(ps)} 步，输出了 {len(st)} 步')
    for i, (s, p) in enumerate(zip(st, ps), 1):
        if s['expect'].strip().lower() != p['expect'].strip().lower():
            errs.append(f'第{i}步 expect 应该是“{p["expect"]}”，不能写成“{s["expect"]}”')
    final = ps[-1]['expect']
    for i, s in enumerate(st[:len(ps)], 1):
        later = [p['expect'] for p in ps[i:] if p['expect'].lower() != ps[i - 1]['expect'].lower()]
        for e in later:
            if _has(s['ask'], e): errs.append(f'第{i}步 ask 提前说出了后面的答案“{e}”')
            if _has(s['explain'], e): errs.append(f'第{i}步 explain 提前说出了后面的答案“{e}”')
    for i, (s, p) in enumerate(zip(st, ps), 1):  # ask 不能直接把本步答案说出来（列选项除外）
        e = re.escape(p['expect'])
        if p['intent'] != 'whole' and re.search(r'((?<![还或])是|填|写|选|改成|用)\s*[「“"]?' + e + r'(?![A-Za-z_])(?!\s*[」”"]?\s*[，,]?\s*(、|/|或|还是|和))', s['ask'], re.I):
            errs.append(f'第{i}步 ask 直接说出了本步答案“{p["expect"]}”')
    for i, s in enumerate(st, 1):
        bad = [w for w in JARGON if w in s['ask'] + s['explain']]
        if bad: errs.append(f'第{i}步用了孩子听不懂的术语：{"、".join(bad)}')
    if plan.get('type') == 'spelling':
        for i, s in enumerate(st, 1):
            if _has(s['ask'], final): errs.append(f'第{i}步 ask 写出了整个单词，拼写题不允许')
    if plan.get('type') != 'be_verb':
        allowed = _allowed_be(plan)
        for i, s in enumerate(st, 1):
            bad = [w for w in BE - allowed if _has(s['explain'], w)]
            if bad: errs.append(f'第{i}步 explain 出现了和本题无关的 {"/".join(bad)}')
    return (not errs), errs, st

# ---------------------------------------------------------------- 模型输入
INTENT_CN = {
    'subject_number': '让孩子看主语说的是一个还是多个', 'subject_who': '让孩子说出主语是谁',
    'choose': '让孩子从选项里选', 'fill': '让孩子写出括号里单词的正确形式',
    'cue_word_before': '让孩子找空格前面紧挨着的提示词', 'cue_word_after': '让孩子找空格后面紧挨着的提示词',
    'cue_word_next_sentence': '让孩子找后一句开头的提示词', 'cue_word_before2': '让孩子找空格前面的 is 或 are',
    'find_wrong_word': '让孩子找出句子里用错的那个词', 'correct_word': '让孩子说出这个词应该改成什么',
    'spell_letter': '让孩子说出第{slot}个空格（单词第{pos}个字母）是哪个字母', 'spell_whole': '让孩子把整个单词拼出来',
    'whole': '让孩子直接回答这道题',
}

def build_user_message(item, plan):
    """item: 题目文本。紧凑中文输入：步骤已定，只写 ask/explain。"""
    L = [f'题目：{item}', f'步骤已由程序定好，共 {len(plan["steps"])} 步，不要增删，expect 照抄：']
    for i, s in enumerate(plan['steps'], 1):
        intent = INTENT_CN[s['intent']].format(**{k: s.get(k) for k in ('slot', 'pos')})
        extra = f'｜可以给孩子看：{s["cue"]}' if s['intent'] == 'spell_letter' else ''
        L.append(f'{i}. 目的：{intent}｜expect：{s["expect"]}｜规则：{s["rule"]}{extra}')
    L.append('你只写每步的 ask（问孩子的一句中文，一次只问一件事）和 explain（按上面的规则，用温暖、简单的中文讲清楚）。')
    if plan.get('type') != 'be_verb' and not _allowed_be(plan):
        L.append('这道题和 am、is、are 无关，ask 和 explain 里都不要提 am、is、are 的口诀。')
    L.append('不要说出后面步骤的 expect；不要讲和规则无关的内容；不要用“第三人称单数”这类术语。' + ('任何 ask 都不能写出整个单词。' if plan.get('type') == 'spelling' else ''))
    L.append('只输出 {"steps":[{"ask":"...","expect":"...","explain":"..."}]}')
    return '\n'.join(L)

SHORT_SYSTEM = ('你是乡村小学英语辅导里的“小模型”。程序已经把题目拆好步骤，并给出每一步的 expect 和规则。'
                '你只负责把每一步写成孩子听得懂的话：ask 是问孩子的一句中文，explain 是答不出时的温暖解释。'
                '步数和 expect 必须和给定的一模一样。只输出一个 JSON 对象 {"steps":[{"ask":"...","expect":"...","explain":"..."}]}，不要输出其他文字。')

def retry_message(item, plan, errors):
    return build_user_message(item, plan) + '\n上次输出有这些问题，请改正后重新输出：\n' + '\n'.join('- ' + e for e in errors)

# ---------------------------------------------------------------- 模板兜底
def template_fallback(plan, item=None):
    out = []
    for s in plan['steps']:
        it, e, r, cue = s['intent'], s['expect'], s['rule'], s.get('cue')
        if it == 'subject_number': ask = f'先看空格前面的 {cue}，它说的是一个，还是多个？说“一个”或“多个”。'
        elif it == 'subject_who': ask = '空格前面是谁？把那个英文词说出来。'
        elif it == 'choose': ask = f'那空里从 {cue} 里选哪一个？'
        elif it == 'fill': ask = f'括号里的 {cue} 要写成什么样子？把它写出来。'
        elif it == 'cue_word_before': ask = '看看空格前面紧挨着的那个英文词，是哪个？'
        elif it == 'cue_word_after': ask = '看看空格后面紧挨着的那个英文词，是哪个？'
        elif it == 'cue_word_next_sentence': ask = '看看后面那一句，开头的英文词是哪个？'
        elif it == 'cue_word_before2': ask = '空格前面用的是 is 还是 are？'
        elif it == 'find_wrong_word': ask = '慢慢读这句话，哪一个英文词用错了？'
        elif it == 'correct_word': ask = f'{cue} 应该改成什么？说出改好的那个词。'
        elif it == 'spell_letter': ask = f'看 {cue}，第{s["slot"]}个空格里填哪个字母？'
        elif it == 'spell_whole': ask = '把字母都填进空格，整个单词怎么拼？一个字母一个字母说出来。'
        else: ask = '这道题的答案是什么？'
        if it in ('spell_letter',): ex = f'{r}这一格是字母 {e}。'
        elif it.startswith('cue_word'): ex = f'提示词是 {e}。{r}'
        elif it == 'subject_who': ex = f'主语是 {e}。{r}'
        elif it == 'find_wrong_word': ex = f'{r}用错的是 {e}。'
        elif it == 'whole': ex = f'{r}'
        else: ex = f'{r}所以是 {e}。'
        out.append({'ask': ask, 'expect': e, 'explain': ex})
    return {'steps': out}

def plan_grammar(plan):
    """可选：把步数和 expect 锁死在 GBNF 里，模型只能写 ask/explain。"""
    def lit(x): return json.dumps(json.dumps(x, ensure_ascii=False), ensure_ascii=False)
    parts = []
    for i, s in enumerate(plan['steps']):
        parts.append(f'step{i} ::= "{{" space "\\"ask\\"" space ":" space string "," space "\\"expect\\"" space ":" space {lit(s["expect"])} "," space "\\"explain\\"" space ":" space string space "}}"')
    root = 'root ::= "{" space "\\"steps\\"" space ":" space "[" space ' + ' "," space '.join(f'step{i}' for i in range(len(plan['steps']))) + ' space "]" space "}"'
    return '\n'.join([root] + parts + [
        'string ::= "\\"" char* "\\""',
        'char ::= [^"\\\\\\x7F\\x00-\\x1F] | "\\\\" (["\\\\bfnrt] | "u" [0-9a-fA-F]{4})',
        'space ::= | " " | "\\n" [ \\t]{0,20}'])

def patch_with_template(model_steps, plan, errors):
    """逐步兜底：只把出错的那几步换成模板 ask/explain，其余保留模型的话。步数不对/格式错返回 None。"""
    if not isinstance(model_steps, list) or len(model_steps) != len(plan['steps']): return None
    if any(not isinstance(s, dict) for s in model_steps): return None
    bad = {int(m) for e in errors for m in re.findall(r'^第(\d+)步', e)}
    if not bad or any(not re.match(r'^第\d+步', e) for e in errors): return None
    tpl = template_fallback(plan)['steps']
    out = [dict(tpl[i - 1]) if i in bad else {'ask': s['ask'], 'expect': plan['steps'][i - 1]['expect'], 'explain': s['explain']}
           for i, s in enumerate(model_steps, 1)]
    ok, errs, _ = check_steps({'steps': out}, plan)
    return ({'steps': out}, sorted(bad)) if ok else None
