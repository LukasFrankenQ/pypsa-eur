# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""Tests for selected helpers in scripts/add_electricity.py."""

import numpy as np
import pandas as pd
import pypsa
import pytest
import xarray as xr

from scripts.add_electricity import (
    attach_conventional_generators,
    attach_hydro,
    attach_load,
    attach_storageunits,
    attach_stores,
    estimate_efficiency,
    retire_powerplants,
)
from scripts.lib.validation.config.conventional import _EstimateEfficienciesConfig


def test_attach_load(tmp_path):
    """Clustered demand is attached per bus with scaling applied."""

    times = pd.date_range("2000-01-01", periods=2, freq="h")
    buses = ["zone_1", "zone_2"]
    values = np.array([[1.0, 2.0], [3.0, 4.0]])

    data = xr.DataArray(
        values,
        coords={"time": times, "bus": buses},
        dims=["time", "bus"],
        name="electricity demand (MW)",
    )
    load_path = tmp_path / "electricity_demand.nc"
    data.to_netcdf(load_path)

    n = pypsa.Network()
    n.set_snapshots(times)
    n.add("Bus", buses)

    attach_load(n, load_path.as_posix(), scaling=2.0)

    names = [f"{bus} electricity" for bus in buses]
    assert sorted(n.loads.index) == names
    assert sorted(n.loads.bus) == buses
    assert sorted(n.loads_t.p_set.columns) == names
    np.testing.assert_allclose(n.loads_t.p_set[names].values, 2.0 * values)


def test_attach_storageunits_energy_basis():
    """A dispatched-basis `max_hours` sizes the store to sustain it, at no extra cost."""
    costs = pd.DataFrame(
        {
            "capital_cost": {"iron-air": 157959.0, "battery": 60630.0},
            "marginal_cost": {"iron-air": 0.0, "battery": 0.0},
            "lifetime": {"iron-air": 17.5, "battery": 17.5},
            "efficiency": {
                "iron-air battery charge": 0.74,
                "iron-air battery discharge": 0.63,
                "battery inverter": 0.96,
            },
        }
    )
    max_hours = {"iron-air": 100, "battery": 6}

    n = pypsa.Network()
    n.add("Bus", ["bus_1", "bus_2"])
    attach_storageunits(n, costs, n.buses.index, ["iron-air", "battery"], max_hours)

    su = n.storage_units.set_index("carrier")

    iron_air = su.loc["iron-air"]
    np.testing.assert_allclose(iron_air.max_hours * iron_air.efficiency_dispatch, 100)
    np.testing.assert_allclose(iron_air.capital_cost, 157959.0)

    # Stored-basis carriers must be left alone, despite efficiency_dispatch < 1.
    np.testing.assert_allclose(su.loc["battery"].max_hours, 6)


def test_attach_stores_energy_basis():
    """A dispatched-basis store cost is converted to cost per MWh stored."""
    costs = pd.DataFrame(
        {
            "capital_cost": {
                "iron-air battery": 1000.0,
                "iron-air battery charge": 0.0,
                "iron-air battery discharge": 0.0,
            },
            "marginal_cost": 0.0,
            "lifetime": 17.5,
            "efficiency": {
                "iron-air battery": 1.0,
                "iron-air battery charge": 0.74,
                "iron-air battery discharge": 0.63,
            },
        }
    )

    n = pypsa.Network()
    n.add("Bus", ["bus_1"])
    attach_stores(n, costs, n.buses.index, ["iron-air"])

    np.testing.assert_allclose(n.stores.capital_cost, 630.0)


def test_estimate_efficiency():
    """Efficiency rises with build year (retrofit first), is clipped and degrades with age."""
    ppl = pd.DataFrame(
        {
            "carrier": ["CCGT", "coal", "coal", "biomass", "CCGT"],
            "datein": [2000, 1950, 1970, 2000, np.nan],
            "dateretrofit": [np.nan, np.nan, 2010, np.nan, np.nan],
        }
    )
    config = _EstimateEfficienciesConfig().model_dump()

    eta = estimate_efficiency(ppl, config)

    expected = [
        0.48 * (1 - 0.015),
        0.28 * (1 - 0.065),
        0.43 * (1 - 0.005),
        np.nan,
        np.nan,
    ]
    np.testing.assert_allclose(eta, expected)


@pytest.mark.parametrize(
    "method, expected",
    [
        # AA0 keeps its 4 TWh and gets the 6 TWh missing. BB's 5 TWh missing are
        # split 1:4 by p_nom on top of the listed 2 and 3 TWh.
        ("energy_capacity_totals_by_country", [10000.0, 3000.0, 1750.0]),
        # E_store / p_nom_discharge
        ("estimate_by_large_installations", [10000.0, 2000.0, 2000.0]),
    ],
)
def test_attach_hydro_max_hours(tmp_path, method, expected):
    """
    Reservoir storage follows the country totals, independent of the number of units.
    """
    ppl = pd.DataFrame(
        {
            "carrier": "hydro",
            "bus": ["AA0", "BB0", "BB1"],
            "country": ["AA", "BB", "BB"],
            "p_nom": [1000.0, 1000.0, 4000.0],
            "max_hours": [4000.0, 2000.0, 750.0],
        },
        index=["AA0 hydro", "BB0 hydro", "BB1 hydro"],
    )
    costs = pd.DataFrame(
        {"CO2 intensity": 0.0, "capital_cost": 0.0, "marginal_cost": 0.0},
        index=["hydro"],
    )
    totals = pd.DataFrame(
        {"E_store[TWh]": 10.0, "p_nom_discharge[GW]": [1.0, 5.0]}, index=["AA", "BB"]
    )
    totals.to_csv(tmp_path / "hydro_capacities.csv")
    times = pd.date_range("2013-01-01", periods=2, freq="h")
    xr.DataArray(
        np.zeros((2, 2)),
        coords={"time": times, "countries": ["AA", "BB"]},
        dims=["time", "countries"],
    ).to_netcdf(tmp_path / "profile_hydro.nc")

    n = pypsa.Network()
    n.set_snapshots(times)
    n.add("Bus", ppl.bus.values, country=ppl.country.values)

    attach_hydro(
        n,
        costs,
        ppl,
        tmp_path / "profile_hydro.nc",
        tmp_path / "hydro_capacities.csv",
        ["hydro"],
        hydro_max_hours=method,
        flatten_dispatch=False,
    )

    np.testing.assert_allclose(n.storage_units.max_hours[ppl.index], expected)


def _plants() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "carrier": ["nuclear", "nuclear", "CCGT", "CCGT", "coal", "coal", "hydro"],
            "datein": [1980.0, 1975.0, 1992.0, np.nan, 1970.0, 1990.0, 1930.0],
            "dateout": [np.nan, 2027.0, np.nan, np.nan, np.nan, 2038.0, np.nan],
            "p_nom": [1000.0, 900.0, 400.0, 300.0, 700.0, 800.0, 50.0],
        },
        index=[
            "reactor",
            "reactor with date",
            "old gas plant",
            "undated gas plant",
            "old coal plant",
            "coal plant with date",
            "old dam",
        ],
    )


_LIFETIMES = pd.DataFrame(
    {"lifetime": {"nuclear": 40.0, "CCGT": 25.0, "coal": 40.0, "hydro": 80.0}}
)


def test_retire_powerplants_uses_the_shared_retirement_rule():
    """Retired thermal plants count with zero capacity; the rest is untouched."""
    ppl = retire_powerplants(_plants(), _LIFETIMES, 2030, {"nuclear": 60, "CCGT": 35})

    # 1980 + 60 years of the default lifetime
    assert ppl.at["reactor", "p_nom"] == 1000.0
    # a known retirement year is kept
    assert ppl.at["reactor with date", "p_nom"] == 0.0
    assert ppl.at["coal plant with date", "p_nom"] == 800.0
    # 1992 + 35 years
    assert ppl.at["old gas plant", "p_nom"] == 0.0
    # no technology default: 1970 + 40 years of the cost data
    assert ppl.at["old coal plant", "p_nom"] == 0.0
    # not a thermal plant
    assert ppl.at["old dam", "p_nom"] == 50.0
    # retired plants stay in the table
    assert len(ppl) == len(_plants())


def test_retire_powerplants_keeps_a_plant_through_its_retirement_year():
    in_2027 = retire_powerplants(_plants(), _LIFETIMES, 2027, {"nuclear": 60})
    in_2028 = retire_powerplants(_plants(), _LIFETIMES, 2028, {"nuclear": 60})

    assert in_2027.at["reactor with date", "p_nom"] == 900.0
    assert in_2028.at["reactor with date", "p_nom"] == 0.0


def test_retire_powerplants_dates_an_undated_plant_like_its_technology():
    """Without a commissioning year the plant gets the mean of its technology (1992)."""
    still_standing = retire_powerplants(_plants(), _LIFETIMES, 2027, {"CCGT": 35})
    retired = retire_powerplants(_plants(), _LIFETIMES, 2028, {"CCGT": 35})

    assert still_standing.at["undated gas plant", "p_nom"] == 300.0
    assert retired.at["undated gas plant", "p_nom"] == 0.0


def test_site_without_capacity_is_kept_only_for_extendable_carriers():
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(2))
    n.add("Bus", "bus")
    costs = pd.DataFrame(
        {
            "CO2 intensity": {"CCGT": 0.2, "coal": 0.34},
            "VOM": {"CCGT": 4.0, "coal": 4.0},
        }
    )
    ppl = pd.DataFrame(
        {
            "carrier": ["CCGT", "coal", "coal"],
            "bus": "bus",
            "p_nom": [0.0, 0.0, 500.0],
            "efficiency": [0.55, 0.35, 0.4],
            "marginal_cost": [80.0, 60.0, 55.0],
            "capital_cost": [1e5, 2e5, 2e5],
            "build_year": 0,
            "lifetime": np.inf,
        },
        index=["bus CCGT", "bus coal retired", "bus coal"],
    )

    attach_conventional_generators(
        n,
        costs,
        ppl,
        conventional_carriers=["CCGT", "coal"],
        extendable_carriers={"Generator": ["CCGT"]},
        renewable_carriers=set(),
        conventional_params={},
        conventional_inputs={},
    )

    assert sorted(n.generators.index) == ["bus CCGT", "bus coal"]
    assert n.generators.at["bus CCGT", "p_nom"] == 0.0
    assert n.generators.at["bus CCGT", "p_nom_extendable"]
    assert n.generators.at["bus coal", "p_nom"] == 500.0
