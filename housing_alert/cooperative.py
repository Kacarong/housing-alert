"""협동조합형 민간임대(조합원 모집) 의심 판정.

공고명(제목)과 본문/단지 정보 매칭을 구분해 사유에 남긴다. 사용자는 웹에서
키워드 목록을 고칠 수 있고, 오탐이면 공고별로 '오탐(허용)' 처리할 수 있다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

DEFAULT_KEYWORDS = [
    "협동조합", "조합원 모집", "조합원모집", "출자금", "확정분양가", "추진위원회",
    "발기인", "조합 가입", "가입비", "업무대행", "지역주택조합", "임대협동조합",
]

LOCATION_LABELS = {"title": "제목", "body": "본문", "complex": "단지정보"}


@dataclass
class CoopMatch:
    keyword: str
    where: str  # title | body | complex
    snippet: str

    def as_dict(self) -> dict:
        return {"keyword": self.keyword, "where": self.where, "snippet": self.snippet}


@dataclass
class CoopResult:
    suspect: bool
    matches: list[CoopMatch] = field(default_factory=list)

    @property
    def title_matched(self) -> bool:
        return any(m.where == "title" for m in self.matches)

    def reason_text(self) -> str:
        if not self.matches:
            return ""
        parts = []
        seen = set()
        for m in self.matches:
            key = (m.keyword, m.where)
            if key in seen:
                continue
            seen.add(key)
            parts.append(f"{LOCATION_LABELS.get(m.where, m.where)} 키워드 '{m.keyword}'")
        return ", ".join(parts)


def _keyword_regex(keyword: str) -> re.Pattern:
    pieces = [re.escape(p) for p in keyword.split()]
    return re.compile(r"\s*".join(pieces))


def _snippet(text: str, start: int, end: int, width: int = 25) -> str:
    s = max(0, start - width)
    e = min(len(text), end + width)
    return re.sub(r"\s+", " ", text[s:e]).strip()


def detect(
    title: str,
    body: str | None = None,
    complex_info: str | None = None,
    keywords: list[str] | None = None,
) -> CoopResult:
    keywords = [k.strip() for k in (keywords if keywords is not None else DEFAULT_KEYWORDS) if k and k.strip()]
    matches: list[CoopMatch] = []
    for where, text in (("title", title or ""), ("complex", complex_info or ""), ("body", body or "")):
        if not text:
            continue
        for kw in keywords:
            m = _keyword_regex(kw).search(text)
            if m:
                matches.append(CoopMatch(kw, where, _snippet(text, m.start(), m.end())))
    return CoopResult(suspect=bool(matches), matches=matches)
