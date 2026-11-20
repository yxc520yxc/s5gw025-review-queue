# -*- coding: utf-8 -*-
"""S5-GW-025 枚举定义：7 个 status / 10 个 action / 3 个 review_level。

每个枚举值都带中文含义，README 的枚举表由本文件生成（`make_enums_doc.py`），
保证文档与代码单一来源、不会漂移。取值与 models.py / DDL CHECK 完全一致。
"""
from __future__ import annotations

from enum import Enum


class DraftStatus(str, Enum):
    """ai_drafts.status —— 草稿生命周期的 7 个状态。"""

    REFERENCE = "reference"                 # AI 生成、仅作参考，未进入复核
    PENDING_REVIEW = "pending_review"       # 已提交，等待复核人（reviewer）复核
    PENDING_APPROVAL = "pending_approval"   # 复核通过，等待审批人（approver）审批（L2）
    EFFECTIVE = "effective"                 # 审批通过并已生效
    REJECTED = "rejected"                   # 复核或审批驳回
    WITHDRAWN = "withdrawn"                 # 发起人撤回
    EXPIRED = "expired"                     # 超过 expires_at 自动过期、不再生效

    @classmethod
    def values(cls) -> tuple[str, ...]:
        return tuple(x.value for x in cls)


class ReviewAction(str, Enum):
    """ai_review_actions.action —— 只允许追加的 10 种复核动作。"""

    CREATE = "create"                       # 生成草稿（写入 ai_content 原稿）
    EDIT = "edit"                           # 人工修改 current_content
    CONFIRM = "confirm"                     # 复核人确认内容（L1 到此生效）
    SUBMIT_FOR_APPROVAL = "submit_for_approval"  # 提交给审批人（L2）
    APPROVE = "approve"                     # 审批人批准
    RETURN = "return"                       # 退回发起人修改
    REJECT = "reject"                       # 驳回草稿
    WITHDRAW = "withdraw"                   # 发起人撤回
    EXPIRE = "expire"                       # 系统按 expires_at 置为过期
    ADOPT = "adopt"                         # L0 草稿被标记采纳

    @classmethod
    def values(cls) -> tuple[str, ...]:
        return tuple(x.value for x in cls)


class ReviewLevel(str, Enum):
    """ai_drafts.review_level —— 复核强度。"""

    L0 = "L0"   # 低风险：AI 生成后可直接标记采纳（adopt），无需人工复核
    L1 = "L1"   # 中风险：需 1 名复核人 confirm 后生效
    L2 = "L2"   # 高风险：需复核人 confirm + 审批人 approve 两步后生效

    @classmethod
    def values(cls) -> tuple[str, ...]:
        return tuple(x.value for x in cls)


STATUSES = DraftStatus.values()
ACTIONS = ReviewAction.values()
REVIEW_LEVELS = ReviewLevel.values()
LEVELS = REVIEW_LEVELS  # 别名，测试与文档共用
