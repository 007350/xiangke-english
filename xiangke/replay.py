"""演示备份：把星火写好的内容录下来，现场太慢或连不上时回放。

录的是星火写的讲法、档案、反馈、专属题和疑惑解答。手机和小模型仍然现场运行。
讲法、反馈和专属题按“这一轮做了哪些题、有没有错”对号入座；疑惑解答按
“哪道题、哪一问、孩子原话”对号入座，都不存孩子和辅导的编号。
现场操作和录制时不一样就不回放，免得老师页面上出现和记录对不上的话。
回放时页面会标明内容是提前写好的。
"""

from __future__ import annotations

import json
from pathlib import Path

from xiangke.drills import _norm
from xiangke.planner import LlmError


def round_key(sessions: list[dict]) -> str:
    """这一轮的记号：做了哪些题，每道有没有做错。顺序不影响。"""
    rows = []
    for session in sessions:
        wrong = any(event.get("result") in ("miss", "assisted") for event in session.get("events", []))
        rows.append(f"{_norm(session.get('recognizedText', ''))}|{'错' if wrong else '对'}")
    return "\n".join(sorted(rows))


def question_key(item: dict) -> str:
    """一条疑惑的记号：哪道题、哪一问、孩子原话。编号每次都不一样，不能用来对号。"""
    return json.dumps(
        [_norm(item.get("text", "")), _norm(item.get("ask", "")), _norm(item.get("question", "")), item.get("kind", "")],
        ensure_ascii=False,
    )


class Recorder:
    """包在真实的星火调用外面，成功的结果写进文件。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.rounds: dict[str, dict] = {}
        self.answers: dict[str, dict] = {}
        self.current: str | None = None

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"rounds": self.rounds, "answers": self.answers}, ensure_ascii=False, indent=1), encoding="utf-8")

    def wrap_planner(self, planner):
        def recorded(current: dict, new_sessions: list[dict]):
            result = planner(current, new_sessions)
            if result is not None:
                self.current = round_key(new_sessions)
                self.rounds[self.current] = {"plan": result}
                self._save()
            return result

        return recorded

    def wrap_reporter(self, reporter):
        def recorded(stats: dict, sessions: list[dict], facts: dict):
            result = reporter(stats, sessions, facts)
            if self.current is not None:
                self.rounds[self.current]["written"] = result
                self._save()
            return result

        return recorded

    def wrap_maker(self, maker):
        def recorded(portrait: dict, sessions: list[dict], known: set[str]):
            result = maker(portrait, sessions, known)
            if self.current is not None:
                self.rounds[self.current]["drills"] = result
                self._save()
            return result

        return recorded

    def wrap_answerer(self, answerer):
        def recorded(item: dict):
            result = answerer(item)
            self.answers[question_key(item)] = result
            self._save()
            return result

        return recorded


class Replayer:
    """用录好的内容代替星火。不调用网络。"""

    def __init__(self, path: str | Path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.rounds = data["rounds"]
        self.answers = data.get("answers")
        self.current: str | None = None

    def planner(self, current: dict, new_sessions: list[dict]) -> dict | None:
        key = round_key(new_sessions)
        found = self.rounds.get(key)
        self.current = key if found else None
        if not found:
            print("这一轮和录制时不一样，不回放:", key.replace("\n", "；"), flush=True)
            return None
        return found["plan"]

    def reporter(self, stats: dict, sessions: list[dict], facts: dict) -> dict:
        written = self.rounds.get(self.current or "", {}).get("written")
        if not written:
            raise LlmError("录制里没有这一轮的反馈")
        return written

    def maker(self, portrait: dict, sessions: list[dict], known: set[str]) -> list[dict]:
        items = self.rounds.get(self.current or "", {}).get("drills") or []
        return [item for item in items if _norm(item["text"]) not in known]

    def answerer(self, item: dict) -> dict:
        found = (self.answers or {}).get(question_key(item))
        if found:
            return found
        return {"status": "needs_teacher", "answer": "这条疑惑的解答没有录进演示备份，需要老师确认。"}
