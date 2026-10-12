<!--
SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
SPDX-License-Identifier: CC-BY-4.0
-->

# Snakemake Storage Plugin: Cached HTTP

> **Vendored copy.** This is [PyPSA/snakemake-storage-plugin-cached-http](https://github.com/PyPSA/snakemake-storage-plugin-cached-http)
> at v0.3 (`99a38cc2`), installed into the fork's pixi environment as a path dependency
> (`pixi.toml`, `[pypi-dependencies]`) in place of the bioconda package, with one change:
> `managed_mtime` and `managed_retrieve` consult the download cache before the record API for
> the immutable hosts (zenodo.org, data.pypsa.org). Upstream fetches the record metadata first,
> so a workdir whose retrieve rules have run once asks Zenodo for every storage input at every
> DAG build, and a Zenodo outage (HTTP 504, 2026-10-12) fails the run with every file cached.
> Tests for the change: `tests/test_cache_offline.py`. Drop the directory and restore the
> conda dependency once a release carries the fix.

A Snakemake storage plugin for downloading files via HTTP with local caching, checksum verification, and adaptive rate limiting.

**Supported sources:**
- **zenodo.org** - Zenodo data repository (checksum from API)
- **data.pypsa.org** - PyPSA data repository (checksum from manifest.yaml)
- **storage.googleapis.com** - Google Cloud Storage (checksum from GCS JSON API)

## Features

- **Local caching**: Downloads are cached to avoid redundant transfers (can be disabled)
- **Checksum verification**: Automatically verifies checksums (from Zenodo API, data.pypsa.org manifests, or GCS object metadata)
- **Rate limit handling**: Automatically respects Zenodo's rate limits using `X-RateLimit-*` headers with exponential backoff retry
- **Concurrent download control**: Limits simultaneous downloads to prevent overwhelming servers
- **Progress bars**: Shows download progress with tqdm
- **Immutable URLs**: Returns mtime=0 for Zenodo and data.pypsa.org (persistent URLs); uses actual mtime for GCS
- **Environment variable support**: Configure via environment variables for CI/CD workflows

## Installation

From the pypsa-eur repository root:

```bash
pip install -e plugins/snakemake-storage-plugin-cached-http
```

## Configuration

The Zenodo storage plugin works alongside other storage providers (like HTTP). Snakemake automatically routes URLs to the correct provider based on the URL pattern.

Register additional settings in your Snakefile if you want to customize the defaults:

```python
# Optional: Configure cached HTTP storage with custom settings
# This extends the existing storage configuration (e.g., for HTTP)
storage cached_http:
    provider="cached-http",
    cache="~/.cache/snakemake-pypsa-eur",  # Default location
    max_concurrent_downloads=3,  # Download max 3 files at once
```

If you don't explicitly configure it, the plugin will use default settings automatically.

### Settings

- **cache** (optional): Cache directory for downloaded files
  - Default: Platform-dependent user cache directory (via `platformdirs.user_cache_dir("snakemake-pypsa-eur")`)
  - Set to `""` (empty string) to disable caching
  - Files are cached here to avoid re-downloading
  - Environment variable: `SNAKEMAKE_STORAGE_CACHED_HTTP_CACHE`

- **skip_remote_checks** (optional): Skip metadata checking with remote API
  - Default: `False` (perform checks)
  - Set to `True` or `"1"` to skip remote existence/size checks (useful for CI/CD)
  - Environment variable: `SNAKEMAKE_STORAGE_CACHED_HTTP_SKIP_REMOTE_CHECKS`

- **max_concurrent_downloads** (optional): Maximum concurrent downloads
  - Default: `3`
  - Controls how many files can be downloaded simultaneously
  - No environment variable support

## Usage

Use Zenodo, data.pypsa.org, or Google Cloud Storage URLs directly in your rules. Snakemake automatically detects supported URLs and routes them to this plugin:

```python
rule download_zenodo:
    input:
        storage("https://zenodo.org/records/3520874/files/natura.tiff"),
    output:
        "resources/natura.tiff"
    shell:
        "cp {input} {output}"

rule download_pypsa:
    input:
        storage("https://data.pypsa.org/workflows/eur/eez/v12_20231025/World_EEZ_v12_20231025_LR.zip"),
    output:
        "resources/eez.zip"
    shell:
        "cp {input} {output}"

rule download_gcs:
    input:
        storage("https://storage.googleapis.com/open-tyndp-data-store/CBA_projects.zip"),
    output:
        "resources/cba_projects.zip"
    shell:
        "cp {input} {output}"
```

Or if you configured a tagged storage entity:

```python
rule download_data:
    input:
        storage.cached_http(
            "https://zenodo.org/records/3520874/files/natura.tiff"
        ),
    output:
        "resources/natura.tiff"
    shell:
        "cp {input} {output}"
```

The plugin will:
1. Check if the file exists in the cache (if caching is enabled)
2. If cached, copy from cache (fast)
3. If not cached, download with:
   - Progress bar showing download status
   - Automatic rate limit handling with exponential backoff retry
   - Concurrent download limiting
   - Checksum verification (from Zenodo API, data.pypsa.org manifest, or GCS metadata)
4. Store in cache for future use (if caching is enabled)

### Example: CI/CD Configuration

For continuous integration environments where you want to skip caching and remote checks:

```yaml
# GitHub Actions example
- name: Run snakemake workflows
  env:
    SNAKEMAKE_STORAGE_CACHED_HTTP_CACHE: ""
    SNAKEMAKE_STORAGE_CACHED_HTTP_SKIP_REMOTE_CHECKS: "1"
  run: |
    snakemake --cores all
```

## Rate Limiting and Retry

Zenodo API limits:
- **Guest users**: 60 requests/minute
- **Authenticated users**: 100 requests/minute

The plugin automatically:
- Monitors `X-RateLimit-Remaining` header
- Waits when rate limit is reached
- Uses `X-RateLimit-Reset` to calculate wait time
- Retries failed requests with exponential backoff (up to 5 attempts)
- Handles transient errors: HTTP errors, timeouts, checksum mismatches, and network issues

## URL Handling

- Handles URLs from `zenodo.org`, `sandbox.zenodo.org`, `data.pypsa.org`, and `storage.googleapis.com`
- Other HTTP(S) URLs are handled by the standard `snakemake-storage-plugin-http`
- Both plugins can coexist in the same workflow

### Plugin Priority

When using `storage()` without specifying a plugin name, Snakemake checks all installed plugins:
- **Cached HTTP plugin**: Only accepts zenodo.org, data.pypsa.org, and storage.googleapis.com URLs
- **HTTP plugin**: Accepts all HTTP/HTTPS URLs (including zenodo.org)

If both plugins are installed, supported URLs would be ambiguous - both plugins accept them.
Typically snakemake would raise an error: **"Multiple suitable storage providers found"** if you try to use `storage()` without specifying which plugin to use, ie. one needs to explicitly call the Cached HTTP provider using `storage.cached_http(url)` instead of `storage(url)`,
but we monkey-patch the http plugin to refuse zenodo.org, data.pypsa.org, and storage.googleapis.com URLs.

## License

MIT License
