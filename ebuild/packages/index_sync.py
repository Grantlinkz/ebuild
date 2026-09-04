# SPDX-License-Identifier: MIT
# Copyright (c) 2026 EoS Project

"""Remote package index synchronization and offline cache manager.

Manages downloading, verifying, and caching package indices and recipe
definitions from remote repositories (HTTPS) into a local user cache directory
(~/.ebuild/index/). Supports offline fallback and security sanitization.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from ebuild.packages.recipe import PackageRecipe, RecipeError, _parse_recipe

logger = logging.getLogger(__name__)

# Default remote repository index URL
DEFAULT_INDEX_URL = (
    "https://raw.githubusercontent.com/embeddedos-org/recipes/main/index.json"
)

# Maximum response size allowed for index download (10 MB)
MAX_INDEX_SIZE_BYTES = 10 * 1024 * 1024

# Network timeout in seconds
DEFAULT_NETWORK_TIMEOUT_SECONDS = 10

# Valid package name pattern (strict validation to prevent path traversal)
_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


class IndexSyncError(Exception):
    """Raised when index synchronization fails and cannot fallback."""


def is_offline(offline_flag: bool = False) -> bool:
    """Check if the execution environment is configured for offline operation."""
    if offline_flag:
        return True
    env_val = os.environ.get("EBUILD_OFFLINE", "").strip().lower()
    return env_val in ("1", "true", "yes", "on")


def get_default_index_dir() -> Path:
    """Get the local index cache directory path, respecting env overrides."""
    if "EBUILD_INDEX_PATH" in os.environ:
        return Path(os.environ["EBUILD_INDEX_PATH"])
    if "EBUILD_CACHE_DIR" in os.environ:
        return Path(os.environ["EBUILD_CACHE_DIR"]) / "index"
    return Path.home() / ".ebuild" / "index"


def sanitize_package_name(name: str) -> str:
    """Validate and sanitize a package name to prevent path traversal attacks.

    Args:
        name: The candidate package name.

    Returns:
        The validated package name.

    Raises:
        ValueError: If the package name contains invalid or unsafe characters.
    """
    cleaned = name.strip()
    if not cleaned or not _SAFE_NAME_RE.match(cleaned):
        raise ValueError(
            f"Invalid package name '{name}': names must only contain alphanumeric "
            f"characters, underscores, or hyphens."
        )
    return cleaned


class IndexSyncManager:
    """Coordinates remote index fetching, integrity validation, and local caching."""

    def __init__(
        self,
        index_dir: Optional[Path | str] = None,
        default_url: str = DEFAULT_INDEX_URL,
    ) -> None:
        self.index_dir = (
            Path(index_dir) if index_dir is not None else get_default_index_dir()
        )
        self.default_url = default_url
        self.packages_json = self.index_dir / "packages.json"
        self.recipes_dir = self.index_dir / "recipes"

    def ensure_directories(self) -> None:
        """Create necessary index directories if they do not exist."""
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.recipes_dir.mkdir(parents=True, exist_ok=True)

    def get_recipe_dirs(self) -> List[Path]:
        """Return list of recipe directories managed by this sync index."""
        if self.recipes_dir.is_dir():
            return [self.recipes_dir]
        return []

    def load_cached_entries(self) -> List[Dict[str, Any]]:
        """Load entries from the local packages.json cache if present."""
        if not self.packages_json.is_file():
            return []
        try:
            with open(self.packages_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            logger.warning("Cached index at %s is not a list", self.packages_json)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load cached index: %s", e)
        return []

    def sync(
        self,
        url: Optional[str] = None,
        force: bool = False,
        offline: bool = False,
        timeout: int = DEFAULT_NETWORK_TIMEOUT_SECONDS,
    ) -> Tuple[int, str]:
        """Synchronize the index from the remote URL to the local cache.

        Args:
            url: The remote index URL (defaults to configured default_url).
            force: If True, re-download even if recently synced.
            offline: If True, skip network download and use cached files.
            timeout: Network timeout in seconds.

        Returns:
            Tuple of (package_count, status_message).
        """
        target_url = (url or self.default_url).strip()
        self.ensure_directories()

        if is_offline(offline):
            cached = self.load_cached_entries()
            count = len(cached)
            return count, f"Offline mode: using cached index ({count} packages)"

        if not target_url.startswith("https://"):
            raise IndexSyncError(
                f"Insecure index URL '{target_url}': only HTTPS URLs are permitted."
            )

        logger.info("Fetching remote package index from %s", target_url)

        try:
            req = urllib.request.Request(
                target_url,
                headers={"User-Agent": "ebuild-package-manager/3.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > MAX_INDEX_SIZE_BYTES:
                    raise IndexSyncError(
                        f"Index download exceeds maximum allowed size ({MAX_INDEX_SIZE_BYTES} bytes)"
                    )
                raw_bytes = response.read(MAX_INDEX_SIZE_BYTES + 1)
                if len(raw_bytes) > MAX_INDEX_SIZE_BYTES:
                    raise IndexSyncError(
                        f"Index download exceeded maximum size limit of {MAX_INDEX_SIZE_BYTES} bytes"
                    )

            # Parse JSON
            try:
                data = json.loads(raw_bytes.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as err:
                raise IndexSyncError(f"Corrupted or invalid JSON index from {target_url}: {err}") from err

            if not isinstance(data, list):
                raise IndexSyncError("Invalid index schema: expected top-level JSON array")

            # Write cached packages.json atomically
            temp_json = self.packages_json.with_suffix(".tmp")
            with open(temp_json, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            temp_json.replace(self.packages_json)

            # Process and cache full recipe YAML definitions
            synced_count = 0
            for entry in data:
                if not isinstance(entry, dict) or "name" not in entry:
                    continue
                try:
                    pkg_name = sanitize_package_name(str(entry["name"]))
                    recipe_filename = f"{pkg_name}.yaml"
                    recipe_path = self.recipes_dir / recipe_filename

                    # Write recipe YAML if entry contains recipe attributes
                    recipe_dict = {
                        "package": pkg_name,
                        "version": str(entry.get("version", "1.0.0")),
                        "description": entry.get("description", ""),
                        "license": entry.get("license", ""),
                        "url": entry.get("url", ""),
                        "checksum": entry.get("checksum", ""),
                        "build": entry.get("build_system", entry.get("build", "cmake")),
                        "dependencies": entry.get("dependencies", []),
                        "configure_args": entry.get("configure_args", []),
                        "build_args": entry.get("build_args", []),
                        "patches": entry.get("patches", []),
                    }

                    # If URL exists, validate recipe structure before saving
                    if recipe_dict["url"]:
                        try:
                            recipe = _parse_recipe(recipe_dict)
                            with open(recipe_path, "w", encoding="utf-8") as rf:
                                yaml.safe_dump(recipe_dict, rf, sort_keys=False)
                        except RecipeError as re_err:
                            logger.warning("Skipping invalid recipe entry %s: %s", pkg_name, re_err)
                            continue

                    synced_count += 1
                except ValueError as ve:
                    logger.warning("Skipping unsafe package entry: %s", ve)
                    continue

            return synced_count, f"Successfully synchronized {synced_count} packages from remote index"

        except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as e:
            # Fallback to local cache if available
            cached = self.load_cached_entries()
            if cached:
                logger.warning("Remote sync failed (%s). Falling back to cached index.", e)
                return len(cached), f"Network sync failed ({e}); fell back to cached index ({len(cached)} packages)"
            raise IndexSyncError(f"Failed to fetch remote package index and no cache is available: {e}") from e
