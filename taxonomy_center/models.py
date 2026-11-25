"""字典与标签中心的数据模型（SQLAlchemy 2.0 声明式）。

三张表 + 两条硬约束：
- taxonomy_dict：字典。max_depth 决定该字典下标签的最大层级。
- taxonomy_tag：标签。同一字典内 code 唯一；level 由 path 推导；status 五态。
- taxonomy_tag_history：变更历史（append-only），记录 before/after 快照。

跨表约束（level <= dict.max_depth）数据库无法用 CHECK 表达，由
taxonomy_center/validate.py 在写入前校验；同字典 code 唯一与状态枚举走数据库约束兜底。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
UUIDType = sa.Uuid().with_variant(postgresql.UUID(as_uuid=True), "postgresql")

TAG_STATUS = ("draft", "pending_review", "active", "deprecated", "merged")


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Dict(Base):
    __tablename__ = "taxonomy_dict"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(UUIDType, nullable=False, default=_uuid)
    site_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    code: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    allow_multi: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=True)
    max_depth: Mapped[int] = mapped_column(sa.Integer(), nullable=False, default=3)
    owner_role: Mapped[str] = mapped_column(sa.String(64), nullable=False, default="knowledge_admin")

    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False, default=_now)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False, default=_now)
    deleted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    tags: Mapped[list["Tag"]] = relationship(back_populates="dict", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("org_id", "code", name="uq_taxonomy_dict_code"),
        CheckConstraint("max_depth BETWEEN 1 AND 6", name="ck_taxonomy_dict_max_depth"),
        Index("ix_taxonomy_dict_org", "org_id"),
    )


class Tag(Base):
    __tablename__ = "taxonomy_tag"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(UUIDType, nullable=False, default=_uuid)
    site_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    dict_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("taxonomy_dict.id", ondelete="CASCADE"),
                                               nullable=False)
    code: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("taxonomy_tag.id", ondelete="SET NULL"), nullable=True)
    path: Mapped[str] = mapped_column(sa.String(512), nullable=False, default="")
    level: Mapped[int] = mapped_column(sa.Integer(), nullable=False, default=1)
    aliases: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    definition: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="draft")
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("taxonomy_tag.id", ondelete="SET NULL"), nullable=True)
    sort_order: Mapped[int] = mapped_column(sa.Integer(), nullable=False, default=0)
    version: Mapped[int] = mapped_column(sa.Integer(), nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False, default=_now)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False, default=_now)
    deleted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    dict: Mapped[Dict] = relationship(back_populates="tags")

    __table_args__ = (
        UniqueConstraint("dict_id", "code", name="uq_taxonomy_tag_dict_code"),
        CheckConstraint("level >= 1", name="ck_taxonomy_tag_level_positive"),
        CheckConstraint(
            "status IN ('draft','pending_review','active','deprecated','merged')",
            name="ck_taxonomy_tag_status",
        ),
        CheckConstraint("version >= 1", name="ck_taxonomy_tag_version_positive"),
        Index("ix_taxonomy_tag_dict_status", "dict_id", "status"),
        Index("ix_taxonomy_tag_path", "path"),
    )


class TagHistory(Base):
    """append-only 变更历史。before/after 存整行快照，便于回滚与审计。"""

    __tablename__ = "taxonomy_tag_history"

    id: Mapped[int] = mapped_column(sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                                    primary_key=True, autoincrement=True)
    tag_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("taxonomy_tag.id", ondelete="CASCADE"),
                                              nullable=False)
    action: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    version: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    before: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    changed_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        CheckConstraint("action IN ('create','update','deprecate','merge')",
                        name="ck_taxonomy_tag_history_action"),
        Index("ix_taxonomy_tag_history_tag", "tag_id", "changed_at"),
    )
