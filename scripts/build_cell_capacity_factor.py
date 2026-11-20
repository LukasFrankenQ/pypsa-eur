# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
Calculates the average capacity factor of a renewable technology for every
grid cell of the weather data cutout.

The quantity is the one [build_renewable_profiles][] computes in memory to
lay out generators within each region, taken over the cutout's whole time
span instead of the run's snapshots.

Outputs
-------

- `resources/cell_capacity_factor_{technology}.nc`: the per unit average
  capacity factor on the cutout's `y` and `x` coordinates.
"""

import logging

from scripts._helpers import (
    configure_logging,
    load_cutout,
    set_scenario_config,
    setup_dask,
)

logger = logging.getLogger(__name__)


if __name__ == "__main__":
    if "snakemake" not in globals():
        from scripts._helpers import mock_snakemake

        snakemake = mock_snakemake("build_cell_capacity_factor", technology="onwind")
    configure_logging(snakemake)
    set_scenario_config(snakemake)

    nprocesses = int(snakemake.threads)
    noprogress = snakemake.config["run"].get("disable_progressbar", True)
    noprogress = noprogress or not snakemake.config["atlite"]["show_progress"]
    technology = snakemake.params.technology
    params = snakemake.params.renewable
    resource = params["resource"]  # pv panel params / wind turbine params
    resource["show_progress"] = not noprogress

    tech = next(t for t in ["panel", "turbine"] if t in resource)
    models = resource[tech]
    if not isinstance(models, dict):
        models = {0: models}
    resource[tech] = models[next(iter(models))]

    correction_factor = params.get("correction_factor", 1.0)

    dask_kwargs = setup_dask(nprocesses)

    cutout = load_cutout(snakemake.input.cutout)
    func = getattr(cutout, resource.pop("method"))

    logger.info(f"Calculate average capacity factor per grid cell for {technology}")
    capacity_factor = correction_factor * func(
        capacity_factor=True,
        dask_kwargs=dask_kwargs,
        **resource,
    )

    capacity_factor.rename("capacity_factor").to_netcdf(snakemake.output[0])
