"""협동조합형 민간임대 차단 규칙 고정."""
from housing_alert.cooperative import detect

COOP_SAMPLES = [
    "○○역 민간임대 협동조합 조합원 모집 (확정분양가, 출자금 3천만원)",
    "[선착순] ○○ 임대협동조합 조합원모집 안내",
    "○○ 지역주택조합 조합원 추가 모집 - 업무대행사 ○○",
    "○○동 민간임대아파트 추진위원회 발기인 모집, 가입비 500만원",
]
NORMAL_LH = [
    "창녕영산 1BL 행복주택 예비입주자 모집 공고 ('26.10.07. 공고)",
    "2026년 하반기 대구경북지역 기존주택매입 입주자 모집공고",
    "[정정공고]대전광역시 영구임대주택 예비입주자 모집(2026.10.06)",
    "인천계양 A17블록 신혼희망타운(공공분양) 입주자모집공고",
]


def test_coop_samples_are_suspect():
    for title in COOP_SAMPLES:
        r = detect(title)
        assert r.suspect, title
        assert r.title_matched


def test_normal_lh_notices_pass():
    for title in NORMAL_LH:
        r = detect(title, body="임대보증금 및 월임대료는 공고문을 확인하시기 바랍니다.")
        assert not r.suspect, title


def test_title_and_body_matches_are_distinguished():
    r = detect("○○ 행복주택 입주자 모집", body="본 사업은 ○○ 협동조합과 무관하며 ...")
    assert r.suspect
    assert [m.where for m in r.matches] == ["body"]
    assert r.reason_text() == "본문 키워드 '협동조합'"
    r2 = detect("○○ 협동조합 조합원 모집", body="출자금 납부")
    reason = r2.reason_text()
    assert "제목 키워드 '협동조합'" in reason and "본문 키워드 '출자금'" in reason


def test_keyword_spacing_variants():
    assert detect("조합원  모집 공고").suspect
    assert detect("조합 가입 안내").suspect


def test_custom_keyword_list():
    assert not detect("○○ 협동조합 안내", keywords=["출자금"]).suspect
    assert detect("○○ 출자금 안내", keywords=["출자금"]).suspect


def test_real_sh_operator_cooperative_is_flagged_in_title():
    # 실제 SH 공고: 정식 매입임대이지만 운영기관이 협동조합 → 제목 매칭으로 제외되고 사유가 남는다(오탐 확인용)
    r = detect("[청년형] 특화형 매입임대주택(금천구) 입주자 모집 공고(운영기관 : 한지붕 협동조합)")
    assert r.suspect and r.title_matched
    assert "운영기관 : 한지붕 협동조합" in r.matches[0].snippet
