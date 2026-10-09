# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
Hydrogen-boosted biofuel routes, biogas and grey/blue methanol, biofuel waste heat
(after Zhang et al. 2026, arXiv 2604.12080).
"""

from types import SimpleNamespace

import pandas as pd
import pypsa
import pytest

from scripts._helpers import check_unique_component_names
from scripts.prepare_sector_network import (
    add_biogas_to_methanol,
    add_e_biomethanol,
    add_e_biosng,
    add_grey_methanol,
    add_waste_heat,
    boosted_hydrogen_input,
    boosted_product,
)

NODES = pd.Index(["N0", "N1"])

# technology-data v0.15.0, 2040
ROWS = {
    ("solid biomass", "CO2 intensity"): 0.3667,
    ("methanol", "CO2 intensity"): 0.2482,
    ("oil", "CO2 intensity"): 0.2571,
    ("gas", "CO2 intensity"): 0.198,
    ("BtL", "efficiency"): 0.4167,
    ("BtL", "C in fuel"): 0.2922,
    ("BtL", "C stored"): 0.7078,
    ("BtL", "capture rate"): 0.9,
    ("Fischer-Tropsch", "hydrogen-input"): 1.363,
    ("electrobiofuels", "efficiency-biomass"): 1.325,
    ("biomass-to-methanol", "efficiency"): 0.63,
    ("biomass-to-methanol", "C in fuel"): 0.4265,
    ("biomass-to-methanol", "C stored"): 0.5735,
    ("biomass-to-methanol", "capture rate"): 0.9,
    ("biomass-to-methanol", "efficiency-heat"): 0.22,
    ("biomass-to-methanol", "capital_cost"): 250.0,
    ("biomass-to-methanol", "lifetime"): 20.0,
    ("methanolisation", "hydrogen-input"): 1.138,
    ("methanolisation", "capital_cost"): 100.0,
    ("methanolisation", "discount rate"): 0.07,
    ("BioSNG", "efficiency"): 0.665,
    ("BioSNG", "C in fuel"): 0.3591,
    ("BioSNG", "C stored"): 0.6409,
    ("BioSNG", "capture rate"): 0.9,
    ("BioSNG", "capital_cost"): 180.0,
    ("BioSNG", "lifetime"): 25.0,
    ("methanation", "hydrogen-input"): 1.282,
    ("methanation", "capital_cost"): 70.0,
    ("biogas", "capital_cost"): 90.0,
    ("grey methanol synthesis", "efficiency"): 0.569,
    ("SMR", "capital_cost"): 50.0,
    ("SMR", "lifetime"): 30.0,
    ("SMR CC", "capital_cost"): 58.0,
    ("SMR CC", "capture_rate"): 0.9,
}

BIOGAS = {
    "biogas_carbon_intensity": 0.33,
    "biogas_to_methanol": {
        "efficiency": 0.6601,
        "hydrogen_input": 0.0,
        "investment": 3307.0,
        "FOM": 0.83,
        "VOM": 2.54,
        "lifetime": 20.0,
    },
    "e_biogas_methanol": {
        "efficiency": 0.9319,
        "hydrogen_input": 0.5238,
        "investment": 3165.6,
        "FOM": 0.83,
        "VOM": 2.388,
        "lifetime": 20.0,
    },
}


def _costs() -> pd.DataFrame:
    costs = pd.Series(ROWS).unstack().fillna(0.0)
    costs["VOM"] = 0.0
    return costs


def _spatial(biomass_spatial: bool = True) -> SimpleNamespace:
    biomass = (
        NODES + " solid biomass" if biomass_spatial else pd.Index(["EU solid biomass"])
    )
    return SimpleNamespace(
        biomass=SimpleNamespace(nodes=biomass),
        h2=SimpleNamespace(nodes=NODES + " H2"),
        methanol=SimpleNamespace(nodes=["EU methanol"]),
        gas=SimpleNamespace(
            nodes=["EU gas"], locations=["EU"], biogas=NODES + " biogas"
        ),
        co2=SimpleNamespace(nodes=["co2 stored"]),
    )


def _network(biomass_spatial: bool = True) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2040-01-01", periods=2, freq="h"))
    for node in NODES:
        n.add("Bus", node, carrier="AC", location=node)
        n.add("Bus", node + " H2", carrier="H2", location=node)
        n.add("Bus", node + " biogas", carrier="biogas", location=node)
    if biomass_spatial:
        for node in NODES:
            n.add(
                "Bus", node + " solid biomass", carrier="solid biomass", location=node
            )
    else:
        n.add("Bus", "EU solid biomass", carrier="solid biomass", location="EU")
    n.add("Bus", "N0 urban central heat", carrier="urban central heat", location="N0")
    n.add("Bus", "EU methanol", carrier="methanol", location="EU")
    n.add("Bus", "EU gas", carrier="gas", location="EU")
    n.add("Bus", "co2 atmosphere", carrier="co2")
    n.add("Bus", "co2 stored", carrier="co2 stored")
    return n


def _carbon_out(link: pd.Series, co2_buses=("co2 atmosphere", "co2 stored")) -> float:
    """Net CO2 the link puts on the CO2 buses per MWh feed."""
    total = 0.0
    for port in range(1, 6):
        if link.get(f"bus{port}") in co2_buses:
            total += link[f"efficiency{port}" if port > 1 else "efficiency"]
    return total


def test_boosted_product_reproduces_the_electrobiofuels_row():
    assert boosted_product(_costs(), "BtL", "oil") == pytest.approx(1.325, abs=1e-3)


def test_electrobiofuels_hydrogen_follows_the_fischer_tropsch_input():
    h2 = boosted_hydrogen_input(_costs(), "BtL", "Fischer-Tropsch", 1.325)
    assert h2 == pytest.approx((1.325 - 0.4167) * 1.363)
    assert h2 == pytest.approx(1.238, abs=1e-3)
    # the row's own ratio, 1.325 / 1.1517, under-counts it
    assert h2 > 1.325 / 1.1517


def test_e_biomethanol_balances_carbon_and_hydrogen():
    n = _network()
    add_e_biomethanol(n, _costs(), _spatial())
    links = n.links[n.links.carrier == "e-biomethanol"]
    assert list(links.index) == [
        "N0 solid biomass N0 e-biomethanol",
        "N1 solid biomass N1 e-biomethanol",
    ]
    link = links.iloc[0]
    assert link.efficiency == pytest.approx(1.393, abs=1e-3)
    assert -link.efficiency2 == pytest.approx((1.393 - 0.63) * 1.138, abs=2e-3)
    # the atmosphere gives exactly the carbon bound in the methanol
    assert -link.efficiency3 == pytest.approx(link.efficiency * 0.2482)
    assert link.capital_cost == pytest.approx(
        250 * 0.63 + 100 * (link.efficiency - 0.63)
    )
    check_unique_component_names(n)


def test_e_biosng_balances_carbon():
    n = _network()
    add_e_biosng(n, _costs(), _spatial())
    link = n.links[n.links.carrier == "e-bioSNG"].iloc[0]
    assert link.efficiency == pytest.approx(1.734, abs=2e-3)
    assert -link.efficiency2 == pytest.approx((link.efficiency - 0.665) * 1.282)
    assert -link.efficiency3 == pytest.approx(link.efficiency * 0.198)


def test_non_spatial_biomass_pairs_with_every_hydrogen_node():
    n = _network(biomass_spatial=False)
    add_e_biomethanol(n, _costs(), _spatial(biomass_spatial=False))
    links = n.links[n.links.carrier == "e-biomethanol"]
    assert set(links.bus0) == {"EU solid biomass"}
    assert set(links.bus2) == {"N0 H2", "N1 H2"}


@pytest.mark.parametrize("boosted", [False, True])
def test_biogas_methanol_routes_bind_only_the_methanol_carbon(boosted):
    n = _network()
    add_biogas_to_methanol(n, _costs(), _spatial(), BIOGAS, boosted=boosted)
    carrier = "e-biogas-methanol" if boosted else "biogas to methanol"
    link = n.links[n.links.carrier == carrier].iloc[0]
    assert -_carbon_out(link) == pytest.approx(link.efficiency * 0.2482)
    if boosted:
        assert link.efficiency2 == pytest.approx(-0.5238)
    # the digester is paid by whichever link leaves the biogas bus
    assert link.capital_cost > 90.0
    check_unique_component_names(n)


def test_biogas_route_binding_more_carbon_than_biogas_carries_is_refused():
    params = {**BIOGAS, "biogas_carbon_intensity": 0.2}
    with pytest.raises(ValueError, match="more than"):
        add_biogas_to_methanol(_network(), _costs(), _spatial(), params, boosted=True)


def test_grey_and_blue_methanol_split_the_process_co2():
    n = _network()
    add_grey_methanol(n, _costs(), _spatial())
    add_grey_methanol(n, _costs(), _spatial(), blue=True)
    grey = n.links.loc["EU grey methanol"]
    blue = n.links.loc["EU blue methanol"]
    process = 0.198 - 0.569 * 0.2482
    assert process == pytest.approx(0.0568, abs=1e-4)
    assert grey.efficiency2 == pytest.approx(process)
    assert blue.efficiency2 == pytest.approx(0.1 * process)
    assert blue.efficiency3 == pytest.approx(0.9 * process)
    assert blue.bus3 == "co2 stored"
    assert blue.capital_cost - grey.capital_cost == pytest.approx(8.0)
    check_unique_component_names(n)


def _waste_heat_options(**overrides) -> dict:
    options = {
        "use_fischer_tropsch_waste_heat": 0,
        "use_methanation_waste_heat": 0,
        "use_haber_bosch_waste_heat": 0,
        "use_methanolisation_waste_heat": 0,
        "use_electrolysis_waste_heat": 0,
        "use_fuel_cell_waste_heat": 0,
        "use_biofuel_waste_heat": 0.25,
        "use_biosng_waste_heat": 0.25,
        "use_biomethanol_waste_heat": 0.25,
    }
    return {**options, **overrides}


def test_waste_heat_goes_to_district_heating_at_the_feed_node():
    n = _network()
    costs = _costs()
    add_e_biomethanol(n, costs, _spatial())
    add_biogas_to_methanol(n, costs, _spatial(), BIOGAS)
    add_waste_heat(n, costs, _waste_heat_options(), cf_industry={})

    e_meoh = n.links.loc["N0 solid biomass N0 e-biomethanol"]
    assert e_meoh.bus4 == "N0 urban central heat"
    expected = 0.95 * (1 - e_meoh.efficiency2) - e_meoh.efficiency
    assert e_meoh.efficiency4 == pytest.approx(0.25 * expected)
    biogas = n.links.loc["N0 biogas to methanol"]
    assert biogas.bus3 == "N0 urban central heat"
    assert biogas.efficiency3 == pytest.approx(0.25 * (0.95 - 0.6601))
    # N1 has no district heating: its heat stays unused
    assert n.links.at["N1 solid biomass N1 e-biomethanol", "bus4"] == ""


def test_waste_heat_is_skipped_for_an_eu_biomass_bus():
    n = _network(biomass_spatial=False)
    costs = _costs()
    add_e_biomethanol(n, costs, _spatial(biomass_spatial=False))
    add_waste_heat(n, costs, _waste_heat_options(), cf_industry={})
    links = n.links[n.links.carrier == "e-biomethanol"]
    assert "bus4" not in n.links.columns or (links.bus4 == "").all()


def test_waste_heat_off_leaves_links_untouched():
    n = _network()
    costs = _costs()
    add_e_biomethanol(n, costs, _spatial())
    add_waste_heat(
        n, costs, _waste_heat_options(use_biomethanol_waste_heat=0), cf_industry={}
    )
    assert "bus4" not in n.links.columns or (n.links.bus4 == "").all()
