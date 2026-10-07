"""설치/초기화: .env 생성(권한 600), DB 생성, 관리자 비밀번호 해시 저장, 예시 필터 등록."""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
from pathlib import Path

from . import db
from .auth import generate_password, hash_password
from .config import Config, env_file_path, load_config
from .cooperative import DEFAULT_KEYWORDS
from .filters import DEFAULT_FILTERS, normalize_config

ENV_TEMPLATE = """# housing-alert 설정 (권한 600 유지, git에 올리지 말 것)
HOST=127.0.0.1
PORT={port}
# 통합 웹사이트 하위 경로로 붙일 때 예: BASE_PATH=/housing
BASE_PATH=
SESSION_SECRET={secret}
# 설치 시 생성된 초기 로그인 비밀번호. 웹 설정에서 비밀번호를 바꾸면 이 줄은 자동으로 지워진다.
ADMIN_INITIAL_PASSWORD={password}
# (선택) 공공데이터포털 인증키·디스코드 웹훅은 웹 설정 화면에서 넣는 것을 권장. 여기 넣으면 DB 값이 없을 때 쓰인다.
DATA_GO_KR_KEY=
DISCORD_WEBHOOK_URL=
TMPDIR={tmpdir}
"""


def ensure_env_file(path: Path | None = None, port: int = 8110) -> tuple[Path, bool]:
    path = path or env_file_path()
    if path.exists():
        os.chmod(path, 0o600)
        return path, False
    path.write_text(ENV_TEMPLATE.format(
        port=port, secret=secrets.token_hex(32), password=generate_password(),
        tmpdir=str(Path.home() / ".tmp"),
    ), encoding="utf-8")
    os.chmod(path, 0o600)
    return path, True


def remove_initial_password_line(path: Path | None = None) -> None:
    path = path or env_file_path()
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    kept = [ln for ln in lines if not ln.startswith("ADMIN_INITIAL_PASSWORD=")]
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def insert_default_filters(conn: sqlite3.Connection) -> int:
    if conn.execute("SELECT COUNT(*) FROM filters").fetchone()[0]:
        return 0
    now = db.now_iso()
    for name, raw in DEFAULT_FILTERS:
        conn.execute(
            "INSERT INTO filters(name, enabled, config, created_at, updated_at) VALUES(?,?,?,?,?)",
            (name, 0, json.dumps(normalize_config(raw), ensure_ascii=False), now, now),
        )
    return len(DEFAULT_FILTERS)


def init_db(conn: sqlite3.Connection, cfg: Config) -> dict:
    out = {"filters_added": insert_default_filters(conn)}
    if db.get_setting(conn, "coop_keywords") is None:
        db.set_setting(conn, "coop_keywords", list(DEFAULT_KEYWORDS))
    if not db.get_setting(conn, "password_hash") and cfg.initial_password:
        db.set_setting(conn, "password_hash", hash_password(cfg.initial_password))
        out["password_set"] = True
    return out


def run_init() -> dict:
    env_path, created = ensure_env_file()
    cfg = load_config()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(cfg.data_dir, 0o700)
    conn = db.connect(cfg.db_path)
    try:
        result = init_db(conn, cfg)
    finally:
        conn.close()
    result.update({"env_file": str(env_path), "env_created": created, "db": str(cfg.db_path)})
    return result
