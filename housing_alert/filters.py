"""필터(조건 묶음) 엔진.

필터 설정은 JSON으로 저장한다. match()는 (통과 여부, 탈락 사유 목록)을 돌려준다.
협동조합 의심 공고는 필터와 무관하게 알림 단계에서 항상 제외된다(오탐 허용 처리된 것만 예외).
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from .classify import CAPITAL_AREA, NATIONWIDE, PUBLIC_RENTAL_TYPES, SUPPLY_TYPES, TARGET_GROUPS

SALE_TYPES = {"공공분양", "민간분양", "무순위/잔여세대"}
OFFICIAL_PRIVATE_RENT_SOURCES = {"applyhome_pvt_rent"}
OFFICIAL_ORGS = {"LH", "SH", "GH"}

DEFAULT_CONFIG: dict[str, Any] = {
    "sidos": [],
    "sigungus": [],
    "supply_types": [],
    "sources": [],
    "area_min": None,
    "area_max": None,
    "deposit_max": None,   # 원
    "rent_max": None,      # 원
    "price_max": None,     # 원
    "households_min": None,
    "unknown_policy": "pass",  # 값을 모르는 공고: pass | exclude
    "include_keywords": [],
    "exclude_keywords": [],
    "keywords_in_body": False,
    "target_groups": [],
    "exclude_closed": True,
    "recruit_only": True,
    "private_rent_official_only": True,
    "remind_d1": False,
}


def normalize_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    raw = raw or {}

    def as_list(v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            parts = v.replace("\n", ",").split(",")
        else:
            parts = list(v)
        return [str(p).strip() for p in parts if str(p).strip()]

    def as_num(v: Any, cast=float):
        if v in (None, ""):
            return None
        try:
            return cast(str(v).replace(",", ""))
        except ValueError:
            return None

    def as_bool(v: Any, default: bool) -> bool:
        if v is None:
            return default
        if isinstance(v, bool):
            return v
        return str(v).lower() in ("1", "true", "on", "yes")

    for key in ("sidos", "sigungus", "supply_types", "sources", "include_keywords", "exclude_keywords", "target_groups"):
        cfg[key] = as_list(raw.get(key))
    cfg["supply_types"] = [t for t in cfg["supply_types"] if t in SUPPLY_TYPES]
    cfg["target_groups"] = [t for t in cfg["target_groups"] if t in TARGET_GROUPS]
    cfg["area_min"] = as_num(raw.get("area_min"))
    cfg["area_max"] = as_num(raw.get("area_max"))
    for key in ("deposit_max", "rent_max", "price_max", "households_min"):
        cfg[key] = as_num(raw.get(key), int)
    cfg["unknown_policy"] = "exclude" if raw.get("unknown_policy") == "exclude" else "pass"
    for key in ("keywords_in_body", "exclude_closed", "recruit_only", "private_rent_official_only", "remind_d1"):
        cfg[key] = as_bool(raw.get(key), DEFAULT_CONFIG[key])
    return cfg


def _contains_any(text: str, words: list[str]) -> str | None:
    for w in words:
        if w and w in text:
            return w
    return None


def match(notice: dict, cfg: dict, today: date) -> tuple[bool, list[str]]:
    """notice: DB row dict. 모든 조건을 평가하고 탈락 사유를 모은다."""
    fails: list[str] = []
    unknown_excludes = cfg.get("unknown_policy") == "exclude"
    title = notice.get("title") or ""
    head_text = " ".join(filter(None, [title, notice.get("complex_name"), notice.get("region_raw"), notice.get("sigungu")]))
    search_text = head_text + ("\n" + (notice.get("body_text") or "") if cfg.get("keywords_in_body") else "")
    supply_type = notice.get("supply_type") or "기타"

    # 출처
    if cfg["sources"] and notice.get("source") not in cfg["sources"]:
        fails.append("출처")

    # 지역
    sidos = [s for s in (notice.get("sido") or "").split(",") if s]
    if cfg["sidos"]:
        if not sidos:
            if unknown_excludes:
                fails.append("지역(미상)")
        elif NATIONWIDE not in sidos and not set(sidos) & set(cfg["sidos"]):
            fails.append("지역")
    if cfg["sigungus"]:
        sgg_text = " ".join(filter(None, [notice.get("sigungu"), title, notice.get("complex_text")]))
        if not _contains_any(sgg_text, cfg["sigungus"]):
            if notice.get("sigungu") or unknown_excludes:
                fails.append("시군구")

    # 유형
    if cfg["supply_types"] and supply_type not in cfg["supply_types"]:
        fails.append(f"유형({supply_type})")
    if cfg["private_rent_official_only"]:
        if supply_type == "일반 민간임대":
            fails.append("일반 민간임대(정식 공공지원만 허용)")
        elif supply_type == "공공지원 민간임대" and not (
            notice.get("source") in OFFICIAL_PRIVATE_RENT_SOURCES or notice.get("org") in OFFICIAL_ORGS
        ):
            fails.append("공공지원 민간임대(청약홈 정식 공고 아님)")

    # 공고 성격·마감
    if cfg["recruit_only"] and notice.get("notice_kind") != "모집":
        fails.append("모집공고 아님")
    if cfg["exclude_closed"]:
        end = notice.get("apply_end")
        status = notice.get("status") or ""
        if (end and end < today.isoformat()) or "마감" in status:
            fails.append("접수 마감")

    # 숫자 조건
    def numeric(label: str, ok: bool | None) -> None:
        if ok is None:
            if unknown_excludes:
                fails.append(f"{label}(미상)")
        elif not ok:
            fails.append(label)

    amin, amax = notice.get("area_min"), notice.get("area_max")
    if cfg["area_min"] is not None or cfg["area_max"] is not None:
        if amin is None and amax is None:
            numeric("전용면적", None)
        else:
            lo = amin if amin is not None else amax
            hi = amax if amax is not None else amin
            ok = (cfg["area_min"] is None or hi >= cfg["area_min"]) and (cfg["area_max"] is None or lo <= cfg["area_max"])
            numeric("전용면적", ok)
    is_sale = supply_type in SALE_TYPES
    if cfg["deposit_max"] is not None and not is_sale:
        dep = notice.get("deposit_min")
        numeric("보증금", None if dep is None else dep <= cfg["deposit_max"])
    if cfg["rent_max"] is not None and not is_sale:
        rent = notice.get("rent_min")
        numeric("월 임대료", None if rent is None else rent <= cfg["rent_max"])
    if cfg["price_max"] is not None and is_sale:
        price = notice.get("price_min")
        numeric("분양가", None if price is None else price <= cfg["price_max"])
    if cfg["households_min"] is not None:
        hh = notice.get("households")
        numeric("세대수", None if hh is None else hh >= cfg["households_min"])

    # 키워드
    if cfg["include_keywords"] and not _contains_any(search_text, cfg["include_keywords"]):
        fails.append("포함 키워드 없음")
    hit = _contains_any(search_text, cfg["exclude_keywords"])
    if hit:
        fails.append(f"제외 키워드 '{hit}'")
    if cfg["target_groups"]:
        words = [w for g in cfg["target_groups"] for w in TARGET_GROUPS.get(g, [g])]
        if not _contains_any(search_text, words):
            fails.append("대상 계층")
    return (not fails), fails


def is_notifiable(notice: dict) -> bool:
    """협동조합 의심(오탐 허용 처리 안 된 것)은 어떤 필터로도 알리지 않는다."""
    return not notice.get("cooperative_suspect") or bool(notice.get("coop_override"))


def load_filter(row: dict) -> dict:
    out = dict(row)
    out["config"] = normalize_config(json.loads(row["config"]) if isinstance(row.get("config"), str) else row.get("config"))
    return out


DEFAULT_FILTERS: list[tuple[str, dict[str, Any]]] = [
    ("수도권 공공임대 전체", {
        "sidos": CAPITAL_AREA,
        "supply_types": PUBLIC_RENTAL_TYPES,
    }),
    ("수도권 청년·신혼", {
        "sidos": CAPITAL_AREA,
        "supply_types": ["행복주택", "매입임대", "전세임대"],
        "target_groups": ["청년", "신혼부부"],
    }),
    ("전국 공공분양 + 무순위", {
        "supply_types": ["공공분양", "무순위/잔여세대"],
    }),
    ("서울 장기전세·공공지원 민간임대 (보증금 3억 이하)", {
        "sidos": ["서울"],
        "supply_types": ["장기전세", "공공지원 민간임대"],
        "deposit_max": 300_000_000,
        "unknown_policy": "pass",
    }),
    ("전국 전부 (협동조합 제외만 적용, 관찰용)", {
        "recruit_only": False,
        "exclude_closed": False,
        "private_rent_official_only": False,
    }),
]
