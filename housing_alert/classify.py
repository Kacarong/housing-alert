"""공고 분류·정규화: 지역, 공급 유형, 공고 성격(모집/안내), 정정 여부, 숫자·날짜 파싱."""
from __future__ import annotations

import re
from datetime import date

# ------------------------------------------------------------------ 지역

SIDO_LIST = [
    "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종",
    "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
]
SIDO_ALIASES: list[tuple[str, list[str]]] = [
    # 순서 중요: 긴 이름 먼저
    ("전남광주통합특별시", ["광주", "전남"]),
    ("서울특별시", ["서울"]), ("부산광역시", ["부산"]), ("대구광역시", ["대구"]),
    ("인천광역시", ["인천"]), ("광주광역시", ["광주"]), ("대전광역시", ["대전"]),
    ("울산광역시", ["울산"]), ("세종특별자치시", ["세종"]), ("경기도", ["경기"]),
    ("강원특별자치도", ["강원"]), ("강원도", ["강원"]), ("충청북도", ["충북"]),
    ("충청남도", ["충남"]), ("전북특별자치도", ["전북"]), ("전라북도", ["전북"]),
    ("전라남도", ["전남"]), ("경상북도", ["경북"]), ("경상남도", ["경남"]),
    ("제주특별자치도", ["제주"]),
]
CAPITAL_AREA = ["서울", "경기", "인천"]
NATIONWIDE = "전국"


def parse_sidos(text: str | None) -> list[str]:
    """지역 표기(예: '경상남도', '대구광역시 외', '전남광주통합특별시', '전국')를 시도 약칭 목록으로."""
    if not text:
        return []
    text = text.strip()
    found: list[str] = []
    rest = text
    for full, shorts in SIDO_ALIASES:
        if full in rest:
            for s in shorts:
                if s not in found:
                    found.append(s)
            rest = rest.replace(full, " ")
    # 약칭 단독 표기 (예: '서울', '경기 수원시')
    for token in re.split(r"[\s,·/()]+", rest):
        for s in SIDO_LIST:
            if token == s or (token.startswith(s) and token[len(s):] in ("시", "도", "특별시", "광역시")):
                if s not in found:
                    found.append(s)
    if not found and NATIONWIDE in text:
        return [NATIONWIDE]
    return found


_SIGUNGU_RE = re.compile(r"^[가-힣]{1,6}(?:시|군|구)$")


def parse_sigungu(address: str | None) -> str | None:
    """주소에서 시군구 추출. '대전광역시 서구 구봉산북로 212' -> '서구', '경기도 수원시 영통구 ...' -> '수원시 영통구'."""
    if not address:
        return None
    tokens = re.split(r"\s+", address.strip())
    # 첫 토큰이 시도면 건너뛴다
    start = 0
    if tokens and parse_sidos(tokens[0]):
        start = 1
    out: list[str] = []
    for tok in tokens[start:start + 2]:
        tok = tok.strip(",()")
        if _SIGUNGU_RE.match(tok) and tok not in ("특별시", "광역시"):
            out.append(tok)
            if tok.endswith("구") or tok.endswith("군"):
                break
        else:
            break
    return " ".join(out) or None


_TITLE_SIGUNGU_RE = re.compile(r"[(\[ ]([가-힣]{1,5}(?:시|군|구))[)\]\s,]")


def sigungu_from_title(title: str) -> str | None:
    """'(경기도 김포시)', '(금천구)' 같은 제목 표기에서 시군구 추정."""
    for m in _TITLE_SIGUNGU_RE.finditer(" " + title + " "):
        name = m.group(1)
        if name in ("특별시", "광역시") or name.endswith(("주택구", "지구")) or len(name) < 2:
            continue
        if parse_sidos(name):  # '서울시' 같은 시도 표기
            continue
        return name
    return None


# ------------------------------------------------------------------ 공급 유형

SUPPLY_TYPES = [
    "영구임대", "국민임대", "행복주택", "통합공공임대", "매입임대", "전세임대", "장기전세",
    "공공임대(5·10·50년)", "공공지원 민간임대", "일반 민간임대", "공공분양", "민간분양",
    "무순위/잔여세대", "기타",
]
PUBLIC_RENTAL_TYPES = ["영구임대", "국민임대", "행복주택", "통합공공임대", "매입임대", "전세임대"]

_SALE_CONTEXT = re.compile(r"분양|매각|민영주택|국민주택")
_LEFTOVER = re.compile(r"무순위|잔여\s*세대|임의\s*공급|선착순|미분양|일반\s*매각")


def classify_supply_type(category_raw: str | None, title: str, org: str | None = None) -> str:
    cat = category_raw or ""
    text = f"{cat} {title}"
    if _LEFTOVER.search(text) and _SALE_CONTEXT.search(text) and "임대" not in cat:
        return "무순위/잔여세대"
    if re.search(r"공공지원\s*민간\s*임대", text):
        return "공공지원 민간임대"
    if re.search(r"민간\s*임대", text):
        return "일반 민간임대"
    ordered = [
        ("통합공공임대", r"통합\s*공공\s*임대"),
        ("영구임대", r"영구\s*임대"),
        ("국민임대", r"국민\s*임대"),
        ("행복주택", r"행복\s*주택"),
        ("전세임대", r"전세\s*임대"),
        ("장기전세", r"장기\s*전세|미리내집"),
        ("매입임대", r"매입\s*임대|기존주택\s*매입|신축\s*매입|든든전세"),
        ("공공임대(5·10·50년)", r"(?:5|10|50)\s*년\s*(?:공공)?임대|공임\s*50\s*년|분납\s*임대|분양\s*전환|공공임대\s*\((?:5|10|50)년\)"),
    ]
    # 카테고리 라벨을 먼저 본다 (제목보다 신뢰도 높음)
    for name, pattern in ordered:
        if re.search(pattern, cat):
            return name
    for name, pattern in ordered:
        if re.search(pattern, title):
            return name
    if re.search(r"공공\s*분양|신혼\s*희망|토지임대부\s*분양|지분\s*적립|국민주택", text):
        return "공공분양"
    if re.search(r"민영\s*주택|민간\s*분양", text):
        return "민간분양"
    if "분양" in text:
        return "공공분양" if org in ("LH", "SH", "GH") else "민간분양"
    return "기타"


# ------------------------------------------------------------------ 공고 성격

_RESULT_RE = re.compile(
    r"당첨자|발표|결과|계약\s*안내|서류\s*제출|서류\s*심사|면접|교육\s*이수|재계약|선정\s*결과|"
    r"이용\s*안내|일정\s*안내|변동\s*안내|소유권|이전\s*등기|사용검사|동호수\s*배정|확인증|명단"
)
_RECRUIT_RE = re.compile(
    r"모집|공급\s*공고|입주자|청약|매각|임차인|수의\s*계약|분양\s*공고|잔여\s*세대|추가\s*모집|예비\s*입주자"
)

NOTICE_KINDS = ["모집", "안내"]


def classify_notice_kind(title: str) -> str:
    """'모집' = 입주자 모집·공급 공고, '안내' = 당첨자 발표·서류 안내·결과 등."""
    if _RESULT_RE.search(title):
        return "안내"
    if _RECRUIT_RE.search(title):
        return "모집"
    return "안내"


_CORRECTION_RE = re.compile(
    r"정정|변경\s*공고|수정\s*공고|\(\s*수정\s*\)|\[\s*\d*\s*차?\s*수정\s*\]|\(\s*변경\s*\)|\[\s*변경\s*\]|재공고"
)


def is_correction_title(title: str) -> bool:
    return bool(_CORRECTION_RE.search(title or ""))


# ------------------------------------------------------------------ 대상 계층

TARGET_GROUPS: dict[str, list[str]] = {
    "청년": ["청년", "사회초년생", "청년안심"],
    "신혼부부": ["신혼", "신생아", "예비신혼"],
    "대학생": ["대학생", "기숙사", "희망하우징"],
    "고령자": ["고령자", "어르신", "노인", "만 65세"],
    "장애인": ["장애인"],
    "다자녀": ["다자녀"],
    "한부모": ["한부모"],
    "주거급여·저소득": ["수급자", "저소득", "주거급여", "차상위"],
}


# ------------------------------------------------------------------ 숫자/날짜

def parse_int(text: str | None) -> int | None:
    if text is None:
        return None
    digits = re.sub(r"[^\d]", "", str(text))
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def parse_float_range(text: str | None) -> tuple[float | None, float | None]:
    if not text:
        return None, None
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", str(text).replace(",", ""))]
    if not nums:
        return None, None
    return min(nums), max(nums)


_DATE_RE = re.compile(r"(\d{4}|\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")
_DATE_COMPACT_RE = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")


def parse_date(text: str | None) -> str | None:
    if not text:
        return None
    text = str(text)
    m = _DATE_RE.search(text)
    if m:
        y, mo, d = m.groups()
        year = int(y) + 2000 if len(y) == 2 else int(y)
    else:
        m = _DATE_COMPACT_RE.search(text)
        if not m:
            return None
        year, mo, d = int(m.group(1)), m.group(2), m.group(3)
    try:
        return date(year, int(mo), int(d)).isoformat()
    except ValueError:
        return None


def parse_date_range(text: str | None) -> tuple[str | None, str | None]:
    if not text:
        return None, None
    parts = re.split(r"~|∼|〜", str(text), maxsplit=1)
    start = parse_date(parts[0])
    end = parse_date(parts[1]) if len(parts) > 1 else None
    return start, end


def dup_key(org: str, title: str) -> str:
    norm = re.sub(r"[^0-9A-Za-z가-힣]", "", title or "")
    return f"{org}:{norm}"
