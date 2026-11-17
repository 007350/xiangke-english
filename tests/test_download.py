import hashlib
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from xiangke.download_model import DownloadFailed, ensure_model

DATA = bytes(range(256)) * 400
SHA = hashlib.sha256(DATA).hexdigest()


class Handler(BaseHTTPRequestHandler):
    support_range = True
    status = 200
    cut_after = None
    requests_seen: list = []

    def log_message(self, *_):
        pass

    def do_GET(self):
        Handler.requests_seen.append(self.headers.get("Range"))
        if Handler.status != 200:
            self.send_error(Handler.status)
            return
        start = 0
        wanted = self.headers.get("Range")
        if wanted and Handler.support_range:
            start = int(wanted.split("=")[1].rstrip("-"))
        body = DATA[start:]
        self.send_response(206 if wanted and Handler.support_range else 200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        Handler.support_range = True
        Handler.status = 200
        Handler.requests_seen = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/m.gguf"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.folder = tempfile.TemporaryDirectory()
        self.target = Path(self.folder.name) / "a" / "m.gguf"
        self.part = self.target.with_name("m.gguf.part")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.folder.cleanup()

    def get(self, sha=SHA):
        return ensure_model(self.url, self.target, len(DATA), sha, say=lambda _: None)

    def test_downloads_and_verifies(self):
        self.assertEqual(self.get(), self.target)
        self.assertEqual(self.target.read_bytes(), DATA)
        self.assertFalse(self.part.exists())

    def test_existing_model_is_not_downloaded_again(self):
        self.get()
        Handler.requests_seen = []
        self.get()
        self.assertEqual(Handler.requests_seen, [])

    def test_resumes_a_partial_file(self):
        self.part.parent.mkdir(parents=True)
        self.part.write_bytes(DATA[:1000])
        self.get()
        self.assertEqual(Handler.requests_seen, ["bytes=1000-"])
        self.assertEqual(self.target.read_bytes(), DATA)

    def test_starts_over_when_server_cannot_resume(self):
        Handler.support_range = False
        self.part.parent.mkdir(parents=True)
        self.part.write_bytes(b"x" * 1000)
        self.get()
        self.assertEqual(self.target.read_bytes(), DATA)

    def test_wrong_hash_is_not_kept(self):
        with self.assertRaises(DownloadFailed):
            self.get(sha="0" * 64)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.part.exists())

    def test_private_or_missing_repo_explains_what_to_do(self):
        Handler.status = 404
        with self.assertRaises(DownloadFailed) as caught:
            self.get()
        self.assertIn("手动下载", str(caught.exception))
        self.assertIn(self.url, str(caught.exception))
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
