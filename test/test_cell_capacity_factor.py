# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""Dry runs of the per-cell capacity factor rules, wired without running atlite."""

import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
ELEC_CONFIG = Path("config/test/config.electricity.yaml")
RULE_FILE = ROOT / "rules" / "cell_capacity_factor.smk"
# The archive cutout source asks data.pypsa.org for its manifest while building
# the DAG; the build source resolves the same cutout path offline.
OFFLINE_CUTOUT = 'data={"cutout":{"source":"build","version":"unknown"}}'


def dry_run(*targets: str) -> str:
    result = subprocess.run(
        [
            "snakemake",
            "-n",
            "--configfile",
            str(ELEC_CONFIG),
            "--config",
            OFFLINE_CUTOUT,
            "--",
            *targets,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    return output


def jobs_of(output: str, rule: str) -> int:
    """Count of `rule` in the dry run's job stats table."""
    match = re.search(rf"^{rule}\s+(\d+)\s*$", output, re.MULTILINE)
    return int(match.group(1)) if match else 0


def test_dry_run_builds_one_technology_from_its_cutout():
    config = yaml.safe_load((ROOT / ELEC_CONFIG).read_text())
    cutout = config["atlite"]["default_cutout"]
    output = dry_run("resources/test-elec/cell_capacity_factor_onwind.nc")
    assert jobs_of(output, "build_cell_capacity_factor") == 1
    rule_block = output.split("rule build_cell_capacity_factor:")[1]
    input_line = rule_block.splitlines()[1]
    assert input_line.strip().startswith("input:")
    assert f"{cutout}.nc" in input_line


def test_collect_rule_requests_every_non_hydro_carrier():
    carriers = yaml.safe_load((ROOT / ELEC_CONFIG).read_text())["electricity"][
        "renewable_carriers"
    ]
    output = dry_run("cell_capacity_factors")
    assert jobs_of(output, "build_cell_capacity_factor") == len(
        [c for c in carriers if c != "hydro"]
    )


def test_rule_is_cached_and_benchmark_free():
    text = RULE_FILE.read_text()
    block = text.split("rule build_cell_capacity_factor:")[1].split("\nrule ")[0]
    assert re.search(r"^\s*cache: True", block, re.MULTILINE)
    assert not re.search(r"^\s*benchmark:", block, re.MULTILINE)
