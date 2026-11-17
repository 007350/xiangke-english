import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data"))

from synthetic_check import evaluate


class SyntheticTests(unittest.TestCase):
    def test_diverse_cases(self):
        report = evaluate(ROOT / "data" / "synthetic.json")
        self.assertEqual(report["failures"], [])


if __name__ == "__main__":
    unittest.main()
