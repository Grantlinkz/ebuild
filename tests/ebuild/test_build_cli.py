# SPDX-License-Identifier: MIT
# Copyright (c) 2026 EoS Project

"""CLI regressions for build backend selection."""

import yaml
from click.testing import CliRunner

from ebuild.cli.commands import cli


def test_system_only_config_does_not_report_build_success(tmp_path):
    config_path = tmp_path / "build.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "project": {"name": "system-image"},
                "system": {"hostname": "eos-device", "image_format": "tar"},
            }
        ),
        encoding="utf-8",
    )
    build_dir = tmp_path / "build"

    result = CliRunner().invoke(
        cli,
        ["build", "--config", str(config_path), "--build-dir", str(build_dir)],
    )

    assert result.exit_code == 1
    assert "Auto-detected backend: ninja" in result.output
    # The dispatcher raised this through two separate mechanisms that a
    # merge left side by side; they are now one, and the surviving message
    # also says what to do about it. The guarantee under test -- ninja is
    # rejected here rather than silently no-op'd -- is unchanged.
    assert "Unknown build backend 'ninja'" in result.output
    assert "requires 'targets' in build.yaml" in result.output
    assert "Build completed successfully" not in result.output
    assert not build_dir.exists()
