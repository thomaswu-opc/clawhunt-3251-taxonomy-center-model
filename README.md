# 字典与标签中心：数据模型与迁移 · #3251

对应工单 **S2-TAX-001**。范围：三张表的数据模型 + Alembic 迁移 + 单元测试 + 表结构说明。
标签的业务接口（增删改、合并流程）属于 S2-TAX-002，不在本单内。

## 1. 目录

```
taxonomy_center/models.py      SQLAlchemy 2.0 声明式模型（Dict / Tag / TagHistory）
taxonomy_center/validate.py    跨表校验：层级 ≤ max_depth、path 与 level 一致、状态机合法性
taxonomy_center/repository.py  唯一写入口：校验 → add → flush → 写历史
alembic/versions/0001_taxonomy_center.py   三张表 + 约束 + 索引
tests/test_acceptance.py       8 个用例（含真跑 alembic 的 upgrade / downgrade -1）
```

## 2. 表结构说明

### 2.1 `taxonomy_dict`（字典）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | uuid | PK | |
| `org_id` | uuid | NOT NULL | 多租户隔离 |
| `site_id` | uuid | NULL | 站点级字典才填 |
| `code` | varchar(64) | NOT NULL，`uq_taxonomy_dict_code (org_id, code)` | 如 `industry`、`demand_type` |
| `name` | varchar(128) | NOT NULL | 中文名 |
| `description` | text | NULL | |
| `allow_multi` | boolean | NOT NULL，默认 true | 一个对象能否打多个标签 |
| `max_depth` | integer | NOT NULL，默认 3，`CHECK 1..6` | 该字典下标签的最大层级 |
| `owner_role` | varchar(64) | NOT NULL，默认 `knowledge_admin` | 谁可以改 |
| `created_at/created_by/updated_at/deleted_at` | timestamptz/uuid | 按统一约定 | 软删除 |

### 2.2 `taxonomy_tag`（标签）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | uuid | PK | |
| `dict_id` | uuid | FK → `taxonomy_dict.id`，`ON DELETE CASCADE` | |
| `code` | varchar(64) | NOT NULL，**`uq_taxonomy_tag_dict_code (dict_id, code)`** | 同一字典内唯一；不同字典可重名 |
| `name` | varchar(128) | NOT NULL | |
| `parent_id` | uuid | FK → 自身，`ON DELETE SET NULL` | 根节点为 NULL |
| `path` | varchar(512) | NOT NULL，`ix_taxonomy_tag_path` | 形如 `A/A01/A0101`，由父路径推导 |
| `level` | integer | NOT NULL，默认 1，`CHECK >= 1` | 段数 = level |
| `aliases` | jsonb | NOT NULL，默认 `[]` | 别名，供搜索召回 |
| `definition` | text | NULL | 定义（与 name 区分） |
| `status` | varchar(16) | NOT NULL，默认 `draft`，`CHECK IN (5 态)` | draft / pending_review / active / deprecated / merged |
| `merged_into_id` | uuid | FK → 自身，`ON DELETE SET NULL` | 仅 merged 态使用 |
| `sort_order` | integer | NOT NULL，默认 0 | 同级排序 |
| `version` | integer | NOT NULL，默认 1，`CHECK >= 1` | 每次变更 +1，供事件与缓存失效 |

索引：`ix_taxonomy_tag_dict_status (dict_id, status)`（列表页与清理扫描）、`ix_taxonomy_tag_path`（子树查询）。

### 2.3 `taxonomy_tag_history`（变更历史，append-only）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigserial | PK | |
| `tag_id` | uuid | FK → `taxonomy_tag.id`，`ON DELETE CASCADE` | |
| `action` | varchar(16) | `CHECK IN ('create','update','deprecate','merge')` | |
| `version` | integer | NOT NULL | 与标签当时的 version 对齐 |
| `before` / `after` | jsonb | NULL | 整行快照，可回滚、可审计 |
| `changed_at` | timestamptz | NOT NULL，默认 now() | `ix_taxonomy_tag_history_tag (tag_id, changed_at)` |

## 3. 两条约束落在哪一层（关键设计）

| 约束 | 落点 | 原因 |
|---|---|---|
| 同一字典内 `code` 唯一 | **数据库** `uq_taxonomy_tag_dict_code` | 应用层查重有并发竞态；唯一索引是唯一可靠解法 |
| `status` 五态、`max_depth` 范围、`version >= 1` | **数据库** CHECK | 枚举与范围是单表规则，放库里对所有人一致生效 |
| `level <= dict.max_depth` | **仓储层** `validate.ensure_depth` | 跨表规则，CHECK 无法引用另一张表；抛 `DepthExceeded(level, max_depth)`，调用方映射 422 |
| `path` 段数 = `level` | **仓储层** `validate.ensure_path` | 同上，避免"层级对但路径错"的脏数据 |
| `merged` 必须带 `merged_into_id` | **仓储层** `validate.ensure_status` | 条件约束，PG 的 CHECK 无法跨行表达 |

写入顺序固定为 **校验 → add → flush**：跨表规则在 flush 前拦下（异常类型明确，不解析数据库报错文本），单表约束由 flush 触发 `IntegrityError` 兜底。

## 4. 运行与测试

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -t . -v      # 8 passed，含真跑 alembic 的 AC-1

# AC-1 用手跑一遍也成立（sqlite 即可，无需 PG）
DATABASE_URL='sqlite:///./tax.db' alembic upgrade head
DATABASE_URL='sqlite:///./tax.db' alembic downgrade -1

# 生产方言的 DDL
DATABASE_URL='postgresql+psycopg://u:p@localhost:5432/tax' alembic upgrade head --sql
```

## 5. 验收对照

| # | 验收原文 | 用例 | 实测 |
|---|---|---|---|
| 1 | alembic upgrade head 与 downgrade -1 均成功 | `test_ac1_upgrade_head_and_downgrade_minus_one` | 真跑三次：upgrade → 三张表 + alembic_version 存在；downgrade -1 → 只剩 alembic_version；再 upgrade → 恢复 |
| 2 | 同一字典下 code 重复插入失败（测试通过） | `test_ac2_same_dict_duplicate_code_fails`、`test_ac2_same_code_in_other_dict_allowed` | 重复插入抛 `IntegrityError`，回滚后库里仍 1 条；不同字典的相同 code 允许（2 条） |
| 3 | 层级超过 max_depth 时保存失败（测试通过） | `test_ac3_level_over_max_depth_fails`、`test_ac3_path_matches_level` | max_depth=2 时第 3 层抛 `DepthExceeded(level=3, max_depth=2)`，回滚后标签数 0；path 段数必须等于 level |

## 6. 边界与取舍

- **UUID 主键 + 软删除**：按统一技术约定；历史表用 bigserial（高频写入，无需全局唯一）。
- **`path` 物化而非递归查询**：字典深度 ≤ 6，物化路径让子树查询变成 `LIKE 'A/A01/%'`，比递归 CTE 稳。
- **历史存整行快照**：jsonb 快照比字段级 diff 冗余，但能直接回滚与审计，且 schema 演进不用改历史表。
- **不建触发器**：历史由仓储层写，避免"绕过应用直接改库"时产生无法追溯的变更；这条在 README 里写明，集成方需遵守。

## 7. 不在本单范围

标签增删改接口与合并流程（S2-TAX-002）、字典变更事件发布（S2-TAX-022）、
标签使用统计（S2-TAX-013）、前端管理后台。
