# SPDX-FileCopyrightText: 2026 Iegor Riepin
#
# SPDX-License-Identifier: MIT
"""
Prepare a base network: apply cited patches to the composed PyPSA-Eur network.

Patch engine of the back-cast. Reads the composed network, applies an ordered list of
cited corrections controlled by ``backcast.patches`` (each individually switchable;
the ``raw`` variant keeps only the France boundary closure), freezes the fleet, and
writes the base network that ``solve_network.py`` consumes. The philosophy is: build
fresh, validate, correct only what the validation report demands, cite every
correction (inputs in ``data/custom``, sources in ``doc/data_sources.md``).

Weather, fleet, demand and prices are all of 2024, the year given by ``--data-year``.
The evidence and numbers behind each patch are in ``doc/validation_log.md``, sections 3
(the patch table) and 7 (the steps).

Patches
-------
- ``boundary_flows``: France's external borders (GB, IT, CH, DE+BE combined) fixed
  to measured hourly 2024 flows (RTE eCO2mix), attached as fixed generators (imports)
  and loads (exports) at the nearest French node. Model closure, not calibration: on
  in every variant.
- ``national_capacities``: set national fleet totals per carrier to the published
  actuals in ``national_capacities.csv``, pro rata over the existing nodal
  distribution, the turbine capacities of reservoirs and pumped storage, and the
  energy capacity of Portuguese and French reservoirs. Wind and
  solar use the average operating capacity (mean of year-end 2023 and 2024) where a
  year-end 2023 row exists, and their output grows month by month with the fleet;
  Portuguese wind and Spanish offshore wind have none.
- ``wind_correction``: residual ERA5 wind-bias factor for ES, PT and FR, derived at
  run time so that the capped profiles hold the cited generation (in Spain, before
  curtailment).
- ``solar_tracking``: a share of mainland Spain's solar fleet moves to the
  single-axis tracker profile; one factor holds the cited solar PV energy before
  curtailment.
- ``phs_split``: pure pumped-storage energy, pumping capacity and round-trip
  efficiency per the cited sources; mixed pumped storage stays inside reservoir
  hydro per Redeia's convention (its capacity enters via ``national_capacities``).
- ``pumped_storage_limits``: French and Portuguese pumped storage pumps at its cited
  pumping capacity (upstream: at full turbine capacity); Portuguese storage energy
  from the plants' reported capacity.
- ``chp_profile``: cogeneration as a fixed profile: mainland Spain and the Balearics
  at Redeia's output with the monthly shape of its cogeneration CO2 series, Portugal
  at REN's monthly output.
- ``csp``: concentrated solar power, capacity from ``national_capacities.csv``, with
  the nodal solar profile rescaled to the published annual CSP output.
- ``biomass_availability``: biomass and waste capped at their realized capacity
  factor (they are fuel-limited).
- ``prices``: thermal marginal costs recomputed as VOM + fuel/eta + ETS x intensity/eta
  from ``prices.csv`` (fuel and CO2 prices) and the processed technology-data costs
  (VOM), plus the Portuguese gas network premium. The composed network carries no CO2
  price at all.
- ``daily_fuel_prices``: thermal marginal costs follow the daily gas price at the
  Spanish hub and the daily EU allowance price (``daily_fuel_prices_<year>.csv``).
- ``biomass_waste_fixed``: Spanish and Portuguese biomass and waste plants run at a
  fixed output, their realized capacity factor; they burn the fuel they have.
- ``demand``: scale the load of each zone (mainland Spain, Balearics, Portugal,
  France) to its cited annual demand.
- ``hydro_inflow``: scale ES/PT/FR reservoir inflow to the observed 2024 natural hydro
  energy (the upstream EIA normalization data ends before 2024) and spread it over the
  months as the water balance of the reservoirs shows (measured output plus the change
  in stored energy); Portuguese reversible plants receive the natural water they
  turbined.
- ``water_value``: reservoir water in Spain, Portugal and France is valued at OMIP's
  baseload futures price (day or week contract), public before delivery; releases
  cost it and spilled water forgoes it.
- ``reservoir_floor``: a share of Spanish reservoir hydro runs as must-run, the 10th
  percentile of its measured hourly output per month (minimum flows, cascades).
- ``es_hydro_availability``: Spanish reservoir plants are limited so that all hydro
  output stays under the highest output the fleet has delivered (2019-2025).
- ``fr_nuclear_profile``: seasonalize French nuclear availability with the measured
  2024 monthly output shape (eCO2mix), preserving the upstream annual availability
  factor.
- ``es_unit_outages``: mainland nuclear, gas and coal plants follow the outages they
  declared to OMIE in the data year, hour by hour; nuclear keeps 60% of its available
  capacity (the depth of market-driven load reductions, CSN).
- ``security_gas_floor``: mainland gas plants keep the minimum output the system
  operator orders for grid security.
- ``coal_floor``: mainland coal keeps the minimum output of Aboño 2 running, the
  unit that burns the steelworks gases of ArcelorMittal and supplies its 220 kV zone.
- ``start_up_costs``: gas, coal and Spanish nuclear plants carry the start-up cost
  per MW of the upstream unit-commitment table; the solve charges it in linear form,
  counting each MW of added output as a MW started.
- ``ma_ad_exports``: fixed ES exports to Morocco and Andorra as flat loads at the
  nearest Spanish nodes.
- ``bidding_zones``: merge the nodes into the bidding zones the day-ahead market
  clears (mainland Spain, Balearics, Portugal, France), linked by the directional
  NTCs of ``ntc.csv``, with one storage unit per zone and carrier; applied after the
  data patches, which work on the nodes.
- ``drop_empty_generators``: remove generators below 1 MW (resource classes and
  carriers without a fleet), which only add size to the problem.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pandas as pd
import pypsa
from scipy.optimize import brentq

logger = logging.getLogger(__name__)

# Border coordinates used to pick the nearest node for fixed border flows.
FR_NEIGHBOUR_COORDS: dict[str, tuple[float, float]] = {
    "DE_BE": (5.90, 49.90),
    "CH": (6.80, 46.80),
    "IT": (7.00, 45.10),
    "GB": (0.50, 50.80),
}
ES_BORDER_COORDS: dict[str, tuple[float, float]] = {
    "MA": (-5.60, 36.00),
    "AD": (1.52, 42.51),
}

# national_capacities.csv carrier_or_metric -> network generator carriers
NATCAP_GEN_MAP: dict[str, tuple[str, ...]] = {
    "onwind": ("onwind",),
    "offwind": ("offwind-ac", "offwind-dc", "offwind-float"),
    "solar PV": ("solar", "solar-hsat"),
    "nuclear": ("nuclear",),
    "CCGT": ("CCGT",),
    "OCGT/gas turbines": ("OCGT",),
    "coal": ("coal",),
    "lignite": ("lignite",),
    "oil/diesel/fuel": ("oil",),
    "biomass/waste": ("biomass", "waste"),
    "run-of-river/small hydro": ("ror",),
}
# national_capacities.csv scope -> metric -> storage-unit carrier. Redeia books mixed
# pumped storage under reservoir hydro; REN and RTE report all pumped storage together.
NATCAP_SU_MAP: dict[str, dict[str, str]] = {
    "ES_peninsular": {
        "reservoir hydro (incl. mixed PHS)": "hydro",
        "pumped hydro (pure)": "PHS",
    },
    "PT": {"reservoir hydro": "hydro", "pumped hydro (total pure+mixed)": "PHS"},
    "FR": {"reservoir hydro": "hydro", "pumped hydro (total pure+mixed)": "PHS"},
}
# scopes whose storage keeps its energy capacity when the turbine capacity is set
STORAGE_ENERGY_KEPT: tuple[str, ...] = ("PT", "FR")
# national_capacities.csv metric for the energy capacity of reservoir hydro (GWh)
RESERVOIR_ENERGY_METRIC = "reservoir hydro energy"
# scopes whose reversible plants stand on river reservoirs and also turbine natural inflow
REVERSIBLE_INFLOW_SCOPES: tuple[str, ...] = ("PT",)
# scopes in national_capacities.csv -> bus prefix in the 15-node network
NATCAP_SCOPES: dict[str, str] = {
    "ES_peninsular": "ES0",
    "ES_baleares": "ES1",
    "PT": "PT",
    "FR": "FR",
}
# annual_demand.csv scope -> (bus prefix, demand metric)
DEMAND_SCOPES: dict[str, tuple[str, str]] = {
    "ES_peninsular": ("ES0", "electricity_demand_busbar"),
    "ES_baleares": ("ES1", "electricity_demand_busbar"),
    "PT": ("PT", "electricity_demand_busbar"),
    "FR": ("FR", "electricity_demand_gross"),
}
# Spanish scopes of the cited tables -> bus prefix of the zone
ES_ZONE_SCOPES: dict[str, str] = {"ES_peninsular": "ES0", "ES_baleares": "ES1"}
# carriers whose 2024 validation capacity is the mean of year-end 2023 and 2024
AVERAGE_OPERATING_CARRIERS: frozenset[str] = frozenset(
    {"onwind", "offwind", "solar PV"}
)

# generator carrier -> fuel price metric family in prices.csv
FUEL_METRIC: dict[str, str] = {
    "CCGT": "gas",
    "OCGT": "gas",
    "coal": "coal",
    "lignite": "lignite",
    "oil": "oil",
    "nuclear": "nuclear",
    "biomass": "biomass",
}
# upper end of the search for a profile correction factor
MAX_PROFILE_FACTOR = 10.0
# time zone of the Iberian market's days and weeks; network snapshots are in UTC
MARKET_TZ = "Europe/Madrid"

# carriers that take a start-up cost from the upstream unit-commitment table
START_UP_CARRIERS: tuple[str, ...] = ("CCGT", "OCGT", "coal")

# patch -> the patch it builds on
PATCH_REQUIRES: dict[str, str] = {
    "daily_fuel_prices": "prices",
    "biomass_waste_fixed": "biomass_availability",
    "coal_floor": "national_capacities",
    "es_unit_outages": "national_capacities",
}


def _ensure_carrier(n: pypsa.Network, carrier: str, co2: float = 0.0) -> None:
    """Add ``carrier`` to ``n.carriers`` if missing."""
    if carrier not in n.carriers.index:
        n.add("Carrier", carrier, co2_emissions=co2)


def _custom(ctx: Mapping[str, Any], name: str) -> pd.DataFrame:
    """Read a CSV from ``data/custom``."""
    return pd.read_csv(Path(ctx["custom_dir"]) / name)


def _value(df: pd.DataFrame, country: str, year: int, metric: str) -> float | None:
    """Return the value of one (country, year, carrier_or_metric) row, or None."""
    sel = df.query(
        "country == @country and year == @year and carrier_or_metric == @metric"
    )
    return float(sel["value"].iloc[0]) if len(sel) else None


def _cited(df: pd.DataFrame, country: str, year: int, metric: str) -> float:
    """Return a cited value; raise if the row is missing."""
    value = _value(df, country, year, metric)
    if value is None:
        raise ValueError(f"Missing {metric} row for {country}/{year}.")
    return value


def _country_buses(n: pypsa.Network, prefix: str) -> pd.Index:
    """AC buses whose name starts with ``prefix``."""
    return n.buses.index[
        n.buses.index.str.startswith(prefix) & (n.buses["carrier"] == "AC")
    ]


def _nearest_bus(n: pypsa.Network, buses: pd.Index, lonlat: tuple[float, float]) -> str:
    """Bus in ``buses`` nearest to ``lonlat``."""
    xy = n.buses.loc[buses, ["x", "y"]]
    return str(((xy["x"] - lonlat[0]) ** 2 + (xy["y"] - lonlat[1]) ** 2).idxmin())


def _local_snapshots(n: pypsa.Network) -> pd.DatetimeIndex:
    """Network snapshots (UTC) in the market's local time."""
    return n.snapshots.tz_localize("UTC").tz_convert(MARKET_TZ)


def _set_dynamic(
    dynamic: dict[str, pd.DataFrame], attr: str, values: pd.DataFrame
) -> None:
    """Replace the columns of ``values`` in time series ``attr``, appending new ones."""
    dynamic[attr] = pd.concat(
        [dynamic[attr].drop(columns=values.columns, errors="ignore"), values], axis=1
    )


def attach_boundary_flows(
    n: pypsa.Network,
    boundary_flows: pd.DataFrame | str | Path,
) -> None:
    """
    Attach fixed hourly external border flows at the nearest French nodes.

    Parameters
    ----------
    n : pypsa.Network
        Network to modify in-place.
    boundary_flows : pd.DataFrame | str | Path
        Measured flows with columns ``['snapshot', 'neighbour', 'flow_mw_into_FR']``
        (positive = import into France), e.g. ``data/custom/fr_boundary_flows_2024.csv``.
    """
    if isinstance(boundary_flows, (str, Path)):
        df = pd.read_csv(boundary_flows)
    else:
        df = boundary_flows.copy()

    required = {"snapshot", "neighbour", "flow_mw_into_FR"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Boundary flows table is missing columns: {sorted(missing)}")

    _ensure_carrier(n, "boundary")
    fr_buses = _country_buses(n, "FR")
    if fr_buses.empty:
        raise ValueError("No French AC buses found in network.")

    df["snapshot"] = pd.to_datetime(df["snapshot"]).dt.tz_localize(None)

    for nb, grp in df.groupby("neighbour", sort=False):
        nb_str = str(nb).strip().upper()
        bus = _nearest_bus(n, fr_buses, FR_NEIGHBOUR_COORDS[nb_str])

        hourly = grp.set_index("snapshot")["flow_mw_into_FR"].astype(float)
        flow = hourly.reindex(n.snapshots)
        if flow.isna().any():
            raise ValueError(
                f"Boundary flows for {nb_str} do not cover all network snapshots."
            )
        import_mw = flow.where(flow > 0.0, 0.0)
        export_mw = (-flow).where(flow < 0.0, 0.0)

        max_imp = float(import_mw.max())
        if max_imp > 0.0:
            name = f"{bus} boundary import {nb_str}"
            imp_pu = import_mw / max_imp
            n.add(
                "Generator",
                name,
                bus=bus,
                carrier="boundary",
                p_nom=max_imp,
                p_nom_extendable=False,
                p_min_pu=imp_pu,
                p_max_pu=imp_pu,
                marginal_cost=0.0,
            )

        if float(export_mw.max()) > 0.0:
            name = f"{bus} boundary export {nb_str}"
            n.add("Load", name, bus=bus, carrier="boundary", p_set=0.0)
            n.loads_t.p_set[name] = export_mw

        logger.info(
            "Attached FR boundary flow %s at '%s': import %.2f TWh, export %.2f TWh.",
            nb_str,
            bus,
            float((import_mw * n.snapshot_weightings["generators"]).sum()) / 1e6,
            float((export_mw * n.snapshot_weightings["generators"]).sum()) / 1e6,
        )


def freeze_fleet(n: pypsa.Network) -> None:
    """Set every asset non-extendable: the model solves dispatch only."""
    for comp, attr in (
        ("generators", "p_nom_extendable"),
        ("storage_units", "p_nom_extendable"),
        ("stores", "e_nom_extendable"),
        ("links", "p_nom_extendable"),
        ("lines", "s_nom_extendable"),
    ):
        df = getattr(n, comp)
        if not df.empty and df[attr].any():
            logger.warning(
                "Freezing %d extendable %s from the composed network.",
                int(df[attr].sum()),
                comp,
            )
            df[attr] = False


def _patch_national_capacities(n: pypsa.Network, ctx: Mapping[str, Any]) -> None:
    """
    Set national fleet totals per carrier from national_capacities.csv, pro rata.

    Storage takes its turbine capacities and, where a row gives it, the energy
    capacity of reservoir hydro.
    """
    df = _custom(ctx, "national_capacities.csv")
    year = int(ctx["data_year"])
    for scope, prefix in NATCAP_SCOPES.items():
        buses = set(_country_buses(n, prefix))
        for metric, carriers in NATCAP_GEN_MAP.items():
            target = _value(df, scope, year, metric)
            if target is None:
                continue
            ramp = None
            if metric in AVERAGE_OPERATING_CARRIERS:
                prev = _value(df, scope, year - 1, metric)
                if prev is None:
                    logger.info(
                        "%s %s: no year-end %d row; year-end %d used.",
                        scope,
                        metric,
                        year - 1,
                        year,
                    )
                else:
                    ramp = (prev, target)
                    target = (target + prev) / 2.0
            sel = n.generators.index[
                n.generators["carrier"].isin(carriers) & n.generators["bus"].isin(buses)
            ]
            existing = float(n.generators.loc[sel, "p_nom"].sum())
            if existing > 0.0:
                n.generators.loc[sel, "p_nom"] *= target / existing
                n.generators.loc[sel, "p_nom_min"] = 0.0
                if ramp is not None:
                    _ramp_profiles(n, sel, *ramp)
            elif target > 0.0 and carriers[0] not in FUEL_METRIC:
                logger.info(
                    "%s %s: no existing units to take a weather profile from; "
                    "%.0f MW left out.",
                    scope,
                    metric,
                    target,
                )
                continue
            elif target > 0.0:
                bus = _load_shares(n, prefix).idxmax()
                costs = pd.read_csv(ctx["costs"], index_col=0)
                eta = float(costs.at[carriers[0], "efficiency"])
                co2 = float(costs.at[carriers[0], "CO2 intensity"])
                _ensure_carrier(n, carriers[0], co2=co2)
                n.add(
                    "Generator",
                    f"{bus} {carriers[0]}",
                    bus=bus,
                    carrier=carriers[0],
                    p_nom=target,
                    p_nom_extendable=False,
                    efficiency=eta,
                )
                logger.info(
                    "Created %.0f MW %s at %s (no existing units; efficiency %.3f).",
                    target,
                    carriers[0],
                    bus,
                    eta,
                )
            logger.info("%s %s: %.0f -> %.0f MW", scope, metric, existing, target)
        for metric, su_carrier in NATCAP_SU_MAP.get(scope, {}).items():
            target = _value(df, scope, year, metric)
            if target is None:
                continue
            sel = n.storage_units.index[
                (n.storage_units["carrier"] == su_carrier)
                & n.storage_units["bus"].isin(buses)
            ]
            existing = float(n.storage_units.loc[sel, "p_nom"].sum())
            if existing > 0.0:
                n.storage_units.loc[sel, "p_nom"] *= target / existing
                if scope in STORAGE_ENERGY_KEPT:
                    # the reservoir does not change with its turbines
                    n.storage_units.loc[sel, "max_hours"] *= existing / target
                logger.info("%s %s: %.0f -> %.0f MW", scope, metric, existing, target)
        energy_gwh = _value(df, scope, year, RESERVOIR_ENERGY_METRIC)
        if energy_gwh is not None:
            sel = n.storage_units.index[
                (n.storage_units["carrier"] == "hydro")
                & n.storage_units["bus"].isin(buses)
            ]
            p_nom = float(n.storage_units.loc[sel, "p_nom"].sum())
            n.storage_units.loc[sel, "max_hours"] = energy_gwh * 1e3 / p_nom
            logger.info("%s reservoir energy: %.0f GWh.", scope, energy_gwh)


def _ramp_profiles(
    n: pypsa.Network, gens: pd.Index, start_mw: float, end_mw: float
) -> None:
    """
    Let a fleet's output grow with its capacity through the year, month by month.

    The fleet holds its average operating capacity; its profiles are scaled each month
    by the capacity in operation at mid-month (linear between the year-end figures)
    over that average, capped at 1.0.
    """
    months = pd.Series(n.snapshots.month, index=n.snapshots)
    in_operation = start_mw + (end_mw - start_mw) * (months - 0.5) / 12.0
    factor = in_operation / ((start_mw + end_mw) / 2.0)
    cols = gens.intersection(n.generators_t.p_max_pu.columns)
    n.generators_t.p_max_pu[cols] = (
        n.generators_t.p_max_pu[cols].mul(factor, axis=0).clip(upper=1.0)
    )


def _load_shares(n: pypsa.Network, prefix: str) -> pd.Series:
    """Share of each bus of a country in its mean non-boundary load."""
    loads = n.loads.index[
        n.loads["bus"].isin(set(_country_buses(n, prefix)))
        & (n.loads["carrier"] != "boundary")
    ].intersection(n.loads_t.p_set.columns)
    per_bus = n.loads_t.p_set[loads].mean().groupby(n.loads["bus"]).sum()
    return per_bus / per_bus.sum()


def _scale_to_energy(
    profile: pd.DataFrame, p_nom: pd.Series, weights: pd.Series, target_twh: float
) -> float:
    """Return the factor at which ``profile``, capped at 1.0, holds ``target_twh``."""
    return brentq(
        lambda f: _capped_energy_twh(profile, p_nom, weights, f) - target_twh,
        0.0,
        MAX_PROFILE_FACTOR,
    )


def _capped_energy_twh(
    profile: pd.DataFrame, p_nom: pd.Series, weights: pd.Series, factor: float = 1.0
) -> float:
    """Return the energy (TWh) of ``profile`` scaled by ``factor`` and capped at 1.0."""
    capped = (profile * factor).clip(upper=1.0)
    return float(capped.mul(p_nom, axis=1).mul(weights, axis=0).sum().sum()) / 1e6


def _fit_profiles(
    n: pypsa.Network, gens: pd.Index, target_twh: float
) -> tuple[float, float, float]:
    """
    Scale the profiles of ``gens`` so that, capped at 1.0, they hold ``target_twh``.

    Returns the factor and the available energy (TWh) before and after scaling.
    """
    profile = n.generators_t.p_max_pu[gens]
    p_nom = n.generators.loc[gens, "p_nom"]
    w = n.snapshot_weightings["generators"]
    factor = _scale_to_energy(profile, p_nom, w, target_twh)
    n.generators_t.p_max_pu[gens] = (profile * factor).clip(upper=1.0)
    before = _capped_energy_twh(profile, p_nom, w)
    return factor, before, _capped_energy_twh(profile, p_nom, w, factor)


def _es_output_share(ctx: Mapping[str, Any]) -> float:
    """
    Return the share of mainland Spain's available wind and solar energy that was produced.

    Published output is after curtailment: offers not matched in the day-ahead market
    (a share of the energy offered) and energy the system operator scheduled down for
    technical constraints. Available = (output + technical) / (1 - economic share).
    """
    df = _custom(ctx, "validation_2024_es.csv")
    year = int(ctx["data_year"])
    economic = _cited(df, "ES_peninsular", year, "curtailment_economic_share")
    technical = _cited(df, "ES_peninsular", year, "curtailment_technical")
    output = _cited(df, "ES_peninsular", year, "gen_onwind") + _cited(
        df, "ES_peninsular", year, "gen_solar_pv"
    )
    share = output / ((output + technical) / (1.0 - economic))
    logger.info(
        "ES wind and solar: %.1f%% of the available energy curtailed.",
        100 * (1 - share),
    )
    return share


def _patch_wind_correction(n: pypsa.Network, ctx: Mapping[str, Any]) -> None:
    """
    Scale onshore wind profiles for the residual ERA5 wind-speed bias.

    Applied after ``national_capacities`` and the resource-class spatial treatment:
    the remaining gap is the ERA5 wind-speed bias (plus poor GEM plant coordinates
    in Portugal). One multiplicative factor per country; profiles are capped at 1.0.
    This is a simple correction: it lifts every hour alike and flattens the windiest
    hours at full output. Correcting the distribution of wind speeds (a quantile
    correction) would keep the hourly shape better; one factor is kept for
    transparency.

    The factor is derived at run time: the factor at which the capped profiles
    hold the cited generation of the year, in Spain the energy available before
    curtailment (``_es_output_share``).
    """
    year = int(ctx["data_year"])
    targets = {
        "ES": _cited(
            _custom(ctx, "validation_2024_es.csv"),
            "ES_mainland_baleares",
            year,
            "gen_onwind",
        )
        / _es_output_share(ctx),
        "PT": _cited(_custom(ctx, "pt_actuals.csv"), "PT", year, "gen_onwind"),
        "FR": _cited(_custom(ctx, "fr_actuals.csv"), "FR", year, "gen_onwind"),
    }
    for prefix, target_twh in targets.items():
        gens = n.generators.index[
            (n.generators["carrier"] == "onwind")
            & n.generators["bus"].isin(set(_country_buses(n, prefix)))
        ].intersection(n.generators_t.p_max_pu.columns)
        factor, before, after = _fit_profiles(n, gens, target_twh)
        logger.info(
            "%s onwind profiles scaled by %.3f (%.1f TWh cited): "
            "%.1f -> %.1f TWh available.",
            prefix,
            factor,
            target_twh,
            before,
            after,
        )


def _patch_solar_tracking(n: pypsa.Network, ctx: Mapping[str, Any]) -> None:
    """
    Put a share of mainland Spain's solar fleet on single-axis trackers.

    Upstream holds the whole fleet as fixed panels at 35 degrees of tilt. Most
    large Spanish plants built since 2019 stand on horizontal single-axis
    trackers, and the measured output of the fleet shows it: a flat top at midday
    and wide shoulders. A tracker lies flat at noon, so it yields less than a
    tilted panel under the low winter sun and more in the mornings and evenings
    of summer.

    The share in ``solar_tracking.csv`` moves from the fixed panels to the
    tracker profile that PyPSA-Eur builds with atlite (``solar-hsat``), node by
    node and resource class by resource class. Trackers yield more per MW, so one
    factor then scales both profiles to the solar PV energy available in the year
    (cited output before curtailment), as ``wind_correction`` does for wind;
    profiles are capped at 1.0.
    """
    year = int(ctx["data_year"])
    share = _cited(
        _custom(ctx, "solar_tracking.csv"),
        "ES_peninsular",
        year,
        "solar_tracking_share",
    )
    target_twh = _cited(
        _custom(ctx, "validation_2024_es.csv"), "ES_peninsular", year, "gen_solar_pv"
    ) / _es_output_share(ctx)

    spanish = n.generators["bus"].isin(_country_buses(n, "ES0"))
    fixed = n.generators.index[(n.generators["carrier"] == "solar") & spanish]
    trackers = fixed + "-hsat"
    n.generators.loc[trackers, "p_nom"] = (
        share * n.generators.loc[fixed, "p_nom"].to_numpy()
    )
    n.generators.loc[fixed, "p_nom"] *= 1.0 - share

    plants = fixed.union(trackers)
    factor, before, after = _fit_profiles(n, plants, target_twh)
    logger.info(
        "ES solar: %.0f%% of %.0f MW on trackers; profiles scaled by %.3f: "
        "%.1f -> %.1f TWh available.",
        100 * share,
        n.generators.loc[plants, "p_nom"].sum(),
        factor,
        before,
        after,
    )


def _patch_phs_split(n: pypsa.Network, ctx: Mapping[str, Any]) -> None:
    """Fix Spanish pure-PHS energy, pumping ratio and round-trip efficiency from cited data."""
    df = _custom(ctx, "national_capacities.csv")
    year = int(ctx["data_year"])
    scope = "ES_peninsular"  # all Spanish pumped storage is on the mainland
    energy_gwh = _cited(df, scope, year, "pumped hydro pure energy")
    pumping_mw = _cited(df, scope, year, "pumped hydro (pure) pumping")
    rt_eff = _cited(df, scope, year, "pumped hydro round-trip efficiency")

    sel = n.storage_units.index[
        (n.storage_units["carrier"] == "PHS")
        & n.storage_units["bus"].isin(set(_country_buses(n, "ES")))
    ]
    p_nom = float(n.storage_units.loc[sel, "p_nom"].sum())
    n.storage_units.loc[sel, "max_hours"] = energy_gwh * 1e3 / p_nom
    n.storage_units.loc[sel, "p_min_pu"] = -pumping_mw / p_nom
    one_way = rt_eff**0.5
    n.storage_units.loc[sel, "efficiency_store"] = one_way
    n.storage_units.loc[sel, "efficiency_dispatch"] = one_way
    logger.info(
        "ES pure PHS: %.0f MW, %.1f GWh (%.1f h), pumping %.0f MW, round-trip %.2f.",
        p_nom,
        energy_gwh,
        energy_gwh * 1e3 / p_nom,
        pumping_mw,
        rt_eff,
    )


# national_capacities.csv scopes (= bus prefixes) with cited pumped-storage limits
PUMPED_STORAGE_SCOPES: tuple[str, ...] = ("FR", "PT")


def _patch_pumped_storage_limits(n: pypsa.Network, ctx: Mapping[str, Any]) -> None:
    """
    Limit pumped storage to its cited pumping capacity and, where cited, energy.

    The upstream units pump at their full turbine capacity, and the Portuguese
    ones hold far more energy than the plants report.
    """
    df = _custom(ctx, "national_capacities.csv")
    year = int(ctx["data_year"])
    for scope in PUMPED_STORAGE_SCOPES:
        pumping_mw = _cited(df, scope, year, "pumped hydro pumping")
        sel = n.storage_units.index[
            (n.storage_units["carrier"] == "PHS")
            & n.storage_units["bus"].isin(_country_buses(n, scope))
        ]
        p_nom = float(n.storage_units.loc[sel, "p_nom"].sum())
        n.storage_units.loc[sel, "p_min_pu"] = -pumping_mw / p_nom
        logger.info("%s pumping limited to %.0f of %.0f MW.", scope, pumping_mw, p_nom)
        energy_gwh = _value(df, scope, year, "pumped hydro storage energy")
        if energy_gwh is None:
            logger.info("%s pumped-storage energy kept from PyPSA-Eur.", scope)
        else:
            n.storage_units.loc[sel, "max_hours"] = energy_gwh * 1e3 / p_nom
            logger.info("%s pumped-storage energy set to %.0f GWh.", scope, energy_gwh)


def _monthly_chp_shape(ctx: Mapping[str, Any]) -> dict[int, float]:
    """Monthly CHP shape (mean 1.0) from the vendored Redeia monthly cogeneration CO2 series."""
    path = (
        Path(ctx["custom_dir"]) / "sources" / "redata_2024_emisiones_co2_mensual.json"
    )
    data = json.loads(path.read_text())
    series = next(
        item
        for item in data["included"]
        if "ogenera" in item.get("attributes", {}).get("title", "")
    )["attributes"]["values"]
    total = sum(v["value"] for v in series)
    return {
        pd.Timestamp(v["datetime"].split("T")[0]).month: v["value"]
        / (total / len(series))
        for v in series
    }


def _attach_chp(
    n: pypsa.Network, prefix: str, cap_mw: float, profile: pd.Series
) -> None:
    """Attach cogeneration with a fixed output profile at the load buses of a country."""
    if float(profile.max()) >= 1.0:
        raise ValueError(
            "CHP profile exceeds 1.0; check capacity and generation inputs."
        )
    for bus, share in _load_shares(n, prefix).items():
        name = f"{bus} CHP"
        # fixed dispatch: marginal cost is an objective constant, so it is left at 0
        n.add(
            "Generator",
            name,
            bus=bus,
            carrier="CHP",
            p_nom=cap_mw * share,
            p_nom_extendable=False,
            efficiency=1.0,
            marginal_cost=0.0,
        )
        n.generators_t.p_min_pu[name] = profile
        n.generators_t.p_max_pu[name] = profile


def _patch_chp_profile(n: pypsa.Network, ctx: Mapping[str, Any]) -> None:
    """
    Cogeneration as a fixed, seasonally shaped profile.

    Spain: Redeia's annual output of the mainland and of the Balearics, each with
    the monthly shape of the national cogeneration emissions. Portugal: the
    monthly output of gas cogeneration in REN's balance;
    upstream holds no Portuguese cogeneration. Cogeneration follows the heat demand
    of its industrial host, not the electricity price.
    """
    natcap = _custom(ctx, "national_capacities.csv")
    val = _custom(ctx, "validation_2024_es.csv")
    year = int(ctx["data_year"])
    co2_mt = _cited(val, "ES", year, "co2_chp")
    national_twh = _cited(val, "ES", year, "gen_chp")
    em_factor = co2_mt / national_twh  # realized intensity, tCO2/MWh_el
    _ensure_carrier(n, "CHP", co2=em_factor)

    w = n.snapshot_weightings["generators"]
    monthly = _monthly_chp_shape(ctx)
    shape = pd.Series([monthly[ts.month] for ts in n.snapshots], index=n.snapshots)
    for scope, prefix in ES_ZONE_SCOPES.items():
        cap_mw = _cited(natcap, scope, year, "cogeneration")
        gen_twh = _cited(val, scope, year, "gen_chp")
        profile = shape * (gen_twh * 1e6 / cap_mw / float((shape * w).sum()))
        _attach_chp(n, prefix, cap_mw, profile)
        logger.info(
            "%s: attached %.0f MW CHP, fixed profile, %.2f TWh/yr, %.3f tCO2/MWh_el.",
            scope,
            cap_mw,
            gen_twh,
            em_factor,
        )

    pt_monthly = _custom(ctx, "pt_cogeneration_monthly_2024.csv").query("year == @year")
    pt_cap_mw = _cited(natcap, "PT", year, "cogeneration")
    if pt_monthly.empty:
        raise ValueError(f"Missing Portuguese cogeneration rows for {year}.")
    pt_gwh = pt_monthly.set_index("month")["value"]
    hours = w.groupby(n.snapshots.month).sum()
    pt_profile = pd.Series(
        n.snapshots.month.map(pt_gwh * 1e3 / hours / pt_cap_mw), index=n.snapshots
    )
    _attach_chp(n, "PT", pt_cap_mw, pt_profile)
    logger.info(
        "Attached %.0f MW Portuguese CHP, fixed profile, %.2f TWh/yr.",
        pt_cap_mw,
        float((pt_profile * w).sum()) * pt_cap_mw / 1e6,
    )


def _patch_csp(n: pypsa.Network, ctx: Mapping[str, Any]) -> None:
    """
    Concentrated solar power: cited capacity, solar profile rescaled to cited output.

    The profile is the first solar resource class at each bus; only its shape matters
    after rescaling.
    """
    natcap = _custom(ctx, "national_capacities.csv")
    val = _custom(ctx, "validation_2024_es.csv")
    year = int(ctx["data_year"])
    scope = "ES_peninsular"  # all Spanish solar thermal is on the mainland
    cap_mw = _cited(natcap, scope, year, "CSP/solar thermal")
    target_twh = _cited(val, scope, year, "gen_solar_thermal_csp")

    _ensure_carrier(n, "csp")
    w = n.snapshot_weightings["generators"]
    es_solar = n.generators[
        (n.generators["carrier"] == "solar")
        & n.generators["bus"].isin(set(_country_buses(n, "ES0")))
    ]
    shares = es_solar.groupby("bus")["p_nom"].sum()
    shares = shares / shares.sum()
    profiles = {
        bus: n.generators_t.p_max_pu[es_solar.index[es_solar["bus"] == bus][0]]
        for bus in shares.index
    }
    profile = pd.DataFrame(profiles)
    factor = _scale_to_energy(profile, shares * cap_mw, w, target_twh)
    for bus, share in shares.items():
        name = f"{bus} csp"
        n.add(
            "Generator",
            name,
            bus=bus,
            carrier="csp",
            p_nom=cap_mw * share,
            p_nom_extendable=False,
            marginal_cost=0.0,
        )
        n.generators_t.p_max_pu[name] = (profiles[bus] * factor).clip(upper=1.0)
    logger.info(
        "Attached %.0f MW CSP, solar-shaped profile scaled by %.3f: %.2f TWh/yr.",
        cap_mw,
        factor,
        _capped_energy_twh(profile, shares * cap_mw, w, factor),
    )


def _patch_biomass_availability(n: pypsa.Network, ctx: Mapping[str, Any]) -> None:
    """
    Cap biomass/waste availability at the realized capacity factor from cited rows.

    Biomass and waste plants run fuel-limited, well below their capacity; an
    unconstrained LP would dispatch them near-baseload. The cap is derived from
    the cited annual output and installed capacity, not tuned.
    """
    year = int(ctx["data_year"])
    natcap = _custom(ctx, "national_capacities.csv")
    val = _custom(ctx, "validation_2024_es.csv")
    es_gen = sum(
        _cited(val, "ES_mainland_baleares", year, m)
        for m in (
            "gen_other_renewables",
            "gen_renewable_waste",
            "gen_nonrenewable_waste",
        )
    )
    cases = {
        "ES": (
            sum(
                _cited(natcap, scope, year, "biomass/waste") for scope in ES_ZONE_SCOPES
            ),
            es_gen,
        ),
        "PT": (
            _cited(natcap, "PT", year, "biomass/waste"),
            _cited(_custom(ctx, "pt_actuals.csv"), "PT", year, "gen_biomass_waste"),
        ),
        "FR": (
            _cited(natcap, "FR", year, "biomass/waste"),
            _cited(_custom(ctx, "fr_actuals.csv"), "FR", year, "gen_biomass_waste"),
        ),
    }
    hours = float(n.snapshot_weightings["generators"].sum())
    for prefix, (cap, gen_twh) in cases.items():
        availability = gen_twh * 1e6 / (cap * hours)
        sel = n.generators.index[
            n.generators["carrier"].isin(("biomass", "waste"))
            & n.generators["bus"].isin(set(_country_buses(n, prefix)))
        ]
        n.generators.loc[sel, "p_max_pu"] = availability
        logger.info(
            "%s biomass/waste availability capped at %.3f (%.2f TWh / %.0f MW).",
            prefix,
            availability,
            gen_twh,
            cap,
        )


def _patch_prices(n: pypsa.Network, ctx: Mapping[str, Any]) -> None:
    """
    Recompute thermal marginal costs: VOM + fuel/eta + ETS x thermal intensity/eta.

    Portuguese gas plants also pay Portugal's gas network charges (ERSE) on top of
    the Iberian hub price. Gas is traded per MWh on higher heating value, while plant
    efficiencies and emission factors are on lower heating value, so the gas price is
    divided by the cited ``gas_lhv_hhv_ratio`` before it meets the efficiency.
    """
    prices = _custom(ctx, "prices.csv")
    year = int(ctx["data_year"])
    costs = pd.read_csv(ctx["costs"], index_col=0)

    def price(metric: str) -> float:
        for country in ("EU", "ES"):
            value = _value(prices, country, year, metric)
            if value is not None:
                return value
        raise ValueError(f"No {metric} row for {year} in prices.csv.")

    ets = price("co2_ets_price")
    fuel = {
        "gas": price("gas_pvb_price"),
        "coal": price("coal_fuel_price"),
        "lignite": price("lignite_fuel_price"),
        "oil": price("oil_fuel_price"),
        "nuclear": price("nuclear_fuel_price"),
        "biomass": price("biomass_fuel_price"),
    }
    heating_value_ratio = price("gas_lhv_hhv_ratio")
    fuel["gas"] /= heating_value_ratio
    co2 = n.carriers["co2_emissions"]
    for carrier, fuel_key in FUEL_METRIC.items():
        sel = n.generators.index[n.generators["carrier"] == carrier]
        if sel.empty:
            continue
        eta = n.generators.loc[sel, "efficiency"].replace(0.0, 1.0)
        vom = float(costs.at[carrier, "VOM"])
        n.generators.loc[sel, "marginal_cost"] = (
            vom + (fuel[fuel_key] + ets * float(co2[carrier])) / eta
        )
        logger.info(
            "%s marginal cost -> %.1f-%.1f EUR/MWh (fuel %.2f, ETS %.2f).",
            carrier,
            float(n.generators.loc[sel, "marginal_cost"].min()),
            float(n.generators.loc[sel, "marginal_cost"].max()),
            fuel[fuel_key],
            ets,
        )

    # Portuguese gas network premium: PT plants source at the Spanish PVB/MIBGAS hub
    # and additionally pay Portugal's regulated gas network charges (ERSE), which
    # Spanish plants do not. Breaks the near-tie in Iberian gas marginal costs in
    # the physically observed direction (PT CCGTs run far less than Spanish ones).
    pt_premium = _cited(prices, "PT", year, "gas_network_premium")
    pt_buses = set(_country_buses(n, "PT"))
    for carrier, fuel_key in FUEL_METRIC.items():
        if fuel_key != "gas":
            continue
        sel = n.generators.index[
            (n.generators["carrier"] == carrier) & n.generators["bus"].isin(pt_buses)
        ]
        if sel.empty:
            continue
        eta = n.generators.loc[sel, "efficiency"].replace(0.0, 1.0)
        n.generators.loc[sel, "marginal_cost"] += pt_premium / heating_value_ratio / eta
        logger.info(
            "PT %s gas network premium +%.2f EUR/MWh_th -> %.1f-%.1f EUR/MWh.",
            carrier,
            pt_premium,
            float(n.generators.loc[sel, "marginal_cost"].min()),
            float(n.generators.loc[sel, "marginal_cost"].max()),
        )


def _patch_daily_fuel_prices(n: pypsa.Network, ctx: Mapping[str, Any]) -> None:
    """
    Make thermal marginal costs follow the daily gas and CO2 prices.

    On top of the annual prices of ``prices``: every hour of a local day
    (Europe/Madrid) takes that day's gas price at the Spanish hub and EU allowance
    price from ``daily_fuel_prices_<year>.csv``. Cogeneration runs a fixed profile
    and keeps its cost. The gas price is converted to the lower heating value, as
    in ``prices``.
    """
    year = int(ctx["data_year"])
    annual = _custom(ctx, "prices.csv")
    daily = _custom(ctx, f"daily_fuel_prices_{year}.csv").set_index("date")
    local = _local_snapshots(n)
    # the year's last UTC hour is already the next local day: it keeps the last price
    today = daily.set_index(pd.to_datetime(daily.index)).reindex(
        local.tz_localize(None).normalize(), method="ffill"
    )
    if today.isna().any().any():
        raise ValueError(f"Daily fuel prices do not cover every snapshot of {year}.")
    today.index = n.snapshots
    gas = today["gas_pvb"] - _cited(annual, "ES", year, "gas_pvb_price")
    gas /= _cited(annual, "EU", year, "gas_lhv_hhv_ratio")
    co2 = today["co2_eua"] - _cited(annual, "EU", year, "co2_ets_price")

    gens = n.generators[n.generators["carrier"].isin(FUEL_METRIC)]
    burns_gas = gens["carrier"].map(FUEL_METRIC) == "gas"
    intensity = gens["carrier"].map(n.carriers["co2_emissions"]).fillna(0.0)
    gens = gens[burns_gas | (intensity > 0.0)]
    eta = gens["efficiency"].replace(0.0, 1.0)
    cost = {
        g: gens.at[g, "marginal_cost"]
        + (gas * float(burns_gas[g]) + co2 * intensity[g]) / eta[g]
        for g in gens.index
    }
    _set_dynamic(n.generators_t, "marginal_cost", pd.DataFrame(cost))
    logger.info(
        "Daily gas %.1f-%.1f EUR/MWh_th and CO2 %.1f-%.1f EUR/t on %d generators.",
        today["gas_pvb"].min(),
        today["gas_pvb"].max(),
        today["co2_eua"].min(),
        today["co2_eua"].max(),
        len(gens),
    )


def _patch_biomass_waste_fixed(n: pypsa.Network, ctx: Mapping[str, Any]) -> None:
    """
    Fix the output of Spanish and Portuguese biomass and waste plants.

    They burn the fuel they have, whatever the price: output stays at the realized
    capacity factor that ``biomass_availability`` sets as their ceiling. Their
    variable cost is kept and counts in the system cost.
    """
    sel = n.generators.index[
        n.generators["carrier"].isin(["biomass", "waste"])
        & n.generators["bus"].isin(
            _country_buses(n, "ES").union(_country_buses(n, "PT"))
        )
    ]
    n.generators.loc[sel, "p_min_pu"] = n.generators.loc[sel, "p_max_pu"]
    logger.info(
        "Fixed output of %d biomass and waste generators at %.3f-%.3f of capacity.",
        len(sel),
        n.generators.loc[sel, "p_min_pu"].min(),
        n.generators.loc[sel, "p_min_pu"].max(),
    )


def _patch_demand(n: pypsa.Network, ctx: Mapping[str, Any]) -> None:
    """Scale the non-boundary load of each zone to its cited annual demand."""
    df = _custom(ctx, "annual_demand.csv")
    year = int(ctx["data_year"])
    w = n.snapshot_weightings["generators"]
    for scope, (prefix, metric) in DEMAND_SCOPES.items():
        target_twh = _cited(df, scope, year, metric)
        cols = n.loads.index[
            n.loads["bus"].isin(set(_country_buses(n, prefix)))
            & (n.loads["carrier"] != "boundary")
        ].intersection(n.loads_t.p_set.columns)
        current = float(n.loads_t.p_set[cols].mul(w, axis=0).sum().sum()) / 1e6
        n.loads_t.p_set[cols] *= target_twh / current
        logger.info("%s demand scaled %.1f -> %.1f TWh.", scope, current, target_twh)


def _patch_hydro_inflow(n: pypsa.Network, ctx: Mapping[str, Any]) -> None:
    """
    Scale ES/PT/FR reservoir inflow to the natural hydro energy observed in the year.

    The upstream EIA inflow normalization ends before 2024, so the inflow is rescaled
    to the observed energy of the wet year 2024.

    Observed energy is turbine output, so the inflow is counted after the dispatch
    efficiency of the reservoir units. Its monthly timing follows the water balance
    (``_shape_inflow_by_month``), and Portuguese reversible plants then receive the
    natural water they turbined (``_move_inflow_to_reversible``).
    """
    year = int(ctx["data_year"])
    targets = {
        "ES": _cited(
            _custom(ctx, "validation_2024_es.csv"),
            "ES_mainland_baleares",
            year,
            "gen_hydro",
        ),
        "PT": _cited(_custom(ctx, "pt_actuals.csv"), "PT", year, "gen_hydro"),
        "FR": _cited(_custom(ctx, "fr_actuals.csv"), "FR", year, "gen_hydro"),
    }
    w = n.snapshot_weightings["generators"]
    for prefix, target in targets.items():
        buses = set(_country_buses(n, prefix))
        su = n.storage_units.index[
            (n.storage_units["carrier"] == "hydro") & n.storage_units["bus"].isin(buses)
        ].intersection(n.storage_units_t.inflow.columns)
        inflow = n.storage_units_t.inflow[su].mul(w, axis=0).sum()
        efficiency = n.storage_units.loc[su, "efficiency_dispatch"]
        inflow_twh = float((inflow * efficiency).sum()) / 1e6
        ror = n.generators[
            (n.generators["carrier"] == "ror") & n.generators["bus"].isin(buses)
        ]
        ror_twh = (
            float(
                n.generators_t.p_max_pu[
                    ror.index.intersection(n.generators_t.p_max_pu.columns)
                ]
                .mul(ror["p_nom"], axis=1)
                .mul(w, axis=0)
                .sum()
                .sum()
            )
            / 1e6
        )
        factor = (target - ror_twh) / inflow_twh
        n.storage_units_t.inflow[list(su)] *= factor
        logger.info(
            "%s reservoir inflow scaled by %.3f: %.1f TWh of output (+%.1f ror) -> %.1f TWh observed.",
            prefix,
            factor,
            inflow_twh,
            ror_twh,
            target,
        )
        _shape_inflow_by_month(n, ctx, prefix, list(su))
    for prefix in REVERSIBLE_INFLOW_SCOPES:
        _move_inflow_to_reversible(n, ctx, prefix)


def _measured(ctx: Mapping[str, Any], prefix: str) -> pd.DataFrame:
    """Return the measured hourly generation of one country, indexed by snapshot."""
    measured = _custom(ctx, f"generation_hourly_{int(ctx['data_year'])}.csv")
    measured = measured[measured["country"] == prefix]
    return measured.set_index(pd.to_datetime(measured["snapshot"]))


def _reversible_natural_output(
    n: pypsa.Network, measured: pd.DataFrame, prefix: str
) -> pd.Series:
    """Measured output of reversible plants beyond their returned pumped energy (MW)."""
    units = n.storage_units[n.storage_units["bus"].isin(_country_buses(n, prefix))]
    reversible = units[units["carrier"] == "PHS"]
    round_trip = (
        reversible["efficiency_store"] * reversible["efficiency_dispatch"]
    ).mean()
    return measured["pumped_storage"] - round_trip * measured["pumping"]


def _shape_inflow_by_month(
    n: pypsa.Network, ctx: Mapping[str, Any], prefix: str, units: list[str]
) -> None:
    """
    Spread the year's reservoir inflow over the months as the water balance shows it.

    A month's net inflow is the measured reservoir output plus the change in the
    energy stored in the reservoirs (ENTSO-E weekly filling, interpolated to the month
    boundaries); natural output of reversible plants counts where they take inflow.
    Upstream runoff knows no irrigation or evaporation, so it puts too much water into
    summer. Each month keeps its hourly shape, scaled to the month's share of the year;
    a month with a net withdrawal takes it evenly in time, from each reservoir by its
    energy capacity.
    """
    year = int(ctx["data_year"])
    measured = _measured(ctx, prefix)
    output = measured["hydro_reservoir"]
    if prefix in REVERSIBLE_INFLOW_SCOPES:
        output = output + _reversible_natural_output(n, measured, prefix)
    levels = _custom(ctx, "reservoir_levels_weekly.csv").query("country == @prefix")
    # weekly averages, placed at mid-week
    stored = pd.Series(
        levels["stored_gwh"].to_numpy() * 1e3,
        index=pd.to_datetime(levels["week_start"]) + pd.Timedelta(days=3.5),
    )
    bounds = pd.date_range(f"{year}-01-01", f"{year + 1}-01-01", freq="MS")
    at_bounds = stored.reindex(stored.index.union(bounds)).interpolate("time")[bounds]
    month = pd.Series(n.snapshots.month, index=n.snapshots)
    net = (
        output.groupby(output.index.month).sum() + at_bounds.diff().dropna().to_numpy()
    )
    # the model year may drop days (29 February): keep the share of the modeled hours
    modeled = n.snapshot_weightings.generators.groupby(month).sum()
    net *= modeled.to_numpy() / (24 * bounds[:-1].days_in_month.to_numpy())
    share = net / net.sum()

    inflow = n.storage_units_t.inflow[units]
    total = float(inflow.sum().sum())
    current = inflow.sum(axis=1).groupby(month).sum()
    energy = (
        n.storage_units.loc[units, "p_nom"] * n.storage_units.loc[units, "max_hours"]
    )
    weights = energy / energy.sum()
    for m, target in (share * total).items():
        hours = month == m
        if target > 0.0 and current[m] > 0.0:
            inflow.loc[hours] *= target / current[m]
        else:
            inflow.loc[hours] = (target / hours.sum()) * weights.to_numpy()
    n.storage_units_t.inflow[units] = inflow
    logger.info(
        "%s inflow by month (TWh), water balance: %s.",
        prefix,
        ", ".join(f"{v:.2f}" for v in share * total / 1e6),
    )


def _move_inflow_to_reversible(
    n: pypsa.Network, ctx: Mapping[str, Any], prefix: str
) -> None:
    """
    Give reversible plants the natural water they turbined in the data year.

    A reversible plant on a river reservoir turbines its pumped water and natural
    inflow alike. Its measured output beyond the returned pumped energy (pumping times
    round-trip efficiency) is natural water; that share of the reservoir inflow moves
    to the reversible units, by turbine capacity, so the zone's hydro energy is kept.
    Only the modeled hours count (the model year may drop the leap day).
    """
    measured = _measured(ctx, prefix).loc[n.snapshots]
    units = n.storage_units[n.storage_units["bus"].isin(_country_buses(n, prefix))]
    hydro = units.index[units["carrier"] == "hydro"]
    reversible = units[units["carrier"] == "PHS"]
    natural_mwh = float(_reversible_natural_output(n, measured, prefix).sum())
    water = natural_mwh / reversible["efficiency_dispatch"].mean()
    inflow = n.storage_units_t.inflow[hydro]
    share = water / float(inflow.sum().sum())
    if not 0.0 < share < 1.0:
        raise ValueError(
            f"{prefix}: natural water share of reversible plants is {share:.2f}."
        )
    n.storage_units_t.inflow[hydro] = inflow * (1.0 - share)
    weights = reversible["p_nom"] / reversible["p_nom"].sum()
    total = inflow.sum(axis=1) * share
    n.storage_units_t.inflow = pd.concat(
        [
            n.storage_units_t.inflow,
            pd.DataFrame({u: total * w for u, w in weights.items()}),
        ],
        axis=1,
    )
    logger.info(
        "%s reversible plants receive %.0f%% of the reservoir inflow (%.1f TWh of natural output).",
        prefix,
        100.0 * share,
        natural_mwh / 1e6,
    )


# bus prefix -> OMIP contract zone that values the zone's reservoir water;
# Portugal takes the Spanish contract (MIBEL clears both at one price in most hours)
WATER_VALUE_ZONES: dict[str, str] = {"ES0": "ES", "PT": "ES", "FR": "FR"}


def _patch_water_value(n: pypsa.Network, ctx: Mapping[str, Any]) -> None:
    """
    Value reservoir water at the futures price of the power it can sell.

    A reservoir owner values its water at the price it expects to sell it for. OMIP's
    baseload futures state that expectation in public before delivery, so every market
    participant had it. ``ctx["water_value_contract"]`` picks the contract: ``day``
    takes each day's contract from the latest bulletin before OMIE's auction (two or
    more days ahead), ``week`` the week contract on its last trading day before the
    week. Days and weeks are local (Europe/Madrid). Releases of reservoir hydro cost
    that price; spilled water forgoes it too (per MWh stored: the price times the
    turbine efficiency), so no water is spilled that could be turbined. Reservoirs
    keep their inflow, energy capacity and cyclic level.
    """
    year = int(ctx["data_year"])
    local = _local_snapshots(n)
    if ctx["water_value_contract"] == "day":
        name, key = f"omip_day_contract_{year}.csv", ["day"]
        periods = pd.Index(local.strftime("%Y-%m-%d"))
    else:
        name, key = f"omip_week_ahead_{year}.csv", ["iso_year", "iso_week"]
        iso = local.isocalendar()
        periods = pd.MultiIndex.from_arrays([iso["year"], iso["week"]])
    futures = _custom(ctx, name)
    costs, spills = {}, {}
    for prefix, zone in WATER_VALUE_ZONES.items():
        price = futures[futures["zone"] == zone].set_index(key)["eur_mwh"]
        value = pd.Series(price.reindex(periods).to_numpy(), index=n.snapshots)
        if value.isna().any():
            raise ValueError(f"{name} misses periods of zone {zone}.")
        units = n.storage_units.index[
            n.storage_units["bus"].isin(_country_buses(n, prefix))
            & (n.storage_units["carrier"] == "hydro")
        ]
        for unit in units:
            costs[unit] = value
            spills[unit] = value * n.storage_units.at[unit, "efficiency_dispatch"]
        logger.info(
            "%s water value on %d reservoir units: %.0f-%.0f EUR/MWh by %s.",
            prefix,
            len(units),
            value.min(),
            value.max(),
            ctx["water_value_contract"],
        )
    _set_dynamic(n.storage_units_t, "marginal_cost", pd.DataFrame(costs))
    _set_dynamic(n.storage_units_t, "spill_cost", pd.DataFrame(spills))


RESERVOIR_FLOOR_QUANTILE = 0.10


def _patch_reservoir_floor(n: pypsa.Network, ctx: Mapping[str, Any]) -> None:
    """
    Run a share of Spanish reservoir hydro as must-run.

    Part of the output booked as reservoir hydro does not respond to price: minimum
    river flows, and cascade plants with little storage. The floor of a calendar
    month is the 10th percentile of the measured hourly reservoir output. Every
    reservoir unit hands its share of the floor (by capacity) to a must-run
    generator: its turbine capacity falls by that share and its inflow by the
    water the floor's energy takes through the turbines, so turbine capacity and
    the year's energy are conserved. The reservoirs' energy capacity falls with
    their turbine capacity.
    """
    measured = _measured(ctx, "ES")["hydro_reservoir"]
    monthly = measured.groupby(measured.index.month).quantile(RESERVOIR_FLOOR_QUANTILE)
    floor = pd.Series(n.snapshots.month.map(monthly), index=n.snapshots)

    units = n.storage_units.index[
        n.storage_units["bus"].isin(_country_buses(n, "ES0"))
        & (n.storage_units["carrier"] == "hydro")
    ]
    share = (
        n.storage_units.loc[units, "p_nom"] / n.storage_units.loc[units, "p_nom"].sum()
    )
    inflow = n.storage_units_t.inflow[units]
    w = n.snapshot_weightings["generators"]
    efficiency = n.storage_units.loc[units, "efficiency_dispatch"]
    output = (inflow.mul(w, axis=0).sum() * efficiency).sum()
    energy_share = (floor * w).sum() / output
    if energy_share >= 1.0:
        raise ValueError("Reservoir floor exceeds the reservoir inflow.")
    n.storage_units_t.inflow[units] = inflow * (1.0 - energy_share)
    n.storage_units.loc[units, "p_nom"] -= share * floor.max()
    names = units + " must-run"
    profile = pd.DataFrame({name: floor / floor.max() for name in names})
    n.add(
        "Generator",
        names,
        bus=n.storage_units.loc[units, "bus"].values,
        carrier="ror",
        p_nom=(share * floor.max()).values,
        p_max_pu=profile,
        p_min_pu=profile,
    )
    logger.info(
        "ES reservoir floor %.0f-%.0f MW by month: %.1f TWh/yr, %.0f%% of the output.",
        floor.min(),
        floor.max(),
        (floor * w).sum() / 1e6,
        100.0 * energy_share,
    )


def _patch_es_hydro_availability(n: pypsa.Network, ctx: Mapping[str, Any]) -> None:
    """
    Limit Spanish hydro to the highest output the fleet has delivered.

    Installed capacity is not deliverable capacity: head, cascades, irrigation and
    minimum-flow rules and maintenance keep the fleet's output far below its turbine
    capacity. Reservoir plants take an hourly upper limit that keeps reservoir plus
    run-of-river output under the multi-year record less the pure pumped storage;
    energy is untouched (validation log, section 7, step 12).
    """
    ceiling = _cited(
        _custom(ctx, "hydro_output_ceiling.csv"),
        "ES_peninsular",
        int(ctx["data_year"]),
        "hydro_output_ceiling",
    )
    buses = _country_buses(n, "ES0")
    storage = n.storage_units[n.storage_units["bus"].isin(buses)]
    pumped = float(storage.loc[storage["carrier"] == "PHS", "p_nom"].sum())
    units = storage.index[storage["carrier"] == "hydro"]
    river = n.generators.index[
        n.generators["bus"].isin(buses) & (n.generators["carrier"] == "ror")
    ]
    river_output = (
        n.get_switchable_as_dense("Generator", "p_max_pu")[river]
        .mul(n.generators.loc[river, "p_nom"], axis=1)
        .sum(axis=1)
    )
    room = (ceiling - pumped - river_output).clip(lower=0.0)
    availability = (room / storage.loc[units, "p_nom"].sum()).clip(upper=1.0)
    _set_dynamic(
        n.storage_units_t,
        "p_max_pu",
        pd.DataFrame({unit: availability for unit in units}),
    )
    logger.info(
        "ES hydro ceiling %.0f MW: reservoir plants limited to %.0f-%.0f MW "
        "of %.0f MW installed.",
        ceiling,
        room.min(),
        room.max(),
        storage.loc[units, "p_nom"].sum(),
    )


def _patch_es_unit_outages(n: pypsa.Network, ctx: Mapping[str, Any]) -> None:
    """
    Give mainland nuclear, gas and coal plants the declared outages of the data year.

    The hourly unavailable capacity per technology comes from the unavailabilities the
    plants declared to OMIE (``scripts/retrieve_unit_outages.py``); the fleet's
    availability is one minus its share out. Shutdowns for market reasons are not
    declared, so the model decides them.

    Reactors run base load and cut output for the market only to about 60-70% of
    their power (CSN), so nuclear keeps the cited minimum share of its available
    capacity: in surplus hours wind and solar are curtailed, not the reactors. Whole
    units switched off for market reasons cannot be represented in a linear model.
    """
    year = int(ctx["data_year"])
    nuclear_min = _cited(
        _custom(ctx, "national_capacities.csv"),
        "ES_peninsular",
        year,
        "nuclear minimum output",
    )
    outages = _custom(ctx, f"es_unit_outages_{year}.csv").set_index("snapshot")
    outages.index = pd.to_datetime(outages.index)
    buses = _country_buses(n, "ES0")
    columns, minimum = {}, {}
    for carrier in outages.columns:
        units = n.generators.index[
            n.generators["bus"].isin(buses) & (n.generators["carrier"] == carrier)
        ]
        p_nom = float(n.generators.loc[units, "p_nom"].sum())
        out = outages[carrier].reindex(n.snapshots)
        if out.isna().any():
            raise ValueError(
                f"es_unit_outages_{year}.csv does not cover the snapshots."
            )
        availability = (1.0 - out / p_nom).clip(lower=0.0)
        columns.update(dict.fromkeys(units, availability))
        if carrier == "nuclear":
            minimum.update(dict.fromkeys(units, nuclear_min * availability))
        logger.info(
            "ES %s outages: %.1f TWh of %.1f TWh unavailable (mean availability %.2f).",
            carrier,
            float(out.sum()) / 1e6,
            p_nom * len(out) / 1e6,
            availability.mean(),
        )
    _set_dynamic(n.generators_t, "p_max_pu", pd.DataFrame(columns))
    _set_dynamic(n.generators_t, "p_min_pu", pd.DataFrame(minimum))


def _patch_fr_nuclear_profile(n: pypsa.Network, ctx: Mapping[str, Any]) -> None:
    """
    Seasonal French nuclear availability from the measured 2024 maintenance calendar.

    Replaces the flat upstream annual availability factor on French nuclear with the
    monthly shape of measured 2024 output (eCO2mix via ODRE), normalized so the
    weighted annual mean equals the upstream factor: annual energy capability is
    unchanged, only its seasonality. Monthly output is a lower bound on
    availability (summer modulation), a stated conservatism.
    """
    monthly = (
        _custom(ctx, "fr_nuclear_monthly_2024.csv")
        .set_index("month")["value"]
        .astype(float)
    )
    if sorted(monthly.index) != list(range(1, 13)):
        raise ValueError("fr_nuclear_monthly_2024.csv must have one row per month.")

    gens = n.generators.index[
        (n.generators["carrier"] == "nuclear")
        & n.generators["bus"].isin(set(_country_buses(n, "FR")))
    ]
    if gens.empty:
        raise ValueError("No French nuclear generators found.")

    w = n.snapshot_weightings["generators"]
    shape = pd.Series([monthly[ts.month] for ts in n.snapshots], index=n.snapshots)
    shape = shape / (float((shape * w).sum()) / float(w.sum()))
    for g in gens:
        profile = shape * float(n.generators.at[g, "p_max_pu"])
        if float(profile.max()) > 1.0:
            raise ValueError(f"FR nuclear profile for '{g}' exceeds 1.0.")
        n.generators_t.p_max_pu[g] = profile
    annual_mean = float(n.generators.loc[gens, "p_max_pu"].mean())
    logger.info(
        "FR nuclear availability seasonalized on %d units: monthly %.3f-%.3f "
        "around the preserved annual mean %.3f.",
        len(gens),
        float(shape.min()) * annual_mean,
        float(shape.max()) * annual_mean,
        annual_mean,
    )


def _set_mainland_floor(
    n: pypsa.Network, ctx: Mapping[str, Any], file: str, carrier: str
) -> None:
    """Give the mainland plants of ``carrier`` the minimum output (MW) in ``file``."""
    metric = file.removesuffix(".csv")
    floor_mw = _cited(
        _custom(ctx, file), "ES_peninsular", int(ctx["data_year"]), metric
    )
    gens = n.generators.index[
        (n.generators["carrier"] == carrier)
        & n.generators["bus"].isin(_country_buses(n, "ES0"))
    ]
    p_nom = float(n.generators.loc[gens, "p_nom"].sum())
    n.generators.loc[gens, "p_min_pu"] = floor_mw / p_nom
    logger.info(
        "Mainland %s keeps %.0f MW running (%.1f%% of %.0f MW).",
        carrier,
        floor_mw,
        100.0 * floor_mw / p_nom,
        p_nom,
    )


def _patch_security_gas_floor(n: pypsa.Network, ctx: Mapping[str, Any]) -> None:
    """
    Keep the gas output running that the system operator requires for security.

    After the day-ahead market, Red Eléctrica orders synchronous plants up where the
    schedule fails its security criteria (P.O. 3.2); they run whatever the price, so
    they must not set it. The floor is the gas the operator scheduled in the hours
    when the market scheduled none, one constant shared by the mainland gas plants
    in proportion to their capacity (validation log, section 7, step 11).
    """
    _set_mainland_floor(n, ctx, "security_gas_floor.csv", "CCGT")


def _patch_coal_floor(n: pypsa.Network, ctx: Mapping[str, Any]) -> None:
    """
    Keep the Aboño 2 coal unit running at its minimum output.

    Aboño 2 burns the blast-furnace gases of the ArcelorMittal steelworks next to it
    and supplies the load of its 220 kV zone (BOE-A-2024-10144), so the operator
    keeps it on whatever the price. The mainland coal fleet takes its minimum stable
    output (validation log, section 7, step 13).
    """
    _set_mainland_floor(n, ctx, "coal_floor.csv", "coal")


def _patch_start_up_costs(n: pypsa.Network, ctx: Mapping[str, Any]) -> None:
    """
    Give gas, coal and Spanish nuclear plants the start-up cost of the upstream table.

    A plant called for a few hours must earn its start back in them, and a plant
    kept on overnight accepts a price below its fuel cost to avoid a new start. The
    cost per MW started is the one PyPSA-Eur ships in ``data/unit_commitment.csv``.
    Spanish reactors take it too because they run at constant output; French
    reactors follow load and are left free. The patch only stores the cost;
    ``solve_network.py`` charges it in linear form, without commitment variables
    (validation log, section 7, steps 14 and 18).
    """
    table = pd.read_csv(ctx["unit_commitment"], index_col=0)
    carrier_of = n.generators["carrier"]
    spanish = n.generators["bus"].isin(_country_buses(n, "ES0"))
    plants = {c: n.generators.index[carrier_of == c] for c in START_UP_CARRIERS}
    plants["nuclear"] = n.generators.index[(carrier_of == "nuclear") & spanish]
    for carrier, gens in plants.items():
        per_mw = float(table.at["start_up_cost", carrier])
        n.generators.loc[gens, "start_up_cost"] = (
            per_mw * n.generators.loc[gens, "p_nom"]
        )
        logger.info(
            "%s: start-up cost %.0f EUR/MW on %d plants.", carrier, per_mw, len(gens)
        )


def _patch_ma_ad_exports(n: pypsa.Network, ctx: Mapping[str, Any]) -> None:
    """Fixed ES exports to Morocco and Andorra as flat loads at the nearest nodes (2024 actuals)."""
    val = _custom(ctx, "validation_2024_es.csv")
    year = int(ctx["data_year"])
    _ensure_carrier(n, "boundary")
    hours = float(n.snapshot_weightings["generators"].sum())
    es_buses = _country_buses(n, "ES0")
    for code, metric in (("MA", "net_export_ma"), ("AD", "net_export_ad")):
        twh = _cited(val, "ES", year, metric)
        bus = _nearest_bus(n, es_buses, ES_BORDER_COORDS[code])
        name = f"{bus} boundary export {code}"
        n.add("Load", name, bus=bus, carrier="boundary", p_set=twh * 1e6 / hours)
        logger.info("Attached fixed ES->%s export %.2f TWh at %s.", code, twh, bus)


# zone pair -> ntc.csv rows (country, metric) of the forward and the backward capacity
ZONE_BORDERS: dict[tuple[str, str], tuple[tuple[str, str], tuple[str, str]]] = {
    ("ES0", "FR0"): (("ES-FR", "ntc_es_to_fr"), ("FR-ES", "ntc_fr_to_es")),
    ("ES0", "PT0"): (("ES-PT", "ntc_es_to_pt"), ("PT-ES", "ntc_pt_to_es")),
    # the Rómulo link, keyed in Redeia's zone codes (ES1 mainland, ES4 Balearics)
    ("ES0", "ES1"): (
        ("ES1-ES4", "ntc_peninsula_to_baleares"),
        ("ES1-ES4", "ntc_peninsula_to_baleares"),
    ),
}


def _patch_bidding_zones(n: pypsa.Network, ctx: Mapping[str, Any]) -> None:
    """
    Merge the nodes into bidding zones linked by published net transfer capacities.

    The day-ahead market clears one price per zone and couples zones through NTCs,
    without the grid inside a zone. Zones are mainland Spain (``ES0``), the Balearics
    (``ES1``), Portugal (``PT0``) and France (``FR0``): the node name up to its first
    space. Every asset moves to its zone and the storage units of a zone merge
    (``_merge_storage_units``); lines and HVDC links are replaced by one transport link
    per border with the directional NTCs of ``ntc.csv``.
    """
    ntc = _custom(ctx, "ntc.csv")
    year = int(ctx["data_year"])
    nodes = n.buses.assign(zone=n.buses.index.str.split(" ").str[0])
    zones = nodes.groupby("zone").agg({"x": "mean", "y": "mean", "country": "first"})

    n.add("Bus", zones.index, carrier="AC", v_nom=380.0, **zones.to_dict("series"))
    for assets in (n.generators, n.loads, n.storage_units):
        assets["bus"] = assets["bus"].map(nodes["zone"])
    _merge_storage_units(n)
    n.remove("Line", n.lines.index)
    n.remove("Link", n.links.index)
    n.remove("Bus", nodes.index)

    _ensure_carrier(n, "NTC")
    for (zone0, zone1), rows in ZONE_BORDERS.items():
        forward, backward = (_cited(ntc, country, year, m) for country, m in rows)
        n.add(
            "Link",
            f"{zone0}-{zone1}",
            bus0=zone0,
            bus1=zone1,
            carrier="NTC",
            p_nom=forward,
            p_min_pu=-backward / forward,
        )
        logger.info("Border %s-%s: %.0f / %.0f MW.", zone0, zone1, forward, backward)


def _merge_storage_units(n: pypsa.Network) -> None:
    """
    Merge the storage units of each zone and carrier into one unit.

    Upstream gives each plant its own storage and shares the country's inflow by
    turbine capacity; clustering then sums the plants per node, so some nodes hold
    most of the storage and others little storage but a full share of inflow. Inside
    a zone without a grid that split has no physical meaning and forces small units
    to spill. Capacities, energy and inflow are summed; availabilities and costs are
    weighted by turbine capacity.
    """
    units = n.storage_units
    for (bus, carrier), names in units.groupby(["bus", "carrier"]).groups.items():
        names = pd.Index(names)
        if len(names) < 2:
            continue
        for col in ("efficiency_store", "efficiency_dispatch", "standing_loss"):
            if units.loc[names, col].nunique() > 1:
                raise ValueError(f"{bus} {carrier}: units differ in {col}.")
        p_nom = units.loc[names, "p_nom"]
        static = units.loc[
            names[0],
            [
                "efficiency_store",
                "efficiency_dispatch",
                "standing_loss",
                "cyclic_state_of_charge",
            ],
        ].to_dict()
        static["p_nom"] = float(p_nom.sum())
        static["max_hours"] = float(
            (p_nom * units.loc[names, "max_hours"]).sum() / p_nom.sum()
        )
        static["state_of_charge_initial"] = float(
            units.loc[names, "state_of_charge_initial"].sum()
        )
        dynamic = {
            attr: n.get_switchable_as_dense("StorageUnit", attr)[names]
            .mul(p_nom, axis=1)
            .sum(axis=1)
            / p_nom.sum()
            for attr in ("p_max_pu", "p_min_pu", "marginal_cost", "spill_cost")
        }
        inflow = n.storage_units_t.inflow.reindex(columns=names, fill_value=0.0).sum(
            axis=1
        )
        merged = f"{bus} {carrier}"
        n.remove("StorageUnit", names)
        n.add("StorageUnit", merged, bus=bus, carrier=carrier, **static)
        for attr, series in dynamic.items():
            n.storage_units_t[attr][merged] = series
        if inflow.any():
            n.storage_units_t.inflow[merged] = inflow
        logger.info(
            "%s: %d units merged (%.0f MW, %.1f TWh).",
            merged,
            len(names),
            static["p_nom"],
            static["p_nom"] * static["max_hours"] / 1e6,
        )
        units = n.storage_units


def _patch_drop_empty_generators(n: pypsa.Network, ctx: Mapping[str, Any]) -> None:
    """Remove generators below 1 MW: resource classes and carriers without a fleet."""
    empty = n.generators.index[n.generators["p_nom"] < 1.0]
    logger.info(
        "Dropping %d of %d generators below 1 MW (%.1f MW in total).",
        len(empty),
        len(n.generators),
        n.generators.loc[empty, "p_nom"].sum(),
    )
    n.remove("Generator", empty)


PATCH_FUNCTIONS: dict[str, Callable[[pypsa.Network, Mapping[str, Any]], None]] = {
    "boundary_flows": lambda n, ctx: attach_boundary_flows(n, ctx["boundary_flows"]),
    "national_capacities": _patch_national_capacities,
    "wind_correction": _patch_wind_correction,
    "solar_tracking": _patch_solar_tracking,
    "phs_split": _patch_phs_split,
    "pumped_storage_limits": _patch_pumped_storage_limits,
    "chp_profile": _patch_chp_profile,
    "csp": _patch_csp,
    "biomass_availability": _patch_biomass_availability,
    "prices": _patch_prices,
    "daily_fuel_prices": _patch_daily_fuel_prices,
    "biomass_waste_fixed": _patch_biomass_waste_fixed,
    "demand": _patch_demand,
    "hydro_inflow": _patch_hydro_inflow,
    "water_value": _patch_water_value,
    "reservoir_floor": _patch_reservoir_floor,
    "es_hydro_availability": _patch_es_hydro_availability,
    "fr_nuclear_profile": _patch_fr_nuclear_profile,
    "es_unit_outages": _patch_es_unit_outages,
    "security_gas_floor": _patch_security_gas_floor,
    "coal_floor": _patch_coal_floor,
    "start_up_costs": _patch_start_up_costs,
    "ma_ad_exports": _patch_ma_ad_exports,
    "bidding_zones": _patch_bidding_zones,
    "drop_empty_generators": _patch_drop_empty_generators,
}
# patches are applied in the order PATCH_FUNCTIONS lists them
PATCH_ORDER: tuple[str, ...] = tuple(PATCH_FUNCTIONS)


def prepare_network(
    n: pypsa.Network,
    patches: Mapping[str, bool],
    ctx: Mapping[str, Any],
) -> pypsa.Network:
    """Apply enabled patches in ``PATCH_ORDER``, then freeze the fleet."""
    unknown = set(patches) - set(PATCH_ORDER)
    if unknown:
        raise ValueError(f"Unknown patches: {sorted(unknown)}")
    for name, required in PATCH_REQUIRES.items():
        if patches.get(name, False) and not patches.get(required, False):
            raise ValueError(f"Patch '{name}' needs patch '{required}'.")
    for name in PATCH_ORDER:
        if patches.get(name, False):
            logger.info("Applying patch '%s'.", name)
            PATCH_FUNCTIONS[name](n, ctx)
    freeze_fleet(n)
    return n


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--network", type=Path, required=True, help="Composed network (.nc)."
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="Base network output (.nc)."
    )
    parser.add_argument(
        "--patches",
        type=str,
        default="boundary_flows",
        help="Comma-separated list of enabled patches.",
    )
    parser.add_argument(
        "--boundary-flows",
        type=Path,
        default=Path("data/custom/fr_boundary_flows_2024.csv"),
        help="Measured FR border flows CSV.",
    )
    parser.add_argument("--custom-dir", type=Path, default=Path("data/custom"))
    parser.add_argument(
        "--costs", type=Path, required=True, help="Processed technology costs CSV."
    )
    parser.add_argument(
        "--unit-commitment",
        type=Path,
        default=Path("pypsa-eur/data/unit_commitment.csv"),
        help="Upstream unit-commitment parameters CSV.",
    )
    parser.add_argument(
        "--data-year",
        type=int,
        required=True,
        help="Year of the back-cast: of its weather and of the cited data rows.",
    )
    parser.add_argument(
        "--water-value-contract",
        choices=("day", "week"),
        default="day",
        help="OMIP futures contract that values reservoir water.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    args = _parse_args(argv)
    net_in = args.network
    net_out = args.output
    enabled = [p.strip() for p in args.patches.split(",") if p.strip()]
    ctx: dict[str, Any] = {
        "boundary_flows": args.boundary_flows,
        "custom_dir": args.custom_dir,
        "costs": args.costs,
        "unit_commitment": args.unit_commitment,
        "data_year": args.data_year,
        "water_value_contract": args.water_value_contract,
    }

    n = pypsa.Network(str(net_in))
    prepare_network(n, patches=dict.fromkeys(enabled, True), ctx=ctx)
    net_out.parent.mkdir(parents=True, exist_ok=True)
    n.export_to_netcdf(str(net_out))
    logger.info("Exported base network to %s", net_out)


if __name__ == "__main__":
    main()
