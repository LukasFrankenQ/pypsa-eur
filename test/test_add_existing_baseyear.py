# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Retirement year and availability of existing power plants."""

import numpy as np
import pandas as pd
import pypsa

from scripts.add_existing_baseyear import (
    conventional_availability,
    estimate_retirement_year,
)

COSTS = pd.DataFrame({"lifetime": {"nuclear": 40.0, "CCGT": 25.0}})


def _powerplants() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Fueltype": ["nuclear", "nuclear", "CCGT", "oil"],
            "DateIn": [1980.0, 1975.0, 2000.0, 1990.0],
            "DateOut": [np.nan, 2035.0, np.nan, np.nan],
        },
        index=["reactor", "reactor with date", "gas plant", "oil plant"],
    )


def test_missing_retirement_year_uses_cost_data_lifetime():
    retirement = estimate_retirement_year(_powerplants(), COSTS, {})

    assert retirement["reactor"] == 2020
    assert retirement["gas plant"] == 2025
    # no lifetime in the cost data
    assert retirement["oil plant"] == 2020


def test_default_lifetime_overrides_cost_data_for_listed_technologies_only():
    retirement = estimate_retirement_year(_powerplants(), COSTS, {"nuclear": 60})

    assert retirement["reactor"] == 2040
    assert retirement["gas plant"] == 2025
    # a known retirement year is kept
    assert retirement["reactor with date"] == 2035


def _network(year: int = 2013) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range(f"{year}-01-01", periods=2, freq="h"))
    n.add("Bus", ["FR0 0", "SE0 0", "PL0 0"], country=["FR", "SE", "PL"])
    return n


def _availability_table(tmp_path) -> dict[str, str]:
    table = pd.DataFrame(
        {"2013": [0.76, 0.77], "2024": [0.71, 0.81]},
        index=pd.Index(["FR", "SE"], name="country"),
    )
    table.to_csv(tmp_path / "nuclear_p_max_pu.csv")
    return {"conventional_nuclear_p_max_pu": str(tmp_path / "nuclear_p_max_pu.csv")}


def test_availability_from_table_by_country_and_weather_year(tmp_path):
    inputs = _availability_table(tmp_path)
    params = {"nuclear": {"p_max_pu": "data/nuclear_p_max_pu.csv"}}

    availability = conventional_availability(_network(), "nuclear", params, inputs)

    assert availability["FR0 0"] == 0.76
    assert availability["SE0 0"] == 0.77
    # no value for the country
    assert availability["PL0 0"] == 1.0
    # weather year not in the table: latest year
    latest = conventional_availability(_network(2019), "nuclear", params, inputs)
    assert latest["FR0 0"] == 0.71


def test_availability_single_value_and_default():
    n = _network()

    assert (
        conventional_availability(n, "coal", {"coal": {"p_max_pu": 0.9}}, {}) == 0.9
    ).all()
    # no setting for the technology
    assert (
        conventional_availability(n, "coal", {"unit_commitment": False}, {}) == 1.0
    ).all()
