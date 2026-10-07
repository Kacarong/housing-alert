"""SH 서울주택도시개발공사 '공고 및 공지' 게시판 스크래핑.

기본 User-Agent로는 307 에러 페이지가 나오므로 브라우저 UA(PoliteClient 기본값)가 필요하다.
게시판: 주택임대(m_247, multi_itm_seq=2), 주택분양(m_244, multi_itm_seq=1). 한 페이지 10건.
상세 본문은 짧은 안내문 위주이고 보증금·면적은 첨부 공고문(PDF/HWP)에만 있어 읽지 않는다.
"""
from __future__ import annotations

import re

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..classify import parse_date, sigungu_from_title
from .base import Collector, NoticeData, block_text, node_text

BASE = "https://www.i-sh.co.kr"
BOARDS = [
    # (게시판 코드, 메뉴 경로, multi_itm_seq, 라벨)
    ("m_247", "S1T294C297", "2", "주택임대"),
    ("m_244", "S1T294C296", "1", "주택분양"),
]
PAGE_SIZE = 10


def board_url(menu: str, board: str, page: str) -> str:
    return f"{BASE}/main/lay2/program/{menu}/www/brd/{board}/{page}"


def view_url(menu: str, board: str, seq: str, multi: str) -> str:
    return board_url(menu, board, "view.do") + f"?seq={seq}&multi_itm_seq={multi}"


_SEQ_RE = re.compile(r"getDetailView\('(\d+)'\)")


def parse_list(html: str, board: str, menu: str, multi: str, label: str) -> list[NoticeData]:
    tree = HTMLParser(html)
    out: list[NoticeData] = []
    table = tree.css_first("#listTb table") or tree
    for row in table.css("tbody tr"):
        link = row.css_first("a[onclick*=getDetailView]")
        if link is None:
            continue
        m = _SEQ_RE.search(link.attributes.get("onclick") or "")
        if not m:
            continue
        seq = m.group(1)
        for badge in link.css("span.icoNew"):
            badge.decompose()
        title = node_text(link)
        tds = row.css("td")
        dept = node_text(tds[2]) if len(tds) > 2 else ""
        posted = parse_date(node_text(tds[3])) if len(tds) > 3 else None
        out.append(NoticeData(
            source="sh",
            source_id=seq,
            org="SH",
            title=title,
            url=view_url(menu, board, seq, multi),
            category_raw=label,
            region_raw="서울특별시",
            sidos=["서울"],
            sigungu=sigungu_from_title(title),
            posted_date=posted,
            raw={"seq": seq, "board": board, "menu": menu, "multi_itm_seq": multi, "dept": dept},
        ))
    return out


def parse_detail(html: str) -> dict:
    tree = HTMLParser(html)
    table = tree.css_first("div.detailTable")
    if table is None:
        raise ValueError("SH 상세 본문(detailTable)을 찾지 못함")
    body = block_text(table)
    return {"body_text": body}


class SHCollector(Collector):
    name = "sh"
    label = "SH 서울주택도시공사"
    org = "SH"

    def list_notices(self, client, is_known, baseline):
        items: list[NoticeData] = []
        for board, menu, multi, label in BOARDS:
            for page in range(1, self.max_pages + 1):
                html = client.get_html(board_url(menu, board, "list.do"),
                                       params={"multi_itm_seq": multi, "page": str(page)})
                page_items = parse_list(html, board, menu, multi, label)
                if page == 1 and not page_items:
                    raise ValueError(f"SH 목록 파싱 실패({label}): 게시글 행이 없음")
                items.extend(page_items)
                if not self.should_fetch_next_page(page_items, is_known, baseline, PAGE_SIZE):
                    break
        return items

    def fetch_detail(self, client, notice):
        return parse_detail(client.get_html(notice["url"]))
