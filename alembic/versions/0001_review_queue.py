# -*- coding: utf-8 -*-
"""S5-GW-025 初始 schema：ai_drafts / ai_review_actions，显式 DDL（PostgreSQL 16）。

DDL 与 app/review/models.py 双端一致（默认值、CHECK、唯一约束、索引名全部对齐），
保证 `alembic check` 不报漂移。downgrade 触发器 / 函数 / 表全部删干净。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0001_review_queue"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STATUSES = ("reference", "pending_review", "pending_approval",
            "effective", "rejected", "withdrawn", "expired")
ACTIONS = ("create", "edit", "confirm", "submit_for_approval", "approve",
           "return", "reject", "withdraw", "expire", "adopt")
LEVELS = ("L0", "L1", "L2")


def _in(values: tuple[str, ...]) -> str:
    return ",".join("'%s'" % v for v in values)


def upgrade() -> None:
    # ---------------- ai_drafts ----------------
    op.execute(f"""
        CREATE TABLE ai_drafts (
            id             UUID PRIMARY KEY,
            org_id         UUID NOT NULL,
            site_id        UUID NOT NULL,
            feature_code   VARCHAR(64) NOT NULL,
            review_level   VARCHAR(4)  NOT NULL DEFAULT 'L1',
            status         VARCHAR(24) NOT NULL DEFAULT 'reference',
            source_type    VARCHAR(32) NOT NULL,
            source_id      UUID NOT NULL,
            title          VARCHAR(255) NOT NULL,
            ai_content     JSONB NOT NULL,
            current_content JSONB NOT NULL,
            schema_id      VARCHAR(64) NOT NULL,
            schema_version INTEGER NOT NULL,
            model          VARCHAR(64) NOT NULL,
            gw_request_id  VARCHAR(128) NOT NULL,
            drafter_id     UUID NOT NULL,
            reviewer_id    UUID NULL,
            approver_id    UUID NULL,
            submitted_at   TIMESTAMPTZ NULL,
            approved_at    TIMESTAMPTZ NULL,
            effective_at   TIMESTAMPTZ NULL,
            expires_at     TIMESTAMPTZ NULL,
            edit_ratio_bp  INTEGER NOT NULL DEFAULT 0,
            adopted        BOOLEAN NOT NULL DEFAULT false,
            lock_version   INTEGER NOT NULL DEFAULT 1,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_by     UUID NULL,
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at     TIMESTAMPTZ NULL,
            CONSTRAINT ck_ai_drafts_status
                CHECK (status IN ({_in(STATUSES)})),
            CONSTRAINT ck_ai_drafts_review_level
                CHECK (review_level IN ({_in(LEVELS)})),
            CONSTRAINT ck_ai_drafts_edit_ratio_bp
                CHECK (edit_ratio_bp BETWEEN 0 AND 10000),
            CONSTRAINT uq_ai_drafts_gw_request_id UNIQUE (gw_request_id)
        )
    """)
    op.execute("CREATE INDEX ix_ai_drafts_org_site_status_level "
               "ON ai_drafts (org_id, site_id, status, review_level)")
    op.execute("CREATE INDEX ix_ai_drafts_drafter_status "
               "ON ai_drafts (drafter_id, status)")
    op.execute("CREATE INDEX ix_ai_drafts_feature_created "
               "ON ai_drafts (feature_code, created_at)")

    # ---------------- ai_review_actions（只追加） ----------------
    op.execute("""
        CREATE TABLE ai_review_actions (
            id            BIGSERIAL PRIMARY KEY,
            draft_id      UUID NOT NULL REFERENCES ai_drafts(id) ON DELETE RESTRICT,
            org_id        UUID NOT NULL,
            site_id       UUID NOT NULL,
            action        VARCHAR(24) NOT NULL,
            actor_id      UUID NOT NULL,
            from_status   VARCHAR(24) NULL,
            to_status     VARCHAR(24) NULL,
            comment       TEXT NULL,
            content_sha256 VARCHAR(64) NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_by    UUID NULL,
            CONSTRAINT ck_ai_review_actions_action
                CHECK (action IN (
                    'create','edit','confirm','submit_for_approval','approve',
                    'return','reject','withdraw','expire','adopt'))
        )
    """)
    op.execute("CREATE INDEX ix_ai_review_actions_draft "
               "ON ai_review_actions (draft_id)")
    op.execute("CREATE INDEX ix_ai_review_actions_org_site_created "
               "ON ai_review_actions (org_id, site_id, created_at)")

    # ---- 触发器 1：ai_review_actions 只读追加，拒绝 UPDATE / DELETE ----
    # 独立函数（非内联 DO），便于 downgrade 精确 DROP，且错误信息带操作名。
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_review_actions_append_only()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION USING
                MESSAGE = 'ai_review_actions is append-only: ' || TG_OP || ' not allowed',
                ERRCODE = 'insufficient_privilege';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_review_actions_append_only
        BEFORE UPDATE OR DELETE ON ai_review_actions
        FOR EACH ROW EXECUTE FUNCTION fn_review_actions_append_only()
    """)

    # ---- 触发器 1b：行级触发器拦不住 TRUNCATE，补语句级触发器 ----
    # TRUNCATE 不触发 ROW 级 BEFORE UPDATE/DELETE，故单独加 STATEMENT 级 BEFORE TRUNCATE。
    op.execute("""
        CREATE TRIGGER trg_review_actions_no_truncate
        BEFORE TRUNCATE ON ai_review_actions
        FOR EACH STATEMENT EXECUTE FUNCTION fn_review_actions_append_only()
    """)

    # ---- 触发器 2：ai_drafts.ai_content 写入后只读（需求明确要求） ----
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_ai_drafts_content_immutable()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.ai_content IS DISTINCT FROM OLD.ai_content THEN
                RAISE EXCEPTION
                    'ai_drafts.ai_content is read-only after write'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_ai_drafts_content_immutable
        BEFORE UPDATE ON ai_drafts
        FOR EACH ROW EXECUTE FUNCTION fn_ai_drafts_content_immutable()
    """)


def downgrade() -> None:
    # 先删触发器与函数，再按外键依赖反向删表；序列随 BIGSERIAL 列删除。
    op.execute("DROP TRIGGER IF EXISTS trg_ai_drafts_content_immutable ON ai_drafts")
    op.execute("DROP TRIGGER IF EXISTS trg_review_actions_no_truncate ON ai_review_actions")
    op.execute("DROP TRIGGER IF EXISTS trg_review_actions_append_only ON ai_review_actions")
    op.execute("DROP FUNCTION IF EXISTS fn_ai_drafts_content_immutable()")
    op.execute("DROP FUNCTION IF EXISTS fn_review_actions_append_only()")
    op.execute("DROP TABLE IF EXISTS ai_review_actions CASCADE")
    op.execute("DROP TABLE IF EXISTS ai_drafts CASCADE")
