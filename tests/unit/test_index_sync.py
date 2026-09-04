# SPDX-License-Identifier: MIT
# Copyright (c) 2026 EoS Project

"""Unit tests for remote package index sync and offline caching."""

import io
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ebuild.packages.index_sync import (
    DEFAULT_INDEX_URL,
    IndexSyncError,
    IndexSyncManager,
    get_default_index_dir,
    is_offline,
    sanitize_package_name,
)


def test_sanitize_package_name():
    assert sanitize_package_name("cjson") == "cjson"
    assert sanitize_package_name("my_pkg-123") == "my_pkg-123"

    with pytest.raises(ValueError, match="Invalid package name"):
        sanitize_package_name("../../etc/passwd")

    with pytest.raises(ValueError, match="Invalid package name"):
        sanitize_package_name("pkg with spaces")

    with pytest.raises(ValueError, match="Invalid package name"):
        sanitize_package_name("")


def test_is_offline(monkeypatch):
    assert not is_offline(False)
    assert is_offline(True)

    monkeypatch.setenv("EBUILD_OFFLINE", "1")
    assert is_offline(False)

    monkeypatch.setenv("EBUILD_OFFLINE", "true")
    assert is_offline(False)

    monkeypatch.setenv("EBUILD_OFFLINE", "0")
    assert not is_offline(False)


def test_get_default_index_dir(monkeypatch, tmp_path):
    custom_dir = tmp_path / "custom_index"
    monkeypatch.setenv("EBUILD_INDEX_PATH", str(custom_dir))
    assert get_default_index_dir() == custom_dir


def test_index_sync_insecure_url(tmp_path):
    mgr = IndexSyncManager(index_dir=tmp_path)
    with pytest.raises(IndexSyncError, match="Insecure index URL"):
        mgr.sync(url="http://insecure.example.com/index.json")


def test_index_sync_success(tmp_path):
    mgr = IndexSyncManager(index_dir=tmp_path)

    sample_index = [
        {
            "name": "mock-pkg",
            "version": "1.0.0",
            "description": "A mock package for testing",
            "license": "MIT",
            "url": "https://example.com/mock-pkg-1.0.0.tar.gz",
            "checksum": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            "build_system": "cmake",
            "configure_args": ["-DMOCK=ON"],
        }
    ]
    raw_json = json.dumps(sample_index).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.read.return_value = raw_json
    mock_resp.headers = {"Content-Length": str(len(raw_json))}
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        count, msg = mgr.sync(url="https://example.com/index.json")

    assert count == 1
    assert "Successfully synchronized 1 packages" in msg
    assert mgr.packages_json.is_file()

    # Check that recipe YAML was cached
    recipe_file = mgr.recipes_dir / "mock-pkg.yaml"
    assert recipe_file.is_file()
    content = recipe_file.read_text(encoding="utf-8")
    assert "mock-pkg" in content
    assert "1.0.0" in content


def test_index_sync_corrupted_json(tmp_path):
    mgr = IndexSyncManager(index_dir=tmp_path)

    mock_resp = MagicMock()
    mock_resp.read.return_value = b"{ invalid json"
    mock_resp.headers = {}
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        with pytest.raises(IndexSyncError, match="Corrupted or invalid JSON"):
            mgr.sync()


def test_index_sync_network_error_fallback(tmp_path):
    mgr = IndexSyncManager(index_dir=tmp_path)
    mgr.ensure_directories()

    # Seed cache
    cached_data = [{"name": "cached-lib", "version": "2.0.0"}]
    mgr.packages_json.write_text(json.dumps(cached_data), encoding="utf-8")

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("No connection")):
        count, msg = mgr.sync()

    assert count == 1
    assert "fell back to cached index" in msg


def test_index_sync_offline_mode(tmp_path):
    mgr = IndexSyncManager(index_dir=tmp_path)
    mgr.ensure_directories()

    cached_data = [{"name": "offline-lib", "version": "1.0.0"}]
    mgr.packages_json.write_text(json.dumps(cached_data), encoding="utf-8")

    count, msg = mgr.sync(offline=True)
    assert count == 1
    assert "Offline mode" in msg
