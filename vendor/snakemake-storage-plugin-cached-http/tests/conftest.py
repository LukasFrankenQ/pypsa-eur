# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""Pytest configuration and shared fixtures."""

from contextlib import contextmanager


@contextmanager
def assert_no_http_requests(provider):
    """
    Context manager that fails if any HTTP requests are made.

    Usage:
        with assert_no_http_requests(storage_provider):
            await obj.managed_retrieve()  # Should use cache, not HTTP
    """
    original_httpr = provider.httpr

    async def httpr_should_not_be_called(*args, **kwargs):
        raise AssertionError("HTTP request made when none was expected")

    provider.httpr = httpr_should_not_be_called

    try:
        yield
    finally:
        provider.httpr = original_httpr
