import unittest
from pathlib import Path

from xiangke.recognize import ENGLISH_REC_MODEL, join_lines, recognize

ROOT = Path(__file__).resolve().parents[1]
PRINTED = ROOT / "data" / "印刷.jpg"


def _box(left, top, right, bottom):
    return [(left, top), (right, top), (right, bottom), (left, bottom)]


class JoinLinesTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(join_lines(None, None), "")
        self.assertEqual(join_lines(None, []), "")
        self.assertEqual(join_lines([_box(0, 0, 10, 10)], ["  "]), "")

    def test_narrow_gap_stays_one_space(self):
        boxes = [_box(0, 0, 20, 10), _box(28, 0, 80, 10)]
        self.assertEqual(join_lines(boxes, ["am", "is"]), "am is")

    def test_wide_gap_becomes_an_empty_blank(self):
        boxes = [_box(0, 0, 20, 10), _box(80, 0, 160, 10)]
        self.assertEqual(join_lines(boxes, ["To", "more"]), "To ___ more")

    def test_spaces_inside_a_box_stay_copied(self):
        self.assertEqual(
            join_lines([_box(0, 0, 80, 10)], ["(am / is / are)"]),
            "(am / is / are)",
        )

    def test_reading_order_ignores_detection_order(self):
        boxes = [_box(80, 0, 160, 10), _box(0, 0, 20, 10)]
        self.assertEqual(join_lines(boxes, ["more", "To"]), "To ___ more")

    def test_next_line_stays_separate(self):
        boxes = [_box(0, 0, 40, 10), _box(0, 30, 40, 40)]
        self.assertEqual(
            join_lines(boxes, ["am / is / are", "I am a student."]),
            "am / is / are\nI am a student.",
        )

    def test_a_tall_box_does_not_glue_two_lines(self):
        boxes = [
            _box(100, 61, 1000, 94),
            _box(0, 73, 95, 145),
            _box(97, 105, 180, 135),
            _box(297, 102, 1045, 137),
        ]
        text = join_lines(boxes, ["Step 2 Complete", "opment of", "1. To", "more time"])
        self.assertIn("1. To ___ more time", text)
        self.assertIn("Step 2 Complete", text.splitlines())

    def test_texts_without_boxes_keep_given_order(self):
        self.assertEqual(join_lines(None, ["Step 2", "1. To"]), "Step 2\n1. To")


class RecognizeEngineTests(unittest.TestCase):
    def test_default_model_is_the_printed_english_one(self):
        self.assertEqual(ENGLISH_REC_MODEL.name, "en_PP-OCRv5_rec_mobile.onnx")
        self.assertNotIn("PP-OCRv6", ENGLISH_REC_MODEL.name)

    def test_missing_model_is_refused(self):
        with self.assertRaises(FileNotFoundError):
            recognize("photo.jpg", model_path=ROOT / "models" / "rapidocr" / "missing.onnx")

    def test_injected_engine_is_copied_through(self):
        seen = {}

        class Engine:
            def __call__(self, image):
                seen["image"] = image
                return type("Result", (), {
                    "boxes": [_box(0, 0, 10, 10), _box(18, 0, 80, 10)],
                    "txts": ("I", "a student."),
                })()

        self.assertEqual(recognize("printed.jpg", engine=Engine()), "I a student.")
        self.assertEqual(seen["image"], "printed.jpg")


@unittest.skipUnless(PRINTED.is_file() and ENGLISH_REC_MODEL.is_file(), "printed photo or model missing")
class PrintedPhotoTests(unittest.TestCase):
    def test_printed_page_keeps_the_sentences(self):
        from xiangke.recognize import default_engine

        engine = default_engine()
        self.assertTrue(str(engine.cfg.Rec.model_path).endswith("en_PP-OCRv5_rec_mobile.onnx"))

        text = recognize(str(PRINTED), engine=engine)
        self.assertIn("Step 2", text)
        self.assertIn("1. To ___ more time", text)
        self.assertIn("designed to ___ the organization's", text)
        self.assertIn("3. ___ new competitors", text)
        self.assertIn("found herself ___ a cycle", text)
        self.assertIn("millennials ___ nearly 50%", text)
        self.assertIn("delegate", text)
