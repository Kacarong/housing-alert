"""CLI: python -m housing_alert {init,collect,serve,set-password,status}"""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys

from . import db
from .config import load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="housing_alert")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help=".env/DB 초기화, 예시 필터 등록")
    p_collect = sub.add_parser("collect", help="수집 1회 실행")
    p_collect.add_argument("--force", action="store_true", help="주기·백오프 무시하고 지금 실행")
    p_collect.add_argument("--source", action="append", help="특정 출처만 (여러 번 지정 가능)")
    p_collect.add_argument("--trigger", default="timer")
    sub.add_parser("serve", help="웹 서버 실행")
    sub.add_parser("set-password", help="로그인 비밀번호 변경 (표준입력 또는 프롬프트)")
    sub.add_parser("status", help="출처별 상태 출력")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.cmd == "init":
        from .bootstrap import run_init
        print(json.dumps(run_init(), ensure_ascii=False, indent=2))
        return 0

    cfg = load_config()
    if args.cmd == "serve":
        import uvicorn
        from .web.app import create_app
        uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, proxy_headers=True,
                    forwarded_allow_ips="127.0.0.1", log_level="info")
        return 0

    conn = db.connect(cfg.db_path)
    try:
        if args.cmd == "collect":
            from .pipeline import run_collection
            summary = run_collection(conn, cfg, force=args.force, only_sources=args.source, trigger=args.trigger)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "set-password":
            from .auth import hash_password
            pw = sys.stdin.readline().strip() if not sys.stdin.isatty() else getpass.getpass("새 비밀번호: ")
            if len(pw) < 8:
                print("비밀번호는 8자 이상이어야 합니다.", file=sys.stderr)
                return 1
            db.set_setting(conn, "password_hash", hash_password(pw))
            from .bootstrap import remove_initial_password_line
            remove_initial_password_line()
            print("비밀번호를 변경했습니다.")
            return 0
        if args.cmd == "status":
            rows = db.rows_to_dicts(conn.execute("SELECT * FROM source_status ORDER BY source"))
            count = conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0]
            print(json.dumps({"notices": count, "sources": rows}, ensure_ascii=False, indent=2))
            return 0
    finally:
        conn.close()
    return 1


if __name__ == "__main__":
    sys.exit(main())
