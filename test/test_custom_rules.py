# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""Tests for the top-level `custom_rules` key, which lists extra rule files the Snakefile includes."""

import subprocess
from pathlib import Path

import yaml

from scripts.lib.validation.config import validate_config

ROOT = Path(__file__).resolve().parent.parent
CUSTOM_CONFIG = Path("config/test/config.custom_rules.yaml")
MARKER = "results/custom_rules_marker.txt"


def test_custom_rules_defaults_to_empty():
    assert validate_config({}).custom_rules == []


def test_custom_rules_accepts_paths():
    assert validate_config({"custom_rules": ["a.smk"]}).custom_rules == ["a.smk"]


def test_default_yaml_carries_custom_rules():
    default = yaml.safe_load((ROOT / "config/config.default.yaml").read_text())
    assert default["custom_rules"] == []


def test_dry_run_includes_listed_rule_file():
    result = subprocess.run(
        ["snakemake", "-n", "--configfile", str(CUSTOM_CONFIG), "--", MARKER],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "custom_rules_marker" in output
