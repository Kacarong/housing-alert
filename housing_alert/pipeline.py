"""수집 실행: 출처별 수집 → 저장/중복 제거/정정 감지 → 상세 보강 → 협동조합 판정 → 필터 매칭 → 발송."""
from __future__ import annotations

import fcntl
import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Callable, Iterator

import httpx

from . import db, notifier
from .classify import classify_notice_kind, classify_supply_type, dup_key, is_correction_title
from .collectors import Collector, KeyMissing, NoticeData, build_collectors
from .config import KST, Config
from .cooperative import DEFAULT_KEYWORDS, detect
from .filters import is_notifiable, load_filter, match
from .http import PoliteClient

log = logging.getLogger(__name__)

MAX_NEW_DETAILS = 30       # 한 번 실행에 새 공고 상세 최대 건수 (출처별)
MAX_BACKLOG_DETAILS = 10   # 상세를 아직 못 가져온 공고 보강 건수 (출처별)
MAX_DETAIL_ATTEMPTS = 3
CANDIDATE_DAYS = 7         # 이 기간 안에 처음 본(또는 정정된) 공고만 알림 후보
FAILURE_ALERT_THRESHOLD = 3
BACKOFF_BASE_MIN = 30
BACKOFF_MAX_MIN = 12 * 60

DEFAULT_SCHEDULE = {"day_interval_min": 60, "night_interval_min": 180, "night_start_hour": 0, "night_end_hour": 7}

LIST_FIELDS = ["title", "url", "category_raw", "region_raw", "sigungu", "complex_name", "households",
               "area_min", "area_max", "deposit_min", "deposit_max", "rent_min", "rent_max", "price_min",
               "price_max", "posted_date", "apply_start", "apply_end", "status", "body_text", "complex_text"]


def now_kst() -> datetime:
    return datetime.now(KST).replace(microsecond=0)


def get_api_key(conn: sqlite3.Connection, cfg: Config) -> str:
    return db.get_setting(conn, "data_go_kr_key") or cfg.data_go_kr_key or ""


def get_coop_keywords(conn: sqlite3.Connection) -> list[str]:
    kws = db.get_setting(conn, "coop_keywords")
    return kws if isinstance(kws, list) else list(DEFAULT_KEYWORDS)


def get_schedule(conn: sqlite3.Connection) -> dict[str, int]:
    out = dict(DEFAULT_SCHEDULE)
    stored = db.get_setting(conn, "schedule") or {}
    for k in out:
        try:
            if k in stored:
                out[k] = int(stored[k])
        except (TypeError, ValueError):
            pass
    return out


def is_due(conn: sqlite3.Connection, now: datetime) -> bool:
    last = db.get_setting(conn, "last_collect_at")
    if not last:
        return True
    sch = get_schedule(conn)
    s, e = sch["night_start_hour"], sch["night_end_hour"]
    night = (s <= now.hour < e) if s <= e else (now.hour >= s or now.hour < e)
    interval = sch["night_interval_min"] if night else sch["day_interval_min"]
    last_dt = datetime.fromisoformat(last)
    # 타이머 오차를 고려해 3분 여유
    return now >= last_dt + timedelta(minutes=max(interval, 10)) - timedelta(minutes=3)


@contextmanager
def collect_lock(cfg: Config) -> Iterator[bool]:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.data_dir / "collect.lock"
    with open(path, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def is_running(cfg: Config) -> bool:
    path = cfg.data_dir / "collect.lock"
    if not path.exists():
        return False
    with open(path, "a") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fh, fcntl.LOCK_UN)
    return False


# ------------------------------------------------------------------ 저장

def _coop_fields(title: str, body: str | None, complex_text: str | None, keywords: list[str]) -> dict:
    res = detect(title, body, complex_text, keywords)
    return {
        "cooperative_suspect": 1 if res.suspect else 0,
        "cooperative_reason": json.dumps(
            {"text": res.reason_text(), "matches": [m.as_dict() for m in res.matches]}, ensure_ascii=False
        ) if res.suspect else None,
    }


def upsert_notice(conn: sqlite3.Connection, item: NoticeData, baseline: bool, now: str,
                  keywords: list[str]) -> tuple[int, bool, bool]:
    """반환: (notice_id, 새로 생김, 정정으로 갱신됨)"""
    row = conn.execute("SELECT * FROM notices WHERE source=? AND source_id=?", (item.source, item.source_id)).fetchone()
    title = item.title.strip()
    computed = {
        "org": item.org,
        "supply_type": item.supply_type or classify_supply_type(item.category_raw, title, item.org),
        "notice_kind": classify_notice_kind(title),
        "is_correction": 1 if is_correction_title(title) else 0,
        "dup_key": dup_key(item.org, title),
    }
    if item.sidos:
        computed["sido"] = ",".join(item.sidos)
    values = {k: getattr(item, k) for k in LIST_FIELDS if getattr(item, k) is not None}
    if row is None:
        data = {
            "source": item.source, "source_id": item.source_id, **values, **computed,
            "raw_summary": json.dumps(item.raw, ensure_ascii=False),
            "detail_fetched": 1 if item.detail_fetched else 0,
            "baseline": 1 if baseline else 0,
            "first_seen_at": now, "updated_at": now,
            **_coop_fields(title, item.body_text, item.complex_text, keywords),
        }
        cols = ", ".join(data)
        cur = conn.execute(f"INSERT INTO notices({cols}) VALUES({', '.join('?' * len(data))})", tuple(data.values()))
        return cur.lastrowid, True, False

    old = dict(row)
    revised = False
    updates = {**values, **computed}
    # 상세에서 얻은 값은 목록 값(None 아님)으로만 덮는다; 지역은 상세가 더 정확할 수 있어 비어 있을 때만
    if old.get("sido") and "sido" in updates:
        updates.pop("sido")
    if title != old["title"] and computed["is_correction"]:
        updates["revision"] = old["revision"] + 1
        updates["revised_at"] = now
        revised = True
    if title != old["title"] or "body_text" in values:
        updates.update(_coop_fields(title, values.get("body_text", old["body_text"]),
                                    values.get("complex_text", old["complex_text"]), keywords))
    raw = json.loads(old["raw_summary"] or "{}")
    raw.update(item.raw)
    updates["raw_summary"] = json.dumps(raw, ensure_ascii=False)
    changed = {k: v for k, v in updates.items() if old.get(k) != v}
    if changed:
        changed["updated_at"] = now
        conn.execute(
            f"UPDATE notices SET {', '.join(f'{k}=?' for k in changed)} WHERE id=?",
            (*changed.values(), old["id"]),
        )
    return old["id"], False, revised


def apply_detail(conn: sqlite3.Connection, notice_id: int, fields: dict[str, Any], keywords: list[str], now: str) -> None:
    row = dict(conn.execute("SELECT * FROM notices WHERE id=?", (notice_id,)).fetchone())
    updates: dict[str, Any] = {}
    for k, v in fields.items():
        if v is None or v == "":
            continue
        if k == "sidos":
            updates["sido"] = ",".join(v)
        elif k in LIST_FIELDS or k in ("status",):
            updates[k] = v
    body = updates.get("body_text", row["body_text"])
    cplx = updates.get("complex_text", row["complex_text"])
    updates.update(_coop_fields(row["title"], body, cplx, keywords))
    updates["detail_fetched"] = 1
    updates["detail_error"] = None
    updates["updated_at"] = now
    conn.execute(f"UPDATE notices SET {', '.join(f'{k}=?' for k in updates)} WHERE id=?", (*updates.values(), notice_id))


def recheck_cooperative(conn: sqlite3.Connection) -> int:
    """제외 키워드 목록이 바뀌면 저장된 공고 전체를 다시 판정한다. 반환: 의심 건수."""
    keywords = get_coop_keywords(conn)
    rows = conn.execute("SELECT id, title, body_text, complex_text FROM notices").fetchall()
    for r in rows:
        f = _coop_fields(r["title"], r["body_text"], r["complex_text"], keywords)
        conn.execute("UPDATE notices SET cooperative_suspect=?, cooperative_reason=? WHERE id=?",
                     (f["cooperative_suspect"], f["cooperative_reason"], r["id"]))
    return conn.execute("SELECT COUNT(*) FROM notices WHERE cooperative_suspect=1").fetchone()[0]


def notice_for_detail(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["raw"] = json.loads(d.get("raw_summary") or "{}")
    except ValueError:
        d["raw"] = {}
    return d


# ------------------------------------------------------------------ 출처 1개 실행

def run_source(conn: sqlite3.Connection, collector: Collector, client: PoliteClient, now: datetime,
               force: bool, api_key: str, keywords: list[str]) -> dict[str, Any]:
    name = collector.name
    st = db.get_source_status(conn, name)
    now_s = now.isoformat()
    if collector.requires_key and not api_key:
        db.update_source_status(conn, name, state="키 미설정", last_error=None)
        return {"source": name, "state": "key_missing"}
    if not force and st.get("next_attempt_at") and st["next_attempt_at"] > now_s:
        return {"source": name, "state": "backoff", "next_attempt_at": st["next_attempt_at"]}

    baseline_src = collector.baseline_source or name
    baseline = not db.get_source_status(conn, baseline_src)["baseline_done"]
    notice_sources = {name, baseline_src}

    def is_known(source_id: str) -> bool:
        q = f"SELECT 1 FROM notices WHERE source_id=? AND source IN ({','.join('?' * len(notice_sources))})"
        return conn.execute(q, (source_id, *notice_sources)).fetchone() is not None

    try:
        items = collector.list_notices(client, is_known, baseline)
    except KeyMissing:
        db.update_source_status(conn, name, state="키 미설정")
        return {"source": name, "state": "key_missing"}
    except Exception as exc:  # 출처 하나의 실패가 나머지를 막지 않도록
        failures = (st.get("consecutive_failures") or 0) + 1
        wait = min(BACKOFF_BASE_MIN * (2 ** (failures - 1)), BACKOFF_MAX_MIN)
        msg = f"{type(exc).__name__}: {exc}"[:500]
        db.update_source_status(
            conn, name, last_run_at=now_s, consecutive_failures=failures, last_error=msg, state="실패",
            next_attempt_at=(now + timedelta(minutes=wait)).isoformat(),
        )
        log.warning("[%s] 수집 실패 %d회째: %s", name, failures, msg)
        return {"source": name, "state": "failed", "error": msg, "failures": failures}

    new_ids: list[int] = []
    revised = 0
    seen: set[tuple[str, str]] = set()
    with_tx(conn)
    try:
        for item in items:
            key = (item.source, item.source_id)
            if key in seen or not item.source_id or not item.title:
                continue
            seen.add(key)
            nid, created, was_revised = upsert_notice(conn, item, baseline, now_s, keywords)
            if created:
                new_ids.append(nid)
            if was_revised:
                revised += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    details_ok = details_failed = 0
    if collector.has_detail:
        targets = new_ids[:MAX_NEW_DETAILS]
        placeholders = ",".join("?" * len(notice_sources))
        backlog = conn.execute(
            f"SELECT id FROM notices WHERE source IN ({placeholders}) AND detail_fetched=0 AND detail_attempts<? "
            f"AND id NOT IN ({','.join('?' * len(targets)) or 'NULL'}) ORDER BY first_seen_at DESC LIMIT ?",
            (*notice_sources, MAX_DETAIL_ATTEMPTS, *targets, MAX_BACKLOG_DETAILS),
        ).fetchall()
        for nid in targets + [r["id"] for r in backlog]:
            row = conn.execute("SELECT * FROM notices WHERE id=?", (nid,)).fetchone()
            try:
                fields = collector.fetch_detail(client, notice_for_detail(row))
                apply_detail(conn, nid, fields, keywords, now_s)
                details_ok += 1
            except Exception as exc:
                details_failed += 1
                conn.execute(
                    "UPDATE notices SET detail_attempts=detail_attempts+1, detail_error=? WHERE id=?",
                    (f"{type(exc).__name__}: {exc}"[:300], nid),
                )
                log.warning("[%s] 상세 실패 id=%s: %s", name, nid, exc)

    db.update_source_status(
        conn, name, last_run_at=now_s, last_success_at=now_s, consecutive_failures=0, last_error=None,
        last_count=len(seen), last_new=len(new_ids), next_attempt_at=None, alerted=0,
        baseline_done=1, state="정상",
    )
    return {"source": name, "state": "ok", "count": len(seen), "new": len(new_ids), "revised": revised,
            "baseline": baseline, "details_ok": details_ok, "details_failed": details_failed}


def with_tx(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")


# ------------------------------------------------------------------ 매칭/알림 대기열

def enabled_filters(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM filters WHERE enabled=1 ORDER BY id").fetchall()
    return [load_filter(dict(r)) for r in rows]


def queue_matches(conn: sqlite3.Connection, now: datetime) -> int:
    filters = enabled_filters(conn)
    if not filters:
        return 0
    since = (now - timedelta(days=CANDIDATE_DAYS)).isoformat()
    rows = conn.execute(
        """SELECT * FROM notices
           WHERE (baseline=0 AND first_seen_at>=?) OR (revision>0 AND revised_at>=?)""",
        (since, since),
    ).fetchall()
    today = now.date()
    now_s = now.isoformat()
    queued = 0
    for r in rows:
        n = dict(r)
        if not is_notifiable(n):
            continue
        ref_time = n["revised_at"] if n["revision"] and n["revised_at"] else n["first_seen_at"]
        for f in filters:
            if not f.get("enabled_at") or ref_time < f["enabled_at"]:
                continue
            ok, _ = match(n, f["config"], today)
            if not ok:
                continue
            exists = conn.execute(
                "SELECT 1 FROM notifications WHERE notice_id=? AND filter_id=? AND revision=? AND kind='new'",
                (n["id"], f["id"], n["revision"]),
            ).fetchone()
            if exists:
                continue
            # 다른 출처에 같은 공고(같은 기관·같은 제목)가 이미 알림 대기/발송됐으면 건너뛴다
            dup = conn.execute(
                """SELECT 1 FROM notifications nt JOIN notices o ON o.id=nt.notice_id
                   WHERE o.dup_key=? AND o.source<>? AND nt.filter_id=? AND nt.kind='new'
                     AND nt.status IN ('pending','sent')""",
                (n["dup_key"], n["source"], f["id"]),
            ).fetchone()
            if dup:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO notifications(notice_id, filter_id, revision, kind, status, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (n["id"], f["id"], n["revision"], "new", "pending", now_s),
            )
            queued += 1
    return queued


def queue_reminders(conn: sqlite3.Connection, now: datetime) -> int:
    if not (8 <= now.hour < 22):
        return 0
    tomorrow = (now.date() + timedelta(days=1)).isoformat()
    queued = 0
    for f in enabled_filters(conn):
        if not f["config"].get("remind_d1"):
            continue
        rows = conn.execute(
            """SELECT DISTINCT n.id FROM notices n JOIN notifications nt ON nt.notice_id=n.id
               WHERE nt.filter_id=? AND nt.kind='new' AND nt.status='sent' AND n.apply_end=?""",
            (f["id"], tomorrow),
        ).fetchall()
        for r in rows:
            cur = conn.execute(
                "INSERT OR IGNORE INTO notifications(notice_id, filter_id, revision, kind, status, created_at) "
                "VALUES(?,?,0,'remind','pending',?)",
                (r["id"], f["id"], now.isoformat()),
            )
            queued += cur.rowcount
    return queued


def send_failure_alerts(conn: sqlite3.Connection, cfg: Config, client: httpx.Client | None, sleep=time.sleep) -> int:
    from .collectors import SOURCE_LABELS

    url = notifier.get_webhook(conn, cfg)
    if not url:
        return 0
    rows = conn.execute(
        "SELECT * FROM source_status WHERE consecutive_failures>=? AND alerted=0", (FAILURE_ALERT_THRESHOLD,)
    ).fetchall()
    sent = 0
    for r in rows:
        text = (f"⚠️ 수집 실패 알림: {SOURCE_LABELS.get(r['source'], r['source'])} 출처가 "
                f"{r['consecutive_failures']}회 연속 실패했습니다.\n마지막 오류: {r['last_error'] or '-'}")
        try:
            own = client is None
            c = client or httpx.Client()
            try:
                notifier.send_text(c, url, text, sleep=sleep)
            finally:
                if own:
                    c.close()
            db.update_source_status(conn, r["source"], alerted=1)
            sent += 1
        except notifier.WebhookError as exc:
            log.warning("실패 알림 전송 실패: %s", exc)
    return sent


# ------------------------------------------------------------------ 전체 실행

def run_collection(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    force: bool = False,
    only_sources: list[str] | None = None,
    trigger: str = "timer",
    now: datetime | None = None,
    client_factory: Callable[[], PoliteClient] | None = None,
    webhook_client: httpx.Client | None = None,
    sleep=time.sleep,
) -> dict[str, Any]:
    now = now or now_kst()
    if not force and not is_due(conn, now):
        return {"skipped": "not_due"}
    with collect_lock(cfg) as acquired:
        if not acquired:
            return {"skipped": "already_running"}
        run_id = conn.execute("INSERT INTO runs(started_at, trigger) VALUES(?,?)", (now.isoformat(), trigger)).lastrowid
        api_key = get_api_key(conn, cfg)
        keywords = get_coop_keywords(conn)
        factory = client_factory or (lambda: PoliteClient(delay=cfg.http_delay))
        results = []
        for collector in build_collectors(key_getter=lambda: api_key):
            if only_sources and collector.name not in only_sources:
                continue
            client = factory()
            try:
                results.append(run_source(conn, collector, client, now, force, api_key, keywords))
            finally:
                client.close()
        queued = queue_matches(conn, now)
        reminders = queue_reminders(conn, now)
        dispatch = notifier.dispatch_pending(conn, cfg, client=webhook_client, sleep=sleep)
        alerts = send_failure_alerts(conn, cfg, webhook_client, sleep=sleep)
        summary = {"sources": results, "queued": queued, "reminders": reminders, "dispatch": dispatch,
                   "failure_alerts": alerts}
        finished = now_kst().isoformat()
        conn.execute("UPDATE runs SET finished_at=?, summary=? WHERE id=?",
                     (finished, json.dumps(summary, ensure_ascii=False), run_id))
        db.set_setting(conn, "last_collect_at", now.isoformat())
        return summary
