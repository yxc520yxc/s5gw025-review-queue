# -*- coding: utf-8 -*-
"""SQLAlchemy 2 模型：ai_drafts / ai_review_actions（对应工单 S5-GW-025）。

约束同时在 DDL（见 alembic/versions/0001_review_queue.py）与模型端声明，
保证 `alembic check` 不报漂移。ai_drafts 含统一公共字段与软删除；
ai_review_actions 为只追加审计表（数据库触发器拒绝 UPDATE / DELETE），
刻意不套用 updated_at / deleted_at —— 它永不修改、永不软删。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey,
                        Index, Integer, String, Text, UniqueConstraint, Uuid,
                        text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# 7 个 status / 10 个 action / 3 个 review_level —— 与 enums.py、DDL CHECK 保持一致
STATUSES = ("reference", "pending_review", "pending_approval",
            "effective", "rejected", "withdrawn", "expired")
ACTIONS = ("create", "edit", "confirm", "submit_for_approval", "approve",
           "return", "reject", "withdraw", "expire", "adopt")
REVIEW_LEVELS = ("L0", "L1", "L2")


class Base(DeclarativeBase):
    pass


class AiDraft(Base):
    """AI 草稿主表。ai_content 写入后只读（由 DDL 触发器保护）。"""
    __tablename__ = "ai_drafts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('reference','pending_review','pending_approval',"
            "'effective','rejected','withdrawn','expired')",
            name="ck_ai_drafts_status"),
        CheckConstraint("review_level IN ('L0','L1','L2')",
                        name="ck_ai_drafts_review_level"),
        CheckConstraint("edit_ratio_bp BETWEEN 0 AND 10000",
                        name="ck_ai_drafts_edit_ratio_bp"),
        UniqueConstraint("gw_request_id", name="uq_ai_drafts_gw_request_id"),
        Index("ix_ai_drafts_org_site_status_level",
              "org_id", "site_id", "status", "review_level"),
        Index("ix_ai_drafts_drafter_status", "drafter_id", "status"),
        Index("ix_ai_drafts_feature_created", "feature_code", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    feature_code: Mapped[str] = mapped_column(String(64), nullable=False)
    review_level: Mapped[str] = mapped_column(
        String(4), nullable=False, default="L1", server_default=text("'L1'"))
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="reference",
        server_default=text("'reference'"))
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    ai_content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    current_content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    schema_id: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    gw_request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    drafter_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    approver_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    edit_ratio_bp: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"))
    adopted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"))
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    actions: Mapped[list["AiReviewAction"]] = relationship(back_populates="draft")


class AiReviewAction(Base):
    """只追加动作日志。UPDATE / DELETE 由 DDL 触发器拒绝。"""
    __tablename__ = "ai_review_actions"
    __table_args__ = (
        CheckConstraint(
            "action IN ('create','edit','confirm','submit_for_approval','approve',"
            "'return','reject','withdraw','expire','adopt')",
            name="ck_ai_review_actions_action"),
        Index("ix_ai_review_actions_draft", "draft_id"),
        Index("ix_ai_review_actions_org_site_created", "org_id", "site_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    draft_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ai_drafts.id", ondelete="RESTRICT"), nullable=False)
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    draft: Mapped[AiDraft] = relationship(back_populates="actions")
