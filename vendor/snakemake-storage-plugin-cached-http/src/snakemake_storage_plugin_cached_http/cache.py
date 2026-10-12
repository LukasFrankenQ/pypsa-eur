# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from snakemake_interface_common.logging import get_logger

logger = get_logger()


@dataclass
class Cache:
    """Manages local file cache for downloaded Zenodo files"""

    cache_dir: Path

    def __post_init__(self):
        """Create cache directory if it doesn't exist"""
        self.cache_dir = Path(self.cache_dir).expanduser()
        self.cache_dir.mkdir(exist_ok=True, parents=True)

    def _get_cache_path(self, url: str) -> Path:
        """Generate cache path from URL"""
        # Use URL parsing to create deterministic path
        parsed = urlparse(url)
        return self.cache_dir / f"{parsed.netloc}{parsed.path}"

    def get(self, url: str) -> Path | None:
        """
        Get cached file path for URL.

        Args:
            url: The file URL

        Returns:
            Path to cached file if it exists, None otherwise
        """
        cache_path = self._get_cache_path(url)
        if cache_path.exists():
            return cache_path
        return None

    def put(self, url: str, filepath: Path) -> None:
        """
        Store downloaded file in cache.

        Args:
            url: The file URL (used as cache key)
            filepath: Path to downloaded file to cache
        """
        cache_path = self._get_cache_path(url)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(filepath, cache_path)
        logger.info(f"Cached {filepath.name} to {self.cache_dir}")
