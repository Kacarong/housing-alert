"""수집기 공통 타입과 HTML 파싱 도우미."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from selectolax.lexbor import LexborHTMLParser as HTMLParser, LexborNode as Node

from ..classify import parse_float_range, parse_int, parse_date_range
from ..http import PoliteClient

BODY_LIMIT = 8000


@dataclass
class NoticeData:
    source: str
    source_id: str
    org: str
    title: str
    url: str | None = None
    category_raw: str | None = None
    supply_type: str | None = None  # 수집기가 확실히 아는 경우만 지정
    region_raw: str | None = None
    sidos: list[str] = field(default_factory=list)
    sigungu: str | None = None
    complex_name: str | None = None
    households: int | None = None
    area_min: float | None = None
    area_max: float | None = None
    deposit_min: int | None = None
    deposit_max: int | None = None
    rent_min: int | None = None
    rent_max: int | None = None
    price_min: int | None = None
    price_max: int | None = None
    posted_date: str | None = None
    apply_start: str | None = None
    apply_end: str | None = None
    status: str | None = None
    body_text: str | None = None
    complex_text: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    detail_fetched: bool = False


class Collector:
    """출처 하나. list_notices()는 목록만, fetch_detail()은 새 공고일 때만 호출된다."""

    name: str = ""
    label: str = ""
    org: str = ""
    requires_key: bool = False
    has_detail: bool = True
    max_pages: int = 3
    baseline_source: str | None = None  # 기준선(최초 실행) 판단을 다른 수집기 상태로 할 때

    def list_notices(self, client: PoliteClient, is_known: Callable[[str], bool], baseline: bool) -> list[NoticeData]:
        raise NotImplementedError

    def fetch_detail(self, client: PoliteClient, notice: dict) -> dict[str, Any]:
        """notice: DB row dict. 반환: 갱신할 필드 dict (NoticeData 필드명)."""
        return {}

    def should_fetch_next_page(self, page_items: list[NoticeData], is_known, baseline: bool, page_size: int) -> bool:
        if len(page_items) < page_size:
            return False
        if baseline:
            return True
        # 이번 페이지가 전부 처음 보는 공고면 다음 페이지에도 새 공고가 있을 수 있다
        return all(not is_known(n.source_id) for n in page_items)


# ------------------------------------------------------------------ 파싱 도우미

def clean(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def node_text(node: Node | None) -> str:
    if node is None:
        return ""
    return clean(node.text(separator=" "))


def block_text(node: Node | None, limit: int = BODY_LIMIT) -> str:
    """줄바꿈을 살린 본문 텍스트."""
    if node is None:
        return ""
    for bad in node.css("script, style, noscript"):
        bad.decompose()
    text = node.text(separator="\n")
    lines = [clean(line) for line in text.splitlines()]
    out = "\n".join(line for line in lines if line)
    return out[:limit]


def fragment_between(html: str, start_marker: str, end_markers: list[str]) -> str:
    i = html.find(start_marker)
    if i < 0:
        return ""
    # 시작 마커가 속한 태그의 시작부터 자르기
    tag_start = html.rfind("<", 0, i)
    i = tag_start if tag_start >= 0 else i
    end = len(html)
    for m in end_markers:
        j = html.find(m, i + len(start_marker))
        if j >= 0:
            end = min(end, j)
    return html[i:end]


def labeled_values(text: str) -> dict[str, str]:
    """'라벨 : 값' 줄들을 dict로. 같은 라벨이 여러 번이면 첫 값 유지 + 목록은 _all 키에."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^\s*([가-힣A-Za-z()㎡²\s]{2,20}?)\s*[:：]\s*(.+)$", line)
        if m:
            key = clean(m.group(1))
            out.setdefault(key, clean(m.group(2)))
            out.setdefault(f"_all:{key}", "")
            out[f"_all:{key}"] += clean(m.group(2)) + "\n"
    return out


def table_cells(row: Node) -> list[str]:
    return [node_text(c) for c in row.iter() if c.tag in ("th", "td")]


@dataclass
class UnitStats:
    area_min: float | None = None
    area_max: float | None = None
    households: int | None = None
    deposit_min: int | None = None
    deposit_max: int | None = None
    rent_min: int | None = None
    rent_max: int | None = None
    price_min: int | None = None
    price_max: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def _minmax(values: list[float | int]) -> tuple[Any, Any]:
    vals = [v for v in values if v]
    if not vals:
        return None, None
    return min(vals), max(vals)


def unit_table_stats(tree: HTMLParser) -> UnitStats:
    """'전용면적' 열이 있는 주택형 표들을 모아 면적·세대수·보증금·월세·분양가 범위를 낸다."""
    areas: list[float] = []
    households: list[int] = []
    deposits: list[int] = []
    rents: list[int] = []
    prices: list[int] = []
    for table in tree.css("table"):
        header_row = table.css_first("thead tr")
        if header_row is None:
            continue
        headers = table_cells(header_row)
        if not any("전용면적" in h for h in headers):
            continue

        def col(*names: str, exclude: tuple[str, ...] = ()) -> int | None:
            for idx, h in enumerate(headers):
                if any(n in h for n in names) and not any(e in h for e in exclude):
                    return idx
            return None

        c_area = col("전용면적")
        c_now = col("금회공급")
        c_hh = c_now if c_now is not None else col("세대수", "모집호수", exclude=("금회",))
        c_dep = col("임대보증금", "보증금")
        c_rent = col("월임대료", "임대료", exclude=("보증금",))
        c_price = col("분양가", "분양가격", "공급금액", "공급가격", "매각가")
        for row in table.css("tbody tr"):
            cells = table_cells(row)
            if len(cells) < len(headers) - 2:
                continue

            def cell(i: int | None) -> str | None:
                if i is None or i >= len(cells):
                    return None
                return cells[i]

            lo, hi = parse_float_range(cell(c_area))
            if lo:
                areas.extend([lo, hi])
            hh = parse_int(cell(c_hh))
            if hh:
                households.append(hh)
            for c, bucket in ((c_dep, deposits), (c_rent, rents), (c_price, prices)):
                v = parse_int(cell(c))
                if v:
                    bucket.append(v)
    st = UnitStats()
    st.area_min, st.area_max = _minmax(areas)
    st.households = sum(households) if households else None
    st.deposit_min, st.deposit_max = _minmax(deposits)
    st.rent_min, st.rent_max = _minmax(rents)
    st.price_min, st.price_max = _minmax(prices)
    return st


def first_date_range(text: str, labels: list[str]) -> tuple[str | None, str | None]:
    for label in labels:
        for m in re.finditer(re.escape(label) + r"\s*[:：]?\s*([0-9.\-/\s:~∼()월화수목금토일]+)", text):
            start, end = parse_date_range(m.group(1))
            if start:
                return start, end
    return None, None
