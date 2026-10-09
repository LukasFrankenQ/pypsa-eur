# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""Tests for helper utilities in scripts/cluster_network.py."""

import geopandas as gpd
import pandas as pd
import pypsa
import pytest
from shapely.geometry import Polygon

from scripts.cluster_network import (
    busmap_from_shapes,
    distribute_n_clusters_to_countries,
    sanitize_busmap,
)


def test_sanitize_busmap_strips_values():
    """Whitespace in busmap values is removed while indices stay untouched."""

    raw = pd.Series([" zone_a ", "zone_b  "], index=[" bus1 ", "bus2  "], name="busmap")

    cleaned = sanitize_busmap(raw)

    assert cleaned.index.tolist() == [" bus1 ", "bus2  "]
    assert cleaned.tolist() == ["zone_a", "zone_b"]
    assert cleaned.name == "busmap"


def test_busmap_from_shapes_returns_trimmed_labels():
    """Busmap inferred from shapes produces sanitized labels."""

    n = pypsa.Network()
    n.add("Bus", "bus1", x=0.0, y=0.0, carrier="AC", country="AA")
    n.add("Bus", "bus2", x=2.0, y=0.0, carrier="AC", country="AA")

    shapes = gpd.GeoDataFrame(
        {
            "name": [" region_1 ", "region_2  "],
            "geometry": [
                Polygon([(-1, -1), (1, -1), (1, 1), (-1, 1)]),
                Polygon([(1, -1), (3, -1), (3, 1), (1, 1)]),
            ],
        },
        crs="EPSG:4326",
    )

    busmap = busmap_from_shapes(n, shapes)

    assert busmap.index.tolist() == ["bus1", "bus2"]
    assert busmap.tolist() == ["region_1", "region_2"]


def test_distribute_n_clusters_assertion_names_requested_value():
    """An out-of-range n_clusters is reported together with the requested value."""

    n = pypsa.Network()
    n.add("Bus", "bus1", x=0.0, y=0.0, carrier="AC", country="AA")
    n.add("Bus", "bus2", x=2.0, y=0.0, carrier="AC", country="AA")
    n.buses["sub_network"] = "0"
    weights = pd.Series(1.0, index=n.buses.index)

    with pytest.raises(AssertionError, match=r"1 <= n_clusters <= 2 .* got 100"):
        distribute_n_clusters_to_countries(n, 100, weights)
