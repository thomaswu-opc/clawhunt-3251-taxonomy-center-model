"""仓储层：唯一的写入入口，校验 + 落库 + 写历史。

顺序固定为 **校验 → add → flush**：
- 跨表规则（层级）在 flush 前拦下，异常类型明确（DepthExceeded），不依赖数据库报错文本；
- 同字典 code 唯一交给数据库唯一约束兜底，`IntegrityError` 直接向上抛，调用方做 409/422 映射。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from taxonomy_center import validate
from taxonomy_center.models import Dict, Tag, TagHistory


def _snapshot(tag: Tag) -> dict:
    return {
        "id": str(tag.id),
        "dict_id": str(tag.dict_id),
        "code": tag.code,
        "name": tag.name,
        "parent_id": str(tag.parent_id) if tag.parent_id else None,
        "path": tag.path,
        "level": tag.level,
        "aliases": list(tag.aliases or []),
        "status": tag.status,
        "merged_into_id": str(tag.merged_into_id) if tag.merged_into_id else None,
        "sort_order": tag.sort_order,
        "version": tag.version,
    }


class Repository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # --- 字典 -----------------------------------------------------------
    def create_dict(self, code: str, name: str, *, max_depth: int = 3,
                    allow_multi: bool = True, owner_role: str = "knowledge_admin") -> Dict:
        node = Dict(code=code, name=name, max_depth=max_depth,
                    allow_multi=allow_multi, owner_role=owner_role)
        self.session.add(node)
        self.session.flush()
        return node

    def get_dict(self, code: str) -> Dict | None:
        return self.session.scalars(select(Dict).where(Dict.code == code)).first()

    # --- 标签 -----------------------------------------------------------
    def create_tag(self, dict_obj: Dict, code: str, name: str, *,
                   parent: Tag | None = None, status: str = "draft",
                   aliases: list[str] | None = None,
                   definition: str | None = None, sort_order: int = 0) -> Tag:
        level = (parent.level + 1) if parent else 1
        path = validate.build_path(parent.path if parent else None, code)
        # 跨表约束：先校验再落库，异常可控
        validate.ensure_depth(level, dict_obj.max_depth)
        validate.ensure_path(path, level)
        validate.ensure_status(status)

        tag = Tag(
            dict_id=dict_obj.id, code=code, name=name,
            parent_id=parent.id if parent else None,
            path=path, level=level, aliases=list(aliases or []),
            definition=definition, status=status, sort_order=sort_order,
        )
        self.session.add(tag)
        self.session.flush()          # 唯一约束在这里生效（IntegrityError）
        self.session.add(TagHistory(
            tag_id=tag.id, action="create", version=tag.version,
            before=None, after=_snapshot(tag),
        ))
        self.session.flush()
        return tag

    def update_tag(self, tag: Tag, **changes) -> Tag:
        if tag.status == "merged" and "status" not in changes:
            raise validate.InvalidStatus("merged 为终态，需先通过 merge 流程回退")
        before = _snapshot(tag)
        for key, value in changes.items():
            if not hasattr(tag, key):
                raise AttributeError("unknown field: %s" % key)
            setattr(tag, key, value)
        tag.version += 1
        tag.updated_at = datetime.now(timezone.utc)
        validate.ensure_status(tag.status, tag.merged_into_id)
        if tag.parent_id:
            parent = self.session.get(Tag, tag.parent_id)
            dict_obj = self.session.get(Dict, tag.dict_id)
            validate.ensure_depth(parent.level + 1, dict_obj.max_depth)
        self.session.flush()
        self.session.add(TagHistory(
            tag_id=tag.id, action="update", version=tag.version,
            before=before, after=_snapshot(tag),
        ))
        self.session.flush()
        return tag

    def history(self, tag_id: uuid.UUID) -> list[TagHistory]:
        return list(self.session.scalars(
            select(TagHistory).where(TagHistory.tag_id == tag_id).order_by(TagHistory.id)))
