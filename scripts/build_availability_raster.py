# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
Writes the fine eligibility raster of a renewable technology: which pixels of
the land eligibility analysis the technology may be built on.

The exclusions are those [determine_availability_matrix][] averages into the
share of each cutout grid cell, kept at the excluder resolution in EPSG:3035
over the bounding box of the technology's regions. Regions of Moldova and
Ukraine carry the plain excluder's verdict, not the separate matrix
[determine_availability_matrix][] uses for them.

Outputs
-------

- `resources/availability_raster_{technology}.tif`: one uint8 band, 1 where
  eligible, 0 where excluded and 255 (nodata) outside the regions. Tiled,
  LZW compressed, with internal overviews.
"""

import functools
import logging

import atlite
import geopandas as gpd
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import geometry_mask

from scripts._helpers import configure_logging, set_scenario_config

logger = logging.getLogger(__name__)

EXCLUDED, ELIGIBLE, OUTSIDE = 0, 1, 255
OVERVIEW_FACTORS = [2, 4, 8, 16, 32, 64]


def write_availability_raster(eligible, transform, regions, path):
    """Write the eligible mask as a tiled uint8 GeoTIFF, nodata outside `regions`."""
    outside = geometry_mask([regions], out_shape=eligible.shape, transform=transform)
    codes = np.where(eligible, np.uint8(ELIGIBLE), np.uint8(EXCLUDED))
    codes[outside] = OUTSIDE

    profile = dict(
        driver="GTiff",
        height=codes.shape[0],
        width=codes.shape[1],
        count=1,
        dtype="uint8",
        crs="EPSG:3035",
        transform=transform,
        nodata=OUTSIDE,
        tiled=True,
        blockxsize=512,
        blockysize=512,
        compress="lzw",
    )
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(codes, 1)

    with rasterio.open(path, "r+") as dst:
        dst.build_overviews(OVERVIEW_FACTORS, Resampling.nearest)
        dst.update_tags(ns="rio_overview", resampling="nearest")


if __name__ == "__main__":
    if "snakemake" not in globals():
        from scripts._helpers import mock_snakemake

        snakemake = mock_snakemake("build_availability_raster", technology="onwind")
    configure_logging(snakemake)
    set_scenario_config(snakemake)

    technology = snakemake.params.technology
    params = snakemake.params.renewable

    # Keep in step with the excluder block of scripts/determine_availability_matrix.py.
    res = params.get("excluder_resolution", 100)
    excluder = atlite.ExclusionContainer(crs=3035, res=res)

    if params["natura"]:
        excluder.add_raster(snakemake.input.natura, nodata=0, allow_no_overlap=True)

    for dataset in ["corine", "luisa"]:
        kwargs = {"nodata": 0} if dataset == "luisa" else {}
        settings = params.get(dataset, {})
        if not settings:
            continue
        if dataset == "luisa" and res > 50:
            logger.info(
                "LUISA data is available at 50m resolution, "
                f"but coarser {res}m resolution is used."
            )
        if isinstance(settings, list):
            settings = {"grid_codes": settings}
        if "grid_codes" in settings:
            codes = settings["grid_codes"]
            excluder.add_raster(
                snakemake.input[dataset], codes=codes, invert=True, crs=3035, **kwargs
            )
        if settings.get("distance", 0.0) > 0.0:
            codes = settings["distance_grid_codes"]
            buffer = settings["distance"]
            excluder.add_raster(
                snakemake.input[dataset], codes=codes, buffer=buffer, crs=3035, **kwargs
            )

    if params.get("ship_threshold"):
        shipping_threshold = (
            params["ship_threshold"] * 8760 * 6
        )  # approximation because 6 years of data which is hourly collected
        func = functools.partial(np.less, shipping_threshold)
        excluder.add_raster(
            snakemake.input.ship_density, codes=func, crs=4326, allow_no_overlap=True
        )

    if params.get("max_depth"):
        # lambda not supported for atlite + multiprocessing
        # use named function np.greater with partially frozen argument instead
        # and exclude areas where: -max_depth > grid cell depth
        func = functools.partial(np.greater, -params["max_depth"])
        excluder.add_raster(snakemake.input.gebco, codes=func, crs=4326, nodata=-1000)

    if params.get("min_depth"):
        func = functools.partial(np.greater, -params["min_depth"])
        excluder.add_raster(
            snakemake.input.gebco, codes=func, crs=4326, nodata=-1000, invert=True
        )

    if params.get("min_shore_distance") is not None:
        buffer = params["min_shore_distance"]
        excluder.add_geometry(snakemake.input.country_shapes, buffer=buffer)

    if params.get("max_shore_distance") is not None:
        buffer = params["max_shore_distance"]
        excluder.add_geometry(
            snakemake.input.country_shapes, buffer=buffer, invert=True
        )

    regions = gpd.read_file(snakemake.input.regions)
    assert not regions.empty, (
        f"List of regions in {snakemake.input.regions} is empty, please "
        "disable the corresponding renewable technology"
    )
    union = regions.to_crs(excluder.crs).union_all()

    logger.info(f"Calculate the fine eligibility raster for {technology}...")
    eligible, transform = excluder.compute_shape_availability(
        gpd.GeoSeries([union], crs=excluder.crs)
    )
    write_availability_raster(eligible, transform, union, snakemake.output[0])
