# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""Tests for helper utilities in scripts/build_renewable_profiles.py."""

import geopandas as gpd
from shapely.geometry import box

from scripts.build_renewable_profiles import shoreline_regions


def test_shoreline_regions_substitutes_nearest_onshore_region():
    """An offshore bus without an onshore region takes the nearest onshore shape."""

    onshore = gpd.GeoSeries(
        {"A": box(0, 0, 1, 1), "B": box(4, 0, 5, 1)}, crs="EPSG:4326"
    ).rename_axis("bus")
    offshore = gpd.GeoSeries(
        {"A": box(0, 1, 1, 2), "B": box(4, 1, 5, 2), "C": box(5.5, 1, 6.5, 2)},
        crs="EPSG:4326",
    ).rename_axis("bus")

    regions = shoreline_regions(onshore, offshore, ["B", "C", "A"])

    assert regions.index.tolist() == ["B", "C", "A"]
    assert regions.crs == onshore.crs
    assert regions.geometry["C"].equals(onshore["B"])
    assert regions.geometry["A"].equals(onshore["A"])
