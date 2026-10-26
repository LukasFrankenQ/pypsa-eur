# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""The industrial sites from the EU ETS, the NAEI and Sodir (build_industrial_sites)."""

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, box

from scripts import build_industrial_sites as sites


@pytest.mark.parametrize(
    ("activity", "nace", "plant", "expected"),
    [
        ("24", None, False, (sites.STEEL, None)),
        ("29", None, False, (sites.CEMENT, None)),
        ("21", "19.20", False, (sites.REFINING, None)),
        ("20", "20.14", False, (sites.CHEMICALS, None)),
        ("20", "23.51", False, (sites.CEMENT, None)),
        ("20", "24.42", False, (sites.NON_FERROUS, None)),
        ("20", "6.20", False, (sites.OIL_GAS, None)),
        ("20", "10.81", False, (sites.FOOD, None)),
        ("20", "35.11", False, (None, sites.POWER)),
        ("1", "20.14", True, (None, sites.POWER)),
        ("20", "86.10", False, (None, sites.NOT_INDUSTRY)),
        ("20", None, False, (None, sites.UNCLASSIFIED)),
        ("10", None, False, (None, sites.NOT_INDUSTRY)),
    ],
)
def test_an_installation_is_classified_by_activity_then_nace(
    activity, nace, plant, expected
):
    assert sites.ets_sector(activity, nace, plant) == expected


def _ets():
    installations = pd.DataFrame(
        {
            "id": ["DE_1", "DE_2", "DE_3", "NO_4", "XI_5", "DE_6"],
            "name": [
                "Hüttenwerk",
                "Kraftwerk",
                "Chemiepark",
                "Equinor Energy Osebergfeltet",
                "Belfast power",
                "Airline",
            ],
            "registry_id": ["DE", "DE", "DE", "NO", "XI", "DE"],
            "activity_id": ["24", "20", "20", "20", "20", "10"],
            "activity_name": ["steel", "comb", "comb", "comb", "comb", "air"],
        }
    )
    rows = [
        (i, y, v)
        for i, v in zip(installations["id"], range(1, 7))
        for y in (2024, 2025)
    ]
    compliance = pd.DataFrame(rows, columns=["installation_id", "year", "verified"])
    compliance["verified"] *= 1e5
    locations = pd.DataFrame(
        {
            "installation_id": installations["id"],
            "latitude": [51.5, 52.0, 50.9, 58.9, 54.6, 50.0],
            "longitude": [7.0, 13.0, 6.9, 5.7, -5.9, 8.6],
        }
    )
    nace = pd.DataFrame(
        {
            "installation_id": ["DE_2", "DE_3", "NO_4"],
            "nace_2015": [None, "20.14", "6.10"],
            "nace_2020": ["35.11", None, "6.10"],
        }
    )
    plants = pd.DataFrame({"installation_id": [], "plant_id": []})
    return dict(
        installations=installations,
        compliance=compliance,
        locations=locations,
        nace=nace,
        plant_links=plants,
    )


def test_ets_keeps_the_industry_of_the_latest_year():
    kept, left = sites.ets_sites(**_ets())
    assert list(kept["site_id"]) == ["DE_1", "DE_3", "NO_4"]
    assert set(kept["year"]) == {2025}
    assert list(kept["sector"]) == [sites.STEEL, sites.CHEMICALS, sites.OIL_GAS]
    # the power plant is counted as left out, the airline and Belfast are not
    assert left.to_dict("records") == [
        {"country": "DE", "reason": sites.POWER, "sites": 1, "co2_t": 2e5}
    ]


def test_a_norwegian_field_is_placed_at_its_facilities():
    kept, _ = sites.ets_sites(**_ets())
    facilities = gpd.GeoDataFrame(
        {
            "fclName": ["OSEBERG A", "OSEBERG C", "LISTA"],
            "fclBelongsToName": ["OSEBERG", "OSEBERG", None],
            "fclKind": ["CONDEEP 4 SHAFTS", "JACKET 8 LEGS", "LANDFALL"],
        },
        geometry=[Point(2.8, 60.5), Point(2.8, 60.6), Point(6.6, 58.2)],
        crs=4326,
    )
    placed = sites.locate_on_facilities(kept, facilities).set_index("site_id")
    assert placed.at["NO_4", "lon"] == pytest.approx(2.8)
    assert placed.at["NO_4", "lat"] == pytest.approx(60.55)
    assert placed.at["NO_4", "location"] == "field"
    assert placed.at["DE_1", "location"] == "address"


def test_naei_sites_are_converted_to_co2_and_lon_lat():
    ghgs = pd.DataFrame(
        {
            "Year": [2024, 2024, 2024, 2023],
            "Pollutant_Name": ["Carbon Dioxide as Carbon"] * 4,
            "PlantID": [1, 2, 3, 1],
            "Site": ["Port Talbot", "Drax", "Tiny", "Port Talbot"],
            "Operator": ["Tata Steel", "Drax", "Bakery", "Tata Steel"],
            "Sector": [
                "Iron & steel industries",
                "Major power producers",
                "Food, drink & tobacco industry",
                "Iron & steel industries",
            ],
            # Port Talbot in British National Grid metres
            "Easting": [277000, 466000, 400000, 277000],
            "Northing": [186000, 428000, 300000, 186000],
            "Emission": [1.2e6, 3e6, 1e3, 2e6],
        }
    )
    kept, left = sites.naei_sites(ghgs, min_site_t=1e4)
    (row,) = kept.to_dict("records")
    assert row["co2_t"] == pytest.approx(1.2e6 * 44 / 12)
    assert row["lon"] == pytest.approx(-3.77, abs=0.05)
    assert row["lat"] == pytest.approx(51.57, abs=0.05)
    assert (row["sector"], row["year"], row["source"]) == (sites.STEEL, 2024, "uk_naei")
    assert set(left["reason"]) == {sites.POWER, "below 10 kt"}


def test_a_point_off_its_country_is_left_out():
    frame = pd.DataFrame(
        {
            "site_id": ["A", "B"],
            "country": ["DE", "DE"],
            "lat": [51.0, 40.0],
            "lon": [10.0, -30.0],
            "co2_t": [1.0, 2.0],
        }
    )
    shapes = gpd.GeoDataFrame({"name": ["DE"]}, geometry=[box(6, 47, 15, 55)], crs=4326)
    kept, left = sites.within_countries(frame, shapes)
    assert list(kept["site_id"]) == ["A"]
    assert left.to_dict("records") == [
        {"country": "DE", "reason": sites.OUTSIDE, "sites": 1, "co2_t": 2.0}
    ]
