from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_demo_builder():
    spec = importlib.util.spec_from_file_location("build_shop_db", ROOT / "demo" / "build_shop_db.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def shop_db(tmp_path_factory) -> Path:
    """A freshly built copy of the demo database."""
    return _load_demo_builder().build(tmp_path_factory.mktemp("demo") / "shop.sqlite")


@pytest.fixture
def tiny_db(tmp_path) -> Path:
    """t(id, grp, val) with a duplicate row and a float column."""
    path = tmp_path / "tiny.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE t (id INTEGER, grp TEXT, val REAL);
        INSERT INTO t VALUES (1, 'a', 0.1), (2, 'a', 0.2), (3, 'b', 0.3), (3, 'b', 0.3);
        """
    )
    conn.commit()
    conn.close()
    return path
