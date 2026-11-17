"""Draft recognizedText from a printed photo.

The English recognition model only copies text. It does not fill blanks,
judge an answer, or confirm the question.
"""

from __future__ import annotations

from pathlib import Path

ENGLISH_REC_MODEL = (
    Path(__file__).resolve().parents[1]
    / "models"
    / "rapidocr"
    / "en_PP-OCRv5_rec_mobile.onnx"
)

_engines: dict[str, object] = {}


def recognize(image, engine=None, model_path=None) -> str:
    """Return an editable draft of the printed text.

    Boxes on one visual line are ordered left to right. A gap wider than the
    shorter box is the missing underline, written as ``___``. A narrower gap
    stays one space. Lines are separated by a newline. No answer is written
    into a blank.
    """
    if engine is None:
        engine = default_engine(model_path)
    result = engine(image)
    return join_lines(getattr(result, "boxes", None), getattr(result, "txts", None))


def default_engine(model_path=None):
    """Open RapidOCR with the printed-English recognition model.

    Detection and angle classification stay on the models shipped in the
    package. The Chinese recognition model is not selected.
    """
    path = ENGLISH_REC_MODEL if model_path is None else Path(model_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    key = str(path)
    engine = _engines.get(key)
    if engine is None:
        from rapidocr import RapidOCR

        engine = RapidOCR(params={"Rec.model_path": key})
        _engines[key] = engine
    return engine


def join_lines(boxes, texts) -> str:
    """Join recognition boxes into editable lines.

    A wide gap is the underline the recognizer did not copy. It becomes
    ``___`` and stays empty. Spaces already inside a box are left as copied.
    """
    if not texts:
        return ""
    cleaned = ["" if text is None else str(text).strip() for text in texts]
    if boxes is None:
        return "\n".join(text for text in cleaned if text)

    items = []
    for box, text in zip(boxes, cleaned):
        if text:
            items.append((_bounds(box), text))
    if not items:
        return ""

    items.sort(key=lambda item: (item[0]["top"], item[0]["left"]))
    lines: list[dict] = []
    for bounds, text in items:
        matched = None
        for line in lines:
            if _same_line(bounds, line["bounds"]):
                matched = line
                break
        if matched is None:
            lines.append({"items": [(bounds, text)], "bounds": dict(bounds)})
            continue
        matched["items"].append((bounds, text))
        matched["bounds"] = _merge_bounds(matched["bounds"], bounds)

    lines.sort(key=lambda line: (line["bounds"]["top"], line["bounds"]["left"]))
    output = []
    for line in lines:
        line["items"].sort(key=lambda item: item[0]["left"])
        parts = [line["items"][0][1]]
        previous = line["items"][0][0]
        for bounds, text in line["items"][1:]:
            parts.append(_between(previous, bounds))
            parts.append(text)
            previous = bounds
        output.append("".join(parts))
    return "\n".join(output)


def _between(previous: dict, current: dict) -> str:
    gap = current["left"] - previous["right"]
    height = max(1.0, min(previous["height"], current["height"]))
    if gap > height:
        return " ___ "
    if gap <= 1:
        return ""
    return " "


def _bounds(box) -> dict:
    xs = [float(point[0]) for point in box]
    ys = [float(point[1]) for point in box]
    top, bottom = min(ys), max(ys)
    left, right = min(xs), max(xs)
    return {
        "top": top,
        "bottom": bottom,
        "left": left,
        "right": right,
        "height": max(1.0, bottom - top),
        "center_y": (top + bottom) / 2,
    }


def _same_line(current: dict, line: dict) -> bool:
    overlap_top = max(line["top"], current["top"])
    overlap_bottom = min(line["bottom"], current["bottom"])
    overlap = max(0.0, overlap_bottom - overlap_top)
    taller = max(current["height"], line["height"], 1.0)
    return overlap / taller > 0.5


def _merge_bounds(a: dict, b: dict) -> dict:
    top = min(a["top"], b["top"])
    bottom = max(a["bottom"], b["bottom"])
    left = min(a["left"], b["left"])
    right = max(a["right"], b["right"])
    return {
        "top": top,
        "bottom": bottom,
        "left": left,
        "right": right,
        "height": max(1.0, bottom - top),
        "center_y": (top + bottom) / 2,
    }
