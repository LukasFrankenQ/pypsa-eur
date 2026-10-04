# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""
Harness for testing Snakemake's between-workflow caching (--cache) on this workflow.

Builds the DAG through the Snakemake Python API with a cache directory pointed at a
temporary path, and inspects which jobs would read from or write to the output file
cache.
"""

import hashlib
import os
import re
import shutil
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

from scripts._helpers import load_data_versions

ROOT = Path(__file__).resolve().parent.parent
ELEC_CONFIG = Path("config/test/config.electricity.yaml")
SCENARIOS_CONFIG = Path("config/test/config.scenarios.yaml")
SCENARIO_RUNS = ("test-elec-no-offshore-wind", "test-elec-no-onshore-wind")

# A small target used to sanity-check the harness itself. Every rule feeding it
# is cache-eligible now that build_electricity.smk is fully cached, so the
# harness must report a non-empty result for it.
SMALL_TARGETS = ["resources/test-elec/networks/base.nc"]

# Rules Snakemake genuinely refuses to cache (checkpoint, pipe()/service()/touch()
# output, multiple unnamed outputs). Every rule in build_electricity.smk and
# build_sector.smk not listed here must carry a `cache:` directive.
UNCACHED_BUILD_RULES: frozenset[str] = frozenset()

CacheKey = tuple[str, tuple[tuple[str, str], ...]]


def cache_outputs(
    configfile: Path,
    targets: list[str],
    cache_dir: Path,
    overrides: dict | None = None,
    workdir: Path = ROOT,
) -> dict[CacheKey, list[tuple[Path, Path]]]:
    """
    Build the DAG for targets and return each cacheable job's (output, cache file) pairs.

    Keyed by (rule name, sorted wildcard items). Only jobs whose rule is marked
    eligible for caching (rule.cache.output) are included. Runs with --cache
    (WorkflowSettings(cache=[])), so a rule's `cache: True` directive is enough
    to make it eligible without naming it explicitly. Empty targets build the
    default target.
    """
    prior_cache_env = os.environ.get("SNAKEMAKE_OUTPUT_CACHE")
    os.environ["SNAKEMAKE_OUTPUT_CACHE"] = str(cache_dir)
    try:
        entries: dict[CacheKey, list[tuple[Path, Path]]] = {}
        with SnakemakeApi(OutputSettings()) as snakemake_api:
            workflow_api = snakemake_api.workflow(
                resource_settings=ResourceSettings(cores=1),
                config_settings=ConfigSettings(
                    configfiles=[workdir / configfile], config=overrides or {}
                ),
                workflow_settings=WorkflowSettings(cache=[]),
                workdir=workdir,
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
                entries[key] = workflow.async_run(
                    workflow.output_file_cache.get_outputfiles_and_cachefiles(job)
                )

        return entries
    finally:
        if prior_cache_env is None:
            os.environ.pop("SNAKEMAKE_OUTPUT_CACHE", None)
        else:
            os.environ["SNAKEMAKE_OUTPUT_CACHE"] = prior_cache_env


def cache_entries(
    configfile: Path,
    targets: list[str],
    cache_dir: Path,
    overrides: dict | None = None,
) -> dict[CacheKey, list[Path]]:
    """Same as cache_outputs, but keep only each job's cache file paths."""
    return {
        key: [cachefile for _, cachefile in pairs]
        for key, pairs in cache_outputs(
            configfile, targets, cache_dir, overrides
        ).items()
    }


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


def _keys_by_run(keys: dict[CacheKey, str]) -> dict[str, dict[CacheKey, str]]:
    """Split a scenarios DAG's keys per run, each job keyed without its run wildcard."""
    by_run: dict[str, dict[CacheKey, str]] = {}
    for (rule, wildcards), key in keys.items():
        run = dict(wildcards).get("run")
        if run is None:
            continue
        rest = tuple(item for item in wildcards if item[0] != "run")
        by_run.setdefault(run, {})[(rule, rest)] = key
    return by_run


def test_caching_accepts_scenarios(cache_dir: Path) -> None:
    by_run = _keys_by_run(cache_keys(SCENARIOS_CONFIG, [], cache_dir))
    assert set(by_run) == set(SCENARIO_RUNS)


def test_scenario_keys_move_only_with_the_scenarios_own_config(
    cache_dir: Path,
) -> None:
    """
    A scenario's override reaches the key of every job that reads it and no other.

    The two scenarios differ in electricity.renewable_carriers alone, which
    compose_network takes as a param and base_network never reads.
    """
    by_run = _keys_by_run(cache_keys(SCENARIOS_CONFIG, [], cache_dir))
    first, second = (by_run[run] for run in SCENARIO_RUNS)
    base, compose, solve = (
        ("base_network", ()),
        ("compose_network", (("horizon", "2030"),)),
        ("solve_network", (("horizon", "2030"),)),
    )
    assert first[base] == second[base]
    assert first[compose] != second[compose]
    assert first[solve] != second[solve]


def test_scenario_key_equals_the_same_config_run_without_scenarios(
    cache_dir: Path,
) -> None:
    """A scenario and a plain run of its merged config share one cache entry."""
    scenario = _keys_by_run(cache_keys(SCENARIOS_CONFIG, [], cache_dir))[
        SCENARIO_RUNS[0]
    ]
    plain = _keys_by_run(
        {
            (rule, (*wildcards, ("run", "plain"))): key
            for (rule, wildcards), key in cache_keys(
                SCENARIOS_CONFIG,
                ["results/plain/networks/solved_2030.nc"],
                cache_dir,
                overrides={
                    "run": {"name": "plain", "scenarios": {"enable": False}},
                    "electricity": {"renewable_carriers": ["solar", "onwind"]},
                },
            ).items()
        }
    )["plain"]
    for job in (
        ("base_network", ()),
        ("compose_network", (("horizon", "2030"),)),
        ("solve_network", (("horizon", "2030"),)),
    ):
        assert scenario[job] == plain[job]


def test_scenario_jobs_share_a_cache_entry_only_across_runs(cache_dir: Path) -> None:
    """
    Two scenarios whose config agrees for a job resolve to the same cache file,
    and nothing else in a scenarios DAG does.
    """
    entries = cache_entries(SCENARIOS_CONFIG, [], cache_dir)
    assert entries
    for cachefile, owners in _shared_cachefiles(entries).items():
        jobs = {
            (rule, tuple(item for item in wildcards if item[0] != "run"))
            for rule, wildcards in owners
        }
        assert len(jobs) == 1, f"{cachefile.name}: {owners}"


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


def test_cache_keys_harness_finds_base_network_chain(cache_dir: Path) -> None:
    """
    Sanity-check the harness itself.

    base_network and its whole upstream chain in build_electricity.smk are all
    cache-eligible, so this small target must yield a non-empty result with an
    entry for the rule that produces it.
    """
    keys = cache_keys(ELEC_CONFIG, SMALL_TARGETS, cache_dir)
    assert keys
    assert ("base_network", ()) in keys


def _has_storage_input(rule) -> bool:
    """Return whether any of the rule's raw (unresolved) input items is storage()."""
    return any(getattr(item, "is_storage", False) for item in rule.input)


def test_every_storage_rule_marks_omit_storage_content() -> None:
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


def test_every_storage_rule_marks_omit_storage_content_branch_blind() -> None:
    """
    Text-level companion to test_every_storage_rule_marks_omit_storage_content.

    That test only sees rules reachable from the elec test config's DAG, so a
    rule gated behind a config branch that config does not take (an alternate
    dataset source, for example) never gets its storage() input checked. This
    scans the raw rules/*.smk text instead, with no DAG build and no
    snakemake import, so it covers every branch regardless of which one the
    active config selects.

    `omit-storage-content` (without the `hash-` prefix) is required. The
    `hash-` variant only sets the omit_storage_content flag without the
    output flag, so it never actually caches the rule's output.
    """
    offenders = []
    storage_input_rule_count = 0
    for path in sorted((ROOT / "rules").glob("*.smk")):
        for name, block in _rule_blocks(path.read_text()):
            if "storage(" not in _input_section(block):
                continue
            storage_input_rule_count += 1
            if (
                'cache: "omit-storage-content"' not in block
                or "hash-omit-storage-content" in block
            ):
                offenders.append(f"{path.name}:{name}")

    # Guards against the regexes or glob silently stopping matching, which
    # would otherwise make the offenders check below pass vacuously.
    assert storage_input_rule_count > 0
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


BUILD_RULE_FILES = ("build_electricity.smk", "build_sector.smk")


def test_build_rules_are_cached_and_benchmark_free() -> None:
    """
    Every rule in build_electricity.smk and build_sector.smk is cache-eligible.

    Snakemake rejects a benchmark: directive on a cached rule (a cache hit has
    no run to benchmark), so every rule not named in UNCACHED_BUILD_RULES must
    carry a cache: directive and none may carry benchmark:.
    """
    missing_cache = []
    still_benchmarked = []
    rule_count = 0
    for filename in BUILD_RULE_FILES:
        text = (ROOT / "rules" / filename).read_text()
        for name, block in _rule_blocks(text):
            if name in UNCACHED_BUILD_RULES:
                continue
            rule_count += 1
            if not re.search(r"^\s*cache:", block, re.MULTILINE):
                missing_cache.append(f"{filename}:{name}")
            if re.search(r"^\s*benchmark:", block, re.MULTILINE):
                still_benchmarked.append(f"{filename}:{name}")

    assert rule_count > 0
    assert missing_cache == []
    assert still_benchmarked == []


LOCAL_IMPORT_RE = re.compile(r"^(?:import|from)\s+(scripts(?:\.\w+)+)")


def _local_script_import_targets(rule) -> set[str]:
    """
    Return the code_dependencies-relative paths a rule's script imports locally.

    Resolves each `scripts.<module>` import (other than scripts._helpers, which
    code_dependencies() always includes) to the scripts/<module>.py path a
    cached rule's code_dependencies input must list for the provenance hash to
    actually cover the code that runs.
    """
    script_path = ROOT / str(rule.script)
    text = script_path.read_text()
    targets = set()
    for line in text.splitlines():
        m = LOCAL_IMPORT_RE.match(line.strip())
        if not m or m.group(1) == "scripts._helpers":
            continue
        targets.add("scripts/" + "/".join(m.group(1).split(".")[1:]) + ".py")
    return targets


def test_build_rules_are_cache_eligible_and_cover_local_imports() -> None:
    """
    Every build_electricity.smk/build_sector.smk rule reachable under the elec
    test config is cache-eligible, and every local module it imports (besides
    scripts._helpers) is listed in its code_dependencies input.
    """
    with SnakemakeApi(OutputSettings()) as snakemake_api:
        workflow_api = snakemake_api.workflow(
            resource_settings=ResourceSettings(cores=1),
            config_settings=ConfigSettings(configfiles=[ROOT / ELEC_CONFIG], config={}),
            workflow_settings=WorkflowSettings(),
            workdir=ROOT,
        )
        workflow = workflow_api._workflow
        rules = [
            rule
            for rule in workflow.rules
            if Path(rule.snakefile).name in BUILD_RULE_FILES
            and rule.name not in UNCACHED_BUILD_RULES
        ]

        not_cacheable = [r.name for r in rules if not (r.cache and r.cache.output)]
        uncovered_imports = {
            rule.name: missing
            for rule in rules
            if (
                missing := _local_script_import_targets(rule)
                - {str(d) for d in getattr(rule.input, "code_dependencies", [])}
            )
        }

    assert len(rules) > 0
    assert not_cacheable == []
    assert uncovered_imports == {}


def test_retrieve_rule_key_moves_with_dataset_version(cache_dir: Path) -> None:
    """
    A retrieve rule cached with omit-storage-content still keys off the
    dataset version.

    run: bodies aren't source-hashed (job.is_shell/is_script/is_notebook are
    all False), so without an explicit version param two versions of the same
    dataset would collide on one cache entry.
    """
    data_versions = load_data_versions(ROOT / "data" / "versions.csv")
    nitrogen = data_versions[
        (data_versions["dataset"] == "nitrogen_statistics")
        & (data_versions["source"] == "archive")
        & data_versions["supported"]
    ]
    supported_versions = nitrogen["version"].tolist()
    assert len(supported_versions) >= 2

    default_version = nitrogen.loc[nitrogen["latest"], "version"].item()
    other_version = next(v for v in supported_versions if v != default_version)

    def target(version: str) -> str:
        return f"data/nitrogen_statistics/archive/{version}/nitro-ert.xlsx"

    baseline = cache_keys(ELEC_CONFIG, [target(default_version)], cache_dir)
    overridden = cache_keys(
        ELEC_CONFIG,
        [target(other_version)],
        cache_dir,
        overrides={"data": {"nitrogen_statistics": {"version": other_version}}},
    )
    key = ("retrieve_nitrogen_statistics", ())
    assert baseline[key] != overridden[key]


def test_solve_network_custom_extra_functionality_is_input_not_param() -> None:
    """
    custom_extra_functionality must be an input so its content is hashed.

    A params entry is only value-hashed (the path string), which would miss
    edits to the referenced script; moving it to input content-hashes it.
    """
    with SnakemakeApi(OutputSettings()) as snakemake_api:
        workflow_api = snakemake_api.workflow(
            resource_settings=ResourceSettings(cores=1),
            config_settings=ConfigSettings(configfiles=[ROOT / ELEC_CONFIG], config={}),
            workflow_settings=WorkflowSettings(),
            workdir=ROOT,
        )
        workflow = workflow_api._workflow
        rule = workflow.get_rule("solve_network")

        assert "custom_extra_functionality" in rule.input.keys()
        assert "custom_extra_functionality" not in rule.params.keys()


PARAMS_HEADER_RE = re.compile(r"^\s*params:\s*$")


def _params_section(block: str) -> str:
    """Return a rule block's params: section text, or "" if it has none."""
    lines = block.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if PARAMS_HEADER_RE.match(line)), None
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


def test_no_api_token_in_rule_params() -> None:
    """
    No params: section may embed an API token.

    A token value would differ per user/machine, so hashing it as a param
    would defeat sharing a cache hit across users for otherwise-identical
    rules. Tokens must be read from the environment inside the script instead.
    """
    offenders = [
        f"{path.name}:{name}"
        for path in sorted((ROOT / "rules").glob("*.smk"))
        for name, block in _rule_blocks(path.read_text())
        if "API_TOKEN" in _params_section(block)
    ]

    assert offenders == []


def test_cache_dry_run_parses_cleanly(cache_dir: Path) -> None:
    """
    `snakemake -n --cache` against the validator config exits 0.

    A parse-level proof that every cache:/input: directive touched by this
    module is syntactically and semantically accepted, independent of which
    specific targets a unit test happens to build a DAG for.
    """
    env = os.environ.copy()
    env["SNAKEMAKE_OUTPUT_CACHE"] = str(cache_dir)
    result = subprocess.run(
        [
            "snakemake",
            "-n",
            "--cache",
            "--configfile",
            "config/test/config.validator.yaml",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _shared_cachefiles(
    entries: dict[CacheKey, list[Path]],
) -> dict[Path, list[CacheKey]]:
    """Return every cache file claimed by more than one job, with its owners."""
    owners: dict[Path, list[CacheKey]] = {}
    for key, cachefiles in entries.items():
        for cachefile in cachefiles:
            owners.setdefault(cachefile, []).append(key)
    return {path: keys for path, keys in owners.items() if len(keys) > 1}


def test_shared_cachefiles_reports_a_duplicated_key() -> None:
    shared = Path("cache/abc.nc")
    entries: dict[CacheKey, list[Path]] = {
        ("rule_a", ()): [shared],
        ("rule_b", (("horizon", "2050"),)): [shared, Path("cache/def.nc")],
        ("rule_c", ()): [Path("cache/ghi.nc")],
    }
    assert _shared_cachefiles(entries) == {
        shared: [("rule_a", ()), ("rule_b", (("horizon", "2050"),))]
    }


@pytest.mark.parametrize(
    "configfile",
    [
        ELEC_CONFIG,
        Path("config/test/config.overnight.yaml"),
        Path("config/test/config.myopic.yaml"),
    ],
    ids=lambda path: path.stem,
)
def test_no_two_jobs_share_a_cache_entry(configfile: Path, cache_dir: Path) -> None:
    """No two jobs in a test config's default DAG resolve to the same cache file."""
    entries = cache_entries(configfile, [], cache_dir)
    assert entries
    shared = {
        path.name: [f"{rule}{dict(wildcards)}" for rule, wildcards in keys]
        for path, keys in _shared_cachefiles(entries).items()
    }
    assert shared == {}


STORED_RE = re.compile(r"Moving output file (.+) to cache\.$", re.MULTILINE)
FETCHED_RE = re.compile(r"Symlinking output file (.+) from cache\.$", re.MULTILINE)
MISSING = "<missing>"


def _logged_paths(pattern: re.Pattern, log: str, workdir: Path) -> set[Path]:
    """Return the workdir-relative paths a snakemake log names in pattern's lines."""
    paths = set()
    for match in pattern.finditer(log):
        path = Path(match.group(1).strip())
        paths.add(path.relative_to(workdir) if path.is_absolute() else path)
    return paths


def _was_fetched(output: Path, job_outputs: Sequence[Path], fetched: set[Path]) -> bool:
    """
    Return whether output, or a directory of its own job holding it, was fetched.

    A fetched directory logs its entries but never itself or the outputs inside it.
    """

    def logged(path: Path) -> bool:
        return any(p == path or p.is_relative_to(path) for p in fetched)

    return logged(output) or any(
        output != other and output.is_relative_to(other) and logged(other)
        for other in job_outputs
    )


def _tree_hashes(workdir: Path, outputs: Sequence[Path]) -> dict[str, str]:
    """Map every file under outputs to its sha256, following symlinks."""
    hashes: dict[str, str] = {}
    for output in outputs:
        path = workdir / output
        if path.is_dir():
            files = [
                Path(root, name)
                for root, _, names in os.walk(path, followlinks=True)
                for name in names
                if name != ".snakemake_timestamp"
            ]
        else:
            files = [path]
        for file in files:
            relative = str(file.relative_to(workdir))
            if not file.exists():
                hashes[relative] = MISSING
                continue
            hashes[relative] = hashlib.sha256(file.read_bytes()).hexdigest()
    return hashes


def test_logged_paths_parses_store_and_fetch_lines(tmp_path: Path) -> None:
    log = "\n".join(
        [
            "Moving output file resources/a.nc to cache.",
            f"Moving output file {tmp_path}/resources/b.nc to cache.",
            "Symlinking output file resources/a.nc from cache.",
            "rule base_network:",
        ]
    )
    assert _logged_paths(STORED_RE, log, tmp_path) == {
        Path("resources/a.nc"),
        Path("resources/b.nc"),
    }
    assert _logged_paths(FETCHED_RE, log, tmp_path) == {Path("resources/a.nc")}


def test_was_fetched_covers_directory_entries() -> None:
    fetched = {Path("data/dir/x.csv"), Path("resources/a.nc")}
    job = [Path("data/dir"), Path("data/dir/nested.csv")]
    assert _was_fetched(Path("resources/a.nc"), [], fetched)
    assert _was_fetched(Path("data/dir"), job, fetched)
    assert _was_fetched(Path("data/dir/nested.csv"), job, fetched)
    assert not _was_fetched(Path("data/dir/nested.csv"), [], fetched)
    assert not _was_fetched(Path("resources/b.nc"), [], fetched)


def test_tree_hashes_follows_symlinks_and_skips_timestamps(tmp_path: Path) -> None:
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "entry").write_text("x")
    out = tmp_path / "out"
    out.mkdir()
    (out / "linked").symlink_to(tmp_path / "cache" / "entry")
    (out / ".snakemake_timestamp").touch()
    hashes = _tree_hashes(tmp_path, [Path("out"), Path("gone.nc")])
    assert hashes == {
        "out/linked": hashlib.sha256(b"x").hexdigest(),
        "gone.nc": MISSING,
    }


def _copy_workdir(dest: Path) -> None:
    """Copy tracked and untracked-not-ignored files of the repo into dest."""
    listed = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode()
    for name in filter(None, listed.split("\0")):
        source = ROOT / name
        if not source.exists() and not source.is_symlink():
            continue
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)


def _run_pass(workdir: Path, cache_root: Path, name: str, log_dir: Path) -> str:
    """Run the default target under the runner's cache flags and return the log."""
    storage = cache_root / "storage" / name
    storage.mkdir(parents=True)
    env = os.environ.copy()
    env["SNAKEMAKE_OUTPUT_CACHE"] = str(cache_root / "output-cache")
    result = subprocess.run(
        [
            "snakemake",
            "-c",
            "all",
            "--configfile",
            str(ELEC_CONFIG),
            "--cache",
            "--local-storage-prefix",
            str(storage),
            "--rerun-incomplete",
        ],
        cwd=workdir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    (log_dir / f"{name}.log").write_text(result.stdout)
    if result.returncode != 0:
        pytest.fail(f"{name} exited {result.returncode}:\n{_tail(result.stdout)}")
    return result.stdout


def _owner_of(path: Path, owner: dict[Path, str]) -> str:
    """Return the job owning path, itself an output or a file inside one."""
    return next(
        (job for output, job in owner.items() if path.is_relative_to(output)), "?"
    )


def _tail(log: str, lines: int = 60) -> str:
    return "\n".join(log.splitlines()[-lines:])


@pytest.mark.slow
def test_electricity_round_trip_stores_then_fetches(tmp_path: Path) -> None:
    """
    Run the electricity config twice, storing into an empty cache, then fetching.

    Pass 2 runs in a fresh copy of the repo, so no output can come from pass 1's tree.
    """
    cache_root = tmp_path / "cache"
    (cache_root / "output-cache").mkdir(parents=True)
    first, second = tmp_path / "pass-1", tmp_path / "pass-2"
    _copy_workdir(first)
    _copy_workdir(second)

    jobs = cache_outputs(ELEC_CONFIG, [], cache_root / "output-cache", workdir=first)
    owner = {
        output: f"{rule}{dict(wildcards)}"
        for (rule, wildcards), pairs in jobs.items()
        for output, _ in pairs
    }
    assert owner

    log = _run_pass(first, cache_root, "pass-1", tmp_path)
    stored = _logged_paths(STORED_RE, log, first)
    not_stored = sorted(
        f"{owner[output]}: {output}" for output in owner if output not in stored
    )
    assert not_stored == [], (
        "fetched from an empty cache (a key collision) or never stored:\n"
        + "\n".join(not_stored)
    )
    first_hashes = _tree_hashes(first, list(owner))

    log = _run_pass(second, cache_root, "pass-2", tmp_path)
    restored = sorted(_logged_paths(STORED_RE, log, second))
    assert restored == [], "executed again instead of fetched:\n" + "\n".join(
        f"{owner.get(output, '?')}: {output}" for output in restored
    )
    fetched = _logged_paths(FETCHED_RE, log, second)
    not_fetched = []
    for pairs in jobs.values():
        outputs = [output for output, _ in pairs]
        not_fetched += [
            f"{owner[output]}: {output}"
            for output in outputs
            if not _was_fetched(output, outputs, fetched)
        ]
    assert not_fetched == [], "not obtained from cache:\n" + "\n".join(not_fetched)

    second_hashes = _tree_hashes(second, list(owner))
    differing = sorted(
        path
        for path in first_hashes.keys() | second_hashes.keys()
        if first_hashes.get(path) != second_hashes.get(path)
    )
    assert differing == [], (
        "fetched tree is missing files or has extra ones:\n"
        + "\n".join(
            f"{_owner_of(Path(path), owner)}: {path} "
            f"{first_hashes.get(path, MISSING)} vs {second_hashes.get(path, MISSING)}"
            for path in differing
        )
    )
