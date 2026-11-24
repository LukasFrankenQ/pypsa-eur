# SPDX-FileCopyrightText: 2026 Iegor Riepin
#
# SPDX-License-Identifier: MIT
"""
Validate a solved back-cast network against published actuals in one report.

Compares the solved 2024 network with Redeia actuals
(``data/custom/validation_2024_es.csv``) for the modeled Spanish scope
(peninsula + Balearics): generation per carrier, demand, installed wind/solar
capacity factors, pumped-hydro use, power-sector CO2 and average emission
intensity, cross-border flows, and prices. Writes one tidy CSV
(metric / model / actual / diff / pct_diff / unit / year / source) and a YAML
summary of the headline gates. The same report runs on the raw and the patched
variants, so the diff between reports attributes the patch set.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd
import pypsa
import yaml

logger = logging.getLogger(__name__)

SCOPE = "ES_mainland_baleares"
MAINLAND = "ES0"  # bus prefix of mainland Spain; the Balearic zone is ES1

# model carriers contributing to each Redeia generation metric
GEN_CARRIERS: dict[str, tuple[str, ...]] = {
    "gen_onwind": ("onwind", "offwind-ac", "offwind-dc", "offwind-float"),
    "gen_solar_pv": ("solar", "solar-hsat"),
    "gen_solar_thermal_csp": ("csp",),
    "gen_nuclear": ("nuclear",),
    "gen_hydro": ("hydro", "ror"),
    "gen_ccgt": ("CCGT",),
    "gen_chp": ("CHP",),
    "gen_coal": ("coal", "lignite"),
    "gen_oil_diesel_gt_st": ("oil", "OCGT"),
    "gen_biomass_waste": ("biomass", "waste"),
}
# Redeia metrics summed into the model's single biomass+waste bucket
BIOMASS_WASTE_ACTUALS = (
    "gen_other_renewables",
    "gen_renewable_waste",
    "gen_nonrenewable_waste",
)
# model metrics compared with a sum of Redeia metrics
ACTUAL_PARTS: dict[str, tuple[str, ...]] = {
    "gen_biomass_waste": BIOMASS_WASTE_ACTUALS,
    "gen_ccgt_plus_chp": ("gen_ccgt", "gen_chp"),
    "net_export_ma_ad": ("net_export_ma", "net_export_ad"),
}
# metrics compared with a national row: published only nationally, where the national
# value is that of the modeled scope (borders, pumped storage, coal, the OMIE price)
NATIONAL_METRICS = frozenset(
    {
        "gen_phs_turbining",
        "demand_phs_pumping",
        "co2_coal",
        "net_export_fr",
        "net_export_pt",
        "net_export_ma_ad",
        "net_export_total",
        "day_ahead_price_omie_avg",
    }
)
# metrics whose actual value is computed below from other metrics
DERIVED_METRICS = frozenset(
    {"load_shedding_share", "emission_intensity_kg_mwh", "cf_onwind", "cf_solar_pv"}
)
# capacity factor -> (model carriers, Redeia generation metric, national_capacities metric)
CF_METRICS: dict[str, tuple[tuple[str, ...], str, str]] = {
    "cf_onwind": (("onwind",), "gen_onwind", "onwind"),
    "cf_solar_pv": (("solar", "solar-hsat"), "gen_solar_pv", "solar PV"),
}
# model carriers behind each Redeia CO2 split; the rest is oil, gas turbines, waste
CO2_CARRIERS: dict[str, tuple[str, ...]] = {
    "co2_ccgt": ("CCGT",),
    "co2_chp": ("CHP",),
    "co2_coal": ("coal", "lignite"),
}
# national_capacities.csv scopes making up SCOPE
CAPACITY_SCOPES = ("ES_peninsular", "ES_baleares")

# (metric, relative tolerance) headline gates; net exports additionally checks the sign
GATES: dict[str, float] = {
    "gen_onwind": 0.15,
    "gen_solar_pv": 0.15,
    "gen_nuclear": 0.10,
    "gen_hydro": 0.20,
    "gen_ccgt_plus_chp": 0.25,
    "co2_power_sector_total": 0.20,
    "net_export_total": 0.50,
    "load_shedding_share": 1.0e-4,
}


def _es_generation_twh(n: pypsa.Network) -> dict[str, float]:
    """Return annual ES generation per Redeia metric (TWh) from generators and storage units."""
    w = n.snapshot_weightings["generators"]
    es_buses = n.buses.index[
        (n.buses["country"] == "ES") & (n.buses["carrier"] == "AC")
    ]
    out: dict[str, float] = {}

    gen = n.generators[n.generators["bus"].isin(es_buses)]
    gen_twh = (
        n.generators_t.p[gen.index.intersection(n.generators_t.p.columns)]
        .mul(w, axis=0)
        .sum()
        .groupby(gen["carrier"])
        .sum()
        / 1e6
    )
    su = n.storage_units[n.storage_units["bus"].isin(es_buses)]
    su_p = n.storage_units_t.p[su.index.intersection(n.storage_units_t.p.columns)]
    su_supply_twh = (
        su_p.where(su_p > 0.0, 0.0).mul(w, axis=0).sum().groupby(su["carrier"]).sum()
        / 1e6
    )
    su_withdraw_twh = (
        su_p.where(su_p < 0.0, 0.0).mul(w, axis=0).sum().groupby(su["carrier"]).sum()
        / 1e6
    )
    supply = gen_twh.add(su_supply_twh, fill_value=0.0)

    for metric, carriers in GEN_CARRIERS.items():
        out[metric] = float(supply.reindex(list(carriers)).fillna(0.0).sum())
    out["gen_ccgt_plus_chp"] = out["gen_ccgt"] + out["gen_chp"]
    out["gen_phs_turbining"] = float(su_supply_twh.get("PHS", 0.0))
    out["demand_phs_pumping"] = float(su_withdraw_twh.get("PHS", 0.0))
    out["load_shedding"] = float(gen_twh.get("load", 0.0))
    return out


def _es_co2_mt(n: pypsa.Network) -> dict[str, float]:
    """
    Power-sector CO2 of the Spanish fleet (Mt), total and per ``CO2_CARRIERS`` split.

    Thermal input x carrier intensity.
    """
    w = n.snapshot_weightings["generators"]
    co2 = n.carriers["co2_emissions"]
    gens = n.generators[
        n.generators["bus"].str.startswith("ES")
        & (n.generators["carrier"].map(co2).fillna(0.0) > 0.0)
    ]
    cols = gens.index.intersection(n.generators_t.p.columns)
    gen_mwh = n.generators_t.p[cols].mul(w, axis=0).sum()
    eff = gens.loc[cols, "efficiency"].replace(0.0, 1.0)
    carrier = gens.loc[cols, "carrier"]
    mt = (gen_mwh / eff * carrier.map(co2)).groupby(carrier).sum() / 1e6
    out = {"co2_power_sector_total": float(mt.sum())}
    for metric, carriers in CO2_CARRIERS.items():
        out[metric] = float(mt.reindex(list(carriers)).fillna(0.0).sum())
    return out


def _es_demand_twh(n: pypsa.Network) -> float:
    """Annual busbar demand of ES loads (TWh), excluding fixed boundary export loads."""
    w = n.snapshot_weightings["generators"]
    es_buses = n.buses.index[
        (n.buses["country"] == "ES") & (n.buses["carrier"] == "AC")
    ]
    loads = n.loads.index[
        n.loads["bus"].isin(es_buses) & (n.loads["carrier"] != "boundary")
    ].intersection(n.loads_t.p_set.columns)
    return float(n.loads_t.p_set[loads].mul(w, axis=0).sum().sum()) / 1e6


def _corridor_import(n: pypsa.Network, exporter: str, importer: str) -> pd.Series:
    """Hourly flow (MW) sent from ``exporter`` into ``importer`` over lines and links."""
    country = n.buses["country"]
    exp_buses, imp_buses = (
        country.index[country == exporter],
        country.index[country == importer],
    )
    total = pd.Series(0.0, index=n.snapshots)
    for static, flows in ((n.lines, n.lines_t), (n.links, n.links_t)):
        from_bus1 = static.index[
            static["bus0"].isin(imp_buses) & static["bus1"].isin(exp_buses)
        ]
        from_bus0 = static.index[
            static["bus0"].isin(exp_buses) & static["bus1"].isin(imp_buses)
        ]
        sent = pd.concat([flows.p1[from_bus1], flows.p0[from_bus0]], axis=1)
        total += sent.where(sent > 0.0, 0.0).sum(axis=1)
    return total


def _net_exports_twh(n: pypsa.Network) -> dict[str, float]:
    """Net ES exports (TWh, positive = export) to FR and PT plus fixed MA/AD boundary loads."""
    w = n.snapshot_weightings["generators"]
    out: dict[str, float] = {}
    for nb in ("FR", "PT"):
        imp = _corridor_import(n, exporter=nb, importer="ES")
        exp = _corridor_import(n, exporter="ES", importer=nb)
        out[f"net_export_{nb.lower()}"] = float(((exp - imp) * w).sum()) / 1e6
    es_buses = n.buses.index[n.buses["country"] == "ES"]
    b_loads = n.loads.index[
        n.loads["bus"].isin(es_buses) & (n.loads["carrier"] == "boundary")
    ]
    ts = b_loads.intersection(n.loads_t.p_set.columns)
    static = b_loads.difference(ts)
    out["net_export_ma_ad"] = (
        float(n.loads_t.p_set[ts].mul(w, axis=0).sum().sum())
        + float(n.loads.loc[static, "p_set"].sum()) * float(w.sum())
    ) / 1e6
    out["net_export_total"] = sum(out.values())
    return out


def mainland_buses(n: pypsa.Network) -> pd.Index:
    """AC buses of mainland Spain."""
    return n.buses.index[
        n.buses.index.str.startswith(MAINLAND) & (n.buses["carrier"] == "AC")
    ]


def es_hourly_price(n: pypsa.Network) -> tuple[pd.Series, pd.Series]:
    """
    Price of mainland Spain per snapshot (EUR/MWh) and the mainland load.

    The price is the load-weighted mean over the mainland buses. OMIE's Spanish
    price is that of the mainland market: generation in the Balearics is
    dispatched apart, outside the system of offers.

    References
    ----------
    - [Real Decreto 738/2015, BOE-A-2015-8646](https://www.boe.es/buscar/act.php?id=BOE-A-2015-8646)
    """
    loads = n.loads.index[
        n.loads["bus"].isin(mainland_buses(n)) & (n.loads["carrier"] != "boundary")
    ].intersection(n.loads_t.p_set.columns)
    per_bus = n.loads_t.p_set[loads].T.groupby(n.loads.loc[loads, "bus"]).sum().T
    prices = n.buses_t.marginal_price[per_bus.columns]
    load = per_bus.sum(axis=1)
    return (prices * per_bus).sum(axis=1) / load, load


def _es_mean_price(n: pypsa.Network) -> float:
    """Mean price of mainland Spain over the hours (EUR/MWh), as OMIE averages it."""
    price, _ = es_hourly_price(n)
    return float(price.mean())


def _year_hours(year: int) -> int:
    """Hours of the calendar year."""
    return pd.Timestamp(year, 12, 31).dayofyear * 24


def _wind_solar_cf(n: pypsa.Network, year: int) -> dict[str, float]:
    """
    Fleet capacity factors of Spanish wind and solar (dispatched, post-curtailment).

    Taken over the hours of the calendar year, as the actual ones: the model holds
    the year's energy although it drops the leap day.
    """
    w = n.snapshot_weightings["generators"]
    hours = _year_hours(year)
    out = {}
    for name, (carriers, _, _) in CF_METRICS.items():
        gens = n.generators[
            n.generators["bus"].str.startswith("ES")
            & n.generators["carrier"].isin(carriers)
        ]
        cols = gens.index.intersection(n.generators_t.p.columns)
        p_nom = gens.loc[cols, "p_nom"]
        gen_mwh = float(n.generators_t.p[cols].mul(w, axis=0).sum().sum())
        out[name] = gen_mwh / (float(p_nom.sum()) * hours) if p_nom.sum() > 0 else 0.0
    return out


def _actual_cf(
    gen_twh: float, capacities: pd.DataFrame, metric: str, year: int
) -> tuple[float, str]:
    """
    Realized capacity factor and its capacity source over the modeled scope.

    The denominator is the average operating capacity, the mean of the prior
    and current year-end fleets, as the patched network sets it.
    """
    sel = capacities[
        capacities["country"].isin(CAPACITY_SCOPES)
        & (capacities["carrier_or_metric"] == metric)
        & capacities["year"].isin([year - 1, year])
    ]
    mw = sel.groupby("year")["value"].sum().loc[[year - 1, year]].mean()
    hours = _year_hours(year)
    return gen_twh * 1e6 / (mw * hours), " + ".join(sel["source_id"].unique())


def validate(
    n: pypsa.Network, actuals: pd.DataFrame, capacities: pd.DataFrame
) -> pd.DataFrame:
    """Build the tidy validation table: model vs Redeia, with gates."""
    year = int(actuals["year"].unique().item())
    act = actuals.query("country == @SCOPE").set_index("carrier_or_metric")
    act_nat = actuals.query("country == 'ES'").set_index("carrier_or_metric")

    model: dict[str, float] = {}
    model.update(_es_generation_twh(n))
    model["demand_b_c"] = _es_demand_twh(n)
    model.update(_es_co2_mt(n))
    model["emission_intensity_kg_mwh"] = (
        model["co2_power_sector_total"] / model["demand_b_c"] * 1e3
    )
    model.update(_net_exports_twh(n))
    model["day_ahead_price_omie_avg"] = _es_mean_price(n)
    model.update(_wind_solar_cf(n, year))
    model["load_shedding_share"] = (
        model.pop("load_shedding") / model["demand_b_c"]
        if model["demand_b_c"] > 0
        else 0.0
    )

    actual: dict[str, float] = {}
    unit: dict[str, str] = {}
    source: dict[str, str] = {}
    for metric in model:
        if metric in DERIVED_METRICS:
            continue
        parts = list(ACTUAL_PARTS.get(metric, (metric,)))
        hit = (act_nat if metric in NATIONAL_METRICS else act).reindex(parts)
        if hit["value"].isna().any():
            raise ValueError(f"Missing actual rows for {metric}: {parts}.")
        actual[metric] = float(hit["value"].sum())
        unit[metric] = str(hit["unit"].iloc[0])
        source[metric] = " + ".join(hit["source_id"].dropna().unique())
    actual["load_shedding_share"] = 0.0
    unit["load_shedding_share"] = "p.u."
    source["load_shedding_share"] = ""
    actual["emission_intensity_kg_mwh"] = (
        actual["co2_power_sector_total"] / actual["demand_b_c"] * 1e3
    )
    unit["emission_intensity_kg_mwh"] = "kgCO2/MWh"
    source["emission_intensity_kg_mwh"] = " + ".join(
        dict.fromkeys([source["co2_power_sector_total"], source["demand_b_c"]])
    )
    for metric, (_, gen_metric, cap_metric) in CF_METRICS.items():
        actual[metric], cap_source = _actual_cf(
            actual[gen_metric], capacities, cap_metric, year
        )
        unit[metric] = "p.u."
        source[metric] = f"{source[gen_metric]} + {cap_source}"

    rows = []
    for metric, m_val in model.items():
        a_val = actual[metric]
        diff = m_val - a_val
        pct = diff / abs(a_val) * 100.0 if a_val and pd.notna(a_val) else float("nan")
        gate = GATES.get(metric)
        if gate is None:
            status = "INFO"
        elif metric == "load_shedding_share":
            status = "PASS" if m_val < gate else "FAIL"
        elif metric == "net_export_total":
            sign_ok = (m_val >= 0) == (a_val >= 0)
            status = "PASS" if sign_ok and abs(pct) <= gate * 100.0 else "FAIL"
        else:
            status = "PASS" if abs(pct) <= gate * 100.0 else "FAIL"
        rows.append(
            {
                "metric": metric,
                "model": round(m_val, 4),
                "actual": round(a_val, 4) if pd.notna(a_val) else a_val,
                "diff": round(diff, 4) if pd.notna(a_val) else float("nan"),
                "pct_diff": round(pct, 2) if pd.notna(pct) else pct,
                "unit": unit[metric],
                "year": year,
                "source": source[metric],
                "gate_rel_tol": gate if gate is not None else "",
                "status": status,
            }
        )
    return pd.DataFrame(rows)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument(
        "--actuals", type=Path, default=Path("data/custom/validation_2024_es.csv")
    )
    parser.add_argument(
        "--capacities",
        type=Path,
        default=Path("data/custom/national_capacities.csv"),
    )
    parser.add_argument("--output-report", type=Path, required=True)
    parser.add_argument("--output-gates", type=Path, required=True)
    args = parser.parse_args(argv)
    net_in, act_in, cap_in = args.network, args.actuals, args.capacities
    report_out, gates_out = args.output_report, args.output_gates

    n = pypsa.Network(str(net_in))
    report = validate(n, pd.read_csv(act_in), pd.read_csv(cap_in))

    report_out.parent.mkdir(parents=True, exist_ok=True)
    report.drop(columns=["gate_rel_tol", "status"]).to_csv(report_out, index=False)

    gated = report[report["status"].isin(["PASS", "FAIL"])]
    summary = {
        "network": str(net_in),
        "gates_passed": int((gated["status"] == "PASS").sum()),
        "gates_total": int(len(gated)),
        "failed": {
            r[
                "metric"
            ]: f"model {r['model']} vs actual {r['actual']} ({r['pct_diff']:+.1f}%)"
            for _, r in gated[gated["status"] == "FAIL"].iterrows()
        },
    }
    gates_out.write_text(yaml.safe_dump(summary, sort_keys=False))
    logger.info(
        "Validation: %d/%d gates pass -> %s",
        summary["gates_passed"],
        summary["gates_total"],
        report_out,
    )
    logger.info("\n%s", report.to_string(index=False))


if __name__ == "__main__":
    main()
