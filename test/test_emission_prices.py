# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""CO2 price per planning horizon on the generators of an electricity-only network."""

import pandas as pd
import pypsa
import pytest

from scripts.prepare_network import (
    add_emission_prices,
    update_brownfield_emission_prices,
)

GAS_INTENSITY = 0.2  # t CO2 per MWh of fuel
EFFICIENCY = 0.5


def _network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(2))
    n.add("Carrier", "gas", co2_emissions=GAS_INTENSITY)
    n.add("Carrier", "wind", co2_emissions=0.0)
    n.add("Bus", "bus")
    for name in ("gas-2025", "gas-2030"):
        n.add(
            "Generator",
            name,
            bus="bus",
            carrier="gas",
            efficiency=EFFICIENCY,
            marginal_cost=50.0,
        )
    n.add("Generator", "wind", bus="bus", carrier="wind", marginal_cost=0.0)
    return n


def _prices(co2) -> dict:
    return {"enable": True, "dynamic": False, "co2": co2}


def test_emission_price_reaches_only_the_named_generators():
    n = _network()

    add_emission_prices(n, {"co2": 100.0}, generators=pd.Index(["gas-2025"]))

    assert n.generators.at["gas-2025", "marginal_cost"] == pytest.approx(
        50.0 + 100.0 * GAS_INTENSITY / EFFICIENCY
    )
    assert n.generators.at["gas-2030", "marginal_cost"] == pytest.approx(50.0)
    assert n.generators.at["wind", "marginal_cost"] == pytest.approx(0.0)


def test_carried_over_generators_move_to_the_price_of_the_horizon():
    n = _network()
    # as composed: the generator of this horizon at 120, the carried-over one at 70
    per_tonne = GAS_INTENSITY / EFFICIENCY
    n.generators.loc["gas-2030", "marginal_cost"] += 120.0 * per_tonne
    n.generators.loc["gas-2025", "marginal_cost"] += 70.0 * per_tonne

    update_brownfield_emission_prices(
        n, pd.Index(["gas-2025"]), _prices({2025: 70.0, 2030: 120.0}), 2030, 2025
    )

    expected = 50.0 + 120.0 * per_tonne
    assert n.generators.at["gas-2025", "marginal_cost"] == pytest.approx(expected)
    assert n.generators.at["gas-2030", "marginal_cost"] == pytest.approx(expected)


@pytest.mark.parametrize(
    "prices",
    [
        _prices(70.0),  # one price for every horizon: nothing to move
        {"enable": False, "dynamic": False, "co2": {2025: 70.0, 2030: 120.0}},
        {"enable": True, "dynamic": True, "co2": {2025: 70.0, 2030: 120.0}},
    ],
)
def test_carried_over_generators_are_left_alone_without_a_price_per_horizon(prices):
    n = _network()
    before = n.generators.marginal_cost.copy()

    update_brownfield_emission_prices(n, pd.Index(["gas-2025"]), prices, 2030, 2025)

    pd.testing.assert_series_equal(n.generators.marginal_cost, before)
