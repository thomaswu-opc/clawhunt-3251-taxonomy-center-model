"""跨表校验：数据库 CHECK 表达不了的规则放这里。

- 层级：level 由父节点推导，必须 <= 所属字典的 max_depth（跨表，CHECK 做不到）；
- 路径：path 必须等于 parent.path + "/" + code，且段数等于 level；
- 合并：merged 状态必须带 merged_into_id，非 merged 状态不得带。
"""
from __future__ import annotations


class DepthExceeded(ValueError):
    def __init__(self, level: int, max_depth: int):
        super().__init__("level %d exceeds dict max_depth %d" % (level, max_depth))
        self.level = level
        self.max_depth = max_depth


class InvalidPath(ValueError):
    pass


class InvalidStatus(ValueError):
    pass


def build_path(parent_path: str | None, code: str) -> str:
    return "%s/%s" % (parent_path, code) if parent_path else code


def ensure_depth(level: int, max_depth: int) -> None:
    if level < 1:
        raise InvalidPath("level must be >= 1, got %d" % level)
    if level > max_depth:
        raise DepthExceeded(level, max_depth)


def ensure_path(path: str, level: int) -> None:
    if len([p for p in path.split("/") if p]) != level:
        raise InvalidPath("path %r has %d segments, level says %d"
                          % (path, len([p for p in path.split("/") if p]), level))


def ensure_status(status: str, merged_into_id=None) -> None:
    allowed = ("draft", "pending_review", "active", "deprecated", "merged")
    if status not in allowed:
        raise InvalidStatus("unknown status: %s" % status)
    if status == "merged" and not merged_into_id:
        raise InvalidStatus("merged 状态必须带 merged_into_id")
    if status != "merged" and merged_into_id:
        raise InvalidStatus("非 merged 状态不得带 merged_into_id")
