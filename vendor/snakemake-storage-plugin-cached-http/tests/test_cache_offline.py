# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""
A cached immutable file is served without a request to the record API.

The fork's patch (README.md): `managed_mtime` and `managed_retrieve` consult
the download cache before fetching metadata for zenodo.org and data.pypsa.org,
so a workflow whose inputs are all cached builds its DAG and retrieves them
while the API is down. Offline by construction: no test here reaches the
network, and the mutable source (GCS) keeps asking.
"""

import logging
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import pytest
from snakemake_storage_plugin_cached_http import (
    StorageObject,
    StorageProvider,
    StorageProviderSettings,
)

IMMUTABLE = {
    "zenodo": "https://zenodo.org/records/4767098/files/IGGIELGN.zip",
    "pypsa": "https://data.pypsa.org/workflows/eur/attributed_ports/2020-07-10/attributed_ports.json",
}
MUTABLE = "https://storage.googleapis.com/open-tyndp-data-store/cached-http/attributed_ports/archive/2020-07-10/attributed_ports.json"


@contextmanager
def offline(provider: StorageProvider) -> Iterator[None]:
    """
    Fail any HTTP request the provider makes.

    Unlike `tests.conftest.assert_no_http_requests`, the stand-in is an async
    context manager like `httpr` itself, so a request raises the
    AssertionError (which the retry decorator does not catch) instead of a
    TypeError on `async with`.
    """

    @asynccontextmanager
    async def refuse(method: str, url: str):
        raise AssertionError(f"HTTP request made: {method} {url}")
        yield  # pragma: no cover

    original = provider.httpr
    provider.httpr = refuse  # type: ignore[method-assign]
    try:
        yield
    finally:
        provider.httpr = original  # type: ignore[method-assign]


def _provider(tmp_path: Path) -> StorageProvider:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    local_prefix = tmp_path / "local"
    local_prefix.mkdir()
    return StorageProvider(
        local_prefix=local_prefix,
        logger=logging.getLogger("test"),
        settings=StorageProviderSettings(
            cache=str(cache_dir), skip_remote_checks=False, max_concurrent_downloads=3
        ),
    )


def _cached(provider: StorageProvider, url: str, content: bytes) -> Path:
    """Place `content` in the download cache as the copy of `url`."""
    assert provider.cache is not None
    path = provider.cache._get_cache_path(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _object(provider: StorageProvider, url: str) -> StorageObject:
    return StorageObject(query=url, keep_local=False, retrieve=True, provider=provider)


@pytest.mark.asyncio
@pytest.mark.parametrize("url", IMMUTABLE.values(), ids=IMMUTABLE.keys())
async def test_a_cached_immutable_file_is_retrieved_without_the_api(tmp_path, url):
    provider = _provider(tmp_path)
    _cached(provider, url, b"cached bytes")
    obj = _object(provider, url)

    with offline(provider):
        await obj.managed_retrieve()

    assert obj.local_path().read_bytes() == b"cached bytes"


@pytest.mark.asyncio
@pytest.mark.parametrize("url", IMMUTABLE.values(), ids=IMMUTABLE.keys())
async def test_a_cached_immutable_file_answers_mtime_without_the_api(tmp_path, url):
    provider = _provider(tmp_path)
    cached = _cached(provider, url, b"cached bytes")
    obj = _object(provider, url)

    with offline(provider):
        mtime = await obj.managed_mtime()
        exists = await obj.managed_exists()
        size = await obj.managed_size()

    assert mtime == cached.stat().st_mtime
    assert exists is True
    assert size == len(b"cached bytes")


@pytest.mark.asyncio
async def test_an_uncached_immutable_file_still_asks_the_api(tmp_path):
    provider = _provider(tmp_path)
    obj = _object(provider, IMMUTABLE["zenodo"])

    with offline(provider), pytest.raises(AssertionError, match="HTTP request made"):
        await obj.managed_mtime()


@pytest.mark.asyncio
async def test_a_cached_mutable_file_still_asks_the_api(tmp_path):
    provider = _provider(tmp_path)
    _cached(provider, MUTABLE, b"cached bytes")
    obj = _object(provider, MUTABLE)

    with offline(provider), pytest.raises(AssertionError, match="HTTP request made"):
        await obj.managed_mtime()
