"""출처별 저장 HTML 픽스처(2026-10-07 수집)로 파서 동작을 고정한다."""
from conftest import fixture_text

from housing_alert.collectors import gh, lh, sh


def test_lh_rent_list():
    items = lh.parse_list(fixture_text("lh_list_rent.html"), "1026")
    assert len(items) == 50
    first = items[0]
    assert first.source == "lh" and first.source_id == "2015122300020880"
    assert first.title == "장애인 자립특화형 주택 여기가 입주자 모집공고(경기도 김포시)"
    assert first.category_raw == "매입임대"
    # 목록 지역이 '전국'이지만 제목의 시도를 쓴다
    assert first.region_raw == "전국" and first.sidos == ["경기"]
    assert first.posted_date == "2026-10-07" and first.apply_end == "2026-10-23"
    assert "selectWrtancInfo.do?panId=2015122300020880" in first.url
    corr = items[1]
    assert corr.title.startswith("[정정공고]") and corr.sidos == ["대전"]


def test_lh_sale_list():
    items = lh.parse_list(fixture_text("lh_list_sale.html"), "1027")
    assert len(items) == 15
    assert items[0].title == "경남 김해시·양산시 다세대주택 잔여세대 일반매각(무순위)"
    assert items[0].raw["mi"] == "1027"


def test_lh_rent_detail():
    d = lh.parse_detail(fixture_text("lh_detail_rent.html"))
    assert d["sidos"] == ["대전"] and d["sigungu"] == "서구"
    assert d["area_min"] == 19.91 and d["area_max"] == 40.32
    assert d["households"] > 0
    assert d["deposit_min"] == 4250000 and d["rent_max"] == 128010
    assert d["apply_start"] == "2026-10-20" and d["apply_end"] == "2026-10-22"
    assert "정정사유" in d["body_text"]
    # 사이트 공통 메뉴(웹접근성 안내 등)는 본문에 들어가지 않는다
    assert "센스리더" not in d["body_text"]


def test_lh_sale_detail():
    d = lh.parse_detail(fixture_text("lh_detail_sale.html"))
    assert d["price_min"] == 549777000 and d["price_max"] == 622521000
    assert d["households"] == 252
    assert d["apply_start"] == "2026-10-06" and d["apply_end"] == "2026-10-07"
    assert d["sidos"] == ["인천"]


def test_sh_lists_and_detail():
    rent = sh.parse_list(fixture_text("sh_list_rent.html"), "m_247", "S1T294C297", "2", "주택임대")
    assert len(rent) == 10
    coop = [n for n in rent if n.source_id == "310950"][0]
    assert coop.title == "[청년형] 특화형 매입임대주택(금천구) 입주자 모집 공고(운영기관 : 한지붕 협동조합)"
    assert coop.sigungu == "금천구" and coop.sidos == ["서울"] and coop.posted_date == "2026-10-02"
    assert coop.url.endswith("view.do?seq=310950&multi_itm_seq=2")
    sale = sh.parse_list(fixture_text("sh_list_sale.html"), "m_244", "S1T294C296", "1", "주택분양")
    assert len(sale) == 10
    d = sh.parse_detail(fixture_text("sh_view.html"))
    assert "한지붕 협동조합이 입주자를 모집" in d["body_text"]
    assert "사이트맵" not in d["body_text"]


def test_gh_apply_list_and_detail():
    items = gh.parse_apply_list(fixture_text("gh_apply_list_rent.html"), "sr7150", "임대주택")
    assert len(items) == 10
    n = [i for i in items if i.source_id == "sr7150-808"][0]
    assert n.title == "경기리츠1호 다산진건데시앙 장기전세주택 예비입주자 모집공고"
    assert n.category_raw == "장기전세" and n.sigungu == "남양주시" and n.sidos == ["경기"]
    assert n.posted_date == "2026-08-13" and n.apply_end == "2026-08-24" and n.status == "접수마감"
    d = gh.parse_apply_detail(fixture_text("gh_apply_detail.html"))
    assert d["area_min"] == 72.9858 and d["area_max"] == 84.9876
    assert d["households"] == 97
    assert d["apply_start"] == "2026-08-24"
    buy = gh.parse_apply_list(fixture_text("gh_apply_list_buy.html"), "sr7155", "매입임대")
    assert buy[0].source_id == "sr7155-823"
    d2 = gh.parse_apply_detail(fixture_text("gh_apply_detail_buy.html"))
    assert d2["apply_start"] == "2026-08-21" and d2["apply_end"] == "2026-08-31"


def test_gh_www_list_and_detail():
    items = gh.parse_www_list(fixture_text("gh_www_list.html"))
    assert len(items) == 20
    assert items[0].source == "gh_www" and items[0].source_id == "65152"
    assert items[0].posted_date == "2026-08-28"
    d = gh.parse_www_detail(fixture_text("gh_www_view.html"))
    assert "연천BIX 경기행복주택 기업체 기숙사" in d["body_text"]


def test_lh_paging_form_values():
    form = lh.paging_form(fixture_text("lh_list_sale.html"))
    assert form["mi"] == "1027" and form["uppAisTpCd"] == "053954" and form["currPage"] == "1"
