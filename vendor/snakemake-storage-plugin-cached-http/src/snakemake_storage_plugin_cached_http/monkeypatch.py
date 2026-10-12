# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""
Monkey-patch the HTTP storage plugin to avoid conflicts with Zenodo URLs.

This module patches snakemake-storage-plugin-http to refuse zenodo.org URLs,
ensuring they are handled exclusively by the cached-http plugin.
"""

from urllib.parse import urlparse

import snakemake_storage_plugin_http as http_base
from snakemake_interface_storage_plugins.storage_provider import (
    StorageQueryValidationResult,
)


def is_pypsa_or_zenodo_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc in (
        "zenodo.org",
        "sandbox.zenodo.org",
        "data.pypsa.org",
        "storage.googleapis.com",
    ) and parsed.scheme in (
        "http",
        "https",
    )


# Patch the original HTTP StorageProvider to refuse zenodo URLs
orig_valid_query = http_base.StorageProvider.is_valid_query
http_base.StorageProvider.is_valid_query = classmethod(
    lambda c, q: (
        StorageQueryValidationResult(
            query=q,
            valid=False,
            reason="Deactivated in favour of cached_http",
        )
        if is_pypsa_or_zenodo_url(q)
        else orig_valid_query(q)
    )
)
