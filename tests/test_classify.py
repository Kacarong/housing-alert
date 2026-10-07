from housing_alert.classify import (
    classify_notice_kind, classify_supply_type, is_correction_title, parse_date, parse_date_range,
    parse_sidos, parse_sigungu, sigungu_from_title,
)


def test_parse_sidos():
    assert parse_sidos("경상남도") == ["경남"]
    assert parse_sidos("전남광주통합특별시") == ["광주", "전남"]
    assert parse_sidos("대구광역시 외") == ["대구"]
    assert parse_sidos("전국") == ["전국"]
    assert parse_sidos("서울") == ["서울"]
    assert parse_sidos("") == []


def test_sigungu():
    assert parse_sigungu("대전광역시 서구 구봉산북로 212") == "서구"
    assert parse_sigungu("경기도 수원시 영통구 광교로") == "수원시 영통구"
    assert parse_sigungu("경기도 남양주시 다산동 6026") == "남양주시"
    assert sigungu_from_title("특화형 매입임대주택(금천구) 입주자 모집") == "금천구"
    assert sigungu_from_title("입주자 모집공고(경기도 김포시)") == "김포시"


def test_supply_types():
    assert classify_supply_type("매입임대", "기존주택매입 입주자 모집") == "매입임대"
    assert classify_supply_type("영구임대", "[정정공고]대전 영구임대주택") == "영구임대"
    assert classify_supply_type("분양주택", "김해시 다세대주택 잔여세대 일반매각(무순위)", "LH") == "무순위/잔여세대"
    assert classify_supply_type("공공분양(신혼희망)", "인천계양 A17블록 신혼희망타운", "LH") == "공공분양"
    assert classify_supply_type("주택임대", "제51차 장기전세주택 입주자 모집공고", "SH") == "장기전세"
    assert classify_supply_type("주택임대", "2026년 신정도시마을 잔여세대 입주자모집공고", "SH") == "기타"
    assert classify_supply_type(None, "○○ 공공지원민간임대 입주자 모집") == "공공지원 민간임대"
    assert classify_supply_type(None, "○○ 민간임대 아파트") == "일반 민간임대"
    assert classify_supply_type("주택", "안양 에버포레 자연&e편한세상(A1BL) 분양주택(민영주택) 입주자 모집공고", "GH") == "민간분양"
    assert classify_supply_type("통합공공임대", "(최초) 다산지금A3 통합공공임대주택 입주자 모집 공고") == "통합공공임대"


def test_notice_kind():
    assert classify_notice_kind("제51차 장기전세주택 입주자 모집공고(2026.08.31.공고)") == "모집"
    assert classify_notice_kind("제8차 장기전세주택2(미리내집) 입주자 모집공고(2026. 8. 21.) 서류심사대상자 발표 및 서류제출 안내") == "안내"
    assert classify_notice_kind("[당첨자발표] 청년 매입임대주택 예비자 공급 계약대상자 발표") == "안내"
    assert classify_notice_kind("경남 김해시 다세대주택 잔여세대 일반매각(무순위)") == "모집"


def test_correction():
    assert is_correction_title("[정정공고]대전광역시 영구임대주택")
    assert is_correction_title("(수정) 2026년 2차 장기미임대 매입임대주택 입주자모집공고")
    assert is_correction_title("[5차수정]마곡지구 17단지")
    assert not is_correction_title("2026년 국민임대주택 입주자 모집공고")


def test_dates():
    assert parse_date("2026.10.07") == "2026-10-07"
    assert parse_date("26.10.07") == "2026-10-07"
    assert parse_date("2026. 9. 9.") == "2026-09-09"
    assert parse_date("20261007") == "2026-10-07"
    assert parse_date("공고문 확인") is None
    assert parse_date_range("2026.10.20 ~ 2026.10.22") == ("2026-10-20", "2026-10-22")


def test_long_term_public_rental():
    assert classify_supply_type("공공임대", "부산당감1단지 50년공공임대주택 예비입주자 모집 공고") == "공공임대(5·10·50년)"
    assert classify_supply_type("공공임대", "마산삼계2공임50년 예비입주자 모집공고문") == "공공임대(5·10·50년)"
    assert classify_supply_type("공공임대", "대전천동3 5블록 10년 분양전환공공임대주택 예비입주자 모집공고") == "공공임대(5·10·50년)"


def test_title_sigungu_ignores_project_district():
    assert sigungu_from_title("안양 냉천지구 공공임대주택(5년) 및 행복주택 지구 내 주민 우선공급") is None
