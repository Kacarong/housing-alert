from datetime import date

from housing_alert.filters import DEFAULT_FILTERS, is_notifiable, match, normalize_config

TODAY = date(2026, 10, 7)


def notice(**kw):
    base = {
        "source": "lh", "org": "LH", "title": "창녕영산 1BL 행복주택 예비입주자 모집 공고", "supply_type": "행복주택",
        "notice_kind": "모집", "sido": "경남", "sigungu": "창녕군", "apply_end": "2026-10-21", "status": "공고중",
        "area_min": 16.0, "area_max": 36.0, "deposit_min": 10_000_000, "rent_min": 60_000, "households": 120,
        "body_text": "청년 신혼부부 고령자", "cooperative_suspect": 0, "coop_override": 0,
    }
    base.update(kw)
    return base


def cfg(**kw):
    return normalize_config(kw)


def test_empty_filter_matches_everything_open():
    ok, fails = match(notice(), cfg(), TODAY)
    assert ok, fails


def test_region():
    assert not match(notice(), cfg(sidos=["서울", "경기"]), TODAY)[0]
    assert match(notice(sido="경기"), cfg(sidos=["서울", "경기"]), TODAY)[0]
    assert match(notice(sido="전국"), cfg(sidos=["서울"]), TODAY)[0]  # 전국 공고는 모든 지역 조건 통과
    assert match(notice(sido="광주,전남"), cfg(sidos=["전남"]), TODAY)[0]
    assert match(notice(), cfg(sigungus=["창녕"]), TODAY)[0]
    assert not match(notice(), cfg(sigungus=["김해"]), TODAY)[0]


def test_supply_types_and_private_rent():
    assert not match(notice(), cfg(supply_types=["국민임대"]), TODAY)[0]
    ok, fails = match(notice(supply_type="일반 민간임대", source="applyhome_urbty", org="청약홈"), cfg(), TODAY)
    assert not ok and any("일반 민간임대" in f for f in fails)
    assert match(notice(supply_type="공공지원 민간임대", source="applyhome_pvt_rent", org="청약홈"), cfg(), TODAY)[0]
    # 체크 해제 시 일반 민간임대도 허용
    assert match(notice(supply_type="일반 민간임대", source="applyhome_urbty", org="청약홈"),
                 cfg(private_rent_official_only=False), TODAY)[0]


def test_area_money_and_unknown_policy():
    assert match(notice(), cfg(area_min=30), TODAY)[0]          # 범위가 겹치면 통과
    assert not match(notice(), cfg(area_min=40), TODAY)[0]
    assert not match(notice(), cfg(deposit_max=5_000_000), TODAY)[0]
    assert match(notice(), cfg(rent_max=100_000), TODAY)[0]
    unknown = notice(deposit_min=None)
    assert match(unknown, cfg(deposit_max=5_000_000), TODAY)[0]
    assert not match(unknown, cfg(deposit_max=5_000_000, unknown_policy="exclude"), TODAY)[0]
    # 분양가 조건은 분양 유형에만 적용
    sale = notice(supply_type="공공분양", price_min=600_000_000)
    assert not match(sale, cfg(price_max=500_000_000), TODAY)[0]
    assert match(notice(), cfg(price_max=1), TODAY)[0]
    assert not match(notice(), cfg(households_min=500), TODAY)[0]


def test_keywords_and_target_groups():
    assert match(notice(), cfg(include_keywords=["행복"]), TODAY)[0]
    assert not match(notice(), cfg(include_keywords=["장기전세"]), TODAY)[0]
    assert not match(notice(), cfg(exclude_keywords=["예비입주자"]), TODAY)[0]
    # 대상 계층은 기본적으로 제목에서만 찾는다(본문 공통 문구 오탐 방지)
    assert not match(notice(), cfg(target_groups=["청년"]), TODAY)[0]
    assert match(notice(), cfg(target_groups=["청년"], keywords_in_body=True), TODAY)[0]
    assert match(notice(title="2026년 하반기 신혼·신생아 매입임대주택 입주자 모집공고"), cfg(target_groups=["신혼부부"]), TODAY)[0]


def test_closed_and_kind():
    assert not match(notice(apply_end="2026-10-01"), cfg(), TODAY)[0]
    assert match(notice(apply_end="2026-10-01"), cfg(exclude_closed=False), TODAY)[0]
    assert not match(notice(status="접수마감", apply_end=None), cfg(), TODAY)[0]
    assert not match(notice(notice_kind="안내"), cfg(), TODAY)[0]
    assert match(notice(notice_kind="안내"), cfg(recruit_only=False), TODAY)[0]


def test_coop_never_notifiable_unless_override():
    assert not is_notifiable(notice(cooperative_suspect=1))
    assert is_notifiable(notice(cooperative_suspect=1, coop_override=1))


def test_default_filters_normalize():
    assert len(DEFAULT_FILTERS) == 5
    for name, raw in DEFAULT_FILTERS:
        c = normalize_config(raw)
        assert c["exclude_closed"] in (True, False)
    everything = normalize_config(DEFAULT_FILTERS[-1][1])
    assert match(notice(notice_kind="안내", apply_end="2020-01-01"), everything, TODAY)[0]
