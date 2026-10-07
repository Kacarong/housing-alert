"""GH 경기주택도시공사.

1) GH 주택청약센터(apply.gh.or.kr): 임대주택(sr7150), 매입임대(sr7155) 공고 목록·상세. 키 없이 GET으로 동작.
2) GH 홈페이지 '분양/임대 공고' 게시판(www.gh.or.kr, 구분=주택): 분양주택·장기전세 등 청약센터에 없는 공고.
   www는 기본 UA면 410, 브라우저 UA면 200.
두 곳에 같은 공고가 함께 올라오는 경우가 있어 알림 단계에서 제목 기준 중복 제거(dup_key)를 한다.
"""
from __future__ import annotations

import re

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..classify import parse_date, parse_float_range, parse_int, parse_sigungu, sigungu_from_title
from .base import Collector, NoticeData, block_text, first_date_range, node_text, unit_table_stats

APPLY_BASE = "https://apply.gh.or.kr"
APPLY_BOARDS = [("sr7150", "임대주택"), ("sr7155", "매입임대")]
APPLY_PAGE_SIZE = 10

WWW_BASE = "https://www.gh.or.kr"
WWW_BOARD = WWW_BASE + "/gh/announcement-of-salerental001.do"
WWW_CATEGORY_HOUSING = "12"
WWW_PAGE_SIZE = 20


def apply_list_url(board: str) -> str:
    return f"{APPLY_BASE}/sb/sr/{board}/selectPbancRentHouseList.do"


def apply_detail_url(board: str, pbanc_no: str) -> str:
    return f"{APPLY_BASE}/sb/sr/{board}/selectPbancDetailView.do?pbancNo={pbanc_no}"


def _gh_region(region: str) -> tuple[list[str], str | None]:
    region = (region or "").strip()
    sigungu = None
    if region and region not in ("경기도", "경기", "전체", "-") and re.search(r"(시|군)$", region):
        sigungu = region
    return ["경기"], sigungu


def parse_apply_list(html: str, board: str, label: str) -> list[NoticeData]:
    tree = HTMLParser(html)
    out: list[NoticeData] = []
    for row in tree.css("table tbody tr"):
        link = row.css_first("a[data-pbancno]")
        if link is None:
            continue
        pbanc_no = link.attributes.get("data-pbancno") or ""
        tds = row.css("td")
        if len(tds) < 8 or not pbanc_no:
            continue
        title = node_text(link)
        region = node_text(tds[3])
        sidos, sigungu = _gh_region(region)
        deadline = node_text(tds[6])
        out.append(NoticeData(
            source="gh",
            source_id=f"{board}-{pbanc_no}",
            org="GH",
            title=title,
            url=apply_detail_url(board, pbanc_no),
            category_raw=node_text(tds[1]) or label,
            region_raw=region,
            sidos=sidos,
            sigungu=sigungu,
            posted_date=parse_date(node_text(tds[5])),
            apply_end=parse_date(deadline),
            status=node_text(tds[7]) if node_text(tds[7]) not in ("", "-") else None,
            raw={"board": board, "pbancNo": pbanc_no,
                 "pbancKndCd": link.attributes.get("data-pbanckndcd"),
                 "bizTyNm": link.attributes.get("data-biztynm")},
        ))
    return out


def _row_pairs(tree: HTMLParser) -> dict[str, str]:
    """<th scope=row>라벨</th><td>값</td> 쌍."""
    pairs: dict[str, str] = {}
    for th in tree.css("th[scope=row]"):
        td = th.next
        while td is not None and td.tag != "td":
            td = td.next
        if td is not None:
            key = node_text(th)
            pairs.setdefault(key, node_text(td))
            pairs[f"_all:{key}"] = pairs.get(f"_all:{key}", "") + node_text(td) + "\n"
    return pairs


def parse_apply_detail(html: str) -> dict:
    tree = HTMLParser(html)
    content = tree.css_first("#sub_content") or tree.body
    if content is None or "공고명" not in (content.text() or ""):
        raise ValueError("GH 상세 본문을 찾지 못함")
    pairs = _row_pairs(tree)
    result: dict = {}
    if pairs.get("공고일"):
        result["posted_date"] = parse_date(pairs["공고일"])
    if pairs.get("공고상태"):
        result["status"] = pairs["공고상태"]
    complexes = [c for c in pairs.get("_all:지구명", "").splitlines() if c.strip()]
    addresses = [a for a in pairs.get("_all:소재지", "").splitlines() if a.strip() and a.strip() != "지도보기"]
    if complexes:
        result["complex_name"] = ", ".join(dict.fromkeys(complexes))[:200]
    if addresses:
        sg = parse_sigungu(addresses[0])
        if sg:
            result["sigungu"] = sg
    result["complex_text"] = "\n".join(complexes + addresses)[:2000]

    stats = unit_table_stats(tree).as_dict()
    if "area_min" not in stats:
        areas = []
        for v in pairs.get("_all:전용면적(m²)", "").splitlines():
            lo, hi = parse_float_range(v)
            if lo:
                areas += [lo, hi]
        if areas:
            stats["area_min"], stats["area_max"] = min(areas), max(areas)
    if "households" not in stats:
        total = sum(parse_int(v) or 0 for v in pairs.get("_all:모집호수", "").splitlines())
        if total:
            stats["households"] = total
    result.update(stats)

    # 본문: 공고 상세 영역만 (사이트 메뉴 제외)
    for nav in content.css("header, nav, footer, #footer, .lnb, .snb"):
        nav.decompose()
    body = block_text(content)
    i = body.find("공고명")
    body = body[i:] if i >= 0 else body
    j = body.find("목록으로")
    body = body[:j] if j > 0 else body
    result["body_text"] = body
    start, end = first_date_range(body, ["온라인접수기간", "접수기간", "신청기간", "접수처 운영기간"])
    if start:
        result["apply_start"] = start
    if end:
        result["apply_end"] = end
    return result


def parse_www_list(html: str) -> list[NoticeData]:
    tree = HTMLParser(html)
    out: list[NoticeData] = []
    for row in tree.css("table.board-list-table tbody tr"):
        link = row.css_first("td.title a")
        if link is None:
            continue
        m = re.search(r"articleNo=(\d+)", link.attributes.get("href") or "")
        if not m:
            continue
        article = m.group(1)
        title = node_text(link)
        category = node_text(row.css_first("td.category"))
        out.append(NoticeData(
            source="gh_www",
            source_id=article,
            org="GH",
            title=title,
            url=f"{WWW_BOARD}?mode=view&articleNo={article}",
            category_raw=category,
            region_raw="경기도",
            sidos=["경기"],
            sigungu=sigungu_from_title(title),
            posted_date=parse_date(node_text(row.css_first("td.date"))),
            raw={"articleNo": article, "department": node_text(row.css_first("td.department"))},
        ))
    return out


def parse_www_detail(html: str) -> dict:
    tree = HTMLParser(html)
    view = tree.css_first("div.board-view")
    if view is None:
        raise ValueError("GH 게시판 상세(board-view)를 찾지 못함")
    body = block_text(view)
    j = body.find("이전글")
    body = body[:j] if j > 0 else body
    result = {"body_text": body}
    start, end = first_date_range(body, ["접수기간", "신청기간", "청약접수", "접수일"])
    if start:
        result["apply_start"] = start
    if end:
        result["apply_end"] = end
    return result


class GHApplyCollector(Collector):
    name = "gh"
    label = "GH 주택청약센터"
    org = "GH"

    def list_notices(self, client, is_known, baseline):
        items: list[NoticeData] = []
        for board, label in APPLY_BOARDS:
            for page in range(1, self.max_pages + 1):
                html = client.get_html(apply_list_url(board), params={"pageIndex": str(page)})
                page_items = parse_apply_list(html, board, label)
                if page == 1 and not page_items:
                    raise ValueError(f"GH 청약센터 목록 파싱 실패({label})")
                items.extend(page_items)
                if not self.should_fetch_next_page(page_items, lambda sid: is_known(sid), baseline, APPLY_PAGE_SIZE):
                    break
        return items

    def fetch_detail(self, client, notice):
        return parse_apply_detail(client.get_html(notice["url"]))


class GHWwwCollector(Collector):
    name = "gh_www"
    label = "GH 분양/임대 공고 게시판"
    org = "GH"

    def list_notices(self, client, is_known, baseline):
        items: list[NoticeData] = []
        for page in range(1, self.max_pages + 1):
            html = client.get_html(WWW_BOARD, params={
                "mode": "list", "srCategoryId": WWW_CATEGORY_HOUSING,
                "articleLimit": str(WWW_PAGE_SIZE), "article.offset": str((page - 1) * WWW_PAGE_SIZE),
            })
            page_items = parse_www_list(html)
            if page == 1 and not page_items:
                raise ValueError("GH 게시판 목록 파싱 실패")
            items.extend(page_items)
            if not self.should_fetch_next_page(page_items, is_known, baseline, WWW_PAGE_SIZE):
                break
        return items

    def fetch_detail(self, client, notice):
        return parse_www_detail(client.get_html(notice["url"]))
