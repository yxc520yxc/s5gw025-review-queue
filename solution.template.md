# S5-GW-025 交付说明 v1 — AI 草稿与复核动作表：状态枚举、只追加日志与迁移

> 状态：实现完成、本地全量验证通过（`VERIFY_PASS`）；测试数据均为虚构，不含真实个人信息或客户名称。

## 1. 需求匹配（本单目标 → 实现）

| 工单目标 | 实现 | 代码 |
|---|---|---|
| 复核队列两张表与迁移 | `ai_drafts`（主表，含统一字段/软删除）+ `ai_review_actions`（只追加审计表）+ Alembic 迁移 | `models.py`、`0001_review_queue.py` |
| 7 状态枚举 + 10 动作枚举 + 含义 | `CHECK` 约束 + Python 枚举（单一来源），README 列表 | `enums.py`、README §4/§5 |
| 动作表只允许插入 | BEFORE UPDATE/DELETE 触发器直接抛 `InsufficientPrivilege` | `fn_review_actions_append_only` |
| AI 原稿写入后只读 | BEFORE UPDATE 触发器拒绝改 `ai_content`，`current_content` 可改 | `fn_ai_drafts_content_immutable` |
| 幂等键 | `gw_request_id` 唯一约束，重复插入抛 `UniqueViolation` | `uq_ai_drafts_gw_request_id` |
| 3 个指定索引 | `(org_id,site_id,status,review_level)`、`(drafter_id,status)`、`(feature_code,created_at)` | DDL + 模型双端 |
| 迁移可复跑/无漂移/可回退 | 显式 DDL 与 ORM 对齐；upgrade/check/downgrade 实测 | `RUN_LOG.txt` |

`ai_drafts` 完整字段：`feature_code`、`review_level`、`status`、`source_type`+`source_id`、`title`、
`ai_content`(JSONB 只读)、`current_content`(JSONB)、`schema_id`、`schema_version`、`model`、
`gw_request_id`(唯一幂等键)、`drafter_id`/`reviewer_id`/`approver_id`、`submitted_at`/`approved_at`/
`effective_at`/`expires_at`、`edit_ratio_bp`(0–10000)、`adopted`、`lock_version`(乐观锁)，
以及统一字段 `id`(UUID)/`org_id`/`site_id`/`created_at`/`created_by`/`updated_at`/`deleted_at`。
`ai_review_actions` 字段：`draft_id`、`action`、`actor_id`、`from_status`、`to_status`、`comment`、
`content_sha256`、`created_at`（追加型审计表，刻意不套 `updated_at`/`deleted_at`，永不改永不删）。

## 2. 依赖与实施路径

| 项 | 要求 | 实测 |
|---|---|---|
| Python / DB | 3.11+ / PostgreSQL 16 | 3.13.12 / `server_version 160015`（16.15） |
| Python 包 | requirements.txt | SQLAlchemy 2.1.8 / alembic 1.20.1 / psycopg 3.3.9 / pytest 9.2.0 |
| 外部访问 | **无**：无客户云、私有仓库、第三方 SDK、境外服务 | — |
| 配置 | 只从环境变量读 | 仅 `REVIEW_DB_DSN`（连接串），不写入代码 |

实施：① `alembic upgrade head` 建两表 + 3 索引 + 唯一约束 + CHECK + 2 触发器函数；
② 应用写入草稿时带 `gw_request_id`，重复请求被唯一约束挡住；
③ 每次状态流转 INSERT 一条 `ai_review_actions`，此后不可改；
④ `ai_content` 由触发器锁定，`current_content` 承载人工修改并记录 `edit_ratio_bp`。
本单「依赖：无」，不调用相邻模块；`source_type`/`source_id` 仅作逻辑外键，便于解耦。

## 3. 验收标准逐条对照（4 条全部有真用例）

| # | 工单验收标准 | 复现命令 | 实际结果 |
|---|---|---|---|
| 1 | `alembic upgrade head`/`downgrade -1` 成功、`check` 无漂移 | `verify.cmd` | 两向迁移 `exit=0`；`check` → `No new upgrade operations detected.`；downgrade 后业务对象全清零；再 upgrade+check 仍通过 |
| 2 | UPDATE/DELETE `ai_review_actions` 被触发器拒绝 | `pytest -q -k append_only` | 两种操作均抛 `InsufficientPrivilege`、行数不变；INSERT 正常；触发器存在 |
| 3 | 重复 `gw_request_id` 插入抛唯一约束错误 | `pytest -q -k "duplicate or gw_request_id"` | 抛 `UniqueViolation`，不同 gw 正常 |
| 4 | README 列出 7 status / 10 action 及含义 | README §4/§5 | 全部列出并注明含义（见下方源码内嵌） |

## 4. 关键源码内嵌（platform_only，离线可审）

`app/review/enums.py`（枚举单一来源，节选）：

```python
class DraftStatus(str, Enum):
    REFERENCE = "reference"                 # AI 生成、仅参考，未进入复核
    PENDING_REVIEW = "pending_review"       # 等待复核人复核
    PENDING_APPROVAL = "pending_approval"   # 复核通过，等待审批人（L2）
    EFFECTIVE = "effective"                 # 已生效
    REJECTED = "rejected"                   # 驳回
    WITHDRAWN = "withdrawn"                 # 发起人撤回
    EXPIRED = "expired"                     # 超期失效

class ReviewAction(str, Enum):
    CREATE = "create"                       # 生成草稿
    EDIT = "edit"                           # 人工修改 current_content
    CONFIRM = "confirm"                     # 复核人确认（L1 生效）
    SUBMIT_FOR_APPROVAL = "submit_for_approval"  # 提交审批（L2）
    APPROVE = "approve"                     # 审批人批准（L2 生效）
    RETURN = "return"                       # 退回修改
    REJECT = "reject"                       # 驳回
    WITHDRAW = "withdraw"                   # 撤回
    EXPIRE = "expire"                       # 系统置过期
    ADOPT = "adopt"                         # L0 标记采纳
```

`0001_review_queue.py`（只追加触发器，核心）：

```sql
CREATE OR REPLACE FUNCTION fn_review_actions_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION USING
        MESSAGE = 'ai_review_actions is append-only: ' || TG_OP || ' not allowed',
        ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_review_actions_append_only
BEFORE UPDATE OR DELETE ON ai_review_actions
FOR EACH ROW EXECUTE FUNCTION fn_review_actions_append_only();
```

`0001_review_queue.py`（幂等唯一键 + 3 索引）：

```sql
CONSTRAINT uq_ai_drafts_gw_request_id UNIQUE (gw_request_id)
CREATE INDEX ix_ai_drafts_org_site_status_level ON ai_drafts (org_id, site_id, status, review_level);
CREATE INDEX ix_ai_drafts_drafter_status ON ai_drafts (drafter_id, status);
CREATE INDEX ix_ai_drafts_feature_created ON ai_drafts (feature_code, created_at);
```

## 5. 可复现验证（真实运行，非模拟）

`verify.cmd` 一条命令复跑全部（`PY` 指定解释器、`REVIEW_DB_DSN` 指向任意 PG16 空库，会重置 public schema）。
依次实跑：`upgrade head` → `check` → `downgrade -1`（对象清零）→ 再 `upgrade` → 再 `check` → `pytest -q`。

`RUN_LOG.txt` 摘录（真实输出）：

```
server_version: 160015
Running upgrade  -> 0001_review_queue
No new upgrade operations detected.
Running downgrade 0001_review_queue -> 
ai_* 表=0  业务索引/约束=0  函数=0  序列=0
DOWNGRADE_CLEAN: PASS
42 passed in 8.57s
RESULT: VERIFY_PASS
```

## 6. 边界与说明

- 只追加由**数据库触发器**强制，非应用层约定；篡改审计日志在数据库层失败。
- `ai_content` 写入后只读；人工修改只落 `current_content`，AI 原稿永久保留。
- `ai_drafts` 用 `deleted_at` 软删除；动作表为追加型审计表，不套 `updated_at`/`deleted_at`。
- `lock_version` 默认 1，应用层用 `WHERE id=? AND lock_version=?` 做乐观锁。
- 测试与示例中所有 UUID、标题均为固定虚构占位，无真实个人信息或企业信息，交付物不含客户名称。
- 附完整源码包（含全部代码、迁移、测试、README、RUN_LOG，可离线复跑）。
