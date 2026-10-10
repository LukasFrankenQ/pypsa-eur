# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
Geology-based geothermal: the EGS cost model, heat-only doublets, the supply
curves per region and their network components.
"""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pypsa
import pytest
import xarray as xr

from scripts._helpers import check_unique_component_names
from scripts.add_brownfield import adjust_geothermal_capacity_limits
from scripts.build_egs_potentials import column_costs
from scripts.build_geothermal_heat_potential import (
    doublet_calibration,
    geology_heat_steps,
)
from scripts.lib.geothermal import (
    annuity,
    bin_supply_curve,
    doublet_costs,
    drilling_cost,
    egs_costs,
)
from scripts.prepare_sector_network import (
    add_enhanced_geothermal,
    add_geothermal_heat_steps,
)
from scripts.solve_network import add_flexible_egs_constraint

EGS_CONFIG = {
    "enable": True,
    "flexible": True,
    "max_hours": 240,
    "max_boost": 0.25,
    "var_cf": False,
    "max_temperature": 250.0,
    "min_temperature": 150.0,
    "reinjection_temperature": 70.0,
    "parasitic_fraction": 0.15,
    "availability": 0.85,
    "producers_per_injector": 1.5,
    "flow_derating": 0.77,
    "lateral_length": 2286.0,
    "lifetime": 30.0,
    "usd2021_to_eur": 0.836,
    "lcoe_bins": [75, 100, 125, 150, 175, 200, 250, 300, 400],
}

HEAT_CONFIG = {
    "depths": [1.0, 1.5, 2.0, 2.5, 3.0, 3.5],
    "min_temperature": 70.0,
    "reinjection_temperature": 40.0,
    "hydrothermal_max_depth": 3.0,
    "well_spacing": 1.0,
    "flow_hydrothermal": 100.0,
    "flow_stimulated": 60.0,
    "pump_cost": 0.6e6,
    "stimulation_cost": 1.03e6,
    "drilling_cost": {"constant": 250000.0, "linear": 700.0, "quadratic": 0.2},
    "calibration": {"depth": 2.5, "production_temperature": 78.0, "investment": 1719.0},
    "fixed_om": 118.0,
    "variable_om": 5.3,
    "price_level_to_eur2020": 0.816,
    "lifetime": 30.0,
    "full_load_hours": 4000.0,
    "lcoh_bins": [40, 50, 60, 70, 80, 100, 125, 150, 200],
    "urban_density": 1500.0,
    "discount_rate": 0.07,
}


@pytest.mark.parametrize(
    "temperature, depth, capex",
    [
        (150, 2.5, 8960),
        (150, 6.5, 17300),
        (200, 4.5, 8000),
        (250, 4.5, 5640),
        (300, 6.5, 6320),
    ],
)
def test_egs_cost_model_reproduces_the_source_reference_values(
    temperature, depth, capex
):
    # Ricks & Jenkins 2025 central case at 283.15 K ambient, USD2021 per gross kW
    out = egs_costs(np.array([temperature]), 283.15, depth)
    total = out["plant_capex"][0] + out["well_capex"][0]
    assert total == pytest.approx(capex, rel=2e-3)


def test_egs_conversion_efficiency_is_a_binary_plant_efficiency():
    out = egs_costs(np.array([150.0, 250.0]), 283.15, 4.5)
    efficiency = out["gross_mw"] / out["thermal_mw"]
    assert ((efficiency > 0.1) & (efficiency < 0.2)).all()
    assert efficiency[1] > efficiency[0]


def test_egs_cost_model_refuses_unfitted_depths():
    with pytest.raises(ValueError, match="fitted"):
        egs_costs(np.array([200.0]), 283.15, 3.0)


def test_drilling_cost_is_the_thermogis_curve():
    assert drilling_cost(2000.0) == pytest.approx(250000 + 700 * 2000 + 0.2 * 2000**2)


def test_doublet_costs_split_hydrothermal_and_stimulated():
    out = doublet_costs(np.array([100.0, 100.0]), 2.0, stimulated=[False, True])
    t_prod = out["production_temperature"]
    assert t_prod[0] == pytest.approx(100 - (0.6889 * 2 - 0.1333))
    assert out["thermal_mw"][0] == pytest.approx(100 * 4.2 * (t_prod[0] - 40) / 1e3)
    assert out["thermal_mw"][1] == pytest.approx(0.6 * out["thermal_mw"][0])
    assert out["capex"][1] - out["capex"][0] == pytest.approx(1.03e6)
    assert out["capex"][0] == pytest.approx(2 * drilling_cost(2000.0) + 0.6e6)


def test_bin_supply_curve_weights_costs_by_capacity():
    cells = pd.DataFrame(
        {
            "bus": ["A", "A", "A", "B"],
            "lcoe": [80.0, 90.0, 140.0, 500.0],
            "p_nom_max": [1.0, 3.0, 2.0, 5.0],
            "capex": [10.0, 20.0, 30.0, 40.0],
        }
    )
    steps = bin_supply_curve(cells, "lcoe", "p_nom_max", bins=[100, 150])
    assert list(steps.bus) == ["A", "A"]
    assert steps.p_nom_max.tolist() == [4.0, 2.0]
    assert steps.capex.iloc[0] == pytest.approx((10 + 60) / 4)
    assert steps.lcoe.iloc[0] == pytest.approx((80 + 270) / 4)


def _columns(temperatures: dict[float, list[float]], **attrs) -> xr.Dataset:
    depths = sorted(temperatures)
    n_columns = len(temperatures[depths[0]])
    defaults = {
        "bus": ["N0"] * n_columns,
        "area": [50.0] * n_columns,
        "land_share": [0.5] * n_columns,
        "urban_population": [0.0] * n_columns,
        "cell_area": [700.0] * n_columns,
        "dh_area": [np.nan] * n_columns,
        "dh_cap": [np.nan] * n_columns,
    }
    defaults.update(attrs)
    ds = xr.Dataset({k: ("column", np.asarray(v)) for k, v in defaults.items()})
    ds["temperature"] = xr.DataArray(
        np.array([temperatures[d] for d in depths]).T,
        dims=("column", "depth"),
        coords={"depth": depths},
    )
    return ds


def test_egs_columns_take_their_cheapest_feasible_depth():
    egs_depths = [2.5, 3.5, 4.5, 5.5, 6.5]
    hot = [140.0, 180.0, 220.0, 260.0, 300.0]
    cold = [60.0, 80.0, 100.0, 120.0, 140.0]
    columns = _columns({d: [h, c] for d, h, c in zip(egs_depths, hot, cold)})
    cells = column_costs(
        columns, pd.Series({"N0": 10.0}), EGS_CONFIG, discount_rate=0.07
    )
    # the cold column never reaches 150 °C
    assert len(cells) == 1
    cell = cells.iloc[0]
    assert cell.depth > 2.5
    assert cell.temperature >= 150
    assert 0.1 < cell.efficiency < 0.2
    # capacity = net density x area x land share
    out = egs_costs(np.array([cell.temperature]), 283.15, cell.depth)
    assert cell.p_nom_max == pytest.approx(
        out["mw_per_km2"][0] * 0.85 * 50 * 0.5, rel=0.05
    )


def test_heat_doublet_calibration_reproduces_the_reference_investment():
    scale = doublet_calibration(HEAT_CONFIG)
    # reference: hydrothermal doublet at 2.5 km, 78 °C at the wellhead, ~16 MW_th
    ref = doublet_costs(78.0 + 0.6889 * 2.5 - 0.1333, 2.5, stimulated=False)
    assert ref["thermal_mw"] == pytest.approx(16.0, abs=0.1)
    per_kw = ref["capex"] * scale / (ref["thermal_mw"] * 1e3)
    assert per_kw == pytest.approx(1719 * 0.816)


def test_heat_doublets_sit_in_district_heating_areas_or_urban_cells():
    depths = HEAT_CONFIG["depths"]
    temps = [45.0, 60.0, 75.0, 90.0, 105.0, 120.0]
    columns = _columns(
        {d: [t, t, t] for d, t in zip(depths, temps)},
        bus=["N0", "N0", "N1"],
        # first column: inside a district-heating area, demand-capped at 30 MW
        dh_area=[4.0, 0.0, np.nan],
        dh_cap=[30.0, 0.0, np.nan],
        # third column: no district-heating data; 210 thousand urban inhabitants
        # in a 700 km² weather cell = 20 % urban at 1,500 inhabitants/km²
        urban_population=[0.0, 0.0, 210.0],
    )
    steps, temperature = geology_heat_steps(columns, HEAT_CONFIG)
    by_bus = steps.groupby("bus").p_nom_max.sum()
    assert by_bus["N0"] == pytest.approx(30.0)
    # urban share of the column area / 2 km² per doublet x MW_th per doublet
    t_prod = 105.0 - (0.6889 * 3.0 - 0.1333)
    per_doublet = 100 * 4.2 * (t_prod - 40) / 1e3
    assert by_bus["N1"] == pytest.approx(50.0 * 0.2 / 2 * per_doublet)
    # hotter brine pays for deeper wells: the cheapest doublet is the deepest
    # unstimulated one (3.0 km, 105 °C rock), not the shallowest reaching 70 °C
    assert set(steps.depth) == {3.0}
    assert (temperature >= 70).all()
    assert steps.fom.iloc[0] == pytest.approx(118 * 0.816)
    assert steps.vom.iloc[0] == pytest.approx(5.3 * 0.816)
    # at PBL cost levels a deep doublet costs tens of EUR per MWh, not a few
    assert 30 < steps.lcoh.min() < 100


def _egs_network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2040-01-01", periods=3, freq="h"))
    for node in ["N0", "N1"]:
        n.add("Bus", node, carrier="AC", location=node)
    n.add("Bus", "N0 urban central heat", carrier="urban central heat", location="N0")
    n.add(
        "Load",
        "N0 urban central heat demand",
        bus="N0 urban central heat",
        carrier="urban central heat",
        p_set=1.0,
    )
    return n


def _egs_steps(tmp_path) -> str:
    steps = pd.DataFrame(
        {
            "bus": ["N0", "N0", "N1"],
            "step": [3, 5, 4],
            "p_nom_max": [100.0, 200.0, 50.0],
            "well_capex": [6000.0, 9000.0, 7000.0],
            "plant_capex": [3000.0, 3500.0, 3000.0],
            "fom": [150.0, 200.0, 160.0],
            "efficiency": [0.12, 0.14, 0.13],
            "lcoe": [140.0, 190.0, 160.0],
            "depth": [4.5, 6.5, 5.5],
            "temperature": [180.0, 210.0, 190.0],
        }
    )
    path = tmp_path / "egs_potentials.csv"
    steps.to_csv(path, index=False)
    return str(path)


def _costs() -> pd.DataFrame:
    return pd.DataFrame(
        {"district heat surcharge": [25.0], "district heat-input": [0.8]},
        index=["geothermal"],
    )


def _spatial() -> SimpleNamespace:
    return SimpleNamespace(
        geothermal_heat=SimpleNamespace(nodes=["EU enhanced geothermal systems"])
    )


def test_egs_links_are_sized_in_heat_and_priced_per_step(tmp_path):
    n = _egs_network()
    add_enhanced_geothermal(
        n, _egs_steps(tmp_path), EGS_CONFIG, _costs(), 0.07, _spatial()
    )
    wells = n.links[n.links.carrier == "geothermal heat"]
    assert list(wells.index) == [
        "N0 enhanced geothermal 0",
        "N0 enhanced geothermal 1",
        "N1 enhanced geothermal 0",
    ]
    well = wells.loc["N0 enhanced geothermal 0"]
    assert well.p_nom_max == pytest.approx(100 / 0.12)
    assert well.p_max_pu == pytest.approx(0.85)
    crf = annuity(30, 0.07)
    # FOM as a share of investment per year, not %/yr used as a fraction
    fom_share = 150 / 9000
    nyears = 3 / 8760
    assert well.capital_cost == pytest.approx(
        (crf + fom_share) * 6000 * 0.12 * 1e3 * nyears
    )

    orc = n.links.loc["N0 geothermal organic rankine cycle"]
    efficiency = (0.12 * 100 + 0.14 * 200) / 300
    assert orc.efficiency == pytest.approx(efficiency)
    assert orc.bus0 == "N0 geothermal heat surface"

    chp = n.links.loc["N0 geothermal heat district heat"]
    assert chp.efficiency == pytest.approx(0.8)
    assert chp.capital_cost == pytest.approx(orc.capital_cost * 0.25)
    # N1 has no district heating
    assert "N1 geothermal heat district heat" not in n.links.index
    assert set(n.storage_units.index) == {
        "N0 geothermal reservoir",
        "N1 geothermal reservoir",
    }
    check_unique_component_names(n)


def test_flexible_egs_constraint_is_one_row_per_region(tmp_path):
    n = _egs_network()
    n.add("Generator", "N0 slack", bus="N0", p_nom=1e3, marginal_cost=1e3)
    n.add(
        "Generator",
        "N0 heat slack",
        bus="N0 urban central heat",
        p_nom=1e3,
        marginal_cost=1e3,
    )
    add_enhanced_geothermal(
        n, _egs_steps(tmp_path), EGS_CONFIG, _costs(), 0.07, _spatial()
    )
    n.optimize.create_model()
    add_flexible_egs_constraint(n)
    constraint = n.model.constraints[
        "upper_bound_charging_capacity_of_geothermal_reservoir"
    ]
    assert set(constraint.indexes["bus"]) == {
        "N0 geothermal heat surface",
        "N1 geothermal heat surface",
    }
    # N0's reservoir is bounded by the sum of its two wells
    assert constraint.lhs.sel(bus="N0 geothermal heat surface").nterm == 3


def test_geothermal_heat_steps_become_generators(tmp_path):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2040-01-01", periods=2, freq="h"))
    carrier = "urban central geothermal heat"
    for node in ["N0", "N1"]:
        n.add("Bus", f"{node} {carrier}", carrier=carrier)
    steps = pd.DataFrame(
        {
            "bus": ["N0", "N0", "N1", "N2"],
            "p_nom_max": [10.0, 20.0, 5.0, 7.0],
            "capex": [1000.0, 1500.0, 1200.0, 900.0],
            "fom": 96.0,
            "vom": 4.3,
            "lifetime": 30.0,
        }
    )
    path = tmp_path / "steps.csv"
    steps.to_csv(path, index=False)
    add_geothermal_heat_steps(
        n,
        str(path),
        pd.Index(["N0", "N1"]),
        carrier,
        overdim_factor=1.2,
        discount_rate=0.07,
    )
    gens = n.generators
    assert list(gens.index) == [
        f"N0 {carrier} Generator",
        f"N0 {carrier} Generator 1",
        f"N1 {carrier} Generator",
    ]
    nyears = 2 / 8760
    expected = (annuity(30, 0.07) * 1500 + 96) * 1e3 * 1.2 * nyears
    assert gens.at[f"N0 {carrier} Generator 1", "capital_cost"] == pytest.approx(
        expected
    )
    assert (gens.marginal_cost == 4.3).all()


def test_brownfield_subtracts_earlier_geothermal_builds():
    n = pypsa.Network()
    n.add("Bus", "EU")
    n.add("Bus", "N0 surface")
    n.add(
        "Link",
        "N0 enhanced geothermal 0-2030",
        bus0="EU",
        bus1="N0 surface",
        carrier="geothermal heat",
        p_nom=40.0,
    )
    n.add(
        "Link",
        "N0 enhanced geothermal 0-2040",
        bus0="EU",
        bus1="N0 surface",
        carrier="geothermal heat",
        p_nom_extendable=True,
        p_nom_max=100.0,
    )
    n.add(
        "Generator",
        "N0 urban central geothermal heat Generator-2030",
        bus="N0 surface",
        carrier="urban central geothermal heat",
        p_nom=8.0,
    )
    n.add(
        "Generator",
        "N0 urban central geothermal heat Generator-2040",
        bus="N0 surface",
        carrier="urban central geothermal heat",
        p_nom_extendable=True,
        p_nom_max=5.0,
    )
    adjust_geothermal_capacity_limits(n, "2040")
    assert n.links.at["N0 enhanced geothermal 0-2040", "p_nom_max"] == 60.0
    assert (
        n.generators.at["N0 urban central geothermal heat Generator-2040", "p_nom_max"]
        == 0.0
    )
