"""수집 파이프라인: 최초 실행 무알림, 새 공고 알림, 정정 재알림, 협동조합 제외, 출처 독립 실패·백오프·실패 알림."""
import json
from datetime import datetime, timedelta

import httpx
import pytest

from housing_alert import db, pipeline
from housing_alert.collectors import Collector, NoticeData
from housing_alert.config import KST
from housing_alert.filters import normalize_config

HOOK = "https://discord.com/api/webhooks/123456789/abcdefTOKEN"
T0 = datetime(2026, 10, 7, 10, 0, tzinfo=KST)


class FakeCollector(Collector):
    def __init__(self, name, items=None, fail=False, detail=None):
        self.name = name
        self.label = name
        self.org = "LH"
        self.items = items or []
        self.fail = fail
        self.detail = detail or {}
        self.detail_calls = []

    def list_notices(self, client, is_known, baseline):
        if self.fail:
            raise RuntimeError("사이트 구조 변경")
        return list(self.items)

    def fetch_detail(self, client, notice):
        self.detail_calls.append(notice["source_id"])
        return self.detail.get(notice["source_id"], {})


def item(sid, title="○○ 행복주택 입주자 모집공고", apply_end="2026-10-30"):
    return NoticeData(source="fake", source_id=sid, org="LH", title=title, category_raw="행복주택",
                      sidos=["서울"], apply_end=apply_end, url=f"https://example.org/{sid}")


class DummyClient:
    def close(self):
        pass


@pytest.fixture
def env(conn, cfg, monkeypatch):
    collectors = {"fake": FakeCollector("fake"), "other": FakeCollector("other")}
    monkeypatch.setattr(pipeline, "build_collectors", lambda key_getter=None: list(collectors.values()))
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(204)

    hook_client = httpx.Client(transport=httpx.MockTransport(handler))

    def run(now=T0, force=True):
        return pipeline.run_collection(conn, cfg, force=force, now=now, client_factory=DummyClient,
                                       webhook_client=hook_client, sleep=lambda s: None)

    return {"conn": conn, "collectors": collectors, "run": run, "sent": sent}


def enable_filter(conn, name="전체", enabled_at="2026-01-01T00:00:00+09:00", **cfgkw):
    return conn.execute(
        "INSERT INTO filters(name, enabled, config, enabled_at, created_at, updated_at) VALUES(?,?,?,?,?,?)",
        (name, 1, json.dumps(normalize_config(cfgkw)), enabled_at, enabled_at, enabled_at)).lastrowid


def set_hook(conn):
    db.set_setting(conn, "webhook_url", HOOK)
    db.set_setting(conn, "webhook_set_at", "2026-01-01T00:00:00+09:00")


def count(conn, sql):
    return conn.execute(sql).fetchone()[0]


def test_first_run_is_baseline_and_sends_nothing(env):
    conn = env["conn"]
    set_hook(conn)
    enable_filter(conn)
    env["collectors"]["fake"].items = [item(str(i)) for i in range(30)]
    summary = env["run"]()
    assert count(conn, "SELECT COUNT(*) FROM notices") == 30
    assert count(conn, "SELECT COUNT(*) FROM notices WHERE baseline=1") == 30
    assert count(conn, "SELECT COUNT(*) FROM notifications") == 0
    assert env["sent"] == []
    src = [s for s in summary["sources"] if s["source"] == "fake"][0]
    assert src["baseline"] is True and src["new"] == 30


def test_new_notice_after_baseline_is_notified_once(env):
    conn = env["conn"]
    set_hook(conn)
    enable_filter(conn)
    fake = env["collectors"]["fake"]
    fake.items = [item("1")]
    env["run"]()
    fake.items = [item("1"), item("2", title="○○ 국민임대 입주자 모집공고")]
    fake.detail = {"2": {"households": 300, "area_min": 26.0, "area_max": 46.0}}
    env["run"](T0 + timedelta(hours=1))
    assert fake.detail_calls == ["1", "2"]  # 상세는 새 공고일 때 1회 (1은 기준선 실행 때)
    assert count(conn, "SELECT COUNT(*) FROM notifications WHERE status='sent'") == 1
    assert len(env["sent"]) == 1
    assert env["sent"][0]["embeds"][0]["title"] == "○○ 국민임대 입주자 모집공고"
    # 다시 돌려도 같은 공고·같은 필터로 두 번 보내지 않는다
    env["run"](T0 + timedelta(hours=2))
    assert len(env["sent"]) == 1
    assert fake.detail_calls == ["1", "2"]


def test_correction_title_change_renotifies(env):
    conn = env["conn"]
    set_hook(conn)
    enable_filter(conn)
    fake = env["collectors"]["fake"]
    fake.items = []
    env["run"]()
    fake.items = [item("9", title="○○ 행복주택 입주자 모집공고")]
    env["run"](T0 + timedelta(hours=1))
    fake.items = [item("9", title="[정정공고] ○○ 행복주택 입주자 모집공고")]
    env["run"](T0 + timedelta(hours=2))
    titles = [s["embeds"][0]["title"] for s in env["sent"]]
    assert titles == ["○○ 행복주택 입주자 모집공고", "[정정] [정정공고] ○○ 행복주택 입주자 모집공고"]
    row = conn.execute("SELECT revision, is_correction FROM notices WHERE source_id='9'").fetchone()
    assert row["revision"] == 1 and row["is_correction"] == 1


def test_cooperative_notice_is_stored_but_not_notified(env):
    conn = env["conn"]
    set_hook(conn)
    enable_filter(conn, **{"recruit_only": False, "private_rent_official_only": False})
    fake = env["collectors"]["fake"]
    env["run"]()
    fake.items = [item("c1", title="○○ 민간임대 협동조합 조합원 모집"),
                  item("c2", title="○○ 행복주택 입주자 모집공고")]
    fake.detail = {"c2": {"body_text": "청약 신청 전 출자금 납부 필요"}}
    env["run"](T0 + timedelta(hours=1))
    rows = {r["source_id"]: dict(r) for r in conn.execute("SELECT * FROM notices")}
    assert rows["c1"]["cooperative_suspect"] == 1
    assert "제목 키워드 '협동조합'" in json.loads(rows["c1"]["cooperative_reason"])["text"]
    assert rows["c2"]["cooperative_suspect"] == 1
    assert "본문 키워드 '출자금'" in json.loads(rows["c2"]["cooperative_reason"])["text"]
    assert env["sent"] == []


def test_filter_only_notifies_notices_seen_after_enabling(env):
    conn = env["conn"]
    set_hook(conn)
    fake = env["collectors"]["fake"]
    env["run"]()
    fake.items = [item("a")]
    env["run"](T0 + timedelta(hours=1))
    # 필터를 나중에 켜면 그 전에 본 공고는 알리지 않는다 (미리보기로만 확인)
    enable_filter(conn, enabled_at=(T0 + timedelta(hours=2)).isoformat())
    env["run"](T0 + timedelta(hours=3))
    assert env["sent"] == []
    fake.items = [item("a"), item("b")]
    env["run"](T0 + timedelta(hours=4))
    assert len(env["sent"]) == 1


def test_no_webhook_accumulates_unsent(env):
    conn = env["conn"]
    enable_filter(conn)
    fake = env["collectors"]["fake"]
    env["run"]()
    fake.items = [item("x"), item("y")]
    summary = env["run"](T0 + timedelta(hours=1))
    assert summary["dispatch"]["waiting"] == 2
    assert count(conn, "SELECT COUNT(*) FROM notifications WHERE status='pending'") == 2
    assert env["sent"] == []


def test_failing_source_does_not_block_others_and_alerts_after_three(env):
    conn = env["conn"]
    set_hook(conn)
    fake, other = env["collectors"]["fake"], env["collectors"]["other"]
    other.fail = True
    fake.items = [item("1")]
    for h in range(3):
        summary = env["run"](T0 + timedelta(hours=h))
        states = {s["source"]: s["state"] for s in summary["sources"]}
        assert states == {"fake": "ok", "other": "failed"}
    st = db.get_source_status(conn, "other")
    assert st["consecutive_failures"] == 3 and st["alerted"] == 1
    assert "사이트 구조 변경" in st["last_error"]
    alerts = [s for s in env["sent"] if "content" in s]
    assert len(alerts) == 1 and "3회 연속 실패" in alerts[0]["content"]
    # 4번째 실패에서는 다시 알리지 않는다
    env["run"](T0 + timedelta(hours=4))
    assert len([s for s in env["sent"] if "content" in s]) == 1
    # 회복하면 카운터 초기화
    other.fail = False
    env["run"](T0 + timedelta(hours=5))
    st = db.get_source_status(conn, "other")
    assert st["consecutive_failures"] == 0 and st["alerted"] == 0 and st["last_success_at"]


def test_backoff_skips_source_until_next_attempt(env):
    conn = env["conn"]
    env["collectors"]["other"].fail = True
    env["run"](T0)
    st = db.get_source_status(conn, "other")
    assert st["next_attempt_at"] > T0.isoformat()
    # force=False + 주기 미도래면 실행 자체가 건너뛰어진다
    db.set_setting(conn, "last_collect_at", (T0 - timedelta(hours=5)).isoformat())
    summary = env["run"](T0 + timedelta(minutes=10), force=False)
    states = {s["source"]: s["state"] for s in summary["sources"]}
    assert states["other"] == "backoff"


def test_schedule_due(conn):
    db.set_setting(conn, "last_collect_at", T0.isoformat())
    assert not pipeline.is_due(conn, T0 + timedelta(minutes=30))
    assert pipeline.is_due(conn, T0 + timedelta(minutes=58))
    night = datetime(2026, 10, 8, 2, 0, tzinfo=KST)
    db.set_setting(conn, "last_collect_at", (night - timedelta(minutes=70)).isoformat())
    assert not pipeline.is_due(conn, night)  # 야간 3시간 간격
    db.set_setting(conn, "last_collect_at", (night - timedelta(minutes=180)).isoformat())
    assert pipeline.is_due(conn, night)


def test_d1_reminder(env):
    conn = env["conn"]
    set_hook(conn)
    enable_filter(conn, remind_d1=True)
    fake = env["collectors"]["fake"]
    env["run"]()
    fake.items = [item("r", apply_end="2026-10-09")]
    env["run"](T0 + timedelta(hours=1))
    assert len(env["sent"]) == 1
    env["run"](datetime(2026, 10, 8, 9, 0, tzinfo=KST))
    assert len(env["sent"]) == 2 and env["sent"][1]["embeds"][0]["title"].startswith("[D-1 마감]")
    env["run"](datetime(2026, 10, 8, 12, 0, tzinfo=KST))
    assert len(env["sent"]) == 2


def test_same_notice_from_two_sources_notified_once(env):
    conn = env["conn"]
    set_hook(conn)
    enable_filter(conn)
    env["run"]()
    title = "연천BIX 경기행복주택 기업체 기숙사 추가모집 공고"
    env["collectors"]["fake"].items = [item("g1", title=title)]
    env["collectors"]["other"].items = [NoticeData(source="other", source_id="w1", org="LH", title=title,
                                                   category_raw="주택", sidos=["경기"])]
    env["run"](T0 + timedelta(hours=1))
    assert len(env["sent"]) == 1
