"""lead_scoring — classify social-media comments into travel sales leads.

Given comments scraped from a social post (for now an uploaded JSON; later the
Meta Graph API), use the LLM to tag each commenter as a hot / warm / cold lead
for a Dubai travel agency, with a 0-100 score and a one-line reason.

The classifier sends ALL comments in ONE batched LLM call (fast + cheap, no
per-comment rate-limit risk) and returns structured rows for the dashboard.
"""

from __future__ import annotations

import json
import re
from typing import Any

from llm import build_llm

# Lead tiers, in priority order (hottest first).
TIERS = ("hot", "warm", "cold")


def normalize_comments(raw: Any) -> list[dict[str, Any]]:
    """Coerce an uploaded JSON blob into a flat list of comment dicts.

    Tolerant of the shapes the dummy file and (later) Meta Graph might use:
      - a top-level list of comment objects
      - {"comments": [...]} / {"data": [...]}
      - posts with nested "comments": [...]
    Each returned row has at least: username, name, comment (+ any extra fields).
    """
    items: list[dict[str, Any]] = []

    def _add(obj: dict[str, Any], post: str | None = None) -> None:
        if not isinstance(obj, dict):
            return
        row = dict(obj)
        if post and "post" not in row:
            row["post"] = post
        items.append(row)

    if isinstance(raw, list):
        for o in raw:
            _add(o)
    elif isinstance(raw, dict):
        if isinstance(raw.get("comments"), list):
            for o in raw["comments"]:
                _add(o)
        elif isinstance(raw.get("data"), list):
            for o in raw["data"]:
                _add(o)
        else:
            # posts -> nested comments
            for v in raw.values():
                if isinstance(v, list):
                    for o in v:
                        if isinstance(o, dict) and isinstance(o.get("comments"), list):
                            for c in o["comments"]:
                                _add(c, post=str(o.get("caption") or o.get("post") or ""))
                        else:
                            _add(o)

    # Normalize the key fields we rely on (be liberal about field names).
    out: list[dict[str, Any]] = []
    for r in items:
        username = r.get("username") or r.get("user") or r.get("handle") or r.get("user_id") or ""
        name = r.get("name") or r.get("full_name") or r.get("display_name") or username
        comment = r.get("comment") or r.get("text") or r.get("message") or r.get("comment_text") or ""
        if not (str(comment).strip() or str(username).strip()):
            continue
        out.append({**r, "username": str(username), "name": str(name), "comment": str(comment)})
    return out


_SYSTEM = """You are a lead-qualification analyst for a Dubai travel agency.
You read social-media comments on the agency's travel posts and judge how likely
each commenter is to become a paying customer (book a trip).

Tag each comment:
- "hot"  = clear buying intent / urgency (asks price, "how to book", "DM me",
  "interested", dates, "planning a trip", wants a quote).
- "warm" = genuine interest or a relevant question, but no clear intent to buy
  yet ("looks beautiful, is it good in December?", "what's included?").
- "cold" = no commercial intent (generic praise, emojis only, spam, unrelated,
  "nice pic", tagging a friend with no question).

Score 0-100 (higher = hotter). Give a SHORT reason (max ~12 words).
Return ONLY valid JSON: an array where each element is
{"index": <int>, "tag": "hot|warm|cold", "score": <int 0-100>, "reason": "<text>"}.
The "index" MUST match the input comment's index. No prose, no markdown fence."""


def _heuristic(comment: str) -> dict[str, Any]:
    """Deterministic fallback if the LLM is unavailable — keyword intent scan."""
    t = comment.lower()
    hot = any(k in t for k in ("price", "cost", "book", "how much", "dm", "quote", "interested",
                               "package", "budget", "planning", "want to go", "kitna", "kaise book"))
    warm = "?" in t or any(k in t for k in ("when", "how", "what", "is it", "good time", "include",
                                            "visa", "days", "nights"))
    if hot:
        return {"tag": "hot", "score": 85, "reason": "buying-intent keywords"}
    if warm:
        return {"tag": "warm", "score": 55, "reason": "asked a question / interest"}
    return {"tag": "cold", "score": 15, "reason": "no commercial intent"}


def _extract_json_array(text: str) -> list[dict[str, Any]]:
    """Pull a JSON array out of an LLM reply (tolerates code fences / stray text)."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except (ValueError, TypeError):
        pass
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                return data
        except (ValueError, TypeError):
            pass
    return []


def classify_leads(comments: list[dict[str, Any]], *, use_llm: bool = True) -> list[dict[str, Any]]:
    """Return the comment rows enriched with tag/score/reason, hottest first.

    One batched LLM call for all comments. Falls back to the keyword heuristic
    per comment if the LLM call fails (so the dashboard always renders).
    """
    if not comments:
        return []

    verdicts: dict[int, dict[str, Any]] = {}
    if use_llm:
        numbered = "\n".join(f"{i}. {c['comment']}" for i, c in enumerate(comments))
        try:
            llm = build_llm(temperature=0.0, max_tokens=4096)
            resp = llm.invoke(
                [
                    ("system", _SYSTEM),
                    ("human", f"Classify these {len(comments)} comments:\n{numbered}"),
                ]
            )
            content = resp.content if isinstance(resp.content, str) else str(resp.content)
            for v in _extract_json_array(content):
                if isinstance(v, dict) and isinstance(v.get("index"), int):
                    verdicts[v["index"]] = v
        except Exception:  # noqa: BLE001 — never let the dashboard crash on LLM issues
            verdicts = {}

    rows: list[dict[str, Any]] = []
    for i, c in enumerate(comments):
        v = verdicts.get(i)
        if not v:
            v = _heuristic(c["comment"])
        tag = str(v.get("tag", "cold")).lower()
        if tag not in TIERS:
            tag = "cold"
        try:
            score = max(0, min(100, int(v.get("score", 0))))
        except (ValueError, TypeError):
            score = 0
        rows.append(
            {
                "name": c.get("name", ""),
                "username": c.get("username", ""),
                "comment": c.get("comment", ""),
                "tag": tag,
                "score": score,
                "reason": str(v.get("reason", ""))[:120],
                **{k: c[k] for k in ("post", "timestamp", "likes") if k in c},
            }
        )
    rows.sort(key=lambda r: -r["score"])
    return rows
