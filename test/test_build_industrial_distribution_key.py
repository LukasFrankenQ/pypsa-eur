# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""The industrial distribution key weighted by the EU ETS sites (build_industrial_distribution_key)."""

from types import SimpleNamespace

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from scripts import build_industrial_distribution_key as key


@pytest.fixture
def regions():
    return gpd.GeoDataFrame(
        {"name": ["DE0 0", "DE0 1", "NL0 0", "CH0 0", "CH0 1"]},
        geometry=[
            box(6, 50, 10, 52),
            box(10, 50, 14, 52),
            box(4, 51, 6, 53),
            box(6, 46, 8, 47.5),
            box(8, 46, 10, 47.5),
        ],
        crs="EPSG:4326",
    ).set_index("name")


def _site(name, country, lon, lat, sector, activity, co2_t, nace=None):
    return {
        "site_id": name,
        "name": name,
        "country": country,
        "lat": lat,
        "lon": lon,
        "sector": sector,
        "activity": activity,
        "nace": nace,
        "co2_t": co2_t,
        "year": 2025,
        "source": "eu_ets",
        "location": "address",
    }


@pytest.fixture
def sites_csv(tmp_path):
    rows = [
        _site("west steel", "DE", 8, 51, "Iron and steel", "steel", 100.0),
        _site("east steel", "DE", 12, 51, "Iron and steel", "steel", 300.0),
        # a German site inside the Dutch region goes to the nearest German one
        _site("border chem", "DE", 5.95, 52, "Chemicals", "Combustion", 40.0, "20.14"),
        _site("east chem", "DE", 12, 51, "Chemicals", "chemicals", 60.0),
        _site(
            "glassworks",
            "DE",
            12,
            51,
            "Glass, ceramics and other minerals",
            "Manufacture of glass",
            50.0,
        ),
        _site(
            "fibre plant",
            "DE",
            8,
            51,
            "Glass, ceramics and other minerals",
            "Combustion of fuels",
            25.0,
            "23.14",
        ),
        _site(
            "brickworks",
            "DE",
            8,
            51,
            "Glass, ceramics and other minerals",
            "Manufacture of ceramics",
            50.0,
        ),
        _site(
            "NL tiles",
            "NL",
            5,
            52,
            "Glass, ceramics and other minerals",
            "Manufacture of ceramics",
            10.0,
        ),
        _site("gas plant", "DE", 8, 51, "Oil and gas extraction", "comb", 1e6),
        _site("far bakery", "DE", 8, 55, "Food and drink", "comb", 1e6),
    ]
    fn = tmp_path / "industrial_sites.csv"
    pd.DataFrame(rows).to_csv(fn, index=False)
    return fn


def test_ets_sites_take_their_subsector_and_a_bus_of_their_country(sites_csv, regions):
    sites = key.prepare_ets_sites(sites_csv, regions).set_index("site_id")

    assert sites.at["west steel", "bus"] == "DE0 0"
    assert sites.at["east steel", "bus"] == "DE0 1"
    assert sites.at["border chem", "bus"] == "DE0 0"
    assert sites.at["border chem", "Subsector"] == "Chemical industry"
    # oil and gas extraction is no industry demand; a site far off is dropped
    assert "gas plant" not in sites.index
    assert "far bakery" not in sites.index

    subsector = sites["Subsector"].groupby(level=0).agg(set)
    assert subsector["glassworks"] == {"Glass"}
    assert subsector["fibre plant"] == {"Glass"}
    assert subsector["brickworks"] == {"Non-metallic mineral products"}
    # the Netherlands name no glassworks: their minerals stand in for glass
    assert subsector["NL tiles"] == {"Non-metallic mineral products", "Glass"}


def test_ets_key_weights_by_emissions_and_falls_back_to_population(
    sites_csv, regions, tmp_path, monkeypatch
):
    pop = pd.DataFrame(
        {"total": [1.0, 1.0, 1.0, 1.0, 3.0]}, index=pd.Index(regions.index)
    )
    pop.to_csv(tmp_path / "pop_layout.csv")
    snakemake = SimpleNamespace(
        input=SimpleNamespace(clustered_pop_layout=tmp_path / "pop_layout.csv")
    )
    monkeypatch.setattr(key, "snakemake", snakemake, raising=False)

    sites = key.prepare_ets_sites(sites_csv, regions)
    steel = pd.DataFrame(
        columns=[
            "country",
            "bus",
            "Status",
            "Retired date",
            "Start date",
            "Nominal EAF steel capacity (ttpa)",
            "Nominal BOF steel capacity (ttpa)",
            "Nominal OHF steel capacity (ttpa)",
            "Nominal iron capacity (ttpa)",
        ]
    )
    cement = pd.DataFrame(
        columns=[
            "country",
            "bus",
            "Operating status",
            "Plant type",
            "Clinker Capacity (millions metric tonnes per annum)",
            "Cement Capacity (millions metric tonnes per annum)",
        ]
    )
    ammonia = pd.DataFrame(columns=["country", "bus", "Ammonia [kt/a]"])
    refineries = pd.DataFrame(columns=["country", "bus", "Capacity [bbl/day]"])

    keys = key.build_nodal_distribution_key(
        sites, steel, ammonia, cement, refineries, regions, ["DE", "NL", "CH"]
    )

    assert set(key.SUBSECTORS) <= set(keys.columns)
    assert keys.at["DE0 0", "Iron and steel"] == pytest.approx(0.25)
    assert keys.at["DE0 1", "Iron and steel"] == pytest.approx(0.75)
    assert keys.at["DE0 0", "Chemical industry"] == pytest.approx(0.4)
    assert keys.at["DE0 0", "Glass"] == pytest.approx(25 / 75)
    assert keys.at["DE0 0", "Non-metallic mineral products"] == pytest.approx(1.0)
    # no sites: population, also for a subsector without any site at all
    assert keys.at["DE0 0", "Paper and printing"] == pytest.approx(0.5)
    assert keys.at["CH0 1", "Chemical industry"] == pytest.approx(0.75)
    assert keys.at["NL0 0", "Glass"] == pytest.approx(1.0)

    by_country = keys.groupby(keys.index.str[:2]).sum()
    assert by_country[key.SUBSECTORS].to_numpy() == pytest.approx(1.0)
