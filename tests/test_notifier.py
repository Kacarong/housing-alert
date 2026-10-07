import json

import httpx

from housing_alert import db, notifier

HOOK = "https://discord.com/api/webhooks/123456789/abcdefTOKEN"


def add_notice(conn, i, **kw):
    now = kw.pop("first_seen_at", "2026-10-07T10:00:00+09:00")
    data = {"source": "lh", "source_id": f"id{i}", "org": "LH", "title": f"테스트 공고 {i}", "supply_type": "행복주택",
            "notice_kind": "모집", "sido": "서울", "url": f"https://apply.lh.or.kr/x?{i}", "first_seen_at": now,
            "updated_at": now, "households": 100, "deposit_min": 10_000_000, "deposit_max": 20_000_000,
            "apply_start": "2026-10-20", "apply_end": "2026-10-22"}
    data.update(kw)
    cols = ",".join(data)
    return conn.execute(f"INSERT INTO notices({cols}) VALUES({','.join('?' * len(data))})", tuple(data.values())).lastrowid


def add_filter(conn, name="필터A", webhook=None):
    return conn.execute(
        "INSERT INTO filters(name, enabled, config, webhook_url, enabled_at, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
        (name, 1, "{}", webhook, "2026-01-01", "2026-01-01", "2026-01-01")).lastrowid


def queue(conn, nid, fid, created="2026-10-07T10:00:00+09:00"):
    conn.execute("INSERT INTO notifications(notice_id, filter_id, revision, kind, status, created_at) VALUES(?,?,0,'new','pending',?)",
                 (nid, fid, created))


class Recorder:
    def __init__(self, responses=None):
        self.requests = []
        self.responses = list(responses or [])

    def __call__(self, request):
        self.requests.append(json.loads(request.content))
        if self.responses:
            return self.responses.pop(0)
        return httpx.Response(200, json={"id": "1"})


def client_for(rec):
    return httpx.Client(transport=httpx.MockTransport(rec))


def test_no_webhook_keeps_pending(conn, cfg):
    fid = add_filter(conn)
    queue(conn, add_notice(conn, 1), fid)
    stats = notifier.dispatch_pending(conn, cfg, client=client_for(Recorder()))
    assert stats == {"sent": 0, "failed": 0, "waiting": 1}
    assert conn.execute("SELECT status FROM notifications").fetchone()[0] == "pending"


def test_single_embed_contents(conn, cfg):
    db.set_setting(conn, "webhook_url", HOOK)
    db.set_setting(conn, "webhook_set_at", "2026-10-01T00:00:00+09:00")
    fid = add_filter(conn, "수도권 공공임대")
    fid2 = add_filter(conn, "전국 전부")
    nid = add_notice(conn, 1, is_correction=1, title="[정정공고]대전 영구임대")
    queue(conn, nid, fid)
    queue(conn, nid, fid2)
    rec = Recorder()
    stats = notifier.dispatch_pending(conn, cfg, client=client_for(rec), sleep=lambda s: None)
    assert stats["sent"] == 2 and len(rec.requests) == 1  # 같은 공고는 embed 하나로
    embed = rec.requests[0]["embeds"][0]
    assert embed["title"].startswith("[정정] ")
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    assert fields["매칭 필터"] == "수도권 공공임대, 전국 전부"
    assert fields["임대보증금"] == "1,000만원 ~ 2,000만원"
    assert fields["접수기간"] == "2026-10-20 ~ 2026-10-22"
    assert embed["url"] == "https://apply.lh.or.kr/x?1"
    assert conn.execute("SELECT COUNT(*) FROM notifications WHERE status='sent'").fetchone()[0] == 2


def test_summary_when_many(conn, cfg):
    db.set_setting(conn, "webhook_url", HOOK)
    db.set_setting(conn, "webhook_set_at", "2026-10-01T00:00:00+09:00")
    fid = add_filter(conn)
    for i in range(12):
        queue(conn, add_notice(conn, i), fid)
    rec = Recorder()
    stats = notifier.dispatch_pending(conn, cfg, client=client_for(rec), sleep=lambda s: None)
    assert stats["sent"] == 12
    assert len(rec.requests) == 1
    assert rec.requests[0]["embeds"][0]["title"] == "새 공고 12건 요약"


def test_rate_limit_retry(conn, cfg):
    db.set_setting(conn, "webhook_url", HOOK)
    db.set_setting(conn, "webhook_set_at", "2026-10-01T00:00:00+09:00")
    fid = add_filter(conn)
    queue(conn, add_notice(conn, 1), fid)
    rec = Recorder([httpx.Response(429, json={"retry_after": 1.5}), httpx.Response(204)])
    slept = []
    stats = notifier.dispatch_pending(conn, cfg, client=client_for(rec), sleep=slept.append)
    assert stats["sent"] == 1 and len(rec.requests) == 2 and slept == [1.5]


def test_backlog_waits_for_user_choice(conn, cfg):
    fid = add_filter(conn)
    queue(conn, add_notice(conn, 1), fid, created="2026-10-05T10:00:00+09:00")
    # 웹훅을 나중에 넣음
    db.set_setting(conn, "webhook_url", HOOK)
    db.set_setting(conn, "webhook_set_at", "2026-10-06T00:00:00+09:00")
    queue(conn, add_notice(conn, 2), fid, created="2026-10-07T10:00:00+09:00")
    rec = Recorder()
    stats = notifier.dispatch_pending(conn, cfg, client=client_for(rec), sleep=lambda s: None)
    assert stats == {"sent": 1, "failed": 0, "waiting": 1}
    assert notifier.backlog_count(conn) == 1
    stats = notifier.dispatch_pending(conn, cfg, client=client_for(rec), include_backlog=True, sleep=lambda s: None)
    assert stats["sent"] == 1 and notifier.backlog_count(conn) == 0


def test_discard_backlog(conn, cfg):
    fid = add_filter(conn)
    queue(conn, add_notice(conn, 1), fid, created="2026-10-05T10:00:00+09:00")
    db.set_setting(conn, "webhook_set_at", "2026-10-06T00:00:00+09:00")
    assert notifier.discard_backlog(conn) == 1
    assert conn.execute("SELECT status FROM notifications").fetchone()[0] == "discarded"


def test_failed_send_stays_pending(conn, cfg):
    db.set_setting(conn, "webhook_url", HOOK)
    db.set_setting(conn, "webhook_set_at", "2026-10-01T00:00:00+09:00")
    fid = add_filter(conn)
    queue(conn, add_notice(conn, 1), fid)
    rec = Recorder([httpx.Response(404, json={"message": "Unknown Webhook"})])
    stats = notifier.dispatch_pending(conn, cfg, client=client_for(rec), sleep=lambda s: None)
    assert stats["failed"] == 1
    row = conn.execute("SELECT status, error FROM notifications").fetchone()
    assert row["status"] == "pending" and "404" in row["error"]


def test_mask_and_validate():
    assert notifier.valid_webhook(HOOK)
    assert not notifier.valid_webhook("https://example.com/api/webhooks/1/x")
    masked = notifier.mask_webhook(HOOK)
    assert "abcdefTOKEN" not in masked and masked.endswith("OKEN")
