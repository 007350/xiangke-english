"""读老师填的课包 Excel，变成手机能用的课包。

格式写在 ``data/课包格式.md``，模板是 ``data/课包模板.xlsx``。任何一处不合格，
整份课包都不收，并说清哪张表、第几行、错在哪里——半份课包比没有更糟，老师会以为生效了。

用法：
    python -m xiangke.pack data/课包样例.xlsx
    python -m xiangke.pack 我的课包.xlsx -o data/pack.json

程序算不出答案的题（现在是单词拼写）照样读进来，只在输出里点出来：这些题手机上
只会讲、不判对错，别让老师以为孩子做错了。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from xiangke.answers import compute_answer

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "pack.json"

INFO = "课包信息"
WORDS = "单词"
ITEMS = "题目"
RULES_SHEET = "本单元规则"
TEACHER = "老师讲法"

HEADERS = {
    INFO: ["项目", "内容"],
    WORDS: ["中文", "英文", "词性"],
    ITEMS: ["题号", "题型", "题目原文", "标准答案"],
    RULES_SHEET: ["规则编号", "规则名称", "例子", "这单元教吗"],
    TEACHER: ["规则名称", "讲法"],
}

POS = {"动词": "verb", "名词": "noun", "形容词": "adj", "其他": "other"}
KINDS = {"选词": "choose", "填空": "fill", "改一个词": "fix", "单词拼写": "spell"}
YES_NO = ("是", "否")
# 规则编号和名称由模板定死，老师只能选教不教
RULES = {
    "be_verb": "选 am / is / are",
    "plural_noun": "选单数或复数",
    "v_3s": "动词直接加 s",
    "v_es": "动词加 es",
    "v_ies": "辅音字母加 y，改成 ies",
    "v_irregular": "go、do、have 的特殊变化",
    "spelling": "单词拼写",
}
RULE_BY_NAME = {name: rule_id for rule_id, name in RULES.items()}
_ID = re.compile(r"[A-Za-z0-9_]+")


class PackError(ValueError):
    """课包没有读进来。``problems`` 是给老师看的全部原因。"""

    def __init__(self, problems: list[str]):
        super().__init__("；".join(problems))
        self.problems = problems


def read_pack(path: str | Path) -> tuple[dict, list[str]]:
    """读一份课包。返回 ``(课包, 要提醒老师的地方)``，有不合格就抛 ``PackError``。"""
    try:
        import openpyxl
    except ImportError:  # pragma: no cover - 装好依赖才走得到下面
        raise PackError(["没有装 openpyxl，读不了 Excel。请先运行：pip install openpyxl"]) from None

    path = Path(path)
    if not path.is_file():
        raise PackError([f"找不到这个文件：{path}"])
    try:
        book = openpyxl.load_workbook(path, data_only=True)
    except Exception as error:  # 文件坏了、密码保护、不是 xlsx
        raise PackError([f"这个文件打不开（{type(error).__name__}），请确认是 Excel 课包"]) from None

    problems: list[str] = []
    warnings: list[str] = []
    sheets = {name: _sheet(book, name, problems) for name in HEADERS}
    info = _info(sheets[INFO], problems) if sheets[INFO] else {}
    words = _words(sheets[WORDS], problems) if sheets[WORDS] else []
    items = _items(sheets[ITEMS], problems, warnings) if sheets[ITEMS] else []
    rules = _rules(sheets[RULES_SHEET], problems) if sheets[RULES_SHEET] else []
    teacher = _teacher_words(sheets[TEACHER], problems) if sheets[TEACHER] else []

    if not items:
        problems.append(f"「{ITEMS}」里一道题都没有")
    if problems:
        raise PackError(problems)

    pack = {
        "packId": info["packId"],
        "packVersion": info["packVersion"],
        "grade": info["grade"],
        "unit": info["unit"],
        "words": words,
        "items": items,
        "rules": rules,
        "teacherWords": teacher,
    }
    return pack, warnings


def _text(value) -> str:
    """单元格转成文字。数字的 1.0 要变成 1，否则题号、版本号会比不上。"""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _sheet(book, name: str, problems: list[str]):
    if name not in book.sheetnames:
        problems.append(f"少了「{name}」这张表，不能改名也不能删")
        return None
    sheet = book[name]
    first = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
    got = [_text(cell) for cell in first[: len(HEADERS[name])]]
    if got != HEADERS[name]:
        problems.append(f"「{name}」的表头被改了，第一行应该是：{'、'.join(HEADERS[name])}")
        return None
    return sheet


def _rows(sheet, width: int):
    """从第二行开始逐行给 ``(行号, 各列文字)``。整行空白的跳过，行号是 Excel 里看到的号。"""
    for index, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
        cells = [_text(value) for value in row[:width]]
        if any(cells):
            yield index, cells


def _info(sheet, problems: list[str]) -> dict:
    cells: dict[str, str] = {}
    for _, (name, content) in _rows(sheet, 2):
        if name:
            cells[name] = content
    missing = [name for name in ("课包编号", "课包版本", "年级", "单元") if not cells.get(name)]
    if missing:
        problems.append(f"「{INFO}」里这些没填：{'、'.join(missing)}")
        return {}
    pack_id, version = cells["课包编号"], cells["课包版本"]
    if not _ID.fullmatch(pack_id):
        problems.append(f"「{INFO}」的课包编号只能用英文字母、数字和下划线，现在是「{pack_id}」")
    if not version.isdigit() or int(version) < 1:
        problems.append(f"「{INFO}」的课包版本要从 1 开始的整数，现在是「{version}」")
    return {"packId": pack_id, "packVersion": int(version) if version.isdigit() else 0, "grade": cells["年级"], "unit": cells["单元"]}


def _words(sheet, problems: list[str]) -> list[dict]:
    rows = []
    for line, (zh, en, pos) in _rows(sheet, 3):
        empty = [name for name, value in (("中文", zh), ("英文", en), ("词性", pos)) if not value]
        if empty:
            problems.append(f"「{WORDS}」第 {line} 行：{'、'.join(empty)}是空的")
            continue
        if not re.fullmatch(r"[A-Za-z]+", en):
            problems.append(f"「{WORDS}」第 {line} 行：英文「{en}」里有字母以外的内容")
            continue
        if pos not in POS:
            problems.append(f"「{WORDS}」第 {line} 行：词性「{pos}」不在 {'、'.join(POS)} 里")
            continue
        rows.append({"zh": zh, "en": en, "pos": POS[pos]})
    if not rows and not problems:
        problems.append(f"「{WORDS}」里一个单词都没有")
    return rows


def _items(sheet, problems: list[str], warnings: list[str]) -> list[dict]:
    rows = []
    numbered: set[str] = set()
    for line, (number, kind, text, answer) in _rows(sheet, 4):
        empty = [name for name, value in (("题号", number), ("题型", kind), ("题目原文", text), ("标准答案", answer)) if not value]
        if empty:
            problems.append(f"「{ITEMS}」第 {line} 行：{'、'.join(empty)}是空的")
            continue
        if number in numbered:
            problems.append(f"「{ITEMS}」第 {line} 行：题号 {number} 前面已经用过")
            continue
        numbered.add(number)
        if kind not in KINDS:
            problems.append(f"「{ITEMS}」第 {line} 行：题型「{kind}」不在 {'、'.join(KINDS)} 里")
            continue
        rows.append({"itemId": number, "kind": KINDS[kind], "recognizedText": text, "answer": answer})
        computed = compute_answer(text)
        if computed and computed["answer"].casefold() != answer.casefold():
            # 老师的答案和程序算的不一样时不自己选，交给老师核对
            warnings.append(f"「{ITEMS}」第 {line} 行：程序算出来是 {computed['answer']}，老师写的是 {answer}，请核对")
    return rows


def _rules(sheet, problems: list[str]) -> list[str]:
    chosen: dict[str, bool] = {}
    for line, (rule_id, name, _example, take) in _rows(sheet, 4):
        if rule_id not in RULES or RULES[rule_id] != name:
            problems.append(f"「{RULES_SHEET}」第 {line} 行：规则编号或名称被改过（{rule_id} / {name}），请用模板里的写法")
            continue
        if rule_id in chosen:
            problems.append(f"「{RULES_SHEET}」第 {line} 行：{name} 这一行重复了")
            continue
        if take not in YES_NO:
            problems.append(f"「{RULES_SHEET}」第 {line} 行：「这单元教吗」只能填「是」或「否」，现在是「{take}」")
            continue
        chosen[rule_id] = take == "是"
    missing = [name for rule_id, name in RULES.items() if rule_id not in chosen]
    if missing:
        problems.append(f"「{RULES_SHEET}」少了这几行，不能删：{'、'.join(missing)}")
    return [rule_id for rule_id, on in chosen.items() if on]


def _teacher_words(sheet, problems: list[str]) -> list[dict]:
    rows = []
    for line, (name, text) in _rows(sheet, 2):
        if name not in RULE_BY_NAME:
            problems.append(f"「{TEACHER}」第 {line} 行：规则名称「{name}」不在「{RULES_SHEET}」里，请从那里的规则名称里选")
            continue
        if not text:
            problems.append(f"「{TEACHER}」第 {line} 行：讲法是空的")
            continue
        rows.append({"ruleId": RULE_BY_NAME[name], "text": text})
    return rows


def unjudged(pack: dict) -> list[str]:
    """程序算不出答案的题号。这些题手机只讲，不判对错。"""
    return [item["itemId"] for item in pack.get("items", []) if compute_answer(item["recognizedText"]) is None]


def texts(pack) -> list[str]:
    """从课包取题目原文。也认旧格式——只有 ``recognizedText`` 的列表。"""
    if isinstance(pack, list):
        rows = pack
    else:
        rows = pack.get("items", [])
    return [row["recognizedText"] for row in rows if isinstance(row, dict) and row.get("recognizedText")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="读老师的课包 Excel，给手机用")
    parser.add_argument("xlsx", help="老师填好的课包文件")
    parser.add_argument("-o", "--out", help=f"课包写到哪个文件，手机读它。默认只显示不写：{DEFAULT_OUT}")
    args = parser.parse_args(argv)

    try:
        pack, warnings = read_pack(args.xlsx)
    except PackError as error:
        print(f"这份课包没有读进来，{len(error.problems)} 处要改：")
        for problem in error.problems:
            print("  -", problem)
        return 1

    print(f"课包 {pack['packId']}（第 {pack['packVersion']} 版）{pack['grade']} {pack['unit']}")
    print(f"  单词 {len(pack['words'])} 个，题目 {len(pack['items'])} 道，老师写讲法 {len(pack['teacherWords'])} 条")

    for warning in warnings:
        print("  请核对：", warning)

    cannot = unjudged(pack)
    if cannot:
        print(f"  这 {len(cannot)} 道题程序算不出答案，手机上只会讲、不判对错：题号 {'、'.join(cannot)}")
    if "spelling" in pack["rules"]:
        print("  这一单元教单词拼写：拼写题手机现在只会讲、不判对错，别当成已经能判。")

    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已写到 {target}，手机下次启动就读它。")
    else:
        print("只看不写。要交给手机，加 -o 指定文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
