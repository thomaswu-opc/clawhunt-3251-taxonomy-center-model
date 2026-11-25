"""taxonomy dict / tag / tag_history

Revision ID: 0001_taxonomy_center
Revises:
Create Date: 2026-11-25

三张表一次建好：字典、标签、变更历史。约束都在库里（唯一、CHECK、外键级联），
跨表的「层级不超过字典 max_depth」由仓储层校验，见 taxonomy_center/validate.py。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_taxonomy_center"
down_revision = None
branch_labels = None
depends_on = None

UUIDType = sa.Uuid().with_variant(postgresql.UUID(as_uuid=True), "postgresql")
JSONType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _common_columns() -> list:
    return [
        sa.Column("org_id", UUIDType, nullable=False),
        sa.Column("site_id", UUIDType, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", UUIDType, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "taxonomy_dict",
        sa.Column("id", UUIDType, primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("allow_multi", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("max_depth", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("owner_role", sa.String(64), nullable=False, server_default="knowledge_admin"),
        *_common_columns(),
        sa.UniqueConstraint("org_id", "code", name="uq_taxonomy_dict_code"),
        sa.CheckConstraint("max_depth BETWEEN 1 AND 6", name="ck_taxonomy_dict_max_depth"),
    )
    op.create_index("ix_taxonomy_dict_org", "taxonomy_dict", ["org_id"])

    op.create_table(
        "taxonomy_tag",
        sa.Column("id", UUIDType, primary_key=True),
        sa.Column("dict_id", UUIDType, sa.ForeignKey("taxonomy_dict.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("parent_id", UUIDType, sa.ForeignKey("taxonomy_tag.id", ondelete="SET NULL"), nullable=True),
        sa.Column("path", sa.String(512), nullable=False, server_default=""),
        sa.Column("level", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("aliases", JSONType, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("definition", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("merged_into_id", UUIDType, sa.ForeignKey("taxonomy_tag.id", ondelete="SET NULL"), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        *_common_columns(),
        sa.UniqueConstraint("dict_id", "code", name="uq_taxonomy_tag_dict_code"),
        sa.CheckConstraint("level >= 1", name="ck_taxonomy_tag_level_positive"),
        sa.CheckConstraint(
            "status IN ('draft','pending_review','active','deprecated','merged')",
            name="ck_taxonomy_tag_status"),
        sa.CheckConstraint("version >= 1", name="ck_taxonomy_tag_version_positive"),
    )
    op.create_index("ix_taxonomy_tag_dict_status", "taxonomy_tag", ["dict_id", "status"])
    op.create_index("ix_taxonomy_tag_path", "taxonomy_tag", ["path"])

    op.create_table(
        "taxonomy_tag_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tag_id", UUIDType, sa.ForeignKey("taxonomy_tag.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("before", JSONType, nullable=True),
        sa.Column("after", JSONType, nullable=True),
        sa.Column("changed_by", UUIDType, nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("action IN ('create','update','deprecate','merge')",
                           name="ck_taxonomy_tag_history_action"),
    )
    op.create_index("ix_taxonomy_tag_history_tag", "taxonomy_tag_history", ["tag_id", "changed_at"])


def downgrade() -> None:
    op.drop_index("ix_taxonomy_tag_history_tag", table_name="taxonomy_tag_history")
    op.drop_table("taxonomy_tag_history")
    op.drop_index("ix_taxonomy_tag_path", table_name="taxonomy_tag")
    op.drop_index("ix_taxonomy_tag_dict_status", table_name="taxonomy_tag")
    op.drop_table("taxonomy_tag")
    op.drop_index("ix_taxonomy_dict_org", table_name="taxonomy_dict")
    op.drop_table("taxonomy_dict")
