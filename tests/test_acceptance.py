"""三条验收标准的机械验证 + 模型行为。

AC-1 真的跑 alembic（sqlite 文件库），不是"看迁移文件觉得没问题"；
AC-2 / AC-3 走真实约束与校验路径，不对异常做字符串匹配。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from taxonomy_center import validate
from taxonomy_center.models import Base, Dict, Tag
from taxonomy_center.repository import Repository

ROOT = Path(__file__).resolve().parent.parent


def _alembic_config(db_path: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", "sqlite:///%s" % db_path)
    return cfg


def _tables(db_path: str) -> set[str]:
    eng = sa.create_engine("sqlite:///%s" % db_path)
    try:
        return set(sa.inspect(eng).get_table_names())
    finally:
        eng.dispose()


class AC1MigrationUpDown(unittest.TestCase):
    def test_ac1_upgrade_head_and_downgrade_minus_one(self):
        tmp = tempfile.mkdtemp()
        db_path = os.path.join(tmp, "tax.db")
        os.environ["DATABASE_URL"] = "sqlite:///%s" % db_path
        cfg = _alembic_config(db_path)
        try:
            command.upgrade(cfg, "head")
            tables = _tables(db_path)
            self.assertTrue({"taxonomy_dict", "taxonomy_tag", "taxonomy_tag_history",
                             "alembic_version"} <= tables, tables)

            command.downgrade(cfg, "-1")
            left = _tables(db_path)
            self.assertEqual(left - {"alembic_version"}, set(), "downgrade -1 应只留 alembic_version")

            command.upgrade(cfg, "head")
            self.assertTrue({"taxonomy_dict", "taxonomy_tag"} <= _tables(db_path))
        finally:
            os.environ.pop("DATABASE_URL", None)


class _Db(unittest.TestCase):
    def setUp(self):
        self.engine = sa.create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.repo = Repository(self.session)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()


class AC2DuplicateCodeRejected(_Db):
    def test_ac2_same_dict_duplicate_code_fails(self):
        d = self.repo.create_dict("industry", "行业")
        self.repo.create_tag(d, "A01", "农业")
        self.session.commit()

        with self.assertRaises(IntegrityError):
            self.repo.create_tag(d, "A01", "农业（重复）")
        self.session.rollback()

        count = self.session.scalar(
            sa.select(sa.func.count()).select_from(Tag).where(Tag.code == "A01"))
        self.assertEqual(count, 1, "重复插入失败后库里仍只有 1 条")

    def test_ac2_same_code_in_other_dict_allowed(self):
        d1 = self.repo.create_dict("industry", "行业")
        d2 = self.repo.create_dict("demand", "需求类型")
        self.repo.create_tag(d1, "A01", "农业")
        self.repo.create_tag(d2, "A01", "农业采购")
        self.session.commit()
        self.assertEqual(
            self.session.scalar(sa.select(sa.func.count()).select_from(Tag)),
            2, "不同字典的相同 code 必须允许")


class AC3DepthExceeded(_Db):
    def test_ac3_level_over_max_depth_fails(self):
        d = self.repo.create_dict("industry", "行业", max_depth=2)
        l1 = self.repo.create_tag(d, "A", "门类")
        l2 = self.repo.create_tag(d, "A01", "大类", parent=l1)
        self.assertEqual((l1.level, l2.level), (1, 2))

        with self.assertRaises(validate.DepthExceeded) as ctx:
            self.repo.create_tag(d, "A0101", "中类", parent=l2)
        self.assertEqual((ctx.exception.level, ctx.exception.max_depth), (3, 2))
        self.session.rollback()

        deep = self.session.scalar(sa.select(sa.func.count()).select_from(Tag))
        self.assertEqual(deep, 0, "超深度的标签不得落库")

    def test_ac3_path_matches_level(self):
        d = self.repo.create_dict("industry", "行业", max_depth=3)
        l1 = self.repo.create_tag(d, "A", "门类")
        l2 = self.repo.create_tag(d, "A01", "大类", parent=l1)
        self.assertEqual((l2.path, l2.level), ("A/A01", 2))
        with self.assertRaises(validate.InvalidPath):
            validate.ensure_path("A/A01/X", 2)


class ModelBehaviour(_Db):
    def test_history_written_on_create_and_update(self):
        d = self.repo.create_dict("industry", "行业")
        tag = self.repo.create_tag(d, "A01", "农业")
        self.repo.update_tag(tag, name="农业（含种植）")
        self.session.commit()
        rows = self.repo.history(tag.id)
        self.assertEqual([r.action for r in rows], ["create", "update"])
        self.assertIsNone(rows[0].before)
        self.assertEqual(rows[1].before["name"], "农业")
        self.assertEqual(rows[1].after["name"], "农业（含种植）")
        self.assertEqual(rows[1].version, 2)

    def test_status_check_constraint(self):
        d = self.repo.create_dict("industry", "行业")
        with self.assertRaises(validate.InvalidStatus):
            self.repo.create_tag(d, "A01", "农业", status="activex")

    def test_merged_requires_target(self):
        with self.assertRaises(validate.InvalidStatus):
            validate.ensure_status("merged", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
