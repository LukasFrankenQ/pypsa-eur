# SPDX-FileCopyrightText: : 2020-2024 The PyPSA-Eur Authors
#
# SPDX-License-Identifier: MIT
"""
Build geothermal heat source potentials for district heating per onshore region.

Two sources (`sector.district_heating.limited_heat_sources.geothermal.source`):

``geology`` (default)
    Heat-only doublets on the Limberger et al. temperature model
    (`build_geothermal_columns`). For each temperature column the cheapest depth
    whose wellhead temperature reaches `min_temperature` is chosen; doublets above
    `hydrothermal_max_depth` produce from a natural aquifer, deeper ones are
    stimulated. A doublet extracts flow x c_p x (T_wellhead - T_reinjection) and
    costs two wells on the ThermoGIS drilling curve plus pumps (plus stimulation),
    as parametrised in the TU Delft module_geothermal (Egberink, Limberger et al.
    2026), scaled so that a reference Dutch doublet costs what realised projects
    cost (PBL SDE++ 2026 deep geothermal, 12-20 MW_th: 1,719 EUR/kW_th, fixed O&M
    118 EUR/kW_th/yr, variable O&M 5.3 EUR/MWh_th, converted to EUR2020). Doublets
    sit only where heat is used: inside the buffered Fraunhofer district-heating
    areas (Manz et al. 2024), capped at their demand over the full-load hours, in
    the countries those areas cover; elsewhere on the urban share of each weather
    cell, its urban population over `urban_density`. Columns are
    grouped per region into steps of levelised cost (`lcoh_bins`), and the region's
    source temperature is the capacity-weighted wellhead temperature.

``manz``
    The hydrothermal supply potentials of Manz et al. 2024 on LAU level at the
    `constant_temperature_celsius` scenario (65 or 85 °C), split onto the onshore
    regions by area and divided by the full-load hours.

Inputs
------
- `resources/<run_name>/onshore_regions.geojson`
- `resources/<run_name>/geothermal_columns.nc` (geology)
- `data/lau_regions.zip`, `data/isi_heat_utilisation_potentials.xlsx` (manz)

Outputs
-------
- `resources/<run_name>/heat_source_power_geothermal.csv`: technical potential per
  region (MW_th)
- `resources/<run_name>/heat_source_steps_geothermal.csv` (geology): one row per
  region and step with `p_nom_max` (MW_th), `capex` (EUR/kW_th), `fom`
  (EUR/kW_th/yr), `vom` (EUR/MWh_th), `lifetime`, `lcoh` (EUR/MWh_th), `depth` and
  `temperature`, all EUR2020
- `resources/<run_name>/temp_geothermal.nc` (geology): source temperature per region

Sources
-------
- Manz et al. 2024: "Spatial analysis of renewable and excess heat potentials for climate-neutral district heating in Europe", Renewable Energy, vol. 224, no. 120111, https://doi.org/10.1016/j.renene.2024.120111
- Limberger et al. 2014, Geothermal Energy Science 2:55; Zenodo 22149195
- Egberink, Limberger et al. 2026, module_geothermal, https://github.com/Yegberink/module_geothermal (Apache-2.0)
- PBL 2026, Eindadvies basisbedragen SDE++ 2026, chapter 11 (deep geothermal), https://www.pbl.nl/publicaties/eindadvies-basisbedragen-sde-2026
"""

import logging

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr

from scripts._helpers import configure_logging, set_scenario_config
from scripts.lib.geothermal import (
    annuity,
    bin_supply_curve,
    doublet_costs,
    wellbore_temperature_loss,
)

logger = logging.getLogger(__name__)

ISI_TEMPERATURE_SCENARIOS = {
    65: "low_temperatures",
    85: "high_temperatures",
}
FULL_LOAD_HOURS = 4000
# 3000 for petrothermal

PYPSA_EUR_UNIT = "MWh"

GEOTHERMAL_SOURCE = (
    "Hydrothermal "  # trailing space for hydrothermal necessary to get correct column
)


def get_unit_conversion_factor(
    input_unit: str,
    output_unit: str,
    unit_scaling: dict = {"Wh": 1, "kWh": 1e3, "MWh": 1e6, "GWh": 1e9, "TWh": 1e12},
) -> float:
    """
    Get the unit conversion factor between two units.

    Parameters
    ----------
    input_unit : str
        Input unit. Must be one of the keys in `unit_scaling`.
    output_unit : str
        Output unit. Must be one of the keys in `unit_scaling`.
    unit_scaling : dict, optional
        Dictionary of unit scaling factors. Default: {"Wh": 1, "kWh": 1e3, "MWh": 1e6, "GWh": 1e9, "TWh": 1e12}.
    """

    if input_unit not in unit_scaling.keys():
        raise ValueError(
            f"Input unit {input_unit} not allowed. Must be one of {unit_scaling.keys()}"
        )
    elif output_unit not in unit_scaling.keys():
        raise ValueError(
            f"Output unit {output_unit} not allowed. Must be one of {
                unit_scaling.keys()
            }"
        )

    return unit_scaling[input_unit] / unit_scaling[output_unit]


def identify_non_covered_regions(
    onshore_regions: gpd.GeoDataFrame, heat_source_power: pd.DataFrame
) -> pd.Index:
    """
    Identify regions without heat source power data.

    Parameters
    ----------
    onshore_regions : gpd.GeoDataFrame
        GeoDataFrame of the onshore regions, indexed by region name.
    heat_source_power : pd.DataFrame
        Heat source power data, indexed by region name.

    Returns
    -------
    pd.Index
        Index of regions that have no heat source power data.
    """
    return onshore_regions.index.difference(heat_source_power.index)


def get_heat_source_power(
    onshore_regions: gpd.GeoDataFrame,
    supply_potentials: gpd.GeoDataFrame,
    lau_regions: gpd.GeoDataFrame,
    full_load_hours: float,
    input_unit: str,
    output_unit: str = "MWh",
    ignore_missing_regions: bool = False,
) -> pd.DataFrame:
    """
    Get the heat source power from supply potentials.

    Note
    ----
    Broadcasts to repeat constant heat source power across snapshots.

    Parameters
    ----------
    onshore_regions : gpd.GeoDataFrame
        GeoDataFrame of the onshore regions.
    supply_potentials : gpd.GeoDataFrame
        GeoDataFrame of the heat source supply potentials.
    lau_regions : gpd.GeoDataFrame
        LAU regions indexed by GISCO_ID.
    full_load_hours : float
        Full load hours assumed in the supply potential computation. Used to scale the supply potentials to technical potentials.
    input_unit : str
        Unit of the supply potentials. Used to convert to the output unit.
    output_unit : str, optional
        Unit of the technical potentials. Default: "MWh".

    Returns
    -------
    pd.DataFrame
        Heat source power in the onshore regions. Indexed by name (onshore region).
    """

    unit_conversion_factor = get_unit_conversion_factor(
        input_unit=input_unit,
        output_unit=output_unit,
    )
    scaling_factor = unit_conversion_factor / full_load_hours

    heat_potentials_in_lau = gpd.GeoDataFrame(
        pd.Series(supply_potentials).rename("geothermal").to_frame(),
        geometry=lau_regions.geometry[supply_potentials.index],
        crs=lau_regions.crs,
    )

    # split each LAU's potential onto the regions by area, so an LAU on a border is
    # not counted in full in each region it touches
    lau = heat_potentials_in_lau.to_crs(3035)
    lau["lau_area"] = lau.area
    pieces = gpd.overlay(
        lau.reset_index(names="lau"),
        onshore_regions.to_crs(3035).reset_index()[["name", "geometry"]],
        how="intersection",
    )
    share = pieces.area / pieces["lau_area"]
    heat_potentials_in_onshore_regions_aggregated = (
        pieces[["geothermal"]].mul(share, axis=0).groupby(pieces["name"]).sum()
    )

    heat_source_power = heat_potentials_in_onshore_regions_aggregated * scaling_factor

    non_covered_regions = identify_non_covered_regions(
        onshore_regions, heat_source_power
    )

    not_eu_27 = [
        "GB",
        "UA",
        "MD",
        "AL",
        "RS",
        "BA",
        "ME",
        "MK",
        "XK",
    ]

    if not non_covered_regions.empty:
        if all(non_covered_regions.str.contains("|".join(not_eu_27))):
            if ignore_missing_regions:
                logger.warning(
                    f"The onshore regions outside EU 27 ({non_covered_regions.to_list()}) have no heat source power. Filling with zeros."
                )
                heat_source_power = heat_source_power.reindex(
                    onshore_regions.index, fill_value=0
                )
            else:
                raise ValueError(
                    f"The onshore regions outside EU 27 {non_covered_regions.to_list()} have no heat source power. Set the ignore_missing_regions parameter in the config to true if you want to include these countries in your analysis despite missing geothermal data."
                )
        else:
            raise ValueError(
                f"The onshore regions {non_covered_regions.to_list()} have no heat source power. The pre-processing of the potential data might be faulty."
            )

    return heat_source_power


def doublet_calibration(config: dict) -> float:
    """
    Factor that scales the modelled doublet investment to the reference doublet
    (`calibration`, PBL SDE++ deep geothermal, converted to EUR2020).
    """
    ref = config["calibration"]
    out = doublet_costs(
        ref["production_temperature"] + wellbore_temperature_loss(ref["depth"]),
        ref["depth"],
        stimulated=False,
        reinjection_temperature_c=config["reinjection_temperature"],
        flow_hydrothermal=config["flow_hydrothermal"],
        flow_stimulated=config["flow_stimulated"],
        pump_cost=config["pump_cost"],
        stimulation_cost=config["stimulation_cost"],
        drilling=config["drilling_cost"],
    )
    modelled = float(out["capex"] / (out["thermal_mw"] * 1e3))  # EUR/kW_th
    return ref["investment"] * config["price_level_to_eur2020"] / modelled


def geology_heat_cells(columns: xr.Dataset, config: dict) -> pd.DataFrame:
    """
    Cheapest heat-only doublet per temperature column that reaches
    `min_temperature`, with its technical potential (MW_th) where heat is used.

    Investment is the doublet model scaled by `doublet_calibration`; fixed and
    variable O&M are the PBL values in EUR2020.
    """
    crf = annuity(config["lifetime"], config.get("discount_rate", 0.07))
    flh = config["full_load_hours"]
    spacing = config["well_spacing"]
    scale = doublet_calibration(config)
    to_eur2020 = config["price_level_to_eur2020"]
    fom = config["fixed_om"] * to_eur2020
    vom = config["variable_om"] * to_eur2020

    dh_area = columns.dh_area.to_series().values
    has_dh = ~np.isnan(dh_area)
    urban_share = np.clip(
        columns.urban_population.values
        * 1e3
        / config["urban_density"]
        / columns.cell_area.values,
        0.0,
        1.0,
    )
    eligible_area = np.where(
        has_dh, np.nan_to_num(dh_area), columns.area.values * urban_share
    )
    doublets = eligible_area / (2 * spacing**2)
    cap = np.where(has_dh, columns.dh_cap.to_series().values, np.inf)

    best = None
    for depth in config["depths"]:
        t_res = columns.temperature.sel(depth=depth).values
        out = doublet_costs(
            t_res,
            depth,
            stimulated=depth > config["hydrothermal_max_depth"],
            reinjection_temperature_c=config["reinjection_temperature"],
            flow_hydrothermal=config["flow_hydrothermal"],
            flow_stimulated=config["flow_stimulated"],
            pump_cost=config["pump_cost"],
            stimulation_cost=config["stimulation_cost"],
            drilling=config["drilling_cost"],
        )
        with np.errstate(divide="ignore", invalid="ignore"):
            capex = out["capex"] * scale / (out["thermal_mw"] * 1e3)  # EUR/kW_th
            lcoh = (capex * crf + fom) * 1e3 / flh + vom
        feasible = (
            (out["production_temperature"] >= config["min_temperature"])
            & (out["thermal_mw"] > 0)
            & np.isfinite(lcoh)
        )
        frame = pd.DataFrame(
            {
                "bus": columns.bus.values,
                "depth": depth,
                "temperature": out["production_temperature"],
                "capex": capex,
                "lcoh": np.where(feasible, lcoh, np.inf),
                "p_nom_max": np.minimum(doublets * out["thermal_mw"], cap),
            }
        )
        if best is None:
            best = frame
        else:
            cheaper = frame.lcoh < best.lcoh
            best = best.where(~cheaper, frame)
    best = best.loc[np.isfinite(best.lcoh) & (best.p_nom_max > 0)]
    return best.infer_objects()


def geology_heat_steps(
    columns: xr.Dataset, config: dict
) -> tuple[pd.DataFrame, pd.Series]:
    """
    Supply-curve steps of heat-only doublets per region and the regions'
    capacity-weighted source temperature.
    """
    cells = geology_heat_cells(columns, config)
    steps = bin_supply_curve(
        cells,
        cost_column="lcoh",
        capacity_column="p_nom_max",
        bins=config["lcoh_bins"],
        extra=("capex", "depth", "temperature"),
    )
    steps["fom"] = config["fixed_om"] * config["price_level_to_eur2020"]
    steps["vom"] = config["variable_om"] * config["price_level_to_eur2020"]
    steps["lifetime"] = config["lifetime"]
    temperature = (
        steps.temperature.mul(steps.p_nom_max).groupby(steps.bus).sum()
        / steps.p_nom_max.groupby(steps.bus).sum()
    )
    return steps, temperature


if __name__ == "__main__":
    if "snakemake" not in globals():
        from scripts._helpers import mock_snakemake

        snakemake = mock_snakemake("build_geothermal_heat_potential")

    configure_logging(snakemake)
    set_scenario_config(snakemake)

    # get onshore regions and index them by region name
    onshore_regions = gpd.read_file(snakemake.input.onshore_regions).to_crs("EPSG:4326")
    onshore_regions.index = onshore_regions.name
    onshore_regions.drop(columns=["name"], inplace=True)

    if snakemake.params.source == "geology":
        config = snakemake.params.geology | {
            "discount_rate": snakemake.params.discount_rate
        }
        steps, temperature = geology_heat_steps(
            xr.open_dataset(snakemake.input.columns), config
        )
        steps.to_csv(snakemake.output.heat_source_steps, index=False)
        power = (
            steps.groupby("bus")
            .p_nom_max.sum()
            .reindex(onshore_regions.index, fill_value=0.0)
        )
        power.rename_axis("name").to_frame("geothermal").to_csv(
            snakemake.output.heat_source_power
        )
        # regions without potential get the mean temperature: it only sets COPs of
        # a heat pump that cannot draw any heat there
        temperature = temperature.reindex(
            onshore_regions.index, fill_value=temperature.mean()
        )
        xr.DataArray(
            temperature.values,
            dims="name",
            coords={"name": temperature.index.values},
            name="temperature",
        ).to_netcdf(snakemake.output.heat_source_temperature)
        summary = steps.groupby(steps.bus.str[:2]).agg(
            gw=("p_nom_max", lambda x: x.sum() / 1e3), min_lcoh=("lcoh", "min")
        )
        logger.info(
            f"Geothermal heat potential per country (GW_th, cheapest step EUR/MWh_th):\n"
            f"{summary.round(1)}"
        )
    else:
        # get LAU regions and index them by LAU-ID
        lau_regions = gpd.read_file(
            f"{snakemake.input.lau_regions}!LAU_RG_01M_2019_3035.geojson",
            crs="EPSG:3035",
        ).to_crs("EPSG:4326")
        lau_regions.index = lau_regions.GISCO_ID

        # temperature scenario that was assumed by Manz et al. when computing potentials is 65C (default) or 85C
        this_temperature_scenario = ISI_TEMPERATURE_SCENARIOS[
            snakemake.params.constant_temperature_celsius
        ]

        # get heat potentials, index them by LAU-ID and get the geothermal potentials
        isi_heat_potentials = pd.read_excel(
            snakemake.input.isi_heat_potentials,
            sheet_name="Matching_results",
            index_col=0,
            header=[0, 1],
        )
        input_unit = isi_heat_potentials[
            (
                "Unnamed: 2_level_0",
                "Unit",
            )
        ].iloc[0]
        geothermal_supply_potentials = isi_heat_potentials[
            (
                f"Supply_potentials_{this_temperature_scenario}",
                GEOTHERMAL_SOURCE,
            )
        ].drop(index="Total")

        # check if all LAU regions in ISI heat potentials are present in LAU Regions data
        if not geothermal_supply_potentials.index.isin(lau_regions.index).all():
            raise ValueError(
                "Some LAU regions in ISI heat potentials are missing from the LAU Regions data."
            )

        # get heat source power by mapping heat potentials to onshore regions and scaling to from supply potentials to technical potentials
        heat_source_power = get_heat_source_power(
            onshore_regions=onshore_regions,
            supply_potentials=geothermal_supply_potentials,
            lau_regions=lau_regions,
            full_load_hours=FULL_LOAD_HOURS,
            input_unit=input_unit,
            ignore_missing_regions=snakemake.params.ignore_missing_regions,
        )

        heat_source_power.to_csv(snakemake.output.heat_source_power)
        # the Manz potentials come as one step at the constant temperature
        pd.DataFrame(
            columns=["bus", "step", "p_nom_max", "capex", "fom", "vom", "lifetime"]
        ).to_csv(snakemake.output.heat_source_steps, index=False)
        xr.DataArray(
            np.full(
                len(onshore_regions), snakemake.params.constant_temperature_celsius
            ),
            dims="name",
            coords={"name": onshore_regions.index.values},
            name="temperature",
        ).to_netcdf(snakemake.output.heat_source_temperature)
