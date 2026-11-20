# -*- coding: utf-8 -*-
"""测试夹具：每个测试模块开始前重置 public schema 并跑 alembic upgrade head。

连接串只从 REVIEW_DB_DSN 环境变量读取；未设置时给出清晰报错，避免误连生产。
"""
from __future__ import annotations

import os
import re
import uuid
import subprocess
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb
import pytest

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("REVIEW_DB_DSN")
if not DSN:
    pytest.skip("REVIEW_DB_DSN not set", allow_module_level=True)

LIBPQ_DSN = re.sub(r"^postgresql\+psycopg://", "postgresql://", DSN)
ALEMBIC = str(ROOT / "alembic")
ALEMBIC_INI = str(ROOT / "alembic.ini")


def _reset_schema() -> None:
    c = psycopg.connect(LIBPQ_DSN, autocommit=True)
    c.execute("DROP SCHEMA public CASCADE")
    c.execute("CREATE SCHEMA public")
    c.close()


@pytest.fixture(scope="session", autouse=True)
def _schema() :
    _reset_schema()
    env = dict(os.environ)
    env["REVIEW_DB_DSN"] = DSN
    r = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", ALEMBIC_INI, "upgrade", "head"],
        cwd=str(ROOT), env=env, capture_output=True, text=True)
    assert r.returncode == 0, f"alembic upgrade head failed:\n{r.stderr}"
    yield
    _reset_schema()


@pytest.fixture()
def conn():
    c = psycopg.connect(LIBPQ_DSN, autocommit=True)
    # 函数级隔离：每个测试从空业务表开始，避免绝对行数断言互相干扰。
    # ai_review_actions 上有 BEFORE TRUNCATE 保护触发器，测试夹具的清理需临时
    # 切换到 replica 角色绕过所有触发器；该设置只作用于本连接的清理阶段，
    # 随后立刻恢复 origin，因此测试主体仍在正常触发器下运行（保护逻辑照常受测）。
    c.execute("SET session_replication_role = replica")
    try:
        c.execute("TRUNCATE TABLE ai_review_actions, ai_drafts RESTART IDENTITY CASCADE")
    finally:
        c.execute("SET session_replication_role = origin")
    yield c
    c.close()


ORG = "11111111-1111-4111-8111-111111111111"
SITE = "22222222-2222-4222-8222-222222222222"
DRAFTER = "33333333-3333-4333-8333-333333333333"
ACTOR = "44444444-4444-4444-8444-444444444444"


def new_draft(c, gw_request_id="gw-0001", review_level="L1", status="reference",
              edit_ratio_bp=0, ai_content=None, current_content=None, **kw):
    """插入一条最小合法草稿，返回 id。"""
    d = dict(
        id=str(kw.get("id") or uuid.uuid4()),
        org_id=ORG, site_id=SITE, feature_code="feat-article",
        review_level=review_level, status=status, source_type="activity",
        source_id="66666666-6666-4666-8666-666666666666",
        title="虚构草稿标题", ai_content=Jsonb(ai_content or {"text": "AI 原稿"}),
        current_content=Jsonb(current_content or {"text": "AI 原稿"}),
        schema_id="schema-article", schema_version=1, model="hunyuan-hy",
        gw_request_id=gw_request_id, drafter_id=DRAFTER,
        edit_ratio_bp=edit_ratio_bp,
    )
    cols = ",".join(d.keys())
    ph = ",".join("%s" for _ in d)
    c.execute(f"INSERT INTO ai_drafts ({cols}) VALUES ({ph})", tuple(d.values()))
    return d["id"]


def new_action(c, draft_id, action="create", **kw):
    d = dict(
        draft_id=draft_id, org_id=ORG, site_id=SITE, action=action,
        actor_id=ACTOR, from_status=kw.get("from_status"),
        to_status=kw.get("to_status"), comment=kw.get("comment"),
        content_sha256=kw.get("content_sha256"),
    )
    cols = ",".join(d.keys())
    ph = ",".join("%s" for _ in d)
    c.execute(f"INSERT INTO ai_review_actions ({cols}) VALUES ({ph})", tuple(d.values()))
