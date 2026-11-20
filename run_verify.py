# -*- coding: utf-8 -*-
"""S5-GW-025 一键复跑：真实执行 alembic 与 pytest，输出写入 RUN_LOG.txt。

执行：python run_verify.py（或 verify.cmd）
前置：一个可连的 PostgreSQL 16 空库（REVIEW_DB_DSN）。脚本会重置该库 public schema。
日志里的每条 stdout/stderr 都来自真实子进程，不含任何模拟结果。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault(
    "REVIEW_DB_DSN",
    "postgresql+psycopg://postgres@127.0.0.1:5432/review2149",
)
DSN = os.environ["REVIEW_DB_DSN"]
LIBPQ_DSN = re.sub(r"^postgresql\+psycopg://", "postgresql://", DSN)

LOG: list[str] = []
RESULTS: list[tuple[str, int]] = []


def note(line: str = "") -> None:
    LOG.append(line)


def exe(name: str) -> list[str]:
    p = Path(sys.executable).parent / (name + ".exe" if os.name == "nt" else name)
    return [str(p)] if p.exists() else [sys.executable, "-m", name]


def run(label: str, cmd: list[str]) -> subprocess.CompletedProcess:
    note(f"$ {label}")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    p = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if p.stdout.strip():
        note("--- stdout ---")
        note(p.stdout.rstrip())
    if p.stderr.strip():
        note("--- stderr ---")
        note(p.stderr.rstrip())
    note(f"[exit={p.returncode}]")
    note()
    RESULTS.append((label, p.returncode))
    return p


def connect():
    import psycopg
    return psycopg.connect(LIBPQ_DSN, autocommit=True)


def main() -> int:
    note(f"S5-GW-025 verify @ {datetime.now(timezone.utc).isoformat()}")
    note(f"python: {sys.version.split()[0]}")
    note(f"DSN: {DSN}")
    with connect() as c:
        note(f"server_version: {c.info.server_version}")
        note(f"PG 版本串: {c.execute('SELECT version()').fetchone()[0]}")
    import importlib
    versions = []
    for m in ("sqlalchemy", "alembic", "psycopg", "pytest"):
        try:
            versions.append(f"{m} {getattr(importlib.import_module(m), '__version__', 'ok')}")
        except Exception:
            versions.append(f"{m} ?")
    note("依赖: " + " / ".join(versions))
    note()

    note("=== 1) 重置目标库 public schema ===")
    with connect() as c:
        c.execute("DROP SCHEMA public CASCADE")
        c.execute("CREATE SCHEMA public")
    note("[ok] schema reset")
    note()

    note("=== 2) 验收 1：alembic upgrade / check / downgrade / 再 upgrade / 再 check ===")
    al = exe("alembic")
    run("alembic upgrade head", al + ["upgrade", "head"])
    run("alembic check", al + ["check"])
    run("alembic downgrade -1", al + ["downgrade", "-1"])

    with connect() as c:
        tables = c.execute(
            "SELECT count(*) FROM pg_tables WHERE schemaname='public' "
            "AND tablename LIKE 'ai_%'").fetchone()[0]
        indexes = c.execute(
            "SELECT count(*) FROM pg_indexes WHERE schemaname='public' "
            "AND (indexname LIKE 'ix_ai_%' OR indexname LIKE 'uq_ai_%')").fetchone()[0]
        funcs = c.execute(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='public' AND p.proname LIKE 'fn_%'").fetchone()[0]
        seqs = c.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n "
            "ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='S'").fetchone()[0]
        av = c.execute(
            "SELECT count(*) FROM pg_tables WHERE schemaname='public' "
            "AND tablename='alembic_version'").fetchone()[0]
    clean = (tables, indexes, funcs, seqs) == (0, 0, 0, 0)
    note("$ downgrade 后对象计数")
    note(f"ai_* 表={tables}  业务索引/约束={indexes}  函数={funcs}  序列={seqs}  "
         f"alembic_version 表={av}（alembic 自管，不属于业务对象）")
    note(f"DOWNGRADE_CLEAN: {'PASS' if clean else 'FAIL'}")
    note()
    RESULTS.append(("DOWNGRADE_CLEAN", 0 if clean else 1))

    run("alembic upgrade head (repeat)", al + ["upgrade", "head"])
    run("alembic check (repeat)", al + ["check"])

    note("=== 3) 验收 2/3/4 + 边界：pytest（真实 PG16）===")
    pt = run("pytest -q", exe("pytest") + ["-q", "--disable-warnings"])
    m = re.search(r"(\d+) passed", pt.stdout)
    passed = int(m.group(1)) if m else 0
    note(f"PARSED_PASSED: {passed}")
    note()

    failed = [label for label, code in RESULTS if code != 0]
    note("=== 汇总 ===")
    note(f"命令数={len(RESULTS)}  失败={len(failed)}  pytest passed={passed}  "
         f"downgrade_clean={'PASS' if clean else 'FAIL'}")
    if failed:
        note("失败项: " + ", ".join(failed))
    ok = not failed and passed > 0 and clean
    note(f"RESULT: {'VERIFY_PASS' if ok else 'VERIFY_FAIL'}")
    (ROOT / "RUN_LOG.txt").write_text("\n".join(LOG) + "\n", encoding="utf-8")
    print("\n".join(LOG[-9:]))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
