# SPDX-FileCopyrightText: 2026 Iegor Riepin
#
# SPDX-License-Identifier: MIT
"""Unit tests for the prepare_network patch engine and some of its patches."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pypsa
import pytest

from scripts.prepare_network import (
    _move_inflow_to_reversible,
    _patch_es_unit_outages,
    _patch_fr_nuclear_profile,
    _patch_national_capacities,
    _patch_water_value,
    attach_boundary_flows,
    freeze_fleet,
    prepare_network,
)


def _toy_network(periods: int = 6) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2024-01-01", periods=periods, freq="h"))
    n.add("Bus", "FR0 0AC", carrier="AC", country="FR", x=-1.0, y=47.0)
    n.add("Bus", "FR0 1AC", carrier="AC", country="FR", x=6.0, y=49.0)
    n.add("Bus", "ES0 0AC", carrier="AC", country="ES", x=-3.7, y=40.4)
    n.add(
        "Generator",
        "ES0 0AC gas",
        bus="ES0 0AC",
        carrier="CCGT",
        p_nom=100.0,
        p_nom_extendable=True,
    )
    return n


def _flows(snapshots: pd.DatetimeIndex) -> pd.DataFrame:
    rows = []
    for nb, values in {
        "GB": [500.0] * 3 + [-300.0] * 3,
        "DE_BE": [-1000.0] * 6,
    }.items():
        for ts, v in zip(snapshots, values):
            rows.append({"snapshot": ts, "neighbour": nb, "flow_mw_into_FR": v})
    return pd.DataFrame(rows)


def test_attach_boundary_flows_nearest_bus_and_fixed() -> None:
    """Imports become fixed generators, exports fixed loads, each at the nearest FR bus."""
    n = _toy_network()
    attach_boundary_flows(n, _flows(n.snapshots))

    gb_gen = "FR0 0AC boundary import GB"
    assert gb_gen in n.generators.index
    assert n.generators.at[gb_gen, "carrier"] == "boundary"
    assert not n.generators.at[gb_gen, "p_nom_extendable"]
    assert np.allclose(n.generators_t.p_min_pu[gb_gen], n.generators_t.p_max_pu[gb_gen])
    imp_mw = n.generators_t.p_max_pu[gb_gen] * n.generators.at[gb_gen, "p_nom"]
    assert np.allclose(imp_mw, [500.0] * 3 + [0.0] * 3)

    assert "FR0 0AC boundary export GB" in n.loads.index
    assert np.allclose(
        n.loads_t.p_set["FR0 0AC boundary export GB"], [0.0] * 3 + [300.0] * 3
    )
    # DE_BE is pure export, attached at the eastern bus
    assert "FR0 1AC boundary import DE_BE" not in n.generators.index
    assert np.allclose(n.loads_t.p_set["FR0 1AC boundary export DE_BE"], 1000.0)


def test_attach_boundary_flows_rejects_gaps() -> None:
    """Flows not covering all snapshots raise instead of silently filling."""
    n = _toy_network()
    short = _flows(n.snapshots[:3])
    with pytest.raises(ValueError, match="do not cover"):
        attach_boundary_flows(n, short)


def test_prepare_network_freezes_and_rejects_unknown_patch() -> None:
    """prepare_network applies patches, freezes the fleet, and fails fast on unknown names."""
    n = _toy_network()
    prepare_network(
        n, patches={"boundary_flows": True}, ctx={"boundary_flows": _flows(n.snapshots)}
    )
    assert not n.generators["p_nom_extendable"].any()

    with pytest.raises(ValueError, match="Unknown patches"):
        prepare_network(_toy_network(), patches={"nonsense": True}, ctx={})


def test_freeze_fleet_idempotent() -> None:
    """freeze_fleet is a no-op on an already frozen network."""
    n = _toy_network()
    freeze_fleet(n)
    freeze_fleet(n)
    assert not n.generators["p_nom_extendable"].any()


def _nuclear_toy() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2024-01-01", "2024-12-31 23:00", freq="MS"))
    n.add("Bus", "FR0 0AC", carrier="AC", country="FR", x=2.0, y=48.0)
    n.add(
        "Generator",
        "FR0 0AC nuclear",
        bus="FR0 0AC",
        carrier="nuclear",
        p_nom=60000.0,
        p_max_pu=0.715,
    )
    return n


def _nuclear_csv(tmp_path: Path) -> Path:
    lines = ["country,year,month,carrier_or_metric,value,unit,source_id,note"]
    values = [
        48294,
        44152,
        40228,
        37040,
        37420,
        36626,
        38422,
        38432,
        38120,
        40224,
        45879,
        49262,
    ]
    lines += [
        f"FR,2024,{m},nuclear_generation_monthly_mean,{v},MW,rte_eco2mix_2024_nuclear,test"
        for m, v in enumerate(values, start=1)
    ]
    (tmp_path / "fr_nuclear_monthly_2024.csv").write_text("\n".join(lines) + "\n")
    return tmp_path


def test_fr_nuclear_profile_seasonalizes_preserving_annual_mean(tmp_path: Path) -> None:
    """The monthly shape is applied with the annual weighted mean kept at the flat factor."""
    n = _nuclear_toy()
    ctx = {"custom_dir": _nuclear_csv(tmp_path)}
    _patch_fr_nuclear_profile(n, ctx)

    profile = n.generators_t.p_max_pu["FR0 0AC nuclear"]
    assert np.isclose(profile.mean(), 0.715, rtol=1e-9)
    assert profile.max() <= 1.0
    dec = profile[profile.index.month == 12].iloc[0]
    jun = profile[profile.index.month == 6].iloc[0]
    assert dec > 0.715 > jun
    assert np.isclose(dec / jun, 49262 / 36626, rtol=1e-9)


def test_created_fleet_carrier_gets_tech_data_co2(tmp_path: Path) -> None:
    """A carrier the patch creates from scratch carries its CO2 intensity (priced and counted)."""
    n = _toy_network()
    n.add("Load", "FR0 1AC demand", bus="FR0 1AC", p_set=pd.Series(1.0, n.snapshots))
    pd.DataFrame(
        [
            {
                "country": "FR",
                "year": 2024,
                "carrier_or_metric": "OCGT/gas turbines",
                "value": 1500.0,
                "unit": "MW",
                "source_id": "x",
                "note": "",
            }
        ]
    ).to_csv(tmp_path / "national_capacities.csv", index=False)
    costs = tmp_path / "costs.csv"
    pd.DataFrame(
        {"efficiency": [0.405], "CO2 intensity": [0.198]}, index=["OCGT"]
    ).to_csv(costs)
    ctx = {"custom_dir": tmp_path, "data_year": 2024, "costs": costs}
    _patch_national_capacities(n, ctx)
    assert n.generators.at["FR0 1AC OCGT", "p_nom"] == pytest.approx(1500.0)
    assert n.carriers.at["OCGT", "co2_emissions"] == pytest.approx(0.198)


def test_water_value_takes_the_contract_of_the_local_day(tmp_path: Path) -> None:
    """Every UTC hour is priced with the day contract of its Madrid day."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2024-01-01", periods=48, freq="h"))
    n.add("Bus", "ES0 0AC", carrier="AC")
    n.add(
        "StorageUnit",
        "ES0 0AC hydro",
        bus="ES0 0AC",
        carrier="hydro",
        p_nom=100.0,
        efficiency_dispatch=0.9,
    )
    rows = [
        {"zone": zone, "day": f"2024-01-0{day}", "eur_mwh": 10.0 * day}
        for zone in ("ES", "FR")
        for day in (1, 2, 3)
    ]
    pd.DataFrame(rows).to_csv(tmp_path / "omip_day_contract_2024.csv", index=False)
    ctx = {"custom_dir": tmp_path, "data_year": 2024, "water_value_contract": "day"}
    _patch_water_value(n, ctx)

    cost = n.storage_units_t.marginal_cost["ES0 0AC hydro"]
    assert cost.tolist() == [10.0] * 23 + [20.0] * 24 + [30.0]  # Madrid is UTC+1
    assert np.allclose(n.storage_units_t.spill_cost["ES0 0AC hydro"], 0.9 * cost)


def test_es_unit_outages_cap_availability_and_keep_nuclear_minimum(
    tmp_path: Path,
) -> None:
    """Declared outages cap output; reactors keep the cited share of what is left."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2024-01-01", periods=3, freq="h"))
    n.add("Bus", "ES0 0AC", carrier="AC")
    for carrier, p_nom in (("nuclear", 1000.0), ("CCGT", 400.0)):
        n.add(
            "Generator",
            f"ES0 0AC {carrier}",
            bus="ES0 0AC",
            carrier=carrier,
            p_nom=p_nom,
        )
    pd.DataFrame(
        {"nuclear": [0.0, 500.0, 1000.0], "CCGT": [0.0, 100.0, 600.0]},
        index=pd.Index(n.snapshots, name="snapshot"),
    ).to_csv(tmp_path / "es_unit_outages_2024.csv")
    pd.DataFrame(
        [
            {
                "country": "ES_peninsular",
                "year": 2024,
                "carrier_or_metric": "nuclear minimum output",
                "value": 0.6,
            }
        ]
    ).to_csv(tmp_path / "national_capacities.csv", index=False)
    _patch_es_unit_outages(n, {"custom_dir": tmp_path, "data_year": 2024})

    p_max_pu, p_min_pu = n.generators_t.p_max_pu, n.generators_t.p_min_pu
    assert p_max_pu["ES0 0AC nuclear"].tolist() == [1.0, 0.5, 0.0]
    assert p_max_pu["ES0 0AC CCGT"].tolist() == [1.0, 0.75, 0.0]
    assert np.allclose(p_min_pu["ES0 0AC nuclear"], [0.6, 0.3, 0.0])
    assert "ES0 0AC CCGT" not in p_min_pu


def test_reversible_plants_take_natural_water_of_modeled_hours_only(
    tmp_path: Path,
) -> None:
    """Measured hours without a snapshot (the dropped leap day) move no inflow."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2024-01-01", periods=48, freq="h"))
    n.add("Bus", "PT0 0AC", carrier="AC")
    n.add("StorageUnit", "PT0 0AC hydro", bus="PT0 0AC", carrier="hydro", p_nom=100.0)
    n.add("StorageUnit", "PT0 0AC PHS", bus="PT0 0AC", carrier="PHS", p_nom=50.0)
    n.storage_units_t.inflow = pd.DataFrame({"PT0 0AC hydro": 100.0}, n.snapshots)
    measured = pd.DataFrame(
        {"country": "PT", "pumped_storage": 10.0, "pumping": 0.0},
        index=pd.date_range("2024-01-01", periods=72, freq="h").rename("snapshot"),
    )
    measured.to_csv(tmp_path / "generation_hourly_2024.csv")
    _move_inflow_to_reversible(n, {"custom_dir": tmp_path, "data_year": 2024}, "PT")

    inflow = n.storage_units_t.inflow.sum()
    assert inflow["PT0 0AC PHS"] == pytest.approx(48 * 10.0)
    assert inflow.sum() == pytest.approx(48 * 100.0)
