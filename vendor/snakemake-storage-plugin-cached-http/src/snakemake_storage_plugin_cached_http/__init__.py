# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

import asyncio
import base64
import hashlib
import json
import shutil
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from logging import Logger
from pathlib import Path
from posixpath import basename, dirname, join, normpath, relpath
from urllib.parse import quote, urlparse

import httpx
import platformdirs
import yaml
from reretry import retry  # pyright: ignore[reportUnknownVariableType]
from snakemake_interface_common.exceptions import WorkflowError
from snakemake_interface_common.logging import get_logger
from snakemake_interface_common.plugin_registry.plugin import SettingsBase
from snakemake_interface_storage_plugins.common import Operation
from snakemake_interface_storage_plugins.io import IOCacheStorageInterface, Mtime
from snakemake_interface_storage_plugins.storage_object import StorageObjectRead
from snakemake_interface_storage_plugins.storage_provider import (
    ExampleQuery,
    QueryType,
    StorageProviderBase,
    StorageQueryValidationResult,
)
from tqdm_loggable.auto import tqdm
from typing_extensions import override

from .cache import Cache
from .monkeypatch import is_pypsa_or_zenodo_url

logger = get_logger()


class ReretryLoggerAdapter:
    """Adapter to make Snakemake's logger compatible with reretry's logging expectations."""

    _logger: Logger

    def __init__(self, snakemake_logger: Logger):
        self._logger = snakemake_logger

    def warning(self, msg: str, *args, **kwargs):  # pyright: ignore[reportUnknownParameterType, reportUnusedParameter, reportMissingParameterType]
        """
        Format message manually before passing to Snakemake logger.

        This is necessary because Snakemake's DefaultFormatter has a bug where it
        returns record["msg"] without calling interpolating the args. This causes
        literal "%s" to appear in log output instead of formatted values.
        """
        if args:
            # Pre-format the message with % operator
            msg = msg % args
        self._logger.warning(msg)


# Define settings for the Zenodo storage plugin
# NB: We derive from SettingsBase rather than StorageProviderSettingsBase to remove the
# unsupported max_requests_per_second option
@dataclass
class StorageProviderSettings(SettingsBase):
    cache: str = field(
        default_factory=lambda: platformdirs.user_cache_dir(
            "snakemake-pypsa-eur", ensure_exists=False
        ),
        metadata={
            "help": 'Cache directory for downloaded files (default: platform-dependent user cache dir). Set to "" to deactivate caching.',
            "env_var": True,
        },
    )
    skip_remote_checks: bool = field(
        default=False,
        metadata={
            "help": "Whether to skip metadata checking with remote server (default: False, ie. do check).",
            "env_var": True,
        },
    )
    max_concurrent_downloads: int = field(
        default=3,
        metadata={
            "help": "Maximum number of concurrent downloads.",
            "env_var": False,
        },
    )


@dataclass
class FileMetadata:
    """Metadata for a file in a Zenodo, data.pypsa.org, or GCS record."""

    checksum: str | None
    size: int
    mtime: float = 0  # modification time (Unix timestamp), used for GCS
    redirect: str | None = None  # used to indicate data.pypsa.org redirection


class WrongChecksum(Exception):
    observed: str
    expected: str

    def __init__(self, observed: str, expected: str):
        self.observed = observed
        self.expected = expected
        super().__init__(f"Checksum mismatch: expected {expected}, got {observed}")


retry_decorator = retry(
    exceptions=(  # pyright: ignore[reportArgumentType]
        httpx.HTTPError,
        TimeoutError,
        OSError,
        WrongChecksum,
        WorkflowError,
    ),
    tries=5,
    delay=3,
    backoff=2,
    logger=ReretryLoggerAdapter(get_logger()),  # pyright: ignore[reportArgumentType]
)


# Implementation of storage provider
class StorageProvider(StorageProviderBase):
    settings: StorageProviderSettings
    cache: Cache | None

    def __post_init__(self):
        super().__post_init__()

        # Set up cache
        self.cache = (
            Cache(cache_dir=Path(self.settings.cache)) if self.settings.cache else None
        )

        # Initialize shared client for bounding connections and pipelining
        self._client: httpx.AsyncClient | None = None
        self._client_refcount: int = 0

        # Cache for record metadata to avoid repeated API calls
        self._zenodo_record_cache: dict[str, dict[str, FileMetadata]] = {}
        self._pypsa_manifest_cache: dict[str, dict[str, FileMetadata]] = {}
        self._gcs_metadata_cache: dict[str, FileMetadata] = {}

    @override
    def use_rate_limiter(self) -> bool:
        """Return False if no rate limiting is needed for this provider."""
        return False

    @override
    def rate_limiter_key(self, query: str, operation: Operation) -> str:
        raise NotImplementedError()

    @override
    def default_max_requests_per_second(self) -> float:
        raise NotImplementedError()

    @override
    @classmethod
    def example_queries(cls) -> list[ExampleQuery]:
        """Return an example query with description for this storage provider."""
        return [
            ExampleQuery(
                query="https://zenodo.org/records/17249457/files/ARDECO-SNPTD.2021.table.csv",
                description="A Zenodo file URL",
                type=QueryType.INPUT,
            ),
            ExampleQuery(
                query="https://data.pypsa.org/workflows/eur/eez/v12_20231025/World_EEZ_v12_20231025_LR.zip",
                description="A data pypsa file URL",
                type=QueryType.INPUT,
            ),
            ExampleQuery(
                query="https://storage.googleapis.com/open-tyndp-data-store/CBA_projects.zip",
                description="A Google Cloud Storage file URL",
                type=QueryType.INPUT,
            ),
        ]

    @override
    @classmethod
    def is_valid_query(cls, query: str) -> StorageQueryValidationResult:
        """Only handle zenodo.org URLs"""
        if is_pypsa_or_zenodo_url(query):
            return StorageQueryValidationResult(query=query, valid=True)

        return StorageQueryValidationResult(
            query=query,
            valid=False,
            reason="Only zenodo.org, data.pypsa.org, and storage.googleapis.com URLs are handled by this plugin",
        )

    @override
    @classmethod
    def get_storage_object_cls(cls):
        return StorageObject

    @asynccontextmanager
    async def client(self):
        """
        Reentrant async context manager for httpx.AsyncClient.

        Creates a client on first entry and reuses it for nested calls.
        The client is closed only when all context managers have exited.

        Usage:
            async with provider.client() as client:
                response = await client.get(url)
                ...
        """
        self._client_refcount += 1

        # Create client on first entry
        if self._client is None:
            max_concurrent_downloads = self.settings.max_concurrent_downloads
            limits = httpx.Limits(
                max_keepalive_connections=max_concurrent_downloads,
                max_connections=max_concurrent_downloads,
            )
            timeout = httpx.Timeout(60, pool=None)

            self._client = httpx.AsyncClient(
                follow_redirects=True, limits=limits, timeout=timeout
            )

        try:
            yield self._client
        finally:
            self._client_refcount -= 1
            if self._client_refcount == 0:
                await self._client.aclose()
                self._client = None

    def _get_rate_limit_wait_time(self, headers: httpx.Headers) -> float | None:
        """
        Calculate wait time based on rate limit headers.

        Returns:
            float | None: Wait time in seconds if rate limited, None otherwise
        """
        remaining = int(headers.get("X-RateLimit-Remaining", 100))
        reset_time = int(headers.get("X-RateLimit-Reset", 0))

        if remaining >= 1:
            return None

        wait_seconds = max(0, reset_time - time.time() + 1)
        return wait_seconds

    @asynccontextmanager
    async def httpr(self, method: str, url: str):
        """
        HTTP request wrapper with rate limiting and exception logging.

        Args:
            method: HTTP method (e.g., "get", "post")
            url: URL to request

        Yields:
            httpx.Response object
        """
        try:
            async with self.client() as client, client.stream(method, url) as response:
                wait_time = self._get_rate_limit_wait_time(response.headers)
                if wait_time is not None:
                    logger.info(
                        f"Zenodo rate limit exceeded. Waiting {wait_time:.0f}s until reset..."
                    )
                    await asyncio.sleep(wait_time)
                    raise httpx.HTTPError("Rate limit exceeded, retrying after wait")

                yield response
        except Exception as e:
            logger.warning(f"{type(e).__name__} while {method}'ing {url}")
            raise

    @retry_decorator
    async def get_metadata(self, path: str, netloc: str) -> FileMetadata | None:
        """
        Retrieve and cache file metadata for a Zenodo record or a data.pypsa.org file.

        Args:
            path: Server path
            netloc: Network location (e.g., "zenodo.org")

        Returns:
            Dictionary mapping filename to FileMetadata
        """
        if netloc in ("zenodo.org", "sandbox.zenodo.org"):
            return await self.get_zenodo_metadata(path, netloc)
        elif netloc == "data.pypsa.org":
            return await self.get_pypsa_metadata(path, netloc)
        elif netloc == "storage.googleapis.com":
            return await self.get_gcs_metadata(path, netloc)

        raise WorkflowError(
            "Cached-http storage plugin is only implemented for zenodo.org, data.pypsa.org, and storage.googleapis.com urls"
        )

    @staticmethod
    def is_immutable(netloc: str):
        if netloc in ("zenodo.org", "sandbox.zenodo.org"):
            return True
        elif netloc == "data.pypsa.org":
            return True
        elif netloc == "storage.googleapis.com":
            return False

        raise WorkflowError(
            "Cached-http storage plugin is only implemented for zenodo.org, data.pypsa.org, and storage.googleapis.com urls"
        )

    async def get_zenodo_metadata(self, path: str, netloc: str) -> FileMetadata | None:
        """
        Retrieve and cache file metadata for a Zenodo record or a data.pypsa.org file.

        Args:
            path: Server path
            netloc: Network location (e.g., "zenodo.org")

        Returns:
            Dictionary mapping filename to FileMetadata
        """

        # Zenodo record
        _records, record_id, _files, filename = path.split("/", maxsplit=3)

        if _records != "records" or _files != "files":
            raise WorkflowError(
                f"Invalid Zenodo URL format: http(s)://{netloc}/{path}. "
                f"Expected format: https://zenodo.org/records/{{record_id}}/files/{{filename}}"
            )

        # Check cache first
        if record_id in self._zenodo_record_cache:
            return self._zenodo_record_cache[record_id].get(filename)

        # Fetch from API
        api_url = f"https://{netloc}/api/records/{record_id}"

        async with self.httpr("get", api_url) as response:
            if response.status_code != 200:
                raise WorkflowError(
                    f"Failed to fetch Zenodo record metadata: HTTP {response.status_code} ({api_url})"
                )

            # Read the full response body
            content = await response.aread()
            data = json.loads(content)

        # Parse files array and build metadata dict
        metadata: dict[str, FileMetadata] = {}
        files = data.get("files", [])
        for file_info in files:
            fn: str | None = file_info.get("key")
            checksum: str | None = file_info.get("checksum")
            size: int = file_info.get("size", 0)

            if not fn:
                continue

            metadata[fn] = FileMetadata(checksum=checksum, size=size)

        # Store in cache
        self._zenodo_record_cache[record_id] = metadata

        return metadata.get(filename)

    async def get_pypsa_metadata(self, path: str, netloc: str) -> FileMetadata | None:
        """
        Retrieve and cache file metadata from data.pypsa.org manifest.

        Args:
            path: Server path
            netloc: Network location (e.g., "data.pypsa.org")

        Returns:
            FileMetadata for the requested file, or None if not found
        """

        # Check cache first
        base_path = dirname(path)
        while base_path:
            if base_path in self._pypsa_manifest_cache:
                filename = relpath(path, base_path)
                return self._pypsa_manifest_cache[base_path].get(filename)
            base_path = dirname(base_path)

        # Fetch manifest
        base_path = dirname(path)
        while base_path:
            manifest_url = f"https://{netloc}/{base_path}/manifest.yaml"

            async with self.httpr("get", manifest_url) as response:
                if response.status_code == 200:
                    content = await response.aread()
                    data = yaml.safe_load(content)
                    break

            base_path = dirname(base_path)
        else:
            raise WorkflowError(
                f"Failed to fetch data.pypsa.org manifest for https://{netloc}/{path}"
            )

        # Parse files array and build metadata dict
        metadata: dict[str, FileMetadata] = {}
        files = data.get("files", {})
        for filename, file_info in files.items():
            redirect: str | None = file_info.get("redirect")
            checksum: str | None = file_info.get("checksum")
            size: int = file_info.get("size", 0)

            if redirect is not None:
                redirect = normpath(join(base_path, redirect))

            metadata[filename] = FileMetadata(
                checksum=checksum, size=size, redirect=redirect
            )

        # Store in cache
        self._pypsa_manifest_cache[base_path] = metadata

        filename = relpath(path, base_path)
        return metadata.get(filename)

    async def get_gcs_metadata(self, path: str, netloc: str) -> FileMetadata | None:
        """
        Retrieve and cache file metadata from Google Cloud Storage.

        Uses the GCS JSON API to fetch object metadata including MD5 hash.
        URL format: https://storage.googleapis.com/{bucket}/{object-path}
        API endpoint: https://storage.googleapis.com/storage/v1/b/{bucket}/o/{encoded-object}

        Args:
            path: Server path (bucket/object-path)
            netloc: Network location (storage.googleapis.com)

        Returns:
            FileMetadata for the requested file, or None if not found
        """
        # Check cache first
        if path in self._gcs_metadata_cache:
            return self._gcs_metadata_cache[path]

        # Parse bucket and object path from the URL path
        # Path format: /{bucket}/{object-path}
        parts = path.split("/", maxsplit=1)
        if len(parts) < 2:
            raise WorkflowError(
                f"Invalid GCS URL format: http(s)://{netloc}/{path}. "
                f"Expected format: https://storage.googleapis.com/{{bucket}}/{{object-path}}"
            )

        bucket, object_path = parts

        # URL-encode the object path for the API request (slashes must be encoded)
        encoded_object = quote(object_path, safe="")

        # GCS JSON API endpoint for object metadata
        api_url = f"https://{netloc}/storage/v1/b/{bucket}/o/{encoded_object}"

        async with self.httpr("get", api_url) as response:
            if response.status_code == 404:
                return None
            if response.status_code != 200:
                raise WorkflowError(
                    f"Failed to fetch GCS object metadata: HTTP {response.status_code} ({api_url})"
                )

            content = await response.aread()
            data = json.loads(content)

        # GCS returns MD5 as base64-encoded bytes
        md5_base64: str | None = data.get("md5Hash")
        checksum: str | None = None
        if md5_base64:
            # Convert base64 to hex digest
            md5_bytes = base64.b64decode(md5_base64)
            checksum = f"md5:{md5_bytes.hex()}"

        size: int = int(data.get("size", 0))

        updated: str | None = data.get("updated")
        mtime: float = datetime.fromisoformat(updated).timestamp() if updated else 0

        metadata = FileMetadata(checksum=checksum, size=size, mtime=mtime)

        # Store in cache
        self._gcs_metadata_cache[path] = metadata

        return metadata


# Implementation of storage object
class StorageObject(StorageObjectRead):
    provider: StorageProvider  # pyright: ignore[reportIncompatibleVariableOverride]
    netloc: str
    path: str

    def __post_init__(self):
        super().__post_init__()

        # Parse URL to extract record ID and filename
        # URL format: https://zenodo.org/records/{record_id}/files/{filename}
        parsed = urlparse(str(self.query))
        self.netloc = parsed.netloc
        self.path = parsed.path.strip("/")

    @override
    def local_suffix(self) -> str:
        """Return the local suffix for this object (used by parent class)."""
        return f"{self.netloc}{self.path}"

    @override
    def get_inventory_parent(self) -> str | None:
        """Return the parent directory of this object."""
        # this is optional and can be left as is
        return None

    @override
    async def managed_exists(self) -> bool:
        if self.provider.settings.skip_remote_checks:
            return True

        if self.provider.cache:
            cached = self.provider.cache.get(str(self.query))
            if cached is not None and self.provider.is_immutable(self.netloc):
                return True

        metadata = await self.provider.get_metadata(self.path, self.netloc)
        return metadata is not None

    def _cached_immutable(self) -> Path | None:
        """The cached copy of an immutable file, or None.

        Zenodo and data.pypsa.org files never change, so a cached copy answers
        existence, size, mtime and retrieval without a request to the record
        API; a workflow whose inputs are all cached then builds its DAG and
        runs offline.
        """
        if self.provider.cache is None or not self.provider.is_immutable(
            self.netloc
        ):
            return None
        return self.provider.cache.get(str(self.query))

    @override
    async def managed_mtime(self) -> float:
        if self.provider.settings.skip_remote_checks:
            return 0

        cached = self._cached_immutable()
        if cached is not None:
            return cached.stat().st_mtime

        metadata = await self.provider.get_metadata(self.path, self.netloc)
        return metadata.mtime if metadata is not None else 0

    @override
    async def managed_size(self) -> int:
        if self.provider.settings.skip_remote_checks:
            return 0

        if self.provider.cache:
            cached = self.provider.cache.get(str(self.query))
            if cached is not None and self.provider.is_immutable(self.netloc):
                return cached.stat().st_size
        else:
            cached = None

        metadata = await self.provider.get_metadata(self.path, self.netloc)
        if metadata is None:
            return 0

        if cached is not None:
            if cached.stat().st_mtime >= metadata.mtime:
                return cached.stat().st_size

        return metadata.size

    @override
    async def inventory(self, cache: IOCacheStorageInterface) -> None:
        """
        Gather file metadata (existence, size) from cache or remote.
        Checks local cache first, then queries remote if needed.
        """
        key = self.cache_key()
        if key in cache.exists_in_storage:
            # Already inventorized
            return

        if self.provider.settings.skip_remote_checks:
            cache.exists_in_storage[key] = True
            cache.mtime[key] = Mtime(storage=0)
            cache.size[key] = 0
            return

        if self.provider.cache:
            cached = self.provider.cache.get(str(self.query))
            if cached is not None and self.provider.is_immutable(self.netloc):
                cache.exists_in_storage[key] = True
                cache.mtime[key] = Mtime(storage=cached.stat().st_mtime)
                cache.size[key] = cached.stat().st_size
                return
        else:
            cached = None

        metadata = await self.provider.get_metadata(self.path, self.netloc)
        if metadata is None:
            cache.exists_in_storage[key] = False
            cache.mtime[key] = Mtime(storage=0)
            cache.size[key] = 0
            return

        if cached is not None:
            if cached.stat().st_mtime >= metadata.mtime:
                cache.exists_in_storage[key] = True
                cache.mtime[key] = Mtime(storage=cached.stat().st_mtime)
                cache.size[key] = cached.stat().st_size
                return

        cache.exists_in_storage[key] = True
        cache.mtime[key] = Mtime(storage=metadata.mtime)
        cache.size[key] = metadata.size

    @override
    def cleanup(self):
        """Nothing to cleanup"""
        pass

    @override
    def exists(self) -> bool:
        raise NotImplementedError()

    @override
    def size(self) -> int:
        raise NotImplementedError()

    @override
    def mtime(self) -> float:
        raise NotImplementedError()

    @override
    def retrieve_object(self) -> None:
        raise NotImplementedError()

    async def verify_checksum(self, path: Path) -> None:
        """
        Verify `path` against checksum provided by zenodo metadata.

        Raises:
            WrongChecksum
        """
        # Get cached or fetch record metadata
        metadata = await self.provider.get_metadata(self.path, self.netloc)
        if metadata is None:
            raise WorkflowError(f"No metadata found for {self.query}")

        checksum = metadata.checksum
        if checksum is None:
            return

        digest, checksum_expected = checksum.split(":", maxsplit=1)

        def compute_hash(path: Path = path, digest: str = digest):
            with open(path, "rb") as f:
                return hashlib.file_digest(f, digest).hexdigest().lower()

        # Compute checksum asynchronously (hashlib releases GIL)
        # checksum_observed = await asyncio.to_thread(compute_hash)
        checksum_observed = compute_hash(path, digest)

        if checksum_expected != checksum_observed:
            raise WrongChecksum(observed=checksum_observed, expected=checksum_expected)

    @retry_decorator
    async def managed_retrieve(self):
        """Async download with concurrency control and progress bar"""
        local_path = self.local_path()
        local_path.parent.mkdir(parents=True, exist_ok=True)

        query = str(self.query)
        filename = basename(self.path)

        # A cached immutable file needs no metadata: the record API only
        # supplies a redirect and a checksum, and the cached copy was verified
        # against that checksum when it was stored.
        cached = self._cached_immutable()
        if cached is not None:
            logger.info(f"Retrieved {filename} from cache ({query})")
            shutil.copy2(cached, local_path)
            return

        metadata = await self.provider.get_metadata(self.path, self.netloc)
        if metadata is not None and metadata.redirect is not None:
            query = f"https://{self.netloc}/{metadata.redirect}"

        # If already in cache, check if still valid
        if self.provider.cache:
            cached = self.provider.cache.get(query)
            if cached is not None:
                if self.provider.is_immutable(self.netloc) or (
                    metadata is not None and cached.stat().st_mtime >= metadata.mtime
                ):
                    logger.info(f"Retrieved {filename} from cache ({query})")
                    shutil.copy2(cached, local_path)
                    return

        try:
            # Download using a get request, rate limit errors are detected and raise
            # WorkflowError to trigger a retry
            async with self.provider.httpr("get", query) as response:
                if response.status_code != 200:
                    raise WorkflowError(
                        f"Failed to download: HTTP {response.status_code} ({query})"
                    )

                total_size = int(response.headers.get("content-length", 0))

                # Download to local path with progress bar
                with local_path.open(mode="wb") as f:
                    with tqdm(
                        total=total_size,
                        unit="B",
                        unit_scale=True,
                        desc=filename,
                        position=None,
                        leave=True,
                    ) as pbar:
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            f.write(chunk)
                            pbar.update(len(chunk))

            await self.verify_checksum(local_path)

            # Copy to cache after successful verification
            if self.provider.cache:
                self.provider.cache.put(query, local_path)

        except:
            if local_path.exists():
                local_path.unlink()
            raise
