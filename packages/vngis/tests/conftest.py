"""Fixture dùng chung cho test của vngis.

Test cần MongoDB thật đánh dấu ``@pytest.mark.mongo`` và dùng fixture ``mongo_db``; tự bỏ qua khi không
kết nối được. URI lấy từ ``VNGIS_TEST_MONGO_URI`` (mặc định ``mongodb://localhost:27017``), database từ
``VNGIS_TEST_MONGO_DB`` (mặc định ``vietnam_gis``). Các test này CHỈ ĐỌC.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_CONFIG = next((d / "config" for d in Path(__file__).resolve().parents if (d / "config" / "default.yaml").is_file()),
                   None)


def pytest_configure(config):
    config.addinivalue_line("markers", "mongo: cần MongoDB thật (bỏ qua khi không kết nối được)")
    config.addinivalue_line("markers", "replset: cần MongoDB replica set (VNGIS_TEST_RS_URI; bỏ qua khi không có)")


@pytest.fixture(scope="session")
def repo_config_dir() -> Path:
    if REPO_CONFIG is None:
        pytest.skip("không tìm thấy config/ của repo")
    return REPO_CONFIG


@pytest.fixture(scope="session")
def mongo_db():
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    uri = os.environ.get("VNGIS_TEST_MONGO_URI", "mongodb://localhost:27017")
    client = MongoClient(uri, serverSelectionTimeoutMS=1500)
    try:
        client.admin.command("ping")
    except PyMongoError as e:
        client.close()
        pytest.skip(f"không kết nối được MongoDB ({uri}): {type(e).__name__}")
    db = client[os.environ.get("VNGIS_TEST_MONGO_DB", "vietnam_gis")]
    yield db
    client.close()
