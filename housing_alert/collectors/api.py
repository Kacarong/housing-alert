"""공공데이터포털 API 수집기 (인증키 필요).

- 한국부동산원_청약홈 분양정보 조회 서비스 (api.odcloud.kr, OAS: infuser.odcloud.kr/api/stages/37000/api-docs)
  page/perPage/serviceKey, cond[RCRIT_PBLANC_DE::GTE]=YYYY-MM-DD 로 최근 공고만 조회.
  응답: {"page","perPage","totalCount","currentCount","matchCount","data":[...]}
- 한국토지주택공사_분양임대공고문 조회 서비스 (apis.data.go.kr/B552555/lhLeaseNoticeInfo1/lhLeaseNoticeInfo1)
  ServiceKey, PG_SZ, PAGE, PAN_NT_ST_DT, CLSG_DT (YYYY.MM.DD). LH 청약플러스 스크래핑과 같은 공고를
  같은 source('lh')/source_id(PAN_ID)로 저장하므로 중복되지 않는다.

키 없이 실제 응답을 받아볼 수 없어서 응답 파싱은 문서(OAS) 기준이다. 키를 넣은 뒤 첫 수집에서 확인 필요.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Iterable
from urllib.parse import unquote

from ..classify import parse_date, parse_int, parse_sidos, parse_sigungu
from ..config import KST
from .base import Collector, NoticeData

ODCLOUD_BASE = "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1"
LH_API_URL = "https://apis.data.go.kr/B552555/lhLeaseNoticeInfo1/lhLeaseNoticeInfo1"
LOOKBACK_DAYS = 45
PER_PAGE = 100


class KeyMissing(Exception):
    pass


def normalize_service_key(key: str) -> str:
    """포털의 'Encoding' 키(%2B 등 포함)를 넣어도 이중 인코딩되지 않도록 디코딩해서 쓴다."""
    key = (key or "").strip()
    return unquote(key) if "%" in key else key


class ApplyhomeCollector(Collector):
    requires_key = True
    org = "청약홈"

    def __init__(self, name: str, label: str, operation: str, model_operation: str, kind: str) -> None:
        self.name = name
        self.label = label
        self.operation = operation
        self.model_operation = model_operation
        self.kind = kind  # apt | pvt_rent | urbty | remndr | opt
        self.key_getter = lambda: ""

    def _key(self) -> str:
        key = normalize_service_key(self.key_getter())
        if not key:
            raise KeyMissing()
        return key

    def _call(self, client, operation: str, params: dict[str, Any]) -> dict:
        resp = client.get(f"{ODCLOUD_BASE}/{operation}", params={**params, "serviceKey": self._key(), "returnType": "JSON"})
        if resp.status_code in (401, 403):
            raise ValueError(f"인증키 거부(HTTP {resp.status_code}) - 키 또는 활용신청 승인 상태 확인 필요")
        if resp.status_code != 200:
            raise ValueError(f"HTTP {resp.status_code}")
        data = resp.json()
        if not isinstance(data, dict) or "data" not in data:
            raise ValueError(f"예상치 못한 응답 형식: {str(data)[:200]}")
        return data

    def supply_type_for(self, item: dict) -> str:
        if self.kind == "pvt_rent":
            return "공공지원 민간임대"
        if self.kind in ("remndr", "opt"):
            return "무순위/잔여세대"
        if self.kind == "urbty":
            secd = str(item.get("SEARCH_HOUSE_SECD") or item.get("HOUSE_DTL_SECD_NM") or "")
            if secd == "0203" or "민간임대" in secd or "민간임대" in str(item.get("HOUSE_DTL_SECD_NM") or ""):
                return "일반 민간임대"
            return "민간분양"
        dtl = str(item.get("HOUSE_DTL_SECD_NM") or "")
        return "공공분양" if "국민" in dtl else "민간분양"

    def to_notice(self, item: dict) -> NoticeData:
        house_no = str(item.get("HOUSE_MANAGE_NO") or "")
        pblanc_no = str(item.get("PBLANC_NO") or "")
        address = item.get("HSSPLY_ADRES") or ""
        area_name = item.get("SUBSCRPT_AREA_CODE_NM") or ""
        sidos = parse_sidos(area_name) or parse_sidos(address.split(" ")[0] if address else "")
        start = parse_date(item.get("RCEPT_BGNDE") or item.get("SUBSCRPT_RCEPT_BGNDE")
                           or item.get("SPSPLY_RCEPT_BGNDE") or item.get("GNRL_RCEPT_BGNDE"))
        end = parse_date(item.get("RCEPT_ENDDE") or item.get("SUBSCRPT_RCEPT_ENDDE")
                         or item.get("GNRL_RCEPT_ENDDE") or item.get("SPSPLY_RCEPT_ENDDE"))
        title = str(item.get("HOUSE_NM") or "").strip()
        label = str(item.get("HOUSE_DTL_SECD_NM") or item.get("HOUSE_SECD_NM") or "")
        body = " ".join(str(item.get(k) or "") for k in ("BSNS_MBY_NM", "CNSTRCT_ENTRPS_NM", "HSSPLY_ADRES", "HMPG_ADRES"))
        return NoticeData(
            source=self.name,
            source_id=f"{house_no}-{pblanc_no}",
            org="청약홈",
            title=f"{title} 입주자모집공고" if title and "공고" not in title else title,
            url=item.get("PBLANC_URL") or None,
            category_raw=f"{self.label} {label}".strip(),
            supply_type=self.supply_type_for(item),
            region_raw=area_name or address,
            sidos=sidos,
            sigungu=parse_sigungu(address),
            complex_name=title,
            households=parse_int(item.get("TOT_SUPLY_HSHLDCO")),
            posted_date=parse_date(item.get("RCRIT_PBLANC_DE")),
            apply_start=start,
            apply_end=end,
            body_text=body.strip(),
            complex_text=f"{title}\n{address}\n사업주체: {item.get('BSNS_MBY_NM') or ''}",
            raw={k: item.get(k) for k in ("HOUSE_MANAGE_NO", "PBLANC_NO", "HOUSE_SECD_NM", "HOUSE_DTL_SECD_NM",
                                          "BSNS_MBY_NM", "MDHS_TELNO", "PRZWNER_PRESNATN_DE", "HMPG_ADRES")},
        )

    def list_notices(self, client, is_known, baseline):
        since = (datetime.now(KST) - timedelta(days=LOOKBACK_DAYS)).date().isoformat()
        items: list[NoticeData] = []
        for page in range(1, self.max_pages + 1):
            data = self._call(client, self.operation, {
                "page": page, "perPage": PER_PAGE, "cond[RCRIT_PBLANC_DE::GTE]": since,
            })
            rows = data.get("data") or []
            items.extend(self.to_notice(r) for r in rows if r.get("HOUSE_MANAGE_NO"))
            total = int(data.get("matchCount") or data.get("totalCount") or 0)
            if page * PER_PAGE >= total or not rows:
                break
        return items

    def fetch_detail(self, client, notice):
        raw = notice.get("raw") or {}
        data = self._call(client, self.model_operation, {
            "page": 1, "perPage": 100,
            "cond[HOUSE_MANAGE_NO::EQ]": raw.get("HOUSE_MANAGE_NO"),
            "cond[PBLANC_NO::EQ]": raw.get("PBLANC_NO"),
        })
        areas: list[float] = []
        prices: list[int] = []
        for m in data.get("data") or []:
            area = m.get("EXCLUSE_AR")
            if area in (None, "") and m.get("HOUSE_TY"):
                hm = re.match(r"0*(\d+(?:\.\d+)?)", str(m["HOUSE_TY"]))
                area = hm.group(1) if hm else None
            try:
                if area not in (None, ""):
                    areas.append(float(area))
            except (TypeError, ValueError):
                pass
            amount = parse_int(m.get("LTTOT_TOP_AMOUNT") or m.get("SUPLY_AMOUNT"))
            if amount:
                prices.append(amount * 10000)  # 만원 -> 원
        out: dict[str, Any] = {}
        if areas:
            out["area_min"], out["area_max"] = min(areas), max(areas)
        if prices:
            if self.kind == "pvt_rent":
                # 공공지원 민간임대의 공급금액은 임대보증금 성격
                out["deposit_min"], out["deposit_max"] = min(prices), max(prices)
            else:
                out["price_min"], out["price_max"] = min(prices), max(prices)
        return out


APPLYHOME_COLLECTORS = [
    ("applyhome_apt", "청약홈 APT 분양", "getAPTLttotPblancDetail", "getAPTLttotPblancMdl", "apt"),
    ("applyhome_pvt_rent", "청약홈 공공지원 민간임대", "getPblPvtRentLttotPblancDetail", "getPblPvtRentLttotPblancMdl", "pvt_rent"),
    ("applyhome_urbty", "청약홈 오피스텔/도시형/민간임대", "getUrbtyOfctlLttotPblancDetail", "getUrbtyOfctlLttotPblancMdl", "urbty"),
    ("applyhome_remndr", "청약홈 무순위/잔여세대", "getRemndrLttotPblancDetail", "getRemndrLttotPblancMdl", "remndr"),
    ("applyhome_opt", "청약홈 임의공급", "getOPTLttotPblancDetail", "getOPTLttotPblancMdl", "opt"),
]


def _walk_dicts(obj: Any) -> Iterable[dict]:
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_dicts(v)


class LHApiCollector(Collector):
    """LH 분양임대공고문 API. 결과는 LH 스크래핑과 같은 source='lh'로 저장 (상세는 스크래핑 파서 사용)."""

    name = "lh_api"
    label = "LH 분양임대공고 API"
    org = "LH"
    requires_key = True
    has_detail = False
    baseline_source = "lh"  # LH 스크래핑이 이미 기준선을 잡았으면 API 첫 수집도 새 공고로 취급

    def __init__(self) -> None:
        self.key_getter = lambda: ""

    def list_notices(self, client, is_known, baseline):
        key = normalize_service_key(self.key_getter())
        if not key:
            raise KeyMissing()
        today = datetime.now(KST).date()
        resp = client.get(LH_API_URL, params={
            "serviceKey": key, "PG_SZ": PER_PAGE, "PAGE": 1,
            "PAN_NT_ST_DT": (today - timedelta(days=LOOKBACK_DAYS)).strftime("%Y.%m.%d"),
            "CLSG_DT": (today + timedelta(days=400)).strftime("%Y.%m.%d"),
        })
        if resp.status_code != 200:
            raise ValueError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except ValueError:
            raise ValueError(f"JSON 아님(키/파라미터 오류 가능): {resp.text[:200]}")
        items: list[NoticeData] = []
        from .lh import detail_url  # 순환 import 방지
        for d in _walk_dicts(data):
            if not d.get("PAN_ID") or not d.get("PAN_NM"):
                continue
            pan_id = str(d["PAN_ID"])
            ccr = str(d.get("CCR_CNNT_SYS_DS_CD") or "")
            upp = str(d.get("UPP_AIS_TP_CD") or "")
            ais = str(d.get("AIS_TP_CD") or "")
            mi = "1027" if upp == "05" else "1026"
            url = detail_url(pan_id, ccr, upp, ais, mi) if ccr else d.get("DTL_URL")
            region = str(d.get("CNP_CD_NM") or "")
            items.append(NoticeData(
                source="lh", source_id=pan_id, org="LH", title=str(d["PAN_NM"]).strip(), url=url,
                category_raw=str(d.get("AIS_TP_CD_NM") or d.get("UPP_AIS_TP_NM") or ""),
                region_raw=region, sidos=parse_sidos(region) or parse_sidos(str(d["PAN_NM"])),
                posted_date=parse_date(d.get("PAN_NT_ST_DT") or d.get("PAN_DT")),
                apply_end=parse_date(d.get("CLSG_DT")), status=d.get("PAN_SS"),
                raw={"panId": pan_id, "ccrCnntSysDsCd": ccr, "uppAisTpCd": upp, "aisTpCd": ais, "mi": mi, "via": "api"},
            ))
        return items
