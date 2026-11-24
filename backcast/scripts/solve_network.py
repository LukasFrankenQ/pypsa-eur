# SPDX-FileCopyrightText: 2026 Iegor Riepin
#
# SPDX-License-Identifier: MIT
"""
Solve the dispatch of a prepared base network.

Takes the base network from ``prepare_network.py`` (frozen fleet), adds high-cost
load shedding on every AC bus and solves the linear dispatch problem with the
configured LP solver (HiGHS by default). Start-up costs enter in linear form: each
MW a plant adds over the previous hour counts as a MW started. The nodal marginal
prices of the solved network are the model's electricity prices.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

import pandas as pd
import pypsa

logger = logging.getLogger(__name__)

LOAD_SHEDDING_COST: float = 10000.0
# Per-solver options passed to linopy; HiGHS dual simplex is the default. The thread
# count comes from the Snakemake rule.
SOLVER_DEFAULTS: dict[str, dict[str, Any]] = {
    "highs": {"solver": "simplex"},
    "gurobi": {
        "method": 2,
        "crossover": 0,
        "BarConvTol": 1.0e-5,
        "Seed": 123,
        "AggFill": 0,
        "PreDual": 0,
        "GURO_PAR_BARDENSETHRESH": 200,
    },
}


def ensure_load_shedding(n: pypsa.Network, cost: float = LOAD_SHEDDING_COST) -> None:
    """Add high-cost diagnostic load-shedding generators (carrier 'load') on every AC bus."""
    if "load" not in n.carriers.index:
        n.add("Carrier", "load", co2_emissions=0.0)
    ac_buses = n.buses.index[n.buses["carrier"] == "AC"]
    covered = set(n.generators.loc[n.generators["carrier"] == "load", "bus"])
    missing = pd.Index([b for b in ac_buses if b not in covered])
    if missing.empty:
        return
    n.add(
        "Generator",
        missing,
        suffix=" load shedding",
        bus=missing,
        carrier="load",
        marginal_cost=float(cost),
        p_nom=1.0e6,
        p_nom_extendable=False,
    )
    logger.info(
        "Added load shedding on %d AC buses (%.0f EUR/MWh).", len(missing), cost
    )


def add_start_up_costs(n: pypsa.Network, snapshots: pd.Index) -> None:
    """
    Charge the start-up costs of thermal plants in linear form.

    A start-up cost is paid per MW of capacity started (EUR/MW), not per MWh. Unit
    commitment would track which capacity is on; to keep the problem an LP, the
    capacity started in an hour is taken to be the increase of a plant's output
    over the previous hour. That is exact when a plant starts and goes to full
    output, too low when it starts to part load, and too high when a running
    plant ramps up. The cost makes plants reluctant to cycle, so the price rises
    above their fuel cost at the peak and falls below it in the hours before one.
    """
    plants = n.generators.index[n.generators["start_up_cost"] > 0.0]
    if plants.empty:
        return
    per_mw = (
        n.generators.loc[plants, "start_up_cost"] / n.generators.loc[plants, "p_nom"]
    ).rename_axis("name")
    output = n.model["Generator-p"].sel(name=plants)
    increase = n.model.add_variables(
        lower=0.0, coords=output.coords, name="Generator-p_increase"
    )
    definition = increase - output + output.shift(snapshot=1)
    n.model.add_constraints(
        definition.sel(snapshot=snapshots[1:]) >= 0.0,
        name="Generator-p_increase-definition",
    )
    n.model.objective = n.model.objective + (increase * per_mw.to_xarray()).sum()
    logger.info("Start-up costs charged on %d plants.", len(plants))


def solve_network(
    n: pypsa.Network, solver_name: str = "highs", threads: int = 2
) -> pypsa.Network:
    """Solve the dispatch of ``n`` in place and return it."""
    if solver_name not in SOLVER_DEFAULTS:
        raise ValueError(
            f"Unsupported solver '{solver_name}'; expected one of {sorted(SOLVER_DEFAULTS)}."
        )
    ensure_load_shedding(n)
    status, condition = n.optimize(
        solver_name=solver_name,
        solver_options={**SOLVER_DEFAULTS[solver_name], "threads": threads},
        io_api="direct",
        extra_functionality=add_start_up_costs,
    )
    if status != "ok":
        raise RuntimeError(f"Optimization failed: {status} / {condition}")
    return n


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", type=Path, required=True, help="Base network.")
    parser.add_argument("--output", type=Path, required=True, help="Solved network.")
    parser.add_argument("--solver", default="highs", choices=sorted(SOLVER_DEFAULTS))
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args(argv)

    n = solve_network(
        pypsa.Network(str(args.network)), solver_name=args.solver, threads=args.threads
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    n.export_to_netcdf(str(args.output))
    logger.info("Solved: objective %.4e -> %s", float(n.objective), args.output)


if __name__ == "__main__":
    main()
