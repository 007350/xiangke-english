import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from xiangke.app import BadRequest, PhoneApp, make_handler
from xiangke.store import Store

LESSON = {
    "steps": [
        {"ask": "这句话的主语是哪个词？", "expect": "They", "explain": "主语是 They。"},
        {"ask": "那空里填 am、is 还是 are？", "expect": "are", "explain": "They 是两个或更多，所以填 are。"},
    ]
}
TEXT = "They ___ my friends. (am / is / are)"
LABELS = {"不知道": "不会", "为什么？": "提问", "我饿了": "其他"}


def fake_write(system, user, grammar, limit=512):
    if "孩子说：" in user:
        return json.dumps({"label": LABELS.get(user.split("孩子说：")[1], "答题")}, ensure_ascii=False)
    if "题目（recognizedText" in user and "bo_k" in user:
        return json.dumps({"steps": [{"ask": "缺什么字母？", "expect": "o", "explain": "写 o。"}]}, ensure_ascii=False)
    if "随便" in user:
        return "坏掉了"
    return json.dumps(LESSON, ensure_ascii=False)


class AppCase(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.app = PhoneApp(fake_write, self.store, recognizer=lambda image: "One line\n\n  Two line ")

    def tearDown(self):
        self.store.connection.close()


class HomeTests(AppCase):
    def test_home_has_plan_pack_and_empty_history(self):
        home = self.app.home()
        self.assertEqual(home["planVersion"], 2)
        self.assertEqual(len(home["pack"]), 4)
        self.assertEqual(home["history"], [])
        self.assertEqual(home["waitingSync"], 0)


class RecognizeTests(AppCase):
    def test_lines_are_cleaned(self):
        self.assertEqual(self.app.recognize(b"x")["lines"], ["One line", "Two line"])

    def test_bad_photo_gives_a_message_not_a_crash(self):
        def broken(_):
            raise RuntimeError("不是图片")

        self.app.recognizer = broken
        with self.assertRaises(BadRequest):
            self.app.recognize(b"x")
        self.app.recognizer = lambda image: "   "
        with self.assertRaises(BadRequest):
            self.app.recognize(b"x")
        with self.assertRaises(BadRequest):
            self.app.recognize(b"")


class FlowTests(AppCase):
    def test_whole_item_saves_the_record(self):
        view = self.app.start(TEXT, "pack", offline=True)
        self.assertEqual(view["messages"], [LESSON["steps"][0]["ask"]])
        self.assertEqual((view["mode"], view["done"], view["step"], view["steps"]), ("judged", False, 1, 2))
        self.assertEqual(len(self.store.unsynced()), 0)

        view = self.app.reply("They")
        self.assertEqual(view["messages"][0], "答对了！")
        self.assertEqual(view["step"], 2)
        view = self.app.reply("is")
        self.assertEqual(view["messages"], ["还不对，再想想。"])
        view = self.app.reply("是 are 吧")
        self.assertEqual(view["confirming"], "are")
        view = self.app.reply("是")
        self.assertTrue(view["done"])
        self.assertEqual(view["outcome"], "finished")
        self.assertEqual(view["summary"], {"right": 2, "wrong": 1, "questions": 2, "answerSource": "program"})

        saved = self.store.unsynced()
        self.assertEqual(len(saved), 1)
        record = saved[0]
        self.assertEqual(record["source"], "pack")
        self.assertTrue(record["confirmed"])
        self.assertTrue(record["offline"])
        self.assertEqual(record["planVersion"], 2)
        self.assertEqual([e["result"] for e in record["events"]], ["hit", "miss", "note", "hit"])

    def test_reply_after_done_changes_nothing(self):
        self.app.start(TEXT)
        for said in ("They", "are"):
            view = self.app.reply(said)
        self.assertTrue(view["done"])
        again = self.app.reply("再说一句")
        self.assertEqual(again["messages"], [])
        self.assertEqual(len(self.store.unsynced()), 1)

    def test_leaving_part_way_keeps_what_happened(self):
        self.app.start(TEXT)
        self.app.reply("They")
        home = self.app.leave()
        self.assertEqual(home["waitingSync"], 1)
        self.assertEqual([e["result"] for e in self.store.unsynced()[0]["events"]], ["hit"])
        self.assertFalse(home["history"][0]["finished"])

    def test_leaving_twice_saves_once(self):
        self.app.start(TEXT)
        self.app.leave()
        self.app.leave()
        self.assertEqual(len(self.store.unsynced()), 1)

    def test_starting_a_new_item_saves_the_old_one(self):
        self.app.start(TEXT)
        self.app.reply("They")
        self.app.start("This is an ___. (apple / apples)")
        self.assertEqual(len(self.store.unsynced()), 1)

    def test_model_try_is_marked_and_saved(self):
        view = self.app.start("看中文写单词：书 bo_k")
        self.assertEqual(view["mode"], "try")
        view = self.app.reply("o")
        self.assertTrue(view["done"])
        self.assertEqual(view["summary"]["answerSource"], "model")
        self.assertEqual(self.store.unsynced()[0]["answerSource"], "model")

    def test_item_that_cannot_be_taught_ends_at_once(self):
        view = self.app.start("随便一句话")
        self.assertTrue(view["done"])
        self.assertEqual(view["outcome"], "cannot")
        self.assertEqual(self.store.unsynced()[0]["answerSource"], "none")

    def test_blank_inputs_are_refused(self):
        with self.assertRaises(BadRequest):
            self.app.start("   ")
        with self.assertRaises(BadRequest):
            self.app.reply("hi")
        self.app.start(TEXT)
        with self.assertRaises(BadRequest):
            self.app.reply("   ")
        with self.assertRaises(BadRequest):
            self.app.start(TEXT, "别的来源")


class HttpTests(AppCase):
    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        super().tearDown()

    def post(self, path, body):
        request = urllib.request.Request(self.url + path, json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"})
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def test_page_and_flow_over_http(self):
        with urllib.request.urlopen(self.url + "/") as response:
            self.assertIn("乡课英语", response.read().decode("utf-8"))
        self.assertEqual(self.post("/api/start", {"text": TEXT})["step"], 1)
        self.assertEqual(self.post("/api/reply", {"said": "They"})["messages"][0], "答对了！")

    def test_errors_are_json_with_a_message(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/reply", {"said": "hi"})
        self.assertEqual(caught.exception.code, 400)
        self.assertIn("没有在做题", json.load(caught.exception)["error"])

    def test_unknown_paths_are_refused(self):
        for path in ("/../README.md", "/data/pack.json", "/xiangke/app.py"):
            with self.subTest(path=path):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(self.url + path)
                self.assertEqual(caught.exception.code, 404)


class PackFileTests(unittest.TestCase):
    """老师用 xlsx 读出来的课包，手机首页要读得出来。"""

    def test_home_reads_new_format_pack(self):
        pack = {
            "packId": "pack_demo_01",
            "packVersion": 1,
            "grade": "三年级",
            "unit": "第一单元",
            "words": [],
            "items": [{"itemId": "1", "kind": "choose", "recognizedText": "He ___ tall. (is / are)", "answer": "is"}],
            "rules": ["be_verb"],
            "teacherWords": [],
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pack.json"
            path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
            store = Store(":memory:")
            try:
                app = PhoneApp(fake_write, store, pack_path=path)
                self.assertEqual(app.home()["pack"], ["He ___ tall. (is / are)"])
            finally:
                store.connection.close()

    def test_missing_pack_file_fails_loudly(self):
        store = Store(":memory:")
        try:
            app = PhoneApp(fake_write, store, pack_path="没有这个文件.json")
            with self.assertRaises(FileNotFoundError):
                app.home()
        finally:
            store.connection.close()


class TodayTests(AppCase):
    def test_finished_item_reports_today_and_left(self):
        self.app.start(TEXT, "pack", offline=True)
        self.app.reply("They")
        view = self.app.reply("are")
        self.assertTrue(view["done"])
        self.assertEqual(view["today"], {"done": 1, "left": 0})

    def test_unfinished_item_has_no_today(self):
        view = self.app.start(TEXT, "pack", offline=True)
        self.assertNotIn("today", view)


if __name__ == "__main__":
    unittest.main()
