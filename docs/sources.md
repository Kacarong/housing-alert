# 수집 출처 조사 결과와 파싱 규칙

조사일: 2026-10-07, 서버 192.168.10.9에서 직접 요청해 확인. 픽스처는 `tests/fixtures/`(당시 받은 원본 HTML).
공통: 브라우저 User-Agent 사용, 같은 출처 연속 요청 사이 2초 이상, 실패 시 2·4초 재시도 후 출처 단위 지수 백오프(30분→1h→2h… 최대 12h).
목록은 한 번에 1~3페이지만(평소 1페이지, 페이지 전체가 새 공고일 때만 다음 페이지), 상세는 새 공고일 때 1회.

## LH 청약플러스 (`lh`, 키 불필요) — 동작

- 목록: `GET https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancList.do?mi=1026`(임대주택), `mi=1027`(분양주택). 기본 상태 필터는 '공고중'(접수중·정정공고중 포함), 50건/페이지.
  - 2페이지 이상은 1페이지 HTML의 `form[name=pagingForm]` 숨은 값 그대로 `currPage`만 바꿔 POST. (`srchUppAisTpCd` 등이 빠지면 빈 오류 페이지가 옴)
  - 행: `table tbody tr` → td[1] 유형, td[2] `a.wrtancInfoBtn`(data-id1=panId, id2=ccrCnntSysDsCd, id3=uppAisTpCd, id4=aisTpCd, 제목은 span 텍스트에서 `em.day` 제거), td[3] 지역, td[5] 게시일, td[6] 마감일, td[7] 상태.
  - 지역이 '전국'인데 제목에 시도가 있으면(예: `(경기도 김포시)`) 제목 쪽을 쓴다. `전남광주통합특별시`는 광주+전남.
- 상세: `GET .../selectWrtancInfo.do?panId=&ccrCnntSysDsCd=&uppAisTpCd=&aisTpCd=&mi=` (GET으로 열림 → 원문 링크로 사용).
  - 본문 영역: `div.bbs_ViewA` ~ `div.btns.ar` (사이트 메뉴·웹접근성 안내 제외).
  - `ul.bbsV_data li`(공고상태/공고일/마감일), 단지 탭 `h4.tit2` + `ul.list_st1`(소재지·전용면적·총 세대수),
    주택형 표(헤더에 '전용면적')의 금회공급 세대수·임대보증금·월임대료·평균분양가격, `#sta_acpDt`(접수기간) 또는 공급일정 표 '신청일시'.
  - 표에 '공고문 확인'만 있으면 스크립트 `wrtancHsByfList3.set('lsGmy'|'rfe', …)` 값으로 보증금·월세 보완.
  - 한계: 선착순/상시 매각 공고(ccr=02 일부)는 주택형 표가 없어 면적·금액 없음.

## SH 서울주택도시개발공사 (`sh`, 키 불필요) — 동작

- 기본 UA는 307 에러 페이지, 브라우저 UA면 200.
- 게시판: 주택임대 `/main/lay2/program/S1T294C297/www/brd/m_247/list.do?multi_itm_seq=2`, 주택분양 `.../S1T294C296/www/brd/m_244/list.do?multi_itm_seq=1`, `&page=N`, 10건/페이지.
  - 행: `#listTb tbody tr`, 제목 `a[onclick*=getDetailView]`(seq), td[3] 등록일. 상세: `.../view.do?seq=…&multi_itm_seq=…`(GET 가능).
- 상세 본문: `div.detailTable` 텍스트. 보증금·면적·접수기간은 첨부 공고문(PDF/HWP)에만 있어 읽지 않는다.
- 게시판에 당첨자 발표·서류 안내가 섞여 있어 제목으로 '모집'/'안내'를 구분(필터 기본값: 모집공고만).
- 지역은 서울 고정, 시군구는 제목의 `(금천구)` 같은 표기에서 추정.

## GH 경기주택도시공사 — 동작

### 주택청약센터 (`gh`, apply.gh.or.kr)
- 목록: `GET /sb/sr/sr7150/selectPbancRentHouseList.do`(임대주택), `/sb/sr/sr7155/selectPbancRentHouseList.do`(매입임대), `?pageIndex=N`, 10건/페이지.
  - 행: td[1] 유형, `a[data-pbancno]` 제목, td[3] 지역(시군), td[5] 게시일, td[6] 마감일, td[7] 상태.
- 상세: `GET /sb/sr/{board}/selectPbancDetailView.do?pbancNo=N` — `th[scope=row]`/`td` 쌍(공고일·상태·지구명·소재지·전용면적·모집호수), 주택형 표, 공급일정 `온라인접수기간 : a ~ b` 또는 `접수처 운영기간`.
- netfunnel 스크립트가 있지만 서버 요청에는 영향 없음.

### 홈페이지 분양/임대 공고 (`gh_www`, www.gh.or.kr)
- 기본 UA 410, 브라우저 UA 200. `GET /gh/announcement-of-salerental001.do?mode=list&srCategoryId=12&articleLimit=20&article.offset=N` (구분=주택).
- 분양주택(국민/민영), 장기전세, 결과 발표 등이 올라온다. 청약센터와 같은 공고는 알림 단계에서 `기관+정규화 제목`으로 중복 제거.
- 상세: `?mode=view&articleNo=N`, `div.board-view` 텍스트(안내 위주, 상세 수치는 첨부 공고문).

## 공공데이터포털 API (키 필요) — 키 대기

| 수집기 | 엔드포인트 | 비고 |
|---|---|---|
| `applyhome_apt` | `https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancDetail` | 상세(주택형): `getAPTLttotPblancMdl` |
| `applyhome_pvt_rent` | `.../getPblPvtRentLttotPblancDetail` | 공공지원 민간임대 (`Mdl`의 공급금액은 보증금으로 저장) |
| `applyhome_urbty` | `.../getUrbtyOfctlLttotPblancDetail` | 오피스텔/도시형/민간임대(SEARCH_HOUSE_SECD 0203 → 일반 민간임대) |
| `applyhome_remndr` | `.../getRemndrLttotPblancDetail` | 무순위/잔여세대 |
| `applyhome_opt` | `.../getOPTLttotPblancDetail` | 임의공급 |
| `lh_api` | `https://apis.data.go.kr/B552555/lhLeaseNoticeInfo1/lhLeaseNoticeInfo1` | 결과는 LH 스크래핑과 같은 `lh`/PAN_ID로 저장(중복 없음) |

- 청약홈(OAS: `https://infuser.odcloud.kr/api/stages/37000/api-docs`): `page`, `perPage`, `serviceKey`(쿼리) 또는 `Authorization` 헤더, 날짜 필터 `cond[RCRIT_PBLANC_DE::GTE]=YYYY-MM-DD`(최근 45일). 응답 `{page, perPage, totalCount, currentCount, matchCount, data:[…]}`. 주요 필드: HOUSE_MANAGE_NO, PBLANC_NO, HOUSE_NM, SUBSCRPT_AREA_CODE_NM, HSSPLY_ADRES, TOT_SUPLY_HSHLDCO, RCRIT_PBLANC_DE, RCEPT_BGNDE/ENDDE(또는 SUBSCRPT_RCEPT_*), PBLANC_URL. 금액 단위 만원.
- LH API(data.go.kr 15058530): `ServiceKey`, `PG_SZ`, `PAGE`(필수), `PAN_NT_ST_DT`, `CLSG_DT`(YYYY.MM.DD, 필수), 선택 `PAN_NM`, `UPP_AIS_TP_CD`, `CNP_CD`, `PAN_SS`. 출력: PAN_ID, PAN_NM, UPP_AIS_TP_NM, AIS_TP_CD_NM, CNP_CD_NM, PAN_SS, DTL_URL 등. 응답 중첩 구조가 문서에 명확하지 않아 `PAN_ID`/`PAN_NM`을 가진 객체를 재귀로 찾는다.
- 포털 '인코딩 키'(%2B 포함)를 넣어도 디코딩 후 사용하므로 이중 인코딩되지 않는다.
- **주의**: 키 없이 실제 응답을 받아볼 수 없어 응답 파싱은 문서 기준이다. 키를 넣은 뒤 첫 수집 결과(대시보드 출처 상태)를 꼭 확인할 것.

## 파싱 결과 샘플 (2026-10-07 실제 수집, 상세까지 받은 모집공고)

#### lh

| 공고명 | 유형 | 지역 | 공고일 | 접수 | 세대 | 면적(㎡) | 보증금/월세/분양가 |
|---|---|---|---|---|---|---|---|
| 창녕영산 1BL 행복주택 예비입주자 모집 공고 ('26.10.07. 공고) | 행복주택 | 경남 창녕군 | 2026-10-07 | 2026-10-19~2026-10-21 | 100 | 25.94~44.98 | - |
| 대전광역시 영구임대주택 예비입주자 모집(2026.10.06) | 영구임대 | 대전 서구 | 2026-10-06 | 2026-10-20~2026-10-22 | 1026 | 19.91~40.32 | 4250000 / 58750 |
| 강릉입암3 영구임대주택 예비입주자 모집 공고 | 영구임대 | 강원 강릉시 | 2026-10-06 | 2026-10-19~2026-10-23 | 100 | 26.37~31.32 | 8170000 / 132760 |

#### sh

| 공고명 | 유형 | 지역 | 공고일 | 접수 | 세대 | 면적(㎡) | 보증금/월세/분양가 |
|---|---|---|---|---|---|---|---|
| [토지임대부 사회주택] 옥류서원 입주자 모집 공고문 | 기타 | 서울  | 2026-10-07 | ?~? | - | - | - |
| [청년형] 특화형 매입임대주택(금천구) 입주자 모집 공고(운영기관 : 한지붕 협동조합) | 매입임대 | 서울 금천구 | 2026-10-02 | ?~? | - | - | - |
| [토지지원 사회주택]에어스페이스 신림3호점_어울리 입주자 모집 공고 | 기타 | 서울  | 2026-10-02 | ?~? | - | - | - |

#### gh

| 공고명 | 유형 | 지역 | 공고일 | 접수 | 세대 | 면적(㎡) | 보증금/월세/분양가 |
|---|---|---|---|---|---|---|---|
| 연천BIX 경기행복주택 기업체 기숙사 추가모집 공고 | 행복주택 | 경기 연천군 | 2026-08-24 | 2026-09-01~? | 36 | 25.22~36.59 | - |
| 경기리츠1호 다산진건데시앙 장기전세주택 예비입주자 모집공고 | 장기전세 | 경기 남양주시 | 2026-08-13 | 2026-08-24~2026-08-24 | 97 | 72.9858~84.9876 | - |
| 다산센트럴파크6단지 국민임대주택 예비입주자 모집공고 | 국민임대 | 경기 남양주시 | 2026-07-10 | 2026-07-20~2026-07-20 | 280 | 33.71~46.5 | - |

#### gh_www

| 공고명 | 유형 | 지역 | 공고일 | 접수 | 세대 | 면적(㎡) | 보증금/월세/분양가 |
|---|---|---|---|---|---|---|---|
| 연천BIX 경기행복주택 기업체 기숙사 추가모집 공고 | 행복주택 | 경기  | 2026-08-24 | ?~? | - | - | - |
| 안양 냉천지구 공공임대주택(5년) 및 행복주택 지구 내 주민 우선공급 입주자 모집공고 | 행복주택 | 경기  | 2026-06-26 | ?~? | - | - | - |
| 고덕 자연앤하우스디(A4BL) 분양주택(민영주택) 임의공급 입주자 모집공고 | 무순위/잔여세대 | 경기  | 2026-06-25 | ?~? | - | - | - |

(보증금/월세/분양가 단위: 원. SH·GH 게시판은 수치를 첨부 공고문에만 싣기 때문에 비어 있음.)

## 협동조합 판정 관찰 (실데이터)

- SH/GH의 "특화형 매입임대주택(운영기관 : 한지붕 협동조합)", SH "협동조합주택", 일부 "사회주택"(본문에 운영 협동조합 명시)이 키워드에 걸려 '제외됨'으로 표시된다.
  이들은 공공 매입임대·사회주택이지만 입주자 모집·계약 주체가 협동조합이다. 사유(제목/본문 구분)를 남기고 공고 상세에서 '오탐 — 알림 허용'으로 개별 해제할 수 있다.
