# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""
Harness for testing Snakemake's between-workflow caching (--cache) on this workflow.

Builds the DAG through the Snakemake Python API with a cache directory pointed at a
temporary path, and inspects which jobs would read from or write to the output file
cache.
"""

import os
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
from snakemake.api import SnakemakeApi
from snakemake.caching.hash import ProvenanceHashMap
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
    """
    Build the DAG for targets and return each cacheable job's cache file paths.

    Keyed by (rule name, sorted wildcard items). Only jobs whose rule is marked
    eligible for caching (rule.cache.output) are included. Runs with --cache
    (WorkflowSettings(cache=[])), so a rule's `cache: True` directive is enough
    to make it eligible without naming it explicitly.
    """
    prior_cache_env = os.environ.get("SNAKEMAKE_OUTPUT_CACHE")
    os.environ["SNAKEMAKE_OUTPUT_CACHE"] = str(cache_dir)
    try:
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
            # Mirrors Workflow.execute() up to _build_dag(), without running anything,
            # to turn on the output file cache and the per-rule cache flag for --cache.
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
    finally:
        if prior_cache_env is None:
            os.environ.pop("SNAKEMAKE_OUTPUT_CACHE", None)
        else:
            os.environ["SNAKEMAKE_OUTPUT_CACHE"] = prior_cache_env


def _provenance_hash(cachefile: Path) -> str:
    """Return the provenance hash prefix of a cache file name."""
    name = cachefile.name
    end = min(
        (i for i in (name.find("_"), name.find(".")) if i != -1), default=len(name)
    )
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
        for key, files in cache_entries(
            configfile, targets, cache_dir, overrides
        ).items()
    }


def dry_run(
    configfile: Path,
    targets: list[str],
    cache_dir: Path,
    extra: Sequence[str] = (),
) -> str:
    """Run `snakemake -n --cache` as a subprocess and return its combined output."""
    env = os.environ.copy()
    env["SNAKEMAKE_OUTPUT_CACHE"] = str(cache_dir)
    # --configfile takes nargs="+" and would otherwise swallow the targets too.
    # "--" marks the rest of argv as explicit targets regardless of what precedes it.
    cmd = [
        "snakemake",
        "-n",
        "--cache",
        "--configfile",
        str(configfile),
        *extra,
        "--",
        *targets,
    ]
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
    # Matplotlib's font-cache banner is unrelated noise, excluded so this only
    # fails on genuine Snakemake caching output. A `cache: True` rule must always
    # report as cache-eligible in dry runs, with or without --cache, so that line
    # here is expected, not the fork diverging from upstream.
    relevant_lines = (
        line
        for line in output.splitlines()
        if "matplotlib" not in line.lower()
        and "eligible for caching between workflows" not in line
    )
    assert not any("cach" in line.lower() for line in relevant_lines)


def test_cache_keys_harness_empty_before_any_rule_cached(cache_dir: Path) -> None:
    assert cache_keys(ELEC_CONFIG, SMALL_TARGETS, cache_dir) == {}


def _has_storage_input(rule) -> bool:
    """Return whether any of the rule's raw (unresolved) input items is storage()."""
    return any(getattr(item, "is_storage", False) for item in rule.input)


def test_every_storage_rule_marks_hash_omit_storage_content() -> None:
    """
    Every rule with a storage() input must carry the omit-storage-content flag.

    Otherwise a cached downstream job's provenance hash would content-hash the
    storage input, forcing a download even when the server reports no checksum.
    """
    with SnakemakeApi(OutputSettings()) as snakemake_api:
        workflow_api = snakemake_api.workflow(
            resource_settings=ResourceSettings(cores=1),
            config_settings=ConfigSettings(configfiles=[ROOT / ELEC_CONFIG], config={}),
            workflow_settings=WorkflowSettings(),
            workdir=ROOT,
        )
        workflow = workflow_api._workflow
        offenders = [
            rule.name
            for rule in workflow.rules
            if _has_storage_input(rule)
            and not (rule.cache and rule.cache.omit_storage_content)
        ]

    assert offenders == []


RULE_HEADER_RE = re.compile(r"^\s*rule\s+(\w+)\s*:\s*$")
DIRECTIVE_RE = re.compile(
    r"^\s*(output|params|log|threads|resources|shell|script|run|conda|"
    r"notebook|message|benchmark|wildcard_constraints|priority|retries|group|"
    r"localrule|handover|envmodules|cache|shadow):"
)
INPUT_HEADER_RE = re.compile(r"^\s*input:\s*$")


def _rule_blocks(text: str) -> list[tuple[str, str]]:
    """
    Split a rules/*.smk file's text into (name, block text) pairs.

    A block starts at a `rule <name>:` line and runs to the line before the
    next one, or the end of the file. Matching ignores the line's own
    indentation, since a rule is often nested inside an `if` block that
    gates an alternate data source.
    """
    lines = text.splitlines()
    starts = [
        (i, m.group(1))
        for i, line in enumerate(lines)
        if (m := RULE_HEADER_RE.match(line))
    ]
    bounds = [start for start, _ in starts] + [len(lines)]
    return [
        (name, "\n".join(lines[bounds[idx] : bounds[idx + 1]]))
        for idx, (_, name) in enumerate(starts)
    ]


def _input_section(block: str) -> str:
    """Return a rule block's input: section text, or "" if it has none."""
    lines = block.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if INPUT_HEADER_RE.match(line)), None
    )
    if start is None:
        return ""
    end = next(
        (
            i
            for i, line in enumerate(lines[start + 1 :], start + 1)
            if DIRECTIVE_RE.match(line)
        ),
        len(lines),
    )
    return "\n".join(lines[start + 1 : end])


def test_every_storage_rule_marks_hash_omit_storage_content_branch_blind() -> None:
    """
    Text-level companion to test_every_storage_rule_marks_hash_omit_storage_content.

    That test only sees rules reachable from the elec test config's DAG, so a
    rule gated behind a config branch that config does not take (an alternate
    dataset source, for example) never gets its storage() input checked. This
    scans the raw rules/*.smk text instead, with no DAG build and no
    snakemake import, so it covers every branch regardless of which one the
    active config selects.
    """
    offenders = []
    for path in sorted((ROOT / "rules").glob("*.smk")):
        for name, block in _rule_blocks(path.read_text()):
            if (
                "storage(" in _input_section(block)
                and "hash-omit-storage-content" not in block
            ):
                offenders.append(f"{path.name}:{name}")

    assert offenders == []


def test_retrieve_cutout_hashes_without_storage_content(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Hashing a storage-input job must not need the storage object's content.

    Provenance hashing runs before a job would ever execute and fetch its input,
    so without `hash-omit-storage-content` it tries to checksum an unretrieved
    storage placeholder and fails with a broken-symlink WorkflowError.
    """
    monkeypatch.setenv("SNAKEMAKE_OUTPUT_CACHE", str(cache_dir))
    with SnakemakeApi(OutputSettings()) as snakemake_api:
        workflow_api = snakemake_api.workflow(
            resource_settings=ResourceSettings(cores=1),
            config_settings=ConfigSettings(configfiles=[ROOT / ELEC_CONFIG], config={}),
            workflow_settings=WorkflowSettings(cache=[]),
            workdir=ROOT,
        )
        workflow = workflow_api._workflow
        rule = workflow.get_rule("retrieve_cutout")
        cutout = workflow.config["atlite"]["default_cutout"]
        target = str(rule.output[0]).format(cutout=cutout)

        workflow_api.dag(dag_settings=DAGSettings(targets=[target]))
        workflow._prepare_dag(
            forceall=False, ignore_incomplete=False, lock_warn_only=True
        )
        workflow._build_dag()

        job = next(j for j in workflow.dag.jobs if j.rule.name == "retrieve_cutout")
        storage_dir = ROOT / ".snakemake" / "storage"
        before = set(storage_dir.rglob("*")) if storage_dir.exists() else set()

        provenance_hash = workflow.async_run(
            ProvenanceHashMap().get_provenance_hash(job)
        )

        after = set(storage_dir.rglob("*")) if storage_dir.exists() else set()

    assert provenance_hash
    assert after == before


def _renewable_profile_targets(run_name: str = "test-elec") -> list[str]:
    """Build_renewable_profiles targets for onwind and solar under a given run name."""
    return [
        f"resources/{run_name}/profile_onwind.nc",
        f"resources/{run_name}/profile_solar.nc",
    ]


def _renewable_profile_key(technology: str) -> CacheKey:
    return ("build_renewable_profiles", (("technology", technology),))


def test_atlite_renewable_profile_keys_differ_by_technology(cache_dir: Path) -> None:
    keys = cache_keys(ELEC_CONFIG, _renewable_profile_targets(), cache_dir)
    assert (
        keys[_renewable_profile_key("onwind")] != keys[_renewable_profile_key("solar")]
    )


def test_atlite_renewable_profile_key_reacts_only_to_its_own_technology(
    cache_dir: Path,
) -> None:
    baseline = cache_keys(ELEC_CONFIG, _renewable_profile_targets(), cache_dir)
    overridden = cache_keys(
        ELEC_CONFIG,
        _renewable_profile_targets(),
        cache_dir,
        overrides={"renewable": {"solar": {"capacity_per_sqkm": 12345}}},
    )
    onwind, solar = _renewable_profile_key("onwind"), _renewable_profile_key("solar")
    assert overridden[solar] != baseline[solar]
    assert overridden[onwind] == baseline[onwind]


def test_atlite_keys_unaffected_by_run_name(cache_dir: Path) -> None:
    baseline = cache_keys(ELEC_CONFIG, _renewable_profile_targets(), cache_dir)
    renamed = cache_keys(
        ELEC_CONFIG,
        _renewable_profile_targets("other-name"),
        cache_dir,
        overrides={"run": {"name": "other-name"}},
    )
    assert baseline == renamed


def test_atlite_dry_run_reports_cache_hit_per_technology(cache_dir: Path) -> None:
    targets = _renewable_profile_targets()
    entries = cache_entries(ELEC_CONFIG, targets, cache_dir)
    for cachefile in entries[_renewable_profile_key("onwind")]:
        cachefile.parent.mkdir(parents=True, exist_ok=True)
        cachefile.touch()

    output = dry_run(ELEC_CONFIG, targets, cache_dir)

    assert "resources/test-elec/profile_onwind.nc will be obtained from" in output
    assert "resources/test-elec/profile_solar.nc will be written to" in output


def _has_local_script_import(rule) -> bool:
    """Return whether a rule's script imports a scripts/ module other than _helpers."""
    script_path = ROOT / str(rule.script)
    text = script_path.read_text()
    return any(
        line.strip().startswith(("import scripts.", "from scripts."))
        and "scripts._helpers" not in line
        for line in text.splitlines()
    )


def test_atlite_chain_rules_are_cache_eligible_without_benchmark() -> None:
    """
    Every rule in the atlite chain is cache-eligible, benchmark-free and only
    depends on scripts/_helpers.py among local scripts modules.

    Cache eligibility and a clean code-dependency surface are prerequisites for
    the provenance hash to be both computable and correct.
    """
    atlite_rule_names = [
        "determine_availability_matrix",
        "determine_availability_matrix_MD_UA",
        "build_renewable_profiles",
        "build_hydro_profile",
        "build_line_rating",
        "build_hac_features",
    ]
    with SnakemakeApi(OutputSettings()) as snakemake_api:
        workflow_api = snakemake_api.workflow(
            resource_settings=ResourceSettings(cores=1),
            config_settings=ConfigSettings(configfiles=[ROOT / ELEC_CONFIG], config={}),
            workflow_settings=WorkflowSettings(),
            workdir=ROOT,
        )
        workflow = workflow_api._workflow
        rules = {name: workflow.get_rule(name) for name in atlite_rule_names}

        not_cacheable = [
            name
            for name, rule in rules.items()
            if not (rule.cache and rule.cache.output)
        ]
        still_benchmarked = [name for name, rule in rules.items() if rule.benchmark]
        importing_other_scripts = [
            name for name, rule in rules.items() if _has_local_script_import(rule)
        ]

    assert not_cacheable == []
    assert still_benchmarked == []
    assert importing_other_scripts == []


NETWORK_CHAIN_RULE_NAMES = [
    "simplify_network",
    "cluster_network",
    "compose_network",
    "solve_network",
    "solve_operations_network",
]


def _network_chain_targets(
    run_name: str = "test-elec", horizon: str = "2050"
) -> list[str]:
    """Outputs of the five network-chain rules for a given run name and horizon."""
    return [
        f"resources/{run_name}/networks/simplified.nc",
        f"resources/{run_name}/networks/clustered.nc",
        f"resources/{run_name}/networks/composed_{horizon}.nc",
        f"results/{run_name}/networks/solved_{horizon}.nc",
        f"results/{run_name}/networks/operations_{horizon}.nc",
    ]


def _network_chain_key(rule: str, horizon: str = "2050") -> CacheKey:
    """Cache key for a network-chain rule. Only the horizon-wildcarded rules take it."""
    if rule in ("compose_network", "solve_network", "solve_operations_network"):
        return (rule, (("horizon", horizon),))
    return (rule, ())


def test_network_chain_rules_are_cache_eligible_without_benchmark_or_shadow() -> None:
    """
    Every network-chain rule is cache-eligible and benchmark-free, and building
    the DAG under --cache resolves their shadow directive to None.

    simplify_network and cluster_network import each other's module, which is
    expected (unlike the atlite chain) and covered by their code_dependencies
    input instead of a zero-local-import rule.
    """
    with SnakemakeApi(OutputSettings()) as snakemake_api:
        workflow_api = snakemake_api.workflow(
            resource_settings=ResourceSettings(cores=1),
            config_settings=ConfigSettings(configfiles=[ROOT / ELEC_CONFIG], config={}),
            workflow_settings=WorkflowSettings(cache=[]),
            workdir=ROOT,
        )
        workflow = workflow_api._workflow
        rules = {name: workflow.get_rule(name) for name in NETWORK_CHAIN_RULE_NAMES}

        not_cacheable = [
            name
            for name, rule in rules.items()
            if not (rule.cache and rule.cache.output)
        ]
        still_benchmarked = [name for name, rule in rules.items() if rule.benchmark]
        still_shadowed = [name for name, rule in rules.items() if rule.shadow_depth]

    assert not_cacheable == []
    assert still_benchmarked == []
    assert still_shadowed == []


def test_network_chain_solver_name_changes_cluster_not_simplify(
    cache_dir: Path,
) -> None:
    targets = _network_chain_targets()
    baseline = cache_keys(ELEC_CONFIG, targets, cache_dir)
    overridden = cache_keys(
        ELEC_CONFIG,
        targets,
        cache_dir,
        overrides={"solving": {"solver": {"name": "cbc"}}},
    )
    cluster, simplify = (
        _network_chain_key("cluster_network"),
        _network_chain_key("simplify_network"),
    )
    assert overridden[cluster] != baseline[cluster]
    assert overridden[simplify] == baseline[simplify]


def test_network_chain_line_types_change_simplify_and_downstream(
    cache_dir: Path,
) -> None:
    targets = _network_chain_targets()
    baseline = cache_keys(ELEC_CONFIG, targets, cache_dir)
    overridden = cache_keys(
        ELEC_CONFIG,
        targets,
        cache_dir,
        overrides={"lines": {"types": {380.0: "94-AL1/15-ST1A 20.0"}}},
    )
    for rule in NETWORK_CHAIN_RULE_NAMES:
        key = _network_chain_key(rule)
        assert overridden[key] != baseline[key], rule


def test_network_chain_tech_colors_changes_compose_not_cluster(
    cache_dir: Path,
) -> None:
    targets = _network_chain_targets()
    baseline = cache_keys(ELEC_CONFIG, targets, cache_dir)
    overridden = cache_keys(
        ELEC_CONFIG,
        targets,
        cache_dir,
        overrides={"plotting": {"tech_colors": {"solar": "#000000"}}},
    )
    compose, cluster = (
        _network_chain_key("compose_network"),
        _network_chain_key("cluster_network"),
    )
    assert overridden[compose] != baseline[compose]
    assert overridden[cluster] == baseline[cluster]


def test_network_chain_run_name_leaves_keys_unchanged(cache_dir: Path) -> None:
    baseline = cache_keys(ELEC_CONFIG, _network_chain_targets(), cache_dir)
    renamed = cache_keys(
        ELEC_CONFIG,
        _network_chain_targets("other-name"),
        cache_dir,
        overrides={"run": {"name": "other-name"}},
    )
    assert baseline == renamed


SECTOR_RULE_NAMES = [
    "build_population_layouts",
    "build_daily_heat_demand",
    "build_temperature_profiles",
    "build_solar_thermal_profiles",
]


def _sector_targets(run_name: str = "test-elec") -> list[str]:
    """
    Outputs of the four sector heavy rules for a given run name.

    None of these rules carry wildcards, so targeting one output per rule pulls
    all four into the DAG even for an electricity-only config, since Snakemake
    resolves a directly requested output regardless of sector coupling.
    """
    return [
        f"resources/{run_name}/pop_layout_total.nc",
        f"resources/{run_name}/daily_heat_demand_total.nc",
        f"resources/{run_name}/temp_soil_total.nc",
        f"resources/{run_name}/solar_thermal_total.nc",
    ]


def _sector_key(rule: str) -> CacheKey:
    return (rule, ())


def test_sector_rules_are_cache_eligible_without_benchmark() -> None:
    """
    Every sector heavy rule is cache-eligible, benchmark-free and only depends
    on scripts/_helpers.py among local scripts modules.
    """
    with SnakemakeApi(OutputSettings()) as snakemake_api:
        workflow_api = snakemake_api.workflow(
            resource_settings=ResourceSettings(cores=1),
            config_settings=ConfigSettings(configfiles=[ROOT / ELEC_CONFIG], config={}),
            workflow_settings=WorkflowSettings(),
            workdir=ROOT,
        )
        workflow = workflow_api._workflow
        rules = {name: workflow.get_rule(name) for name in SECTOR_RULE_NAMES}

        not_cacheable = [
            name
            for name, rule in rules.items()
            if not (rule.cache and rule.cache.output)
        ]
        still_benchmarked = [name for name, rule in rules.items() if rule.benchmark]
        importing_other_scripts = [
            name for name, rule in rules.items() if _has_local_script_import(rule)
        ]

    assert not_cacheable == []
    assert still_benchmarked == []
    assert importing_other_scripts == []


def test_sector_keys_present_for_all_four_rules(cache_dir: Path) -> None:
    """
    All four sector rules get a cache key.

    The DAG for these targets also pulls in upstream cache-eligible rules
    (e.g. simplify_network, cluster_network, build_line_rating), so this
    checks the four expected keys are present rather than an exact key set.
    """
    keys = cache_keys(ELEC_CONFIG, _sector_targets(), cache_dir)
    expected = {_sector_key(name) for name in SECTOR_RULE_NAMES}
    assert expected <= set(keys)


def test_sector_solar_thermal_change_affects_only_that_rule(cache_dir: Path) -> None:
    targets = _sector_targets()
    baseline = cache_keys(ELEC_CONFIG, targets, cache_dir)
    overridden = cache_keys(
        ELEC_CONFIG,
        targets,
        cache_dir,
        overrides={"solar_thermal": {"clearsky_model": "enhanced"}},
    )
    for name in SECTOR_RULE_NAMES:
        key = _sector_key(name)
        if name == "build_solar_thermal_profiles":
            assert overridden[key] != baseline[key]
        else:
            assert overridden[key] == baseline[key]


def test_sector_run_name_leaves_keys_unchanged(cache_dir: Path) -> None:
    baseline = cache_keys(ELEC_CONFIG, _sector_targets(), cache_dir)
    renamed = cache_keys(
        ELEC_CONFIG,
        _sector_targets("other-name"),
        cache_dir,
        overrides={"run": {"name": "other-name"}},
    )
    assert baseline == renamed
