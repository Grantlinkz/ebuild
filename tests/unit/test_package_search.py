# SPDX-License-Identifier: MIT
# Copyright (c) 2026 EoS Project

"""Unit tests for package discovery and search across repository sources."""

import json
from pathlib import Path
from click.testing import CliRunner

from ebuild.cli.commands import cli
from ebuild.packages.index_sync import IndexSyncManager
from ebuild.packages.repository import PackageInfo, PackageRepository


def test_package_repository_search(tmp_path):
    repo = PackageRepository()

    # Create dummy local recipe directory
    recipe_dir = tmp_path / "recipes"
    recipe_dir.mkdir()
    (recipe_dir / "my_crypto.yaml").write_text(
        """package: my_crypto
version: "1.0.0"
description: "Embedded cryptography primitives"
license: Apache-2.0
url: https://example.com/crypto.tar.gz
checksum: sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
build: cmake
""",
        encoding="utf-8",
    )

    repo.add_recipe_directory(recipe_dir)
    assert repo.package_count == 1

    # Search by keyword
    results = repo.search("crypto")
    assert len(results) == 1
    assert results[0].name == "my_crypto"

    # Search by license
    assert len(repo.search("", license="Apache")) == 1
    assert len(repo.search("", license="GPL")) == 0

    # Search by build system
    assert len(repo.search("", build_system="cmake")) == 1
    assert len(repo.search("", build_system="meson")) == 0


def test_cli_search_command(tmp_path):
    runner = CliRunner()

    result = runner.invoke(cli, ["search", "cjson"])
    assert result.exit_code == 0
    assert "cjson" in result.output

    # JSON output
    json_result = runner.invoke(cli, ["search", "cjson", "--json"])
    assert json_result.exit_code == 0
    data = json.loads(json_result.output)
    assert isinstance(data, list)
    assert any(p["name"] == "cjson" for p in data)


def test_cli_update_index_offline():
    runner = CliRunner()
    result = runner.invoke(cli, ["update-index", "--offline"])
    assert result.exit_code == 0
    assert "Offline mode" in result.output
