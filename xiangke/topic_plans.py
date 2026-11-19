"""Shared format for a child's teaching plans, keyed by an answer rule."""

import re

MAX_TOPIC_VOICE = 80
_TOPIC_KEY = re.compile(r"[a-z][a-z0-9_']*(?::[a-z][a-z0-9_']*){0,2}")
_STRATEGIES = {"be_i": "self_first", "v_base_after:can": "cue_first"}


def without_demo_history(plan: dict) -> dict:
    """Keep the version numbers of old starter plans for sync and replay."""
    legacy = plan.get("planVersion") == 2 and plan.get("basedOnSessionId") == "s_01"
    if not plan.get("starter") and not legacy:
        return plan
    return {**plan, "starter": True, "basedOnSessionId": None,
            "voice": "一步一步问，让他自己说出答案。", "reminders": [], "examples": []}


def topic_key(computed: dict | None) -> str | None:
    if not computed or not computed.get("ruleKey"):
        return None
    key = computed["ruleKey"]
    if computed.get("kind") == "verb_3s":
        return f"correction:{key}"
    if key == "v_base_after" and computed.get("cue"):
        return f"{key}:{computed['cue'].casefold()}"
    return key


def topic_strategies(key: str) -> list[str]:
    return ["explain", _STRATEGIES[key]] if key in _STRATEGIES else ["explain"]


def valid_topic_entry(key, entry) -> bool:
    if not isinstance(key, str) or not _TOPIC_KEY.fullmatch(key) or not isinstance(entry, dict):
        return False
    voice = entry.get("voice")
    return (
        isinstance(voice, str) and 0 < len(voice.strip()) <= MAX_TOPIC_VOICE
        and entry.get("strategy") in topic_strategies(key)
    )
