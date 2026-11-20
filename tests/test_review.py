# -*- coding: utf-8 -*-
"""S5-GW-025 验收测试：逐条对齐 4 条验收标准 + ai_content 只读 + 枚举完整性。

所有数据均为虚构（UUID 固定占位、标题"虚构草稿标题"），不含真实个人信息或企业信息。
"""
from __future__ import annotations

import uuid

import psycopg
from psycopg.types.json import Jsonb
import pytest

from app.review.enums import ACTIONS, LEVELS, STATUSES, DraftStatus, ReviewAction
from tests.conftest import ORG, SITE, new_action, new_draft


# ============ 验收 1：迁移结构（upgrade/downgrade/check 在 run_verify.py 验证） ============
def test_tables_and_indexes_exist(conn):
    names = {r[0] for r in conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()}
    assert {"ai_drafts", "ai_review_actions"} <= names
    idx = {r[0] for r in conn.execute(
        "SELECT indexname FROM pg_indexes WHERE schemaname='public'").fetchall()}
    for i in ("ix_ai_drafts_org_site_status_level", "ix_ai_drafts_drafter_status",
              "ix_ai_drafts_feature_created", "ix_ai_review_actions_draft",
              "ix_ai_review_actions_org_site_created", "uq_ai_drafts_gw_request_id"):
        assert i in idx, f"missing index/constraint {i}"


def test_column_types_are_jsonb_and_timestamptz(conn):
    t = conn.execute("""
        SELECT column_name, data_type FROM information_schema.columns
        WHERE table_name='ai_drafts' AND column_name IN ('ai_content','current_content','created_at')
    """).fetchall()
    got = dict(t)
    assert got["ai_content"] == "jsonb"
    assert got["current_content"] == "jsonb"
    assert got["created_at"] == "timestamp with time zone"


# ============ 验收 2：ai_review_actions 触发器拒绝 UPDATE / DELETE ============
def test_append_only_rejects_update(conn):
    did = new_draft(conn)
    new_action(conn, did)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("UPDATE ai_review_actions SET comment='篡改' WHERE draft_id=%s", (did,))


def test_append_only_rejects_delete(conn):
    did = new_draft(conn, "gw-del")
    new_action(conn, did)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("DELETE FROM ai_review_actions WHERE draft_id=%s", (did,))
    n = conn.execute("SELECT count(*) FROM ai_review_actions WHERE draft_id=%s", (did,)).fetchone()[0]
    assert n == 1, "DELETE 必须被拒绝，行数不变"


def test_append_only_allows_insert(conn):
    did = new_draft(conn, "gw-ins")
    new_action(conn, did, action="create")
    new_action(conn, did, action="confirm", from_status="reference", to_status="effective")
    n = conn.execute("SELECT count(*) FROM ai_review_actions WHERE draft_id=%s", (did,)).fetchone()[0]
    assert n == 2


def test_append_only_rejects_truncate(conn):
    """行级触发器拦不住 TRUNCATE，另由 BEFORE TRUNCATE 语句级触发器拒绝。"""
    did = new_draft(conn, "gw-trunc")
    new_action(conn, did)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("TRUNCATE TABLE ai_review_actions")
    n = conn.execute("SELECT count(*) FROM ai_review_actions WHERE draft_id=%s", (did,)).fetchone()[0]
    assert n == 1, "TRUNCATE 必须被拒绝，行数不变"


def test_append_only_trigger_exists(conn):
    r = conn.execute("""
        SELECT tgname FROM pg_trigger
        WHERE tgrelid='ai_review_actions'::regclass AND NOT tgisinternal
    """).fetchall()
    assert ("trg_review_actions_append_only",) in r
    assert ("trg_review_actions_no_truncate",) in r


# ============ 验收 3：重复 gw_request_id 触发唯一约束错误 ============
def test_duplicate_gw_request_id_rejected(conn):
    new_draft(conn, "gw-dup")
    with pytest.raises(psycopg.errors.UniqueViolation):
        new_draft(conn, "gw-dup", id=str(uuid.uuid4()))


def test_different_gw_request_id_ok(conn):
    new_draft(conn, "gw-a")
    new_draft(conn, "gw-b", id=str(uuid.uuid4()))
    n = conn.execute("SELECT count(*) FROM ai_drafts").fetchone()[0]
    assert n == 2


# ============ 验收 4：枚举完整性（README 由 enums.py 生成，此处验证代码口径） ============
def test_status_enum_has_7_values():
    assert len(STATUSES) == 7
    assert STATUSES == DraftStatus.values()


def test_action_enum_has_10_values():
    assert len(ACTIONS) == 10
    assert ACTIONS == ReviewAction.values()


@pytest.mark.parametrize("v", STATUSES)
def test_each_status_accepted_by_db_check(conn, v):
    new_draft(conn, f"gw-{v}", status=v, id=str(uuid.uuid4()))
    n = conn.execute("SELECT count(*) FROM ai_drafts WHERE status=%s", (v,)).fetchone()[0]
    assert n == 1


@pytest.mark.parametrize("v", ACTIONS)
def test_each_action_accepted_by_db_check(conn, v):
    did = new_draft(conn, f"gw-act-{v}", id=str(uuid.uuid4()))
    new_action(conn, did, action=v)


def test_invalid_status_rejected_by_check(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        new_draft(conn, "gw-bad-status", status="not_a_status")


def test_invalid_action_rejected_by_check(conn):
    did = new_draft(conn, "gw-bad-action")
    with pytest.raises(psycopg.errors.CheckViolation):
        new_action(conn, did, action="not_an_action")


# ============ 边界：review_level / edit_ratio_bp / ai_content 只读 ============
@pytest.mark.parametrize("v", LEVELS)
def test_review_level_accepted(conn, v):
    new_draft(conn, f"gw-lvl-{v}", review_level=v, id=str(uuid.uuid4()))


def test_invalid_review_level_rejected(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        new_draft(conn, "gw-bad-level", review_level="L3")


@pytest.mark.parametrize("v", [0, 1, 5000, 10000])
def test_edit_ratio_bp_bounds(conn, v):
    new_draft(conn, f"gw-bp-{v}", edit_ratio_bp=v, id=str(uuid.uuid4()))


@pytest.mark.parametrize("v", [-1, 10001])
def test_edit_ratio_bp_out_of_bounds_rejected(conn, v):
    with pytest.raises(psycopg.errors.CheckViolation):
        new_draft(conn, f"gw-bp-bad-{v}", edit_ratio_bp=v, id=str(uuid.uuid4()))


def test_ai_content_immutable_after_write(conn):
    """需求写明 ai_content 写入后只读；触发器拒绝修改，current_content 可改。"""
    did = new_draft(conn, "gw-immutable", ai_content={"text": "原稿不可改"},
                    current_content={"text": "当前稿"})
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("UPDATE ai_drafts SET ai_content=%s WHERE id=%s",
                     (Jsonb({"text": "篡改原稿"}), did))
    # 当前稿允许更新，且 AI 原稿未变
    conn.execute("UPDATE ai_drafts SET current_content=%s, lock_version=lock_version+1 WHERE id=%s",
                 (Jsonb({"text": "人工修改"}), did))
    row = conn.execute(
        "SELECT ai_content->>'text', current_content->>'text', lock_version "
        "FROM ai_drafts WHERE id=%s", (did,)).fetchone()
    assert row[0] == "原稿不可改"
    assert row[1] == "人工修改"
    assert row[2] == 2


def test_foreign_key_restricts_orphan_actions(conn):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        new_action(conn, str(uuid.uuid4()))
