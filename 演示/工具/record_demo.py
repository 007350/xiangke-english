"""在真实的 Edge 窗口里按演示步骤走一遍，并录下网页画面。

真的云端和星火、真的小模型、真的点击和打字。只录这两个网页，不录桌面。
用法：record_demo.py 输出视频 记录文件。令牌从环境变量读，不打印。
"""
import base64
import io
import json
import os
import re
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(r"D:\competition")
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
from xiangke.answers import compute_answer  # noqa: E402

VIDEO, LOG = sys.argv[1], sys.argv[2]
RUN = Path(tempfile.mkdtemp(prefix="xiangke_run_", dir=str(Path(VIDEO).parent)))
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
FFMPEG = r"D:\bin\ffmpeg.exe"
DEBUG_PORT = 9333
PHONE, CLOUD = "http://127.0.0.1:8765/", "http://127.0.0.1:8766/"
W, H, BAR, FPS = 1100, 760, 70, 5
log_file = open(LOG, "w", encoding="utf-8")


def note(*parts):
    line = " ".join(str(part) for part in parts)
    print(line, flush=True)
    log_file.write(f"{time.strftime('%H:%M:%S')} {line}\n")
    log_file.flush()


class Socket:
    """够用的 WebSocket 客户端，只用标准库。"""

    def __init__(self, url):
        parts = urllib.parse.urlsplit(url)
        self.sock = socket.create_connection((parts.hostname, parts.port))
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            f"GET {parts.path} HTTP/1.1\r\nHost: {parts.hostname}:{parts.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        reply = b""
        while b"\r\n\r\n" not in reply:
            reply += self.sock.recv(4096)
        if b" 101 " not in reply.split(b"\r\n")[0]:
            raise RuntimeError(reply[:200])

    def send(self, text):
        data = text.encode()
        mask = os.urandom(4)
        head = bytes([0x81])
        if len(data) < 126:
            head += bytes([0x80 | len(data)])
        elif len(data) < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", len(data))
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", len(data))
        self.sock.sendall(head + mask + bytes(byte ^ mask[index % 4] for index, byte in enumerate(data)))

    def _read(self, count):
        chunks = []
        while count:
            chunk = self.sock.recv(min(count, 1 << 20))
            if not chunk:
                raise ConnectionError("连接断了")
            chunks.append(chunk)
            count -= len(chunk)
        return b"".join(chunks)

    def recv(self):
        buffer = b""
        while True:
            first, second = self._read(2)
            size = second & 0x7F
            if size == 126:
                size = struct.unpack(">H", self._read(2))[0]
            elif size == 127:
                size = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(size)
            opcode = first & 0x0F
            if opcode in (0, 1, 2):
                buffer += payload
                if first & 0x80:
                    return buffer.decode()
            elif opcode == 9:
                continue


class Tab:
    def __init__(self, ws_url):
        self.ws = Socket(ws_url)
        self.next_id = 0
        self.lock = threading.Lock()

    def call(self, method, **params):
        with self.lock:
            self.next_id += 1
            self.ws.send(json.dumps({"id": self.next_id, "method": method, "params": params}))
            while True:
                reply = json.loads(self.ws.recv())
                if reply.get("id") == self.next_id:
                    if "error" in reply:
                        raise RuntimeError(f"{method}: {reply['error']}")
                    return reply["result"]

    def js(self, code):
        result = self.call("Runtime.evaluate", expression=code, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in result:
            raise RuntimeError(f"页面脚本出错: {result['exceptionDetails']}")
        return result["result"].get("value")


class Screen:
    """后台一直抓当前网页，合上字幕和鼠标，送进 ffmpeg。"""

    def __init__(self):
        self.tab = None
        self.caption = ""
        self.mouse = (W // 2, H // 2)
        self.pressed = 0
        self.speed = 1
        self.stop = False
        self.font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 26)
        self.small = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 18)
        self.ffmpeg = subprocess.Popen(
            [FFMPEG, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H + BAR}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p", "-movflags", "+faststart", VIDEO],
            stdin=subprocess.PIPE,
        )
        self.thread = threading.Thread(target=self.run, daemon=True)

    def frame(self):
        shot = self.capture.call("Page.captureScreenshot", format="jpeg", quality=80)
        image = Image.open(io.BytesIO(base64.b64decode(shot["data"]))).convert("RGB")
        if image.size != (W, H):
            image = image.resize((W, H))
        canvas = Image.new("RGB", (W, H + BAR), (31, 45, 39))
        canvas.paste(image, (0, 0))
        draw = ImageDraw.Draw(canvas)
        x, y = self.mouse
        radius = 13 if self.pressed > 0 else 9
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=(214, 60, 40), width=4, fill=(255, 200, 60) if self.pressed > 0 else None)
        self.pressed -= 1
        draw.text((20, H + 16), self.caption, font=self.font, fill="white")
        if self.speed > 1:
            draw.text((W - 170, H + 22), f"已加速 {self.speed} 倍", font=self.small, fill=(255, 200, 60))
        return canvas

    def run(self):
        tick = 0
        next_at = time.time()
        while not self.stop:
            try:
                canvas = self.frame()
                tick += 1
                if tick % self.speed == 0:
                    self.ffmpeg.stdin.write(canvas.tobytes())
            except Exception as error:  # 切换网页的瞬间可能抓不到，下一帧再来
                note("抓帧没成功:", repr(error)[:120])
            next_at += 1 / FPS
            time.sleep(max(0, next_at - time.time()))

    def switch(self, tab, capture):
        self.tab, self.capture = tab, capture

    def close(self):
        self.stop = True
        self.thread.join(timeout=5)
        self.ffmpeg.stdin.close()
        self.ffmpeg.wait(timeout=120)


screen = Screen()


def http_ok(url, timeout):
    deadline = time.time() + timeout
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.time() < deadline:
        try:
            with opener.open(url, timeout=3) as response:
                return response.status == 200
        except OSError:
            time.sleep(1)
    raise RuntimeError(f"打不开 {url}")


def open_tab(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(f"http://127.0.0.1:{DEBUG_PORT}/json/new?{urllib.parse.quote(url, safe='')}", method="PUT")
    with opener.open(request, timeout=10) as response:
        target = json.load(response)
    tab, capture = Tab(target["webSocketDebuggerUrl"]), Tab(target["webSocketDebuggerUrl"])
    for client in (tab, capture):
        client.call("Page.enable")
    tab.call("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=False)
    tab.call("Emulation.setFocusEmulationEnabled", enabled=True)
    return tab, capture


def show(tab_pair):
    tab, capture = tab_pair
    tab.call("Page.bringToFront")
    screen.switch(tab, capture)


def move_and_click(tab, x, y):
    start = screen.mouse
    for step in range(1, 7):
        screen.mouse = (int(start[0] + (x - start[0]) * step / 6), int(start[1] + (y - start[1]) * step / 6))
        tab.call("Input.dispatchMouseEvent", type="mouseMoved", x=screen.mouse[0], y=screen.mouse[1])
        time.sleep(0.05)
    screen.pressed = 3
    for kind in ("mousePressed", "mouseReleased"):
        tab.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=1)
    time.sleep(0.35)


def rect_of(tab, code):
    box = tab.js(f"(() => {{ const el = {code}; if (!el) return null; el.scrollIntoView({{block: 'center'}}); const r = el.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }})()")
    if box is None:
        raise RuntimeError(f"页面上找不到: {code}")
    time.sleep(0.2)
    return int(box[0]), int(box[1])


def click(tab, selector):
    move_and_click(tab, *rect_of(tab, f"document.querySelector({json.dumps(selector)})"))


def click_text(tab, selector, text):
    code = f"[...document.querySelectorAll({json.dumps(selector)})].find(el => el.textContent.includes({json.dumps(text)}))"
    move_and_click(tab, *rect_of(tab, code))


def wait_for(tab, code, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if tab.js(code):
            return True
        time.sleep(0.3)
    raise RuntimeError(f"等不到: {code}")


NOT_BUSY = "!document.getElementById('send').disabled"


def last_said(tab):
    return tab.js("(() => { const items = [...document.querySelectorAll('#chat .phone-say:not(.thinking)')]; return items.length ? items[items.length - 1].textContent : ''; })()")


def say(tab, text):
    click(tab, "#say")
    for char in text:
        tab.call("Input.insertText", text=char)
        time.sleep(0.08)
    time.sleep(0.4)
    click(tab, "#send")
    time.sleep(0.3)
    wait_for(tab, f"{NOT_BUSY} || !document.getElementById('finish').classList.contains('hidden')", 120)
    time.sleep(1.1)
    note(f"  孩子: {text}  → 手机: {last_said(tab)}")


def done(tab):
    return tab.js("!document.getElementById('finish').classList.contains('hidden')")


def reply_to(ask, text, answer):
    """按手机问的内容回答。只在脚本里用，手机端不知道答案。"""
    sentence = text.split("：")[-1]
    subject = re.findall(r"[A-Za-z]+", sentence)[0]
    many = subject.casefold() in ("they", "we", "these", "those")
    if "用错" in ask:
        computed = compute_answer(text)
        return computed["wrong"] if computed else re.sub(r"e?s$", "", answer)
    if "说谁" in ask or "主语是哪个词" in ask or "哪个词告诉" in ask:
        return subject
    if "一个" in ask and "多个" in ask and "选" not in ask:
        return "多个" if many else "一个"
    if "要不要" in ask or "要变吗" in ask or "说「要」" in ask:
        return "不要" if many else "要"
    return answer


def play(tab, text, answer, wrong_first=None, ask_at_final=None):
    """答完这道题。``wrong_first`` 记录某一问要先答错什么。"""
    wrong_first = dict(wrong_first or {})
    for _ in range(14):
        if done(tab):
            break
        ask = last_said(tab)
        if "再想想" in ask or "答对了" in ask or "主语是" in ask and ask.endswith("加 s。"):
            ask = tab.js("(() => { const items = [...document.querySelectorAll('#chat .phone-say:not(.thinking)')].map(el => el.textContent).filter(t => /[？?]/.test(t)); return items.length ? items[items.length - 1] : ''; })()")
        reply = reply_to(ask, text, answer)
        if reply in wrong_first:
            for wrong in wrong_first.pop(reply):
                say(tab, wrong)
            if reply == answer and ask_at_final:
                say(tab, ask_at_final)
        say(tab, reply)
    if not done(tab):
        note("  这道题没有走到结束")
    time.sleep(1.6)
    title = tab.js("document.getElementById('finish-title').textContent")
    note(f"  结束: {title}")
    click(tab, "#again")
    wait_for(tab, "document.getElementById('title').textContent === '乡课英语'", 20)
    time.sleep(0.8)


def wait_cloud(tab):
    """同步之后等云端写完。等待的这段加速。"""
    started = time.time()
    screen.speed = 8
    last = ""
    while time.time() - started < 480:
        text = tab.js("document.getElementById('sync-result').textContent")
        if text != last:
            note(f"  [{time.time() - started:5.0f} 秒] {text}")
            last = text
        if text and "还在看" not in text:
            break
        time.sleep(1)
    screen.speed = 1
    time.sleep(1.5)
    return time.time() - started


def read_report(tab):
    tab.js("window.scrollTo(0, 0)")
    tab.call("Page.reload")
    wait_for(tab, "document.readyState === 'complete' && document.getElementById('p-facts') && document.getElementById('p-facts').textContent", 20)
    screen.caption = screen.caption
    click(tab, "#tab-parent")
    time.sleep(5)
    note("  家长:", tab.js("document.getElementById('parent').innerText").replace("\n", " "))
    click(tab, "#tab-teacher")
    time.sleep(5)
    note("  老师:", tab.js("document.getElementById('teacher').innerText").replace("\n", " ")[:700])
    height = tab.js("document.documentElement.scrollHeight")
    if height > H:
        for position in range(0, height - H + 300, 300):
            tab.js(f"window.scrollTo({{top: {position}, behavior: 'smooth'}})")
            time.sleep(1.6)
    time.sleep(1.5)
    tab.js("window.scrollTo(0, 0)")


clean = {key: value for key, value in os.environ.items()}
phone_log, cloud_log = open(RUN / "phone.log", "wb"), open(RUN / "cloud.log", "wb")
cloud = subprocess.Popen([PY, "-B", "-m", "xiangke.cloud", "--db", str(RUN / "cloud.db")], cwd=ROOT, env=clean, stdout=cloud_log, stderr=subprocess.STDOUT)
phone = subprocess.Popen([PY, "-B", "-m", "xiangke.app", "--db", str(RUN / "phone.db")], cwd=ROOT, env=clean, stdout=phone_log, stderr=subprocess.STDOUT)
edge = None
failed = None
try:
    note("开云端和手机……")
    http_ok(CLOUD, 60)
    http_ok(PHONE, 240)
    edge = subprocess.Popen([EDGE, f"--remote-debugging-port={DEBUG_PORT}", f"--user-data-dir={RUN / 'edge'}", "--no-first-run", "--no-default-browser-check", "--disable-sync", "--disable-features=msEdgeSidebarV2,EdgeCollections", "--disable-background-timer-throttling", "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows", "--window-size=1160,900", "about:blank"])
    http_ok(f"http://127.0.0.1:{DEBUG_PORT}/json/version", 30)
    report_pair = open_tab(CLOUD)
    phone_pair = open_tab(PHONE)
    tab = phone_pair[0]
    show(phone_pair)
    screen.caption = "乡课英语：手机（电脑网页代替）。云端也在这台电脑上，断网用开关模拟"
    screen.thread.start()
    wait_for(tab, "document.getElementById('net').textContent.includes('已断网')", 20)
    time.sleep(3.5)

    note("== 第一轮：断网做错题 ==")
    screen.caption = "第一步：断网。孩子拍下一道不会的题，本地认字，孩子确认"
    node = tab.call("DOM.getDocument")["root"]["nodeId"]
    picker = tab.call("DOM.querySelector", nodeId=node, selector="#photo")["nodeId"]
    move_and_click(tab, *rect_of(tab, "document.querySelector('label.camera')"))
    tab.call("DOM.setFileInputFiles", nodeId=picker, files=[str(ROOT / "演示" / "题目.png")])
    wait_for(tab, "document.querySelectorAll('.line-pick').length > 0", 60)
    time.sleep(1.5)
    note("OCR 认出:", tab.js("[...document.querySelectorAll('.line-pick')].map(el => el.textContent).join(' | ')"))
    click_text(tab, ".line-pick", "She ___ apples")
    time.sleep(1)
    click(tab, "#confirm")
    screen.caption = "小模型现场把题拆成几问。答案由程序算，对错由程序判"
    wait_for(tab, NOT_BUSY, 120)
    time.sleep(1.2)
    note("第 1 道 手机:", last_said(tab))
    play(tab, "She ___ apples. (like / likes)", "likes", {"likes": ["like"]})

    screen.caption = "第二道：课包里的改错题。孩子提问不算答错"
    click_text(tab, "#pack .row", "She like cats")
    time.sleep(0.8)
    click(tab, "#confirm")
    wait_for(tab, NOT_BUSY, 120)
    time.sleep(1.2)
    note("第 2 道 手机:", last_said(tab))
    play(tab, "改错（只改一个词）：She like cats.", "likes", {"like": ["cats"], "likes": ["like"]}, "为什么要加s")

    screen.caption = "第三道：这一类孩子已经会了"
    node = tab.call("DOM.getDocument")["root"]["nodeId"]
    picker = tab.call("DOM.querySelector", nodeId=node, selector="#photo")["nodeId"]
    tab.call("DOM.setFileInputFiles", nodeId=picker, files=[str(ROOT / "演示" / "题目.png")])
    wait_for(tab, "document.querySelectorAll('.line-pick').length > 0", 60)
    time.sleep(1)
    click_text(tab, ".line-pick", "They ___ my friends")
    time.sleep(0.8)
    click(tab, "#confirm")
    wait_for(tab, NOT_BUSY, 120)
    time.sleep(1.2)
    note("第 3 道 手机:", last_said(tab))
    play(tab, "They ___ my friends. (am / is / are)", "are")
    time.sleep(1.5)

    note("== 第二轮：联网，云端星火工作 ==")
    screen.caption = "第二步：联网同步。记录传上云端，错过的题马上发回来"
    click(tab, "#net")
    time.sleep(1.2)
    click(tab, "#sync-now")
    time.sleep(2.5)
    screen.caption = "星火大模型在云端读记录：写讲法、写反馈、出专属题（现场调用，这段加速）"
    first = wait_cloud(tab)
    note(f"第一轮云端用时 {first:.0f} 秒")
    screen.caption = "新讲法和专属题到了手机"
    tab.js("document.getElementById('drills-box').scrollIntoView({block: 'center'})")
    note("专属题:", tab.js("[...document.querySelectorAll('#drills .row')].map(el => el.textContent).join(' | ')"))
    time.sleep(5)

    show(report_pair)
    screen.caption = "老师和家长的反馈页：次数由程序算，卡在哪由星火写"
    read_report(report_pair[0])

    note("== 第三轮：再断网，做专属题 ==")
    show(phone_pair)
    screen.caption = "第三步：再断网。小模型按新讲法讲，这一步不需要网络"
    click(tab, "#net")
    time.sleep(1.5)
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(CLOUD + "api/child/child_01", timeout=10) as response:
        answers = {item["text"]: item["answer"] for item in json.load(response)["drills"]}
    texts = tab.js("[...document.querySelectorAll('#drills .row .en')].map(el => el.textContent)")
    texts.sort(key=lambda value: 0 if "She ___ apples" in value else 1)
    for index, text in enumerate(texts, start=1):
        screen.caption = f"专属题 {index}/{len(texts)}：错过的原题和星火新出的题"
        click_text(tab, "#drills .row", text)
        wait_for(tab, NOT_BUSY, 120)
        time.sleep(1.2)
        note(f"专属题 {index}: {text} 手机:", last_said(tab))
        play(tab, text, answers[text])

    note("== 第四轮：看到进步 ==")
    screen.caption = "第四步：再联网。孩子练好了，云端更新判断"
    click(tab, "#net")
    time.sleep(1.2)
    click(tab, "#sync-now")
    time.sleep(2.5)
    screen.caption = "星火大模型读新的记录（现场调用，这段加速）"
    second = wait_cloud(tab)
    note(f"第二轮云端用时 {second:.0f} 秒")
    time.sleep(3)
    show(report_pair)
    screen.caption = "反馈页更新：数字变了，老师的建议也变了"
    read_report(report_pair[0])
    screen.caption = "演示结束"
    time.sleep(2)
    errors = tab.js("document.getElementById('home-error').textContent")
    note("手机页错误提示:", repr(errors))
except Exception as error:
    failed = error
    note("走不下去了:", repr(error))
finally:
    if screen.thread.is_alive():
        screen.close()
    for process in (edge, phone, cloud):
        if process:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
    phone_log.close()
    cloud_log.close()
    note("云端记录:\n" + (RUN / "cloud.log").read_text(encoding="utf-8", errors="replace")[-2500:])
    log_file.close()
sys.exit(1 if failed else 0)
