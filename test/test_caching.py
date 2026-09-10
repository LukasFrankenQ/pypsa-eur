# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""Harness for testing Snakemake's between-workflow caching (--cache) on this workflow.

The helpers here build the DAG through the Snakemake Python API (snakemake.api) with
a cache directory pointed at a temporary path, and inspect which jobs would read from
or write to the output file cache. Later tasks that add `cache: True` to specific
rules import these helpers to assert on cache keys and cache stability.
"""

import os
import subprocess
from pathlib import Path

import pytest
from snakemake.api import SnakemakeApi
from snakemake.settings.types import (
    ConfigSettings,
    DAGSettings,
    OutputSettings,
    ResourceSettings,
    WorkflowSettings,
)

ROOT = Path(__file__).resolve().parent.parent
ELEC_CONFIG = Path("config/test/config.electricity.yaml")

# A small, network-free target used to sanity-check the harness itself.
SMALL_TARGETS = ["resources/test-elec/networks/base.nc"]

CacheKey = tuple[str, tuple[tuple[str, str], ...]]


def cache_entries(
    configfile: Path,
    targets: list[str],
    cache_dir: Path,
    overrides: dict | None = None,
) -> dict[CacheKey, list[Path]]:
    """Build the DAG for targets and return each cacheable job's cache file paths.

    Keyed by (rule name, sorted wildcard items). Only jobs whose rule is marked
    eligible for caching (rule.cache.output) are included. Runs with --cache
    (WorkflowSettings(cache=[])), so a rule's `cache: True` directive is enough
    to make it eligible without naming it explicitly.
    """
    os.environ["SNAKEMAKE_OUTPUT_CACHE"] = str(cache_dir)

    entries: dict[CacheKey, list[Path]] = {}
    with SnakemakeApi(OutputSettings()) as snakemake_api:
        workflow_api = snakemake_api.workflow(
            resource_settings=ResourceSettings(cores=1),
            config_settings=ConfigSettings(
                configfiles=[ROOT / configfile], config=overrides or {}
            ),
            workflow_settings=WorkflowSettings(cache=[]),
            workdir=ROOT,
        )
        workflow_api.dag(dag_settings=DAGSettings(targets=targets))
        workflow = workflow_api._workflow
        # Mirrors what Workflow.execute() does before _build_dag(), minus actually
        # running anything: it is what turns on the output file cache and the
        # per-rule cache flag for --cache.
        workflow._prepare_dag(
            forceall=False, ignore_incomplete=False, lock_warn_only=True
        )
        workflow._build_dag()

        for job in workflow.dag.jobs:
            if not (job.rule.cache and job.rule.cache.output):
                continue
            key: CacheKey = (job.rule.name, tuple(sorted(job.wildcards.items())))
            files = workflow.async_run(
                workflow.output_file_cache.get_outputfiles_and_cachefiles(job)
            )
            entries[key] = [cachefile for _, cachefile in files]

    return entries


def _provenance_hash(cachefile: Path) -> str:
    """Return the provenance hash prefix of a cache file name."""
    name = cachefile.name
    end = min((i for i in (name.find("_"), name.find(".")) if i != -1), default=len(name))
    return name[:end]


def cache_keys(
    configfile: Path,
    targets: list[str],
    cache_dir: Path,
    overrides: dict | None = None,
) -> dict[CacheKey, str]:
    """Same as cache_entries, but map each job to its provenance hash instead of paths."""
    return {
        key: _provenance_hash(files[0])
        for key, files in cache_entries(configfile, targets, cache_dir, overrides).items()
    }


def dry_run(
    configfile: Path, targets: list[str], cache_dir: Path, extra: list[str] = ()
) -> str:
    """Run `snakemake -n --cache` as a subprocess and return its combined output."""
    env = os.environ.copy()
    env["SNAKEMAKE_OUTPUT_CACHE"] = str(cache_dir)
    cmd = ["snakemake", "-n", "--cache", "--configfile", str(configfile), *extra, *targets]
    result = subprocess.run(
        cmd, cwd=ROOT, env=env, capture_output=True, text=True, check=False
    )
    return result.stdout + result.stderr


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    path = tmp_path / "cache"
    path.mkdir()
    return path


def test_caching_switch_refuses_scenarios(cache_dir: Path) -> None:
    output = dry_run(
        ELEC_CONFIG,
        [],
        cache_dir,
        extra=["--config", 'run={"scenarios": {"enable": true}}'],
    )
    assert (
        "Between-workflow caching (--cache) does not support run.scenarios.enable"
        in output
    )


def test_caching_switch_no_cache_lines_without_flag() -> None:
    result = subprocess.run(
        ["snakemake", "-n", "--configfile", str(ELEC_CONFIG)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0
    # Matplotlib's own font-cache banner is unrelated noise; exclude it so this
    # only fails on genuine Snakemake caching output.
    relevant_lines = (
        line for line in output.splitlines() if "matplotlib" not in line.lower()
    )
    assert not any("cach" in line.lower() for line in relevant_lines)


def test_cache_keys_harness_empty_before_any_rule_cached(cache_dir: Path) -> None:
    assert cache_keys(ELEC_CONFIG, SMALL_TARGETS, cache_dir) == {}
