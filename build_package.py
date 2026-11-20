# -*- coding: utf-8 -*-
"""打包 S5-GW-025 交付物，并校验 solution 正文字符数（平台硬上限 10000）。

- 白名单打 zip；solution.md / attachments.json 本身不在包里，避免自引用。
- 字符数、文件数、字节数由源码计算，超限即构建失败，绝不手写。
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PKG_NAME = "S5-GW-025-delivery-v1.zip"
PLATFORM_LIMIT = 10000
BACKEND_LIMIT = 9500

WHITELIST = [
    "README.md", "RUN_LOG.txt", "requirements.txt", "alembic.ini", "verify.cmd",
    "run_verify.py", "build_package.py", "solution.template.md",
    "app/__init__.py", "app/review/__init__.py",
    "app/review/enums.py", "app/review/models.py",
    "alembic/env.py", "alembic/script.py.mako",
    "alembic/versions/0001_review_queue.py",
    "tests/__init__.py", "tests/conftest.py", "tests/test_review.py",
]

# 关键源码在正文里内嵌；与包内文件保持一致（逐字）
EMBED = ["app/review/enums.py", "alembic/versions/0001_review_queue.py"]


def main() -> int:
    # 打 zip
    out = ROOT / PKG_NAME
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in WHITELIST:
            p = ROOT / rel
            if not p.exists():
                raise SystemExit(f"MISSING: {rel}")
            z.write(p, rel)
    digest = hashlib.sha256(out.read_bytes()).hexdigest()

    # 由模板生成 solution.md，并把包元信息注入末尾
    tpl = (ROOT / "solution.template.md").read_text(encoding="utf-8")
    footer = (
        "\n---\n\n"
        f"源码包：`{PKG_NAME}` · SHA256 `{digest[:16]}…` · "
        f"{len(WHITELIST)} 个文件 · 由 `build_package.py` 生成于 "
        f"{datetime.now(timezone.utc).isoformat()}\n"
    )
    solution = tpl + footer
    n = len(solution)
    print(f"solution chars = {n} (limit {PLATFORM_LIMIT}, backend {BACKEND_LIMIT})")
    if n > BACKEND_LIMIT:
        (ROOT / "solution.md").write_text(solution, encoding="utf-8")
        raise SystemExit(f"OVER LIMIT by {n - BACKEND_LIMIT}; solution.md written for editing")
    (ROOT / "solution.md").write_text(solution, encoding="utf-8")

    # 断言：README 含 7 status / 10 action 词条
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for v in ("reference", "pending_review", "pending_approval", "effective",
              "rejected", "withdrawn", "expired"):
        assert f"`{v}`" in readme, f"README missing status {v}"
    for v in ("create", "edit", "confirm", "submit_for_approval", "approve",
              "return", "reject", "withdraw", "expire", "adopt"):
        assert f"`{v}`" in readme, f"README missing action {v}"

    # RUN_LOG 必须 VERIFY_PASS
    log = (ROOT / "RUN_LOG.txt").read_text(encoding="utf-8")
    assert "RESULT: VERIFY_PASS" in log, "RUN_LOG not VERIFY_PASS"

    print(f"package: {out.name}  {out.stat().st_size} bytes  sha256={digest}")
    print(f"files: {len(WHITELIST)}")
    print("BUILD_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
