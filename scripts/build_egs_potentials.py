# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
Build supply curves of enhanced geothermal (EGS) power per onshore region from the
subsurface temperature and the Ricks & Jenkins (2025) EGS cost model.

For every temperature column (`build_geothermal_columns`) and fitted depth (2.5 to
6.5 km) the cost model (`scripts/lib/geothermal.py`, doi:10.5281/zenodo.15485307,
CC-BY-4.0) gives the plant and wellfield investment, fixed O&M, the gross electric
output per injector and the heat it extracts. A depth is feasible where the rock
reaches `min_temperature` (150 °C, the binary-cycle floor); the production
temperature fed to the plant fits is capped at `max_temperature`. Costs and capacity
are per net kW (wellfield pumping, `parasitic_fraction`, netted out), converted from
USD2021 to EUR2020 with `usd2021_to_eur`. Each column keeps its cheapest depth by
levelised cost (at `availability`, the cost data's discount rate and `lifetime`);
capacity is the model's technical density times the column's ground area times the
share of its land available for onshore wind. Columns are grouped per region into
steps of levelised cost (`lcoe_bins`).

The capacity factors follow the ambient-temperature dependence of binary plants
(Ricks et al., The Role of Flexible Geothermal Power in Decarbonized Electricity
Systems, Supplementary Figure 20, https://zenodo.org/records/7093330).

Outputs
-------
- `resources/<run_name>/egs_potentials.csv`: one row per region and step, with the
  net electric capacity `p_nom_max` (MW), `well_capex` and `plant_capex`
  (EUR/kW_el), `fom` (EUR/kW_el/yr), the organic Rankine cycle efficiency
  `efficiency` (net electricity per heat extracted), `lcoe` (EUR/MWh_el), `depth`
  (km) and `temperature` (°C).
- `resources/<run_name>/egs_capacity_factors.csv`: hourly capacity factors per region.
"""

import logging

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr

from scripts._helpers import configure_logging, get_snapshots, set_scenario_config
from scripts.lib.geothermal import (
    EGS_DEPTHS_KM,
    annuity,
    bin_supply_curve,
    egs_costs,
)

logger = logging.getLogger(__name__)


def column_costs(
    columns: xr.Dataset,
    air_temperature: pd.Series,
    config: dict,
    discount_rate: float,
) -> pd.DataFrame:
    """
    Cheapest feasible EGS depth per temperature column with its net capacity
    (MW_el), costs (EUR2020) and conversion efficiency.
    """
    net = 1 - config["parasitic_fraction"]
    to_eur = config["usd2021_to_eur"]
    crf = annuity(config["lifetime"], discount_rate)
    t_air = columns.bus.to_series().map(air_temperature).values + 273.15
    area = columns.area.values * columns.land_share.values

    best = None
    for depth in EGS_DEPTHS_KM:
        t_res = columns.temperature.sel(depth=depth).values
        out = egs_costs(
            t_res,
            t_air,
            depth,
            producers_per_injector=config["producers_per_injector"],
            lateral_length_m=config["lateral_length"],
            flow_derating=config["flow_derating"],
            max_temperature_c=config["max_temperature"],
            reinjection_temperature_c=config["reinjection_temperature"],
        )
        plant = out["plant_capex"] * to_eur / net
        well = out["well_capex"] * to_eur / net
        fom = out["fom"] * to_eur / net
        lcoe = ((plant + well) * crf + fom) * 1e3 / (8760 * config["availability"])
        feasible = (
            (t_res >= config["min_temperature"])
            & (out["thermal_mw"] > 0)
            & np.isfinite(lcoe)
        )
        frame = pd.DataFrame(
            {
                "bus": columns.bus.values,
                "depth": depth,
                "temperature": t_res,
                "plant_capex": plant,
                "well_capex": well,
                "fom": fom,
                "lcoe": np.where(feasible, lcoe, np.inf),
                "efficiency": out["gross_mw"] * net / out["thermal_mw"],
                "p_nom_max": out["mw_per_km2"] * net * area,
            }
        )
        if best is None:
            best = frame
        else:
            cheaper = frame.lcoe < best.lcoe
            best = best.where(~cheaper, frame)
    best = best.loc[np.isfinite(best.lcoe)]
    return best.infer_objects()


def get_capacity_factors(
    air_temperature: xr.DataArray, snapshots: pd.DatetimeIndex
) -> pd.DataFrame:
    """
    Capacity factors from the deviation of the ambient temperature from its mean,
    after Ricks et al. (Supplementary Figure 20, https://zenodo.org/records/7093330),
    linearly extrapolated beyond the figure's range.
    """
    delta_t = [-15, -10, -5, 0, 5, 10, 15, 20]
    cf = [1.17, 1.13, 1.07, 1, 0.925, 0.84, 0.75, 0.65]

    x = np.linspace(-15, 20, 200)
    y = np.interp(x, delta_t, cf)

    upper_x = np.linspace(20, 25, 50)
    m_upper = (y[-1] - y[-2]) / (x[-1] - x[-2])
    upper_y = upper_x * m_upper - x[-1] * m_upper + y[-1]

    lower_x = np.linspace(-20, -15, 50)
    m_lower = (y[1] - y[0]) / (x[1] - x[0])
    lower_y = lower_x * m_lower - x[0] * m_lower + y[0]

    x = np.hstack((lower_x, x, upper_x))
    y = np.hstack((lower_y, y, upper_y))

    temp = air_temperature.to_pandas()
    capacity_factors = pd.DataFrame(
        np.interp((temp - temp.mean()).values, x, y),
        index=temp.index,
        columns=temp.columns,
    )
    return capacity_factors.reindex(snapshots, method="nearest")


if __name__ == "__main__":
    if "snakemake" not in globals():
        from scripts._helpers import mock_snakemake

        snakemake = mock_snakemake("build_egs_potentials")

    configure_logging(snakemake)
    set_scenario_config(snakemake)

    config = snakemake.params.enhanced_geothermal
    discount_rate = snakemake.params.costs["fill_values"]["discount rate"]

    columns = xr.open_dataset(snakemake.input.columns)
    air_temperature = xr.open_dataset(snakemake.input.air_temperature)["temperature"]
    mean_air = air_temperature.mean("time").to_pandas()

    cells = column_costs(columns, mean_air, config, discount_rate)
    steps = bin_supply_curve(
        cells,
        cost_column="lcoe",
        capacity_column="p_nom_max",
        bins=config["lcoe_bins"],
        extra=(
            "plant_capex",
            "well_capex",
            "fom",
            "efficiency",
            "depth",
            "temperature",
        ),
    )
    regions = gpd.read_file(snakemake.input.regions).name
    missing = sorted(set(regions) - set(steps.bus))
    if missing:
        logger.info(
            f"No EGS potential below {config['lcoe_bins'][-1]} EUR/MWh in {missing}."
        )
    summary = steps.groupby(steps.bus.str[:2]).agg(
        gw=("p_nom_max", lambda s: s.sum() / 1e3), min_lcoe=("lcoe", "min")
    )
    logger.info(
        f"EGS potential per country (GW, cheapest step EUR/MWh):\n{summary.round(1)}"
    )
    steps.to_csv(snakemake.output.egs_potentials, index=False)

    snapshots = get_snapshots(
        snakemake.params.snapshots, snakemake.params.drop_leap_day
    )
    get_capacity_factors(air_temperature, snapshots).to_csv(
        snakemake.output.egs_capacity_factors
    )
