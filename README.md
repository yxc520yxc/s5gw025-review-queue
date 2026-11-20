# 【S5-GW-025】会员空间平台 · AI 草稿与复核动作表：状态枚举、只追加日志与迁移

> 交付版本 v1 · 全部代码可离线复跑 · 测试数据均为虚构，不含任何真实个人信息或客户名称

## 0. 需求匹配（本单要做什么）

新增复核队列两张表与迁移：`ai_drafts`（AI 草稿主表）与 `ai_review_actions`（只追加复核动作日志）。
前者承载 7 状态生命周期、3 档复核强度、乐观锁与幂等键；后者以数据库触发器强制「只插入、不改不删」，
为每一次状态流转留下不可篡改的审计轨迹。

| 客户目标 | 本交付的做法 | 代码位置 |
|---|---|---|
| 草稿有明确状态机、7 状态可枚举 | `status` 列 + `CHECK` 约束 + `DraftStatus` 枚举，取值与文档单一来源 | `app/review/enums.py`、`0001_review_queue.py` |
| 复核动作不可篡改、可审计 | `ai_review_actions` 表 + BEFORE UPDATE/DELETE 行级触发器 + BEFORE TRUNCATE 语句级触发器直接抛异常 | `0001_review_queue.py::fn_review_actions_append_only` |
| AI 原稿写入后只读 | `ai_content` 列 + BEFORE UPDATE 触发器拒绝改动（当前稿 `current_content` 可改） | `0001_review_queue.py::fn_ai_drafts_content_immutable` |
| 生成幂等、不重复落库 | `gw_request_id` 唯一约束，重复插入抛 `UniqueViolation` | `uq_ai_drafts_gw_request_id` |
| 修改幅度可量化 / 乐观锁 | `edit_ratio_bp`（0–10000 基点）+ `lock_version`（应用层用 `WHERE lock_version=?` 更新） | `app/review/models.py` |
| 迁移可复跑、无漂移、可回退 | 显式 DDL 与 ORM 双端对齐；`upgrade`/`downgrade`/`check` 全部实测通过 | `alembic/versions/0001_review_queue.py`、`RUN_LOG.txt` |

## 1. 依赖与访问要求（全部本地可满足）

| 类别 | 要求 | 本交付取值 |
|---|---|---|
| Python | 3.11+ | 实测 3.13.12 |
| 数据库 | PostgreSQL 16（JSONB / pgvector 兼容） | 实测 `server_version 160015`（16.15） |
| Python 包 | 见 `requirements.txt` | SQLAlchemy 2.1.8 / alembic 1.20.1 / psycopg 3.3.9 / pytest 9.2.0 |
| 外部账号 / 密钥 | **无** | 不需要客户云、第三方 SDK、境外服务 |
| 配置 | 密钥只从环境变量读取 | 仅 `REVIEW_DB_DSN`（数据库连接串），不写入代码 |
| 仓库访问 | 无 | `repo_access=none`，本交付为独立可复跑工程 |

**对接点（工单允许用桩）**：本单「依赖：无」，不调用任何相邻模块接口。
`source_type` / `source_id` 记录关联的业务对象（如活动、会议），仅作逻辑外键，
不建跨模块物理外键，便于与相邻工单解耦。

## 2. 实施路径与文件清单

```
app/review/enums.py                  7 status / 10 action / 3 review_level 枚举与中文含义（单一来源）
app/review/models.py                 SQLAlchemy 2 两张表模型（CHECK / 唯一约束 / 索引双端声明）
alembic/versions/0001_review_queue.py 显式 DDL：建表 + 3 索引 + 2 触发器函数（3 个触发器：UPDATE/DELETE 行级、TRUNCATE 语句级、ai_content 只读）+ downgrade 反操作
alembic/env.py                       连接串只从 REVIEW_DB_DSN 读取
alembic.ini / script.py.mako         alembic 标准脚手架
tests/conftest.py                    函数级数据隔离 + 虚构数据夹具
tests/test_review.py                 42 个用例，逐条对齐 4 条验收标准 + 边界（含 TRUNCATE 拒绝）
run_verify.py / verify.cmd           一键复跑全部命令并生成 RUN_LOG.txt
RUN_LOG.txt                          真实命令输出（非模拟）
requirements.txt                     运行依赖
```

## 3. 验收标准逐条对照

| # | 工单验收标准 | 复现命令 | 实际结果（详见 RUN_LOG.txt） |
|---|---|---|---|
| 1 | `alembic upgrade head` 与 `downgrade -1` 均成功，`alembic check` 无漂移 | `verify.cmd`（内含 4 步 alembic） | `upgrade head` exit=0；`check` → `No new upgrade operations detected.`；`downgrade -1` 后对象清零；再 `upgrade`+`check` 仍通过 |
| 2 | 对 `ai_review_actions` 执行 UPDATE 或 DELETE 被触发器拒绝 | `pytest -q -k "append_only"` | UPDATE / DELETE 均抛 `InsufficientPrivilege`，行数不变；INSERT 正常。触发器 `trg_review_actions_append_only` 存在 |
| 3 | 重复 `gw_request_id` 插入 `ai_drafts` 抛唯一约束错误 | `pytest -q -k "gw_request_id or duplicate"` | 重复 gw 抛 `UniqueViolation`；不同 gw 正常插入 |
| 4 | README 列出 7 个 status 与 10 个 action 枚举及含义 | 见下方 §4、§5 | 7 状态 + 10 动作 + 3 复核级别全部列出并注明含义 |

附加：`alembic check` 无漂移证明 ORM 与 DDL 完全一致；`ai_content` 写入后只读触发器、
`edit_ratio_bp` 范围、`review_level` CHECK、外键 RESTRICT 均有独立真用例覆盖。

## 4. 枚举一：`ai_drafts.status`（7 个状态）

| 值 | 含义 |
|---|---|
| `reference` | AI 生成、仅作参考，尚未进入人工复核（默认初始状态） |
| `pending_review` | 已提交，等待复核人（`reviewer_id`）复核 |
| `pending_approval` | 复核通过，等待审批人（`approver_id`）审批（L2 专用） |
| `effective` | 审批通过并已生效（L1 经 confirm / L2 经 approve 后到达） |
| `rejected` | 复核或审批驳回，不再生效 |
| `withdrawn` | 发起人主动撤回 |
| `expired` | 超过 `expires_at`，由定时任务置为过期、不再生效 |

## 5. 枚举二：`ai_review_actions.action`（10 个动作）

| 值 | 含义 |
|---|---|
| `create` | 生成草稿（写入 `ai_content` AI 原稿） |
| `edit` | 人工修改当前稿 `current_content`（`edit_ratio_bp` 记录修改幅度） |
| `confirm` | 复核人确认内容（L1 到此即可生效） |
| `submit_for_approval` | 提交给审批人（L2 进入审批环节） |
| `approve` | 审批人批准（L2 生效） |
| `return` | 退回发起人修改 |
| `reject` | 驳回草稿 |
| `withdraw` | 发起人撤回草稿 |
| `expire` | 系统按 `expires_at` 置为过期 |
| `adopt` | L0 草稿被标记采纳（`adopted=true`） |

## 6. 枚举三：`ai_drafts.review_level`（3 档复核强度）

| 值 | 含义 |
|---|---|
| `L0` | 低风险：AI 生成后可直接标记采纳（`adopt`），无需人工复核 |
| `L1` | 中风险：需 1 名复核人 `confirm` 后生效 |
| `L2` | 高风险：需复核人 `confirm` + 审批人 `approve` 两步后生效 |

## 7. 边界与约定

- **`ai_review_actions` 只追加**：UPDATE / DELETE 由 BEFORE ROW 触发器拒绝（非应用层约定），任何试图篡改审计日志的操作都会在数据库层失败。TRUNCATE 不走行级触发器，因此另加 **BEFORE TRUNCATE 语句级触发器 `trg_review_actions_no_truncate`** 一并拒绝（复用同一函数）。三层保护均抛 `insufficient_privilege`。注：测试夹具清理数据时会临时设 `session_replication_role=replica` 绕过触发器，仅用于隔离，业务代码无任何绕过路径。
- **`ai_content` 写入后只读**：由触发器保证；人工修改只落在 `current_content`，`ai_content` 永久保留 AI 原稿。
- **软删除**：`ai_drafts` 用 `deleted_at` 软删除；动作日志为追加型审计表，不套用 `updated_at` / `deleted_at`（永不修改、永不软删）。组织与站点归属可由 `draft_id → ai_drafts` 取得。
- **乐观锁**：`lock_version` 默认 1，应用层更新需带 `WHERE id=? AND lock_version=?`，受影响行数为 0 时说明已被他人修改。
- **幂等**：`gw_request_id` 为模型网关请求 ID，唯一约束保证同一请求不会重复生成草稿。
- **金额/配置**：本单无金额字段；不引入任何环境变量以外的配置或密钥，不引入向境外发送数据的 SDK。
- **虚构数据**：测试与示例中的 UUID、标题均为固定占位值，无真实个人信息或企业信息，交付物不出现客户名称。
