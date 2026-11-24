# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""Dry runs of the fine eligibility raster rules and the raster writer, without atlite."""

import re
import subprocess
from pathlib import Path

import numpy as np
import rasterio
import yaml
from rasterio.transform import from_origin
from shapely.geometry import box

from scripts.build_availability_raster import write_availability_raster

ROOT = Path(__file__).resolve().parent.parent
ELEC_CONFIG = Path("config/test/config.electricity.yaml")
RULE_FILE = ROOT / "rules" / "availability_raster.smk"
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


def test_dry_run_builds_one_technology_from_its_regions_and_cutout():
    config = yaml.safe_load((ROOT / ELEC_CONFIG).read_text())
    cutout = config["atlite"]["default_cutout"]
    output = dry_run("resources/test-elec/availability_raster_onwind.tif")
    assert jobs_of(output, "build_availability_raster") == 1
    rule_block = output.split("rule build_availability_raster:")[1]
    input_line = rule_block.splitlines()[1]
    assert input_line.strip().startswith("input:")
    assert f"{cutout}.nc" in input_line
    assert "onshore_regions.geojson" in input_line


def test_collect_rule_requests_every_non_hydro_carrier():
    carriers = yaml.safe_load((ROOT / ELEC_CONFIG).read_text())["electricity"][
        "renewable_carriers"
    ]
    output = dry_run("availability_rasters")
    assert jobs_of(output, "build_availability_raster") == len(
        [c for c in carriers if c != "hydro"]
    )


def test_rule_is_cached_and_benchmark_free():
    text = RULE_FILE.read_text()
    block = text.split("rule build_availability_raster:")[1].split("\nrule ")[0]
    assert re.search(r"^\s*cache: True", block, re.MULTILINE)
    assert not re.search(r"^\s*benchmark:", block, re.MULTILINE)


def test_writer_codes_regions_and_writes_a_tiled_raster_with_overviews(tmp_path):
    size, res = 1024, 100.0
    transform = from_origin(4_000_000, 3_000_000, res, res)
    eligible = np.zeros((size, size), dtype=bool)
    eligible[:, : size // 2] = True
    regions = box(
        4_000_000, 3_000_000 - size // 2 * res, 4_000_000 + size * res, 3_000_000
    )
    path = tmp_path / "availability_raster_onwind.tif"

    write_availability_raster(eligible, transform, regions, path)

    with rasterio.open(path) as src:
        codes = src.read(1)
        assert src.nodata == 255
        assert src.crs.to_epsg() == 3035
        assert src.profile["tiled"]
        assert src.block_shapes[0] == (512, 512)
        assert src.compression.value == "LZW"
        assert src.overviews(1)
    inside, outside = codes[: size // 2], codes[size // 2 :]
    assert set(np.unique(inside)) == {0, 1}
    assert (inside[:, : size // 2] == 1).all()
    assert (inside[:, size // 2 :] == 0).all()
    assert (outside == 255).all()
