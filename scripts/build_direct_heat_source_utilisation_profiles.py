# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
Build availability profiles for direct heat source utilisation (1 in regions and time steps where heat source can be utilised, 0 otherwise).
When direct utilisation is possible, heat pump COPs are set to zero (c.f. `build_cop_profiles`).
"""

import logging

import xarray as xr

from scripts._helpers import configure_logging, set_scenario_config

logger = logging.getLogger(__name__)


def get_source_temperature(heat_source_key: str, limited_heat_sources: dict) -> float:
    """
    Get the temperature of a heat source: per region where the rule passes a
    temperature file (`temp_<heat source>`), the configured constant otherwise.

    Args:
    ----
    heat_source_key: str
        The key (name) of the heat source.
    limited_heat_sources: dict
        Settings of the temperature-limited heat sources, keyed by heat source.

    Returns:
    -------
    float | xr.DataArray
        The temperature of the heat source in degrees Celsius, per region (`name`)
        if read from a file.

    Raises:
    ------
    ValueError
        If the heat source is not in `limited_heat_sources`.
    """

    # per-region source temperature (geology-based geothermal); the rule passes
    # an empty input where the source has no temperature file
    temperature_file = snakemake.input.get(f"temp_{heat_source_key}")
    if temperature_file:
        return xr.open_dataarray(temperature_file)
    if heat_source_key in limited_heat_sources:
        return limited_heat_sources[heat_source_key]["constant_temperature_celsius"]
    else:
        raise ValueError(
            f"Unknown heat source {heat_source_key}. Must be one of "
            f"{limited_heat_sources.keys()}."
        )


def get_profile(
    source_temperature: float | xr.DataArray, forward_temperature: xr.DataArray
) -> xr.DataArray:
    """
    Get the direct heat source utilisation profile.

    Args:
    ----
    source_temperature: float | xr.DataArray
        The constant temperature of the heat source in degrees Celsius. If `xarray`, indexed by `time` and `region`. If a float, it is broadcasted to the shape of `forward_temperature`.
    forward_temperature: xr.DataArray
        The central heating forward temperature profiles. If `xarray`, indexed by `time` and `region`. If a float, it is broadcasted to the shape of `return_temperature`.

    Returns:
    -------
    xr.DataArray | float
        The direct heat source utilisation profile.

    """
    # a per-region source broadcasts its own dimension first, so restore the
    # forward temperature's (time, name) order the network expects
    return xr.where(source_temperature >= forward_temperature, 1.0, 0.0).transpose(
        *forward_temperature.dims
    )


if __name__ == "__main__":
    if "snakemake" not in globals():
        from scripts._helpers import mock_snakemake

        snakemake = mock_snakemake(
            "build_direct_heat_source_utilisation_profiles",
            horizon=2030,
        )
    configure_logging(snakemake)
    set_scenario_config(snakemake)

    direct_utilisation_heat_sources: list[str] = (
        snakemake.params.direct_utilisation_heat_sources
    )

    central_heating_forward_temperature: xr.DataArray = xr.open_dataarray(
        snakemake.input.central_heating_forward_temperature_profiles
    )

    xr.concat(
        [
            get_profile(
                source_temperature=get_source_temperature(
                    heat_source_key, snakemake.params.limited_heat_sources
                ),
                forward_temperature=central_heating_forward_temperature,
            ).assign_coords(heat_source=heat_source_key)
            for heat_source_key in direct_utilisation_heat_sources
        ],
        dim="heat_source",
    ).to_netcdf(snakemake.output.direct_heat_source_utilisation_profiles)
