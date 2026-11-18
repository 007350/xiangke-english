"""读课包的测试。没有 openpyxl 时整组跳过——它只是读取用的可选依赖。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from xiangke.pack import PackError, read_pack, texts, unjudged

try:
    import openpyxl
except ImportError:  # pragma: no cover
    openpyxl = None

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "课包样例.xlsx"


@unittest.skipUnless(openpyxl, "没有 openpyxl，读不了 Excel")
class ReadPackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.count = 0

    def broken(self, change) -> Path:
        """把样例改坏一份，返回路径。``change`` 收到 workbook，直接改。"""
        self.count += 1
        path = Path(self.tmp.name) / f"broken{self.count}.xlsx"
        book = openpyxl.load_workbook(SAMPLE)
        change(book)
        book.save(path)
        return path

    def problems_of(self, change) -> list[str]:
        with self.assertRaises(PackError) as caught:
            read_pack(self.broken(change))
        return caught.exception.problems

    def test_sample_reads(self):
        pack, warnings = read_pack(SAMPLE)
        self.assertEqual(pack["packId"], "pack_demo_01")
        self.assertEqual(pack["packVersion"], 1)
        self.assertEqual(pack["grade"], "三年级")
        self.assertEqual(len(pack["words"]), 5)
        self.assertEqual(pack["words"][0], {"zh": "书", "en": "book", "pos": "noun"})
        self.assertEqual(len(pack["items"]), 4)
        self.assertEqual(
            pack["items"][0],
            {"itemId": "1", "kind": "choose", "recognizedText": "They ___ my friends. (am / is / are)", "answer": "are"},
        )
        self.assertEqual(pack["items"][3]["kind"], "spell")
        self.assertEqual(pack["rules"], ["be_verb", "plural_noun", "v_3s", "spelling"])
        self.assertEqual(pack["teacherWords"][0], {"ruleId": "be_verb", "text": "主语是 they，用 are。"})
        self.assertEqual(warnings, [])

    def test_missing_sheet(self):
        problems = self.problems_of(lambda book: book.remove(book["单词"]))
        self.assertTrue(any("少了「单词」" in line for line in problems), problems)

    def test_changed_header(self):
        def change(book):
            book["题目"]["A1"] = "编号"

        problems = self.problems_of(change)
        self.assertTrue(any("表头被改了" in line for line in problems), problems)

    def test_empty_required_cell(self):
        def change(book):
            book["题目"]["D2"] = None

        problems = self.problems_of(change)
        self.assertTrue(any("标准答案是空的" in line for line in problems), problems)

    def test_duplicate_item_number(self):
        def change(book):
            book["题目"]["A3"] = 1

        problems = self.problems_of(change)
        self.assertTrue(any("题号 1 前面已经用过" in line for line in problems), problems)

    def test_bad_word_kind(self):
        def change(book):
            book["单词"]["C2"] = "名词性"

        problems = self.problems_of(change)
        self.assertTrue(any("词性「名词性」" in line for line in problems), problems)

    def test_english_with_other_characters(self):
        def change(book):
            book["单词"]["B2"] = "bo ok"

        problems = self.problems_of(change)
        self.assertTrue(any("字母以外的内容" in line for line in problems), problems)

    def test_rule_name_changed(self):
        def change(book):
            book["本单元规则"]["B2"] = "选 am/is/are"

        problems = self.problems_of(change)
        self.assertTrue(any("规则编号或名称被改过" in line for line in problems), problems)

    def test_rule_removed(self):
        def change(book):
            book["本单元规则"].delete_rows(8)

        problems = self.problems_of(change)
        self.assertTrue(any("少了这几行" in line for line in problems), problems)

    def test_bad_yes_no(self):
        def change(book):
            book["本单元规则"]["D2"] = "教"

        problems = self.problems_of(change)
        self.assertTrue(any("只能填「是」或「否」" in line for line in problems), problems)

    def test_teacher_rule_not_listed(self):
        def change(book):
            book["老师讲法"]["A2"] = "选 be 动词"

        problems = self.problems_of(change)
        self.assertTrue(any("不在「本单元规则」里" in line for line in problems), problems)

    def test_version_not_positive(self):
        def change(book):
            book["课包信息"]["B3"] = 0

        problems = self.problems_of(change)
        self.assertTrue(any("从 1 开始的整数" in line for line in problems), problems)

    def test_pack_id_with_bad_characters(self):
        def change(book):
            book["课包信息"]["B2"] = "pack-demo-01"

        problems = self.problems_of(change)
        self.assertTrue(any("课包编号只能用" in line for line in problems), problems)

    def test_answer_disagrees_with_program_is_warning(self):
        # 第 2 行是第 1 题，程序算得出 are；老师写成 is 只提醒，不拒绝
        def change(book):
            book["题目"]["D2"] = "is"

        pack, warnings = read_pack(self.broken(change))
        self.assertEqual(pack["items"][0]["answer"], "is")
        self.assertTrue(any("程序算出来是 are" in line for line in warnings), warnings)

    def test_empty_pack_has_no_items(self):
        def change(book):
            for row in range(2, 6):
                for column in "ABCD":
                    book["题目"][f"{column}{row}"] = None

        problems = self.problems_of(change)
        self.assertTrue(any("一道题都没有" in line for line in problems), problems)

    def test_unjudged_lists_spelling_item(self):
        pack, _ = read_pack(SAMPLE)
        self.assertEqual(unjudged(pack), ["4"])

    def test_texts_accepts_old_and_new(self):
        old = [{"recognizedText": "A ___ B. (a / b)"}]
        self.assertEqual(texts(old), ["A ___ B. (a / b)"])
        pack, _ = read_pack(SAMPLE)
        self.assertEqual(len(texts(pack)), 4)
        self.assertEqual(texts(pack)[2], "改错（只改一个词）：She like cats.")

    def test_written_pack_round_trips(self):
        """写出去的文件，再读回来还是同一份。"""
        pack, _ = read_pack(SAMPLE)
        path = Path(self.tmp.name) / "pack.json"
        path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
        again = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(again, pack)


if __name__ == "__main__":
    unittest.main()
