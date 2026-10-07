from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from housing_alert import db  # noqa: E402
from housing_alert.config import Config  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_bytes().decode("utf-8", "replace")


@pytest.fixture
def cfg(tmp_path) -> Config:
    data = tmp_path / "data"
    return Config(
        root=tmp_path, data_dir=data, db_path=data / "test.db", host="127.0.0.1", port=0, base_path="",
        session_secret="test-secret", initial_password="initial-pass-123", data_go_kr_key="",
        discord_webhook_url="", http_delay=0.0, cookie_secure=False,
    )


@pytest.fixture
def conn(cfg):
    c = db.connect(cfg.db_path)
    yield c
    c.close()
