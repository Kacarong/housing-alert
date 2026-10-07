# housing-alert — 공공·민간 임대/분양 공고 알림

LH·SH·GH(키 불필요 스크래핑)와 청약홈·LH 공공데이터 API(키 필요)에서 임대·분양 공고를 모아,
웹에서 저장한 조건(필터)에 맞는 **새 공고**를 디스코드로 알린다. **협동조합형 민간임대(조합원 모집) 의심 공고는 걸러낸다.**

- 상시 프로세스는 가벼운 웹 서버 하나뿐이고, 수집은 systemd 타이머가 깨우는 oneshot 프로세스(헤드리스 브라우저 없음).
- Python 3 · FastAPI · uvicorn · SQLite(단일 파일) · httpx · selectolax · Jinja2.

## 구조

```
housing_alert/
  collectors/   출처별 독립 수집기 (lh, sh, gh, gh_www, applyhome_*, lh_api) — 하나가 깨져도 나머지는 동작
  classify.py   지역·공급유형·모집/안내·정정 판정, 숫자/날짜 파싱
  cooperative.py 협동조합 의심 판정 (제목/본문/단지정보 매칭 구분)
  filters.py    필터 엔진 + 기본 예시 필터 5개
  notifier.py   디스코드 웹훅 발송 (embed, 10건 초과 요약, 429 재시도, 미발송 큐)
  pipeline.py   수집 → 저장/중복제거/정정감지 → 상세 보강 → 매칭 → 발송, 실패 백오프·실패 알림
  web/          서버 렌더링 UI + JSON API
deploy/systemd/ 사용자 유닛 템플릿 (web 서비스, collect oneshot + timer)
docs/sources.md 출처 조사 결과·파싱 규칙·샘플
tests/          단위 테스트 (출처별 실제 HTML 픽스처 포함)
```

## 설치

```bash
git clone https://github.com/Kacarong/housing-alert.git ~/projects/housing-alert
cd ~/projects/housing-alert
scripts/install.sh
```

`install.sh`가 하는 일: `.venv` 생성·의존성 설치 → `.env`(권한 600) 생성(세션 키, **랜덤 초기 비밀번호** `ADMIN_INITIAL_PASSWORD`) →
DB 초기화(예시 필터 5개, 모두 꺼진 상태) → `~/.config/systemd/user/`에 유닛 설치 → `housing-alert-web.service`, `housing-alert-collect.timer` 기동.
포트는 기본 8110, 이미 쓰이면 8111~8130 중 빈 포트를 고른다. `/tmp`는 쓰지 않는다(`TMPDIR=$HOME/.tmp`).

수동 실행:

```bash
.venv/bin/python -m housing_alert init                 # .env/DB 초기화(이미 있으면 유지)
.venv/bin/python -m housing_alert collect --force       # 지금 1회 수집 (주기·백오프 무시)
.venv/bin/python -m housing_alert collect --force --source lh
.venv/bin/python -m housing_alert status               # 출처별 상태
echo '새비밀번호123' | .venv/bin/python -m housing_alert set-password
.venv/bin/python -m pytest -q                           # 테스트 (requirements-dev.txt)
```

## 동작 요약

- **최초 실행**: 출처별로 처음 성공한 수집에서 본 공고는 전부 '기존(기준선)'으로 저장만 하고 알리지 않는다.
- **새 공고**: `(출처, 출처 내 ID)`가 처음이면 새 공고. 상세 페이지를 1회 받아 면적·세대수·보증금·접수기간 등을 채운다.
- **정정 공고**: 제목에 정정/변경공고/수정 등이 있으면 `[정정]`으로 표시. 같은 ID의 제목이 정정 표기로 바뀌면 revision을 올려 다시 알린다.
- **필터**: 켠 시점 이후 처음 본(또는 정정된) 공고만 알린다. 같은 공고는 같은 필터로 두 번 보내지 않는다. 다른 출처에 올라온 같은 공고(같은 기관·제목)는 한 번만.
- **협동조합 차단**: (1) 공식 출처만 수집, (2) 키워드(웹에서 편집) 매칭 시 `제외됨(사유: 제목 키워드 '협동조합')`처럼 표시하고 어떤 필터로도 알리지 않음, (3) 필터별 "민간임대는 정식 공공지원 민간임대만" 옵션(기본 켜짐). 오탐은 공고 상세에서 '오탐 — 알림 허용'.
- **디스코드**: 공고 하나당 embed 하나(매칭된 필터 이름 모두 표시). 한 번에 10건 초과면 요약 메시지. 429는 `retry_after`만큼 대기 후 재시도.
- **웹훅이 없을 때**: 알림은 '미발송'으로 쌓인다. 웹훅을 넣은 뒤 설정 화면에서 밀린 알림을 '모두 보내기' 또는 '모두 버리기'.
- **D-1 리마인드**: 필터별 옵션. 이미 보낸 공고의 접수 마감 전날 08~22시에 한 번.
- **실패 처리**: 출처별 연속 실패 횟수·마지막 성공 시각을 대시보드에 표시, 실패 시 30분부터 지수 백오프(최대 12시간), 3회 연속 실패하면 웹훅으로 한 번 알림.
- **수집 주기**: 타이머는 매시 05·35분에 깨어나고, 실제 간격은 웹 설정(기본 주간 60분, 00~07시 야간 180분).

## 나중에 할 일

### 공공데이터포털 인증키
1. data.go.kr에서 「한국부동산원_청약홈 분양정보 조회 서비스」(15098547), 「한국토지주택공사_분양임대공고문 조회 서비스」(15058530) 활용신청.
2. 웹 **설정 → 공공데이터포털 인증키**에 붙여넣고 저장(Encoding/Decoding 키 모두 가능). 재시작 불필요, 다음 수집부터 동작.
   (또는 `.env`의 `DATA_GO_KR_KEY=` — 이 경우 `systemctl --user restart housing-alert-web` 불필요, 수집 프로세스가 매번 읽음)
3. 대시보드에서 청약홈·LH API 출처 상태가 '정상'인지 확인. API 응답 파싱은 문서 기준이라 첫 결과를 꼭 확인할 것.

### 디스코드 웹훅
디스코드 채널 설정 → 연동 → 웹후크 → 새 웹후크 → URL 복사 → 웹 **설정 → 디스코드 웹훅**에 저장 → **테스트 발송**.
그동안 쌓인 미발송 알림은 같은 화면에서 보내기/버리기 선택. 필터마다 다른 웹훅을 지정할 수도 있다.

### 내부망에서 보기
`.env`에서 `HOST=0.0.0.0`으로 바꾸고 `systemctl --user restart housing-alert-web`.

### 외부 공개 (Cloudflare quick tunnel, 필요할 때만)
이 서버의 다른 앱(`exvideo-tunnel.service`, `geoglobe-tunnel.service`)과 같은 방식:

```ini
# ~/.config/systemd/user/housing-alert-tunnel.service
[Unit]
Description=housing-alert Cloudflare quick tunnel
After=housing-alert-web.service network.target
Wants=housing-alert-web.service

[Service]
Type=simple
ExecStart=/home/claude/bin/cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8110
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload && systemctl --user enable --now housing-alert-tunnel
journalctl --user -u housing-alert-tunnel | grep trycloudflare.com   # 공개 주소 확인 (재시작마다 바뀜)
```
HTTPS로 공개하면 `.env`에 `COOKIE_SECURE=1`을 넣고 웹을 재시작한다. 로그인 실패 5회면 IP별 5분 잠금.

## 통합 웹사이트로 편입할 때

- **하위 경로**: `.env`의 `BASE_PATH=/housing`이면 모든 화면·API·정적 파일이 `/housing/...` 아래로 간다(리버스 프록시에서 그대로 전달).
- **라이브러리로 붙이기**: 다른 FastAPI 앱에서
  ```python
  from housing_alert.config import load_config
  from housing_alert.web.app import build_router, mount_static
  cfg = load_config()            # BASE_PATH=/housing
  app.include_router(build_router(cfg))
  mount_static(app, cfg)
  ```
  (로그인 리다이렉트용 `LoginRequired` 예외 핸들러는 `create_app()` 참고)
- **JSON API** (세션 쿠키 인증): `GET /api/status`, `GET /api/notices?q=&source=&type=&sido=&show=open|ok|coop|notified&limit=&offset=`,
  `GET /api/filters`, `GET /api/filters/{id}/preview`, `POST /api/filters/preview`(폼+csrf), `GET /api/health`(인증 없음).
- 수집·필터·발송 로직(`pipeline`, `filters`, `notifier`, `collectors`)은 웹과 독립이라 통합 사이트의 스케줄러에서 `run_collection()`을 직접 불러도 된다.
- 통합 사이트가 자체 로그인을 쓰게 되면 `current_session` 의존성만 교체하면 된다.

## 남은 한계

- SH·GH 게시판 공고는 보증금·면적·접수기간이 첨부 공고문(PDF/HWP)에만 있어 비어 있다 → 필터의 '값을 모르는 공고' 정책(통과/제외)이 적용된다.
- LH 선착순·상시 매각 공고 일부는 상세에 주택형 표가 없어 수치가 없다.
- 공급유형은 출처 분류 + 제목 키워드로 추정한다(예: SH 사회주택·청년안심주택은 '기타').
- 청약홈·LH API 응답 파싱은 키가 없어 실응답으로 검증하지 못했다(문서 기준 구현).
- 공고명이 같은 서로 다른 공고가 다른 출처에 있으면 중복으로 보고 한 번만 알린다.
