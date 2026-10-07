#!/usr/bin/env bash
# housing-alert 설치: venv 생성, 의존성 설치, .env/DB 초기화, systemd 사용자 유닛 설치·기동.
# 사용: scripts/install.sh            (프로젝트 루트에서)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export TMPDIR="${TMPDIR:-$HOME/.tmp}"
mkdir -p "$TMPDIR"
cd "$ROOT"

PY="${PYTHON:-python3}"
if [ ! -x .venv/bin/python ]; then
  if command -v uv >/dev/null 2>&1; then uv venv -q .venv --python "$PY"; else "$PY" -m venv .venv; fi
fi
if command -v uv >/dev/null 2>&1; then
  uv pip install -q --python .venv/bin/python -r requirements.txt
else
  .venv/bin/pip install -q -r requirements.txt
fi

# 포트 확인 (기본 8110, .env에 PORT가 있으면 그 값)
PORT="$(grep -E '^PORT=' .env 2>/dev/null | cut -d= -f2 || true)"; PORT="${PORT:-8110}"
if [ ! -f .env ] && ss -ltn | awk '{print $4}' | grep -qE "[:.]${PORT}\$"; then
  for p in $(seq 8111 8130); do
    if ! ss -ltn | awk '{print $4}' | grep -qE "[:.]${p}\$"; then PORT=$p; break; fi
  done
  echo "8110 사용 중 → $PORT 사용"
fi
if [ ! -f .env ]; then
  .venv/bin/python - "$PORT" <<'PY'
import sys
from housing_alert.bootstrap import ensure_env_file
ensure_env_file(port=int(sys.argv[1]))
PY
fi
.venv/bin/python -m housing_alert init

UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
for f in deploy/systemd/*; do
  sed -e "s#@ROOT@#$ROOT#g" -e "s#@HOME@#$HOME#g" "$f" > "$UNIT_DIR/$(basename "$f")"
done
systemctl --user daemon-reload
systemctl --user enable --now housing-alert-web.service housing-alert-collect.timer
systemctl --user --no-pager status housing-alert-web.service housing-alert-collect.timer | head -20
echo "설치 완료: http://127.0.0.1:${PORT}/  (초기 비밀번호: $ROOT/.env 의 ADMIN_INITIAL_PASSWORD)"
