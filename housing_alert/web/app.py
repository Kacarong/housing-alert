"""웹 UI + JSON API.

통합 웹사이트 편입을 고려해:
- 모든 경로는 BASE_PATH(예: /housing) 아래에 붙는다. create_app()으로 독립 실행하거나,
  build_router()를 다른 FastAPI 앱에 include_router 해도 된다(정적 파일은 mount_static() 사용).
- 화면과 같은 데이터를 /api/* JSON으로도 제공한다.
"""
from __future__ import annotations

import json
import logging
import math
import secrets
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import db, notifier, pipeline
from ..auth import csrf_token, hash_password, make_session, password_version, read_session, verify_password
from ..bootstrap import remove_initial_password_line
from ..classify import SIDO_LIST, SUPPLY_TYPES, TARGET_GROUPS
from ..collectors import SOURCE_LABELS, build_collectors
from ..config import Config, load_config
from ..cooperative import DEFAULT_KEYWORDS
from ..filters import DEFAULT_CONFIG, is_notifiable, load_filter, match, normalize_config

log = logging.getLogger(__name__)
HERE = Path(__file__).resolve().parent
COOKIE = "ha_session"
PAGE_SIZE = 50
BOOL_KEYS = ["keywords_in_body", "exclude_closed", "recruit_only", "private_rent_official_only", "remind_d1"]
LIST_KEYS = ["sidos", "supply_types", "sources", "target_groups"]
MANWON_KEYS = ["deposit_max", "rent_max", "price_max"]  # 화면은 만원 단위, 저장은 원 단위


class LoginRequired(Exception):
    pass


class LoginLimiter:
    """IP별 연속 실패 5회면 5분 잠금."""

    def __init__(self) -> None:
        self.fails: dict[str, list[float]] = {}

    def blocked(self, ip: str) -> bool:
        recent = [t for t in self.fails.get(ip, []) if time.time() - t < 300]
        self.fails[ip] = recent
        return len(recent) >= 5

    def fail(self, ip: str) -> None:
        self.fails.setdefault(ip, []).append(time.time())

    def reset(self, ip: str) -> None:
        self.fails.pop(ip, None)


def won_to_manwon(v: Any) -> str:
    if v in (None, ""):
        return ""
    v = int(v)
    return str(v // 10000) if v % 10000 == 0 else f"{v / 10000:g}"


def fmt_won(v: Any) -> str:
    if v in (None, ""):
        return ""
    return notifier._won(int(v)) or ""


def build_router(cfg: Config) -> APIRouter:
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    base = cfg.base_path
    secret = cfg.session_secret or secrets.token_hex(32)
    if not cfg.session_secret:
        log.warning("SESSION_SECRET 미설정: 임시 값 사용(재시작하면 로그아웃됨). `python -m housing_alert init` 권장")
    limiter = LoginLimiter()
    templates.env.globals.update(
        base=base, SOURCE_LABELS=SOURCE_LABELS, SUPPLY_TYPES=SUPPLY_TYPES, SIDO_LIST=SIDO_LIST,
        TARGET_GROUPS=TARGET_GROUPS, won_to_manwon=won_to_manwon, fmt_won=fmt_won,
    )
    templates.env.filters["fromjson"] = lambda s: json.loads(s) if s else {}

    router = APIRouter(prefix=base)

    # ---------------------------------------------------------- 의존성

    def get_conn():
        conn = db.connect(cfg.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def current_session(request: Request, conn=Depends(get_conn)) -> dict:
        pw_hash = db.get_setting(conn, "password_hash")
        session = read_session(secret, request.cookies.get(COOKIE), password_version(pw_hash))
        if session is None:
            if request.url.path.startswith(f"{base}/api/"):
                raise HTTPException(status_code=401, detail="login required")
            raise LoginRequired()
        return session

    async def check_csrf(request: Request, session: dict) -> dict:
        form = await request.form()
        if not secrets.compare_digest(str(form.get("csrf", "")), csrf_token(secret, session)):
            raise HTTPException(status_code=400, detail="CSRF 토큰 불일치. 페이지를 새로고침하세요.")
        return dict(form)

    def render(request: Request, name: str, session: dict | None, **ctx) -> HTMLResponse:
        ctx.update(request=request, msg=request.query_params.get("msg"), err=request.query_params.get("err"),
                   csrf=csrf_token(secret, session) if session else "", logged_in=session is not None)
        return templates.TemplateResponse(request, name, ctx)

    def redirect(path: str, msg: str | None = None, err: str | None = None) -> RedirectResponse:
        params = {k: v for k, v in (("msg", msg), ("err", err)) if v}
        url = f"{base}{path}" + (("&" if "?" in path else "?") + urlencode(params) if params else "")
        return RedirectResponse(url, status_code=303)

    # ---------------------------------------------------------- 공통 데이터

    def source_rows(conn) -> list[dict]:
        api_key = pipeline.get_api_key(conn, cfg)
        out = []
        for c in build_collectors():
            st = db.get_source_status(conn, c.name)
            state = st.get("state") or "아직 실행 안 됨"
            if c.requires_key and not api_key:
                state = "키 미설정"
            out.append({**st, "label": c.label, "requires_key": c.requires_key, "state": state})
        return out

    def notice_query(params: dict) -> tuple[str, list]:
        where, args = ["1=1"], []
        q = (params.get("q") or "").strip()
        if q:
            where.append("(title LIKE ? OR complex_name LIKE ? OR region_raw LIKE ? OR sigungu LIKE ?)")
            args += [f"%{q}%"] * 4
        if params.get("source"):
            where.append("source=?")
            args.append(params["source"])
        if params.get("type"):
            where.append("supply_type=?")
            args.append(params["type"])
        if params.get("sido"):
            where.append("(',' || sido || ',') LIKE ?")
            args.append(f"%,{params['sido']},%")
        show = params.get("show") or ""
        if show == "coop":
            where.append("cooperative_suspect=1")
        elif show == "ok":
            where.append("(cooperative_suspect=0 OR coop_override=1)")
        elif show == "open":
            where.append("(apply_end IS NULL OR apply_end>=?) AND (cooperative_suspect=0 OR coop_override=1)")
            args.append(date.today().isoformat())
        elif show == "notified":
            where.append("id IN (SELECT notice_id FROM notifications WHERE status='sent')")
        return " AND ".join(where), args

    def preview_filter(conn, config: dict, limit: int = 30) -> dict:
        today = pipeline.now_kst().date()
        rows = conn.execute("SELECT * FROM notices ORDER BY COALESCE(posted_date, '') DESC, id DESC").fetchall()
        matched, coop_blocked = [], 0
        for r in rows:
            n = dict(r)
            ok, _ = match(n, config, today)
            if not ok:
                continue
            if not is_notifiable(n):
                coop_blocked += 1
                continue
            matched.append(n)
        return {
            "total_notices": len(rows),
            "count": len(matched),
            "coop_blocked": coop_blocked,
            "samples": [{"id": n["id"], "title": n["title"], "org": n["org"], "supply_type": n["supply_type"],
                         "sido": n["sido"], "apply_end": n["apply_end"], "url": n["url"]} for n in matched[:limit]],
        }

    def config_from_form(form: dict, multi: dict[str, list[str]]) -> dict:
        raw: dict[str, Any] = {k: v for k, v in form.items() if k not in LIST_KEYS}
        for k in LIST_KEYS:
            raw[k] = multi.get(k, [])
        for k in BOOL_KEYS:
            raw[k] = k in form
        for k in MANWON_KEYS:
            v = str(form.get(k) or "").replace(",", "").strip()
            raw[k] = int(float(v) * 10000) if v else None
        return normalize_config(raw)

    # ---------------------------------------------------------- 로그인

    @router.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return render(request, "login.html", None)

    @router.post("/login")
    async def login(request: Request, conn=Depends(get_conn)):
        ip = request.client.host if request.client else "?"
        if limiter.blocked(ip):
            return redirect("/login", err="로그인 실패가 많아 5분간 잠겼습니다.")
        form = await request.form()
        pw_hash = db.get_setting(conn, "password_hash")
        if not verify_password(str(form.get("password", "")), pw_hash):
            limiter.fail(ip)
            return redirect("/login", err="비밀번호가 틀렸습니다.")
        limiter.reset(ip)
        resp = redirect("/")
        resp.set_cookie(COOKIE, make_session(secret, password_version(pw_hash)), httponly=True, samesite="lax",
                        secure=cfg.cookie_secure, max_age=14 * 24 * 3600, path=base or "/")
        return resp

    @router.post("/logout")
    def logout():
        resp = redirect("/login", msg="로그아웃했습니다.")
        resp.delete_cookie(COOKIE, path=base or "/")
        return resp

    # ---------------------------------------------------------- 대시보드

    @router.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, session=Depends(current_session), conn=Depends(get_conn)):
        recent = db.rows_to_dicts(conn.execute(
            """SELECT nt.*, n.title, n.url, n.org, n.is_correction, f.name AS filter_name FROM notifications nt
               JOIN notices n ON n.id=nt.notice_id JOIN filters f ON f.id=nt.filter_id
               ORDER BY nt.id DESC LIMIT 20"""))
        last_run = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        counts = {
            "notices": conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0],
            "coop": conn.execute("SELECT COUNT(*) FROM notices WHERE cooperative_suspect=1").fetchone()[0],
            "open": conn.execute("SELECT COUNT(*) FROM notices WHERE apply_end>=? AND cooperative_suspect=0",
                                 (date.today().isoformat(),)).fetchone()[0],
            "pending": conn.execute("SELECT COUNT(*) FROM notifications WHERE status='pending'").fetchone()[0],
            "filters_on": conn.execute("SELECT COUNT(*) FROM filters WHERE enabled=1").fetchone()[0],
        }
        return render(request, "dashboard.html", session, sources=source_rows(conn), recent=recent,
                      last_run=dict(last_run) if last_run else None, counts=counts,
                      webhook=bool(notifier.get_webhook(conn, cfg)), api_key=bool(pipeline.get_api_key(conn, cfg)),
                      running=pipeline.is_running(cfg), backlog=notifier.backlog_count(conn),
                      last_collect=db.get_setting(conn, "last_collect_at"))

    @router.post("/collect")
    async def collect_now(request: Request, session=Depends(current_session)):
        await check_csrf(request, session)
        if pipeline.is_running(cfg):
            return redirect("/", err="이미 수집이 진행 중입니다.")
        log_path = cfg.data_dir / "manual-collect.log"
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w") as fh:
            subprocess.Popen(
                [sys.executable, "-m", "housing_alert", "collect", "--force", "--trigger", "manual"],
                cwd=str(cfg.root), stdout=fh, stderr=subprocess.STDOUT, start_new_session=True,
            )
        return redirect("/", msg="수집을 시작했습니다. 출처당 요청 간격 2초라 몇 분 걸릴 수 있습니다. 잠시 후 새로고침하세요.")

    # ---------------------------------------------------------- 공고

    @router.get("/notices", response_class=HTMLResponse)
    def notices_page(request: Request, session=Depends(current_session), conn=Depends(get_conn)):
        params = dict(request.query_params)
        where, args = notice_query(params)
        total = conn.execute(f"SELECT COUNT(*) FROM notices WHERE {where}", args).fetchone()[0]
        page = max(1, int(params.get("page") or 1))
        rows = db.rows_to_dicts(conn.execute(
            f"SELECT * FROM notices WHERE {where} ORDER BY COALESCE(posted_date,'') DESC, id DESC LIMIT ? OFFSET ?",
            (*args, PAGE_SIZE, (page - 1) * PAGE_SIZE)))
        qs = {k: v for k, v in params.items() if k != "page" and v}
        return render(request, "notices.html", session, notices=rows, total=total, page=page,
                      pages=max(1, math.ceil(total / PAGE_SIZE)), params=params, qs=urlencode(qs),
                      today=date.today().isoformat())

    @router.get("/notices/{notice_id}", response_class=HTMLResponse)
    def notice_detail(notice_id: int, request: Request, session=Depends(current_session), conn=Depends(get_conn)):
        row = conn.execute("SELECT * FROM notices WHERE id=?", (notice_id,)).fetchone()
        if row is None:
            raise HTTPException(404)
        n = dict(row)
        today = pipeline.now_kst().date()
        filter_results = []
        for f in conn.execute("SELECT * FROM filters ORDER BY id").fetchall():
            lf = load_filter(dict(f))
            ok, fails = match(n, lf["config"], today)
            filter_results.append({"name": lf["name"], "enabled": lf["enabled"], "ok": ok, "fails": fails})
        notes = db.rows_to_dicts(conn.execute(
            """SELECT nt.*, f.name AS filter_name FROM notifications nt JOIN filters f ON f.id=nt.filter_id
               WHERE nt.notice_id=? ORDER BY nt.id""", (notice_id,)))
        return render(request, "notice_detail.html", session, n=n, filter_results=filter_results, notes=notes)

    @router.post("/notices/{notice_id}/coop-override")
    async def coop_override(notice_id: int, request: Request, session=Depends(current_session), conn=Depends(get_conn)):
        form = await check_csrf(request, session)
        value = 1 if form.get("value") == "1" else 0
        conn.execute("UPDATE notices SET coop_override=? WHERE id=?", (value, notice_id))
        back = form.get("back") or f"/notices/{notice_id}"
        return redirect(back if back.startswith("/") else "/", msg="오탐(알림 허용)으로 표시했습니다." if value else "다시 제외 처리했습니다.")

    # ---------------------------------------------------------- 필터

    @router.get("/filters", response_class=HTMLResponse)
    def filters_page(request: Request, session=Depends(current_session), conn=Depends(get_conn)):
        rows = [load_filter(dict(r)) for r in conn.execute("SELECT * FROM filters ORDER BY id").fetchall()]
        for f in rows:
            f["preview_count"] = preview_filter(conn, f["config"], limit=0)["count"]
            f["sent"] = conn.execute("SELECT COUNT(*) FROM notifications WHERE filter_id=? AND status='sent'",
                                     (f["id"],)).fetchone()[0]
        return render(request, "filters.html", session, filters=rows)

    def filter_form(request, session, f: dict | None, conn):
        sources = [(c.name, c.label) for c in build_collectors() if c.name != "lh_api"]
        return render(request, "filter_form.html", session, f=f or {"id": None, "name": "", "enabled": 0,
                      "config": dict(DEFAULT_CONFIG), "webhook_url": ""}, sources=sources)

    @router.get("/filters/new", response_class=HTMLResponse)
    def filter_new(request: Request, session=Depends(current_session), conn=Depends(get_conn)):
        return filter_form(request, session, None, conn)

    @router.get("/filters/{filter_id}", response_class=HTMLResponse)
    def filter_edit(filter_id: int, request: Request, session=Depends(current_session), conn=Depends(get_conn)):
        row = conn.execute("SELECT * FROM filters WHERE id=?", (filter_id,)).fetchone()
        if row is None:
            raise HTTPException(404)
        return filter_form(request, session, load_filter(dict(row)), conn)

    @router.post("/filters/save")
    async def filter_save(request: Request, session=Depends(current_session), conn=Depends(get_conn)):
        form_data = await request.form()
        form = await check_csrf(request, session)
        multi = {k: form_data.getlist(k) for k in LIST_KEYS}
        config = config_from_form(form, multi)
        name = str(form.get("name") or "").strip() or "이름 없는 필터"
        webhook = str(form.get("webhook_url") or "").strip() or None
        if webhook and not notifier.valid_webhook(webhook):
            return redirect(f"/filters/{form.get('id') or 'new'}", err="필터 전용 웹훅 URL 형식이 올바르지 않습니다.")
        enabled = 1 if "enabled" in form else 0
        now = db.now_iso()
        fid = form.get("id")
        if fid:
            old = conn.execute("SELECT * FROM filters WHERE id=?", (int(fid),)).fetchone()
            if old is None:
                raise HTTPException(404)
            enabled_at = old["enabled_at"] if old["enabled"] and enabled else (now if enabled else None)
            keep_hook = form.get("keep_webhook") == "1" and not webhook
            conn.execute(
                "UPDATE filters SET name=?, enabled=?, config=?, webhook_url=?, enabled_at=?, updated_at=? WHERE id=?",
                (name, enabled, json.dumps(config, ensure_ascii=False), old["webhook_url"] if keep_hook else webhook,
                 enabled_at, now, int(fid)))
        else:
            conn.execute(
                "INSERT INTO filters(name, enabled, config, webhook_url, enabled_at, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                (name, enabled, json.dumps(config, ensure_ascii=False), webhook, now if enabled else None, now, now))
        return redirect("/filters", msg=f"'{name}' 필터를 저장했습니다.")

    @router.post("/filters/{filter_id}/toggle")
    async def filter_toggle(filter_id: int, request: Request, session=Depends(current_session), conn=Depends(get_conn)):
        await check_csrf(request, session)
        row = conn.execute("SELECT * FROM filters WHERE id=?", (filter_id,)).fetchone()
        if row is None:
            raise HTTPException(404)
        if row["enabled"]:
            conn.execute("UPDATE filters SET enabled=0, enabled_at=NULL, updated_at=? WHERE id=?", (db.now_iso(), filter_id))
            return redirect("/filters", msg=f"'{row['name']}' 필터를 껐습니다.")
        conn.execute("UPDATE filters SET enabled=1, enabled_at=?, updated_at=? WHERE id=?", (db.now_iso(), db.now_iso(), filter_id))
        return redirect("/filters", msg=f"'{row['name']}' 필터를 켰습니다. 지금부터 새로 들어오는 공고를 알립니다.")

    @router.post("/filters/{filter_id}/duplicate")
    async def filter_duplicate(filter_id: int, request: Request, session=Depends(current_session), conn=Depends(get_conn)):
        await check_csrf(request, session)
        row = conn.execute("SELECT * FROM filters WHERE id=?", (filter_id,)).fetchone()
        if row is None:
            raise HTTPException(404)
        now = db.now_iso()
        new_id = conn.execute(
            "INSERT INTO filters(name, enabled, config, webhook_url, created_at, updated_at) VALUES(?,?,?,?,?,?)",
            (row["name"] + " (복사)", 0, row["config"], row["webhook_url"], now, now)).lastrowid
        return redirect(f"/filters/{new_id}", msg="복제했습니다(꺼진 상태). 고친 뒤 저장하세요.")

    @router.post("/filters/{filter_id}/delete")
    async def filter_delete(filter_id: int, request: Request, session=Depends(current_session), conn=Depends(get_conn)):
        await check_csrf(request, session)
        conn.execute("DELETE FROM filters WHERE id=?", (filter_id,))
        return redirect("/filters", msg="필터를 삭제했습니다.")

    # ---------------------------------------------------------- 설정

    @router.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, session=Depends(current_session), conn=Depends(get_conn)):
        db_hook = db.get_setting(conn, "webhook_url")
        db_key = db.get_setting(conn, "data_go_kr_key")
        return render(
            request, "settings.html", session,
            webhook_masked=notifier.mask_webhook(db_hook or cfg.discord_webhook_url),
            webhook_from_env=bool(not db_hook and cfg.discord_webhook_url),
            key_masked=notifier.mask_secret(db_key or cfg.data_go_kr_key),
            key_from_env=bool(not db_key and cfg.data_go_kr_key),
            backlog=notifier.backlog_count(conn),
            schedule=pipeline.get_schedule(conn),
            keywords="\n".join(pipeline.get_coop_keywords(conn)),
            summary_threshold=db.get_setting(conn, "summary_threshold", notifier.DEFAULT_SUMMARY_THRESHOLD),
            initial_pw_in_env=bool(cfg.initial_password),
        )

    @router.post("/settings/webhook")
    async def settings_webhook(request: Request, session=Depends(current_session), conn=Depends(get_conn)):
        form = await check_csrf(request, session)
        action = form.get("action")
        if action == "clear":
            db.delete_setting(conn, "webhook_url")
            db.delete_setting(conn, "webhook_set_at")
            return redirect("/settings", msg="웹훅을 지웠습니다. 이후 알림은 미발송으로 쌓입니다.")
        url = str(form.get("webhook_url") or "").strip()
        if not notifier.valid_webhook(url):
            return redirect("/settings", err="디스코드 웹훅 URL 형식이 아닙니다 (https://discord.com/api/webhooks/...).")
        db.set_setting(conn, "webhook_url", url)
        db.set_setting(conn, "webhook_set_at", db.now_iso())
        backlog = notifier.backlog_count(conn)
        msg = "웹훅을 저장했습니다. '테스트 발송'으로 확인하세요."
        if backlog:
            msg += f" 밀린 알림 {backlog}건은 아래에서 보내기/버리기를 고르세요."
        return redirect("/settings", msg=msg)

    @router.post("/settings/webhook/test")
    async def settings_webhook_test(request: Request, session=Depends(current_session), conn=Depends(get_conn)):
        await check_csrf(request, session)
        url = notifier.get_webhook(conn, cfg)
        if not url:
            return redirect("/settings", err="웹훅이 설정되지 않았습니다.")
        try:
            with httpx.Client() as client:
                notifier.send_webhook(client, url, {"embeds": [{
                    "title": "housing-alert 테스트 발송", "color": 0x2E7D32,
                    "description": "이 메시지가 보이면 웹훅이 정상입니다.",
                    "fields": [{"name": "시각", "value": db.now_iso(), "inline": True}],
                }]})
        except notifier.WebhookError as exc:
            return redirect("/settings", err=f"테스트 발송 실패: {exc}")
        return redirect("/settings", msg="테스트 메시지를 보냈습니다. 디스코드 채널을 확인하세요.")

    @router.post("/settings/backlog")
    async def settings_backlog(request: Request, session=Depends(current_session), conn=Depends(get_conn)):
        form = await check_csrf(request, session)
        if form.get("action") == "discard":
            n = notifier.discard_backlog(conn)
            return redirect("/settings", msg=f"밀린 알림 {n}건을 버렸습니다.")
        if not notifier.get_webhook(conn, cfg):
            return redirect("/settings", err="먼저 웹훅을 넣어 주세요.")
        stats = notifier.dispatch_pending(conn, cfg, include_backlog=True)
        return redirect("/settings", msg=f"밀린 알림 발송: 성공 {stats['sent']}건, 실패 {stats['failed']}건")

    @router.post("/settings/apikey")
    async def settings_apikey(request: Request, session=Depends(current_session), conn=Depends(get_conn)):
        form = await check_csrf(request, session)
        if form.get("action") == "clear":
            db.delete_setting(conn, "data_go_kr_key")
            return redirect("/settings", msg="API 키를 지웠습니다.")
        key = str(form.get("api_key") or "").strip()
        if len(key) < 20:
            return redirect("/settings", err="공공데이터포털 인증키가 너무 짧습니다.")
        db.set_setting(conn, "data_go_kr_key", key)
        for c in build_collectors():
            if c.requires_key:
                db.update_source_status(conn, c.name, next_attempt_at=None, state="키 설정됨(다음 수집 때 확인)")
        return redirect("/settings", msg="API 키를 저장했습니다. 다음 수집부터 API 수집기가 동작합니다(재시작 불필요).")

    @router.post("/settings/schedule")
    async def settings_schedule(request: Request, session=Depends(current_session), conn=Depends(get_conn)):
        form = await check_csrf(request, session)
        try:
            sch = {
                "day_interval_min": max(30, int(form.get("day_interval_min") or 60)),
                "night_interval_min": max(30, int(form.get("night_interval_min") or 180)),
                "night_start_hour": min(23, max(0, int(form.get("night_start_hour") or 0))),
                "night_end_hour": min(23, max(0, int(form.get("night_end_hour") or 7))),
            }
            threshold = max(1, int(form.get("summary_threshold") or 10))
        except ValueError:
            return redirect("/settings", err="숫자를 입력하세요.")
        db.set_setting(conn, "schedule", sch)
        db.set_setting(conn, "summary_threshold", threshold)
        return redirect("/settings", msg="수집 주기를 저장했습니다(타이머는 30분마다 깨어나 주기를 확인).")

    @router.post("/settings/keywords")
    async def settings_keywords(request: Request, session=Depends(current_session), conn=Depends(get_conn)):
        form = await check_csrf(request, session)
        if form.get("action") == "reset":
            kws = list(DEFAULT_KEYWORDS)
        else:
            kws = [k.strip() for k in str(form.get("keywords") or "").splitlines() if k.strip()]
        db.set_setting(conn, "coop_keywords", list(dict.fromkeys(kws)))
        n = pipeline.recheck_cooperative(conn)
        return redirect("/settings", msg=f"협동조합 제외 키워드 {len(kws)}개 저장, 저장된 공고 재판정 결과 제외 {n}건.")

    @router.post("/settings/password")
    async def settings_password(request: Request, session=Depends(current_session), conn=Depends(get_conn)):
        form = await check_csrf(request, session)
        if not verify_password(str(form.get("current") or ""), db.get_setting(conn, "password_hash")):
            return redirect("/settings", err="현재 비밀번호가 틀렸습니다.")
        new = str(form.get("new") or "")
        if len(new) < 8 or new != form.get("confirm"):
            return redirect("/settings", err="새 비밀번호는 8자 이상, 확인 값과 같아야 합니다.")
        new_hash = hash_password(new)
        db.set_setting(conn, "password_hash", new_hash)
        remove_initial_password_line()
        resp = redirect("/settings", msg="비밀번호를 바꿨습니다(.env의 초기 비밀번호 줄은 지웠습니다).")
        resp.set_cookie(COOKIE, make_session(secret, password_version(new_hash)), httponly=True, samesite="lax",
                        secure=cfg.cookie_secure, max_age=14 * 24 * 3600, path=base or "/")
        return resp

    # ---------------------------------------------------------- JSON API

    @router.get("/api/health")
    def api_health():
        return {"ok": True}

    @router.get("/api/status")
    def api_status(session=Depends(current_session), conn=Depends(get_conn)):
        return {
            "sources": [{k: v for k, v in s.items()} for s in source_rows(conn)],
            "last_collect_at": db.get_setting(conn, "last_collect_at"),
            "running": pipeline.is_running(cfg),
            "webhook_configured": bool(notifier.get_webhook(conn, cfg)),
            "api_key_configured": bool(pipeline.get_api_key(conn, cfg)),
            "pending_notifications": conn.execute("SELECT COUNT(*) FROM notifications WHERE status='pending'").fetchone()[0],
        }

    @router.get("/api/notices")
    def api_notices(request: Request, session=Depends(current_session), conn=Depends(get_conn)):
        params = dict(request.query_params)
        where, args = notice_query(params)
        limit = min(200, int(params.get("limit") or 50))
        offset = int(params.get("offset") or 0)
        rows = db.rows_to_dicts(conn.execute(
            f"SELECT * FROM notices WHERE {where} ORDER BY COALESCE(posted_date,'') DESC, id DESC LIMIT ? OFFSET ?",
            (*args, limit, offset)))
        for r in rows:
            r.pop("raw_summary", None)
            if not params.get("body"):
                r.pop("body_text", None)
            r["cooperative_reason"] = json.loads(r["cooperative_reason"]) if r.get("cooperative_reason") else None
        total = conn.execute(f"SELECT COUNT(*) FROM notices WHERE {where}", args).fetchone()[0]
        return {"total": total, "items": rows}

    @router.get("/api/filters")
    def api_filters(session=Depends(current_session), conn=Depends(get_conn)):
        out = []
        for r in conn.execute("SELECT * FROM filters ORDER BY id").fetchall():
            f = load_filter(dict(r))
            f["webhook_url"] = notifier.mask_webhook(f.get("webhook_url"))
            out.append(f)
        return out

    @router.post("/api/filters/preview")
    async def api_filter_preview(request: Request, session=Depends(current_session), conn=Depends(get_conn)):
        form_data = await request.form()
        form = await check_csrf(request, session)
        config = config_from_form(form, {k: form_data.getlist(k) for k in LIST_KEYS})
        return preview_filter(conn, config)

    @router.get("/api/filters/{filter_id}/preview")
    def api_filter_preview_saved(filter_id: int, session=Depends(current_session), conn=Depends(get_conn)):
        row = conn.execute("SELECT * FROM filters WHERE id=?", (filter_id,)).fetchone()
        if row is None:
            raise HTTPException(404)
        return preview_filter(conn, load_filter(dict(row))["config"])

    return router


def mount_static(app: FastAPI, cfg: Config) -> None:
    app.mount(f"{cfg.base_path}/static", StaticFiles(directory=str(HERE / "static")), name="ha_static")


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load_config()
    app = FastAPI(title="housing-alert", docs_url=None, redoc_url=None, openapi_url=None)
    base = cfg.base_path

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired):
        nxt = request.url.path
        return RedirectResponse(f"{base}/login?next={quote(nxt)}", status_code=303)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp: Response = await call_next(request)
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    app.include_router(build_router(cfg))
    mount_static(app, cfg)
    if base:
        @app.get("/")
        def _root():
            return RedirectResponse(f"{base}/")
    return app
