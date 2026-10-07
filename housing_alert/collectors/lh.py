"""LH 청약플러스 공고문 목록 스크래핑 (키 없이 동작).

목록: POST /lhapply/apply/wt/wrtanc/selectWrtancList.do (mi=1026 임대주택, mi=1027 분양주택)
상세: GET  /lhapply/apply/wt/wrtanc/selectWrtancInfo.do?panId=&ccrCnntSysDsCd=&uppAisTpCd=&aisTpCd=&mi=
"""
from __future__ import annotations

import re
from urllib.parse import urlencode

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..classify import parse_date, parse_date_range, parse_float_range, parse_int, parse_sidos, parse_sigungu
from .base import (
    Collector, NoticeData, block_text, clean, first_date_range, fragment_between, labeled_values,
    node_text, unit_table_stats,
)

BASE = "https://apply.lh.or.kr"
LIST_URL = BASE + "/lhapply/apply/wt/wrtanc/selectWrtancList.do"
DETAIL_URL = BASE + "/lhapply/apply/wt/wrtanc/selectWrtancInfo.do"
BOARDS = [("1026", "임대주택"), ("1027", "분양주택")]
PAGE_SIZE = 50


def detail_url(pan_id: str, ccr: str, upp: str, ais: str, mi: str) -> str:
    return DETAIL_URL + "?" + urlencode(
        {"panId": pan_id, "ccrCnntSysDsCd": ccr, "uppAisTpCd": upp, "aisTpCd": ais, "mi": mi}
    )


def parse_list(html: str, mi: str) -> list[NoticeData]:
    tree = HTMLParser(html)
    out: list[NoticeData] = []
    for row in tree.css("table tbody tr"):
        link = row.css_first("a.wrtancInfoBtn")
        if link is None:
            continue
        tds = row.css("td")
        if len(tds) < 8:
            continue
        attrs = link.attributes
        pan_id = attrs.get("data-id1") or ""
        ccr, upp, ais = attrs.get("data-id2") or "", attrs.get("data-id3") or "", attrs.get("data-id4") or ""
        span = link.css_first("span")
        if span is not None:
            for em in span.css("em"):
                em.decompose()
        title = node_text(span or link)
        region = node_text(tds[3])
        sidos = parse_sidos(region)
        if (not sidos or sidos == ["전국"]) and parse_sidos(title):
            sidos = parse_sidos(title)
        out.append(NoticeData(
            source="lh",
            source_id=pan_id,
            org="LH",
            title=title,
            url=detail_url(pan_id, ccr, upp, ais, mi),
            category_raw=node_text(tds[1]),
            region_raw=region,
            sidos=sidos,
            posted_date=parse_date(node_text(tds[5])),
            apply_end=parse_date(node_text(tds[6])),
            status=node_text(tds[7]),
            raw={"panId": pan_id, "ccrCnntSysDsCd": ccr, "uppAisTpCd": upp, "aisTpCd": ais, "mi": mi,
                 "board": dict(BOARDS).get(mi)},
        ))
    return out


def paging_form(html: str) -> dict[str, str]:
    tree = HTMLParser(html)
    form = tree.css_first('form[name="pagingForm"]')
    if form is None:
        return {}
    return {i.attributes.get("name"): i.attributes.get("value") or "" for i in form.css("input[name]")}


_JS_SET_RE = re.compile(r"\.set\('(lsGmy|rfe|ddoAr|nowHshCnt|hshCnt|sbdLgoNm)',\s*'([^']*)'\)")


def parse_detail(html: str) -> dict:
    frag = fragment_between(html, 'class="bbs_ViewA"', ['class="btns ar"', "openHistoryPopupForm"])
    if not frag:
        raise ValueError("LH 상세 본문 영역(bbs_ViewA)을 찾지 못함")
    tree = HTMLParser(frag)
    body = block_text(HTMLParser(frag).body)
    result: dict = {"body_text": body}

    # 게시글 정보 (공고상태/유형/공고일/마감일)
    info = {}
    for li in tree.css("ul.bbsV_data li"):
        strong = li.css_first("strong")
        if strong is None:
            continue
        key = node_text(strong)
        info[key] = clean(node_text(li)[len(key):])
    if info.get("공고일"):
        result["posted_date"] = parse_date(info["공고일"])
    if info.get("마감일"):
        result["apply_end"] = parse_date(info["마감일"])
    if info.get("공고상태"):
        result["status"] = info["공고상태"]

    # 단지 정보 (소재지/전용면적/총 세대수) - 단지 탭마다 반복
    complex_names = [node_text(h) for h in tree.css("h4.tit2")
                     if not re.search(r"이미지|위치|주택형|접수처|관련|특장점|평면|동영상", node_text(h))]
    labels = labeled_values(body)
    addresses = [a for a in labels.get("_all:소재지", "").splitlines() if a.strip()]
    if complex_names:
        result["complex_name"] = ", ".join(dict.fromkeys(complex_names))[:200]
    if addresses:
        result["sigungu"] = parse_sigungu(addresses[0])
        sidos = parse_sidos(addresses[0].split(" ")[0])
        if sidos:
            result["sidos"] = sidos
    result["complex_text"] = "\n".join(complex_names + addresses)[:2000]

    stats = unit_table_stats(tree).as_dict()
    if "area_min" not in stats:
        areas = []
        for v in labels.get("_all:전용면적(㎡)", "").splitlines():
            lo, hi = parse_float_range(v)
            if lo:
                areas += [lo, hi]
        if areas:
            stats["area_min"], stats["area_max"] = min(areas), max(areas)
    if "households" not in stats:
        total = sum(parse_int(v) or 0 for v in labels.get("_all:총 세대수", "").splitlines())
        if total:
            stats["households"] = total
    # 표에 '공고문 확인'만 있을 때 스크립트 데이터에서 보증금·월세 보완
    js = _JS_SET_RE.findall(html)
    if "deposit_min" not in stats:
        deps = [parse_int(v) for k, v in js if k == "lsGmy" and parse_int(v)]
        if deps:
            stats["deposit_min"], stats["deposit_max"] = min(deps), max(deps)
    if "rent_min" not in stats:
        rents = [parse_int(v) for k, v in js if k == "rfe" and parse_int(v)]
        if rents:
            stats["rent_min"], stats["rent_max"] = min(rents), max(rents)
    result.update(stats)

    # 접수기간
    acp = tree.css_first("#sta_acpDt")
    start = end = None
    if acp is not None:
        start, end = parse_date_range(node_text(acp))
    if not start:
        # 분양: 공급일정 표의 신청일시 열
        dates = []
        for table in tree.css("table"):
            header = table.css_first("thead tr")
            if header is None or "신청일시" not in node_text(header):
                continue
            for row in table.css("tbody tr"):
                s, e = parse_date_range(node_text(row))
                if s:
                    dates.append((s, e or s))
        if dates:
            start = min(d[0] for d in dates)
            end = max(d[1] for d in dates)
    if not start:
        start, end = first_date_range(body, ["접수기간", "신청기간"])
    if start:
        result["apply_start"] = start
    if end:
        result["apply_end"] = end
    return result


class LHCollector(Collector):
    name = "lh"
    label = "LH 청약플러스"
    org = "LH"

    def list_notices(self, client, is_known, baseline):
        items: list[NoticeData] = []
        for mi, _label in BOARDS:
            # 1페이지는 GET, 다음 페이지는 1페이지의 pagingForm 숨은 값 그대로 currPage만 바꿔 POST
            html = client.get_html(LIST_URL, params={"mi": mi})
            form = paging_form(html)
            for page in range(1, self.max_pages + 1):
                if page > 1:
                    html = client.post_html(LIST_URL, data={**form, "currPage": str(page)})
                page_items = parse_list(html, mi)
                if page == 1 and not page_items:
                    raise ValueError(f"LH 목록 파싱 실패(mi={mi}): 공고 행이 없음")
                items.extend(page_items)
                if not form or not self.should_fetch_next_page(page_items, is_known, baseline, PAGE_SIZE):
                    break
        return items

    def fetch_detail(self, client, notice):
        url = notice.get("url")
        if not url:
            return {}
        return parse_detail(client.get_html(url))
