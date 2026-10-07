"""환경 설정 로딩.

우선순위: 실제 환경변수 > 프로젝트 루트의 `.env` > 기본값.
비밀값(웹훅, API 키)은 주로 DB settings 테이블에 저장되며, 환경변수는 선택적 오버라이드다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KST = ZoneInfo("Asia/Seoul")


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def env_file_path() -> Path:
    return Path(os.environ.get("HA_ENV_FILE", PROJECT_ROOT / ".env"))


@dataclass
class Config:
    root: Path
    data_dir: Path
    db_path: Path
    host: str
    port: int
    base_path: str
    session_secret: str
    initial_password: str
    data_go_kr_key: str
    discord_webhook_url: str
    http_delay: float
    cookie_secure: bool


def _normalize_base_path(value: str) -> str:
    value = (value or "").strip()
    if not value or value == "/":
        return ""
    if not value.startswith("/"):
        value = "/" + value
    return value.rstrip("/")


def load_config() -> Config:
    file_values = _parse_env_file(env_file_path())

    def get(key: str, default: str = "") -> str:
        if key in os.environ:
            return os.environ[key]
        return file_values.get(key, default)

    root = Path(get("HA_HOME", str(PROJECT_ROOT)))
    data_dir = Path(get("HA_DATA_DIR", str(root / "data")))
    db_path = Path(get("HA_DB_PATH", str(data_dir / "housing_alert.db")))
    return Config(
        root=root,
        data_dir=data_dir,
        db_path=db_path,
        host=get("HOST", "127.0.0.1"),
        port=int(get("PORT", "8110")),
        base_path=_normalize_base_path(get("BASE_PATH", "")),
        session_secret=get("SESSION_SECRET", ""),
        initial_password=get("ADMIN_INITIAL_PASSWORD", ""),
        data_go_kr_key=get("DATA_GO_KR_KEY", ""),
        discord_webhook_url=get("DISCORD_WEBHOOK_URL", ""),
        http_delay=float(get("HA_HTTP_DELAY", "2.0")),
        cookie_secure=get("COOKIE_SECURE", "0") in ("1", "true", "yes"),
    )
