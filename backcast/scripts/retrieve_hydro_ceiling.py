# SPDX-FileCopyrightText: 2026 Iegor Riepin
#
# SPDX-License-Identifier: MIT
"""
Derive the highest output the Spanish hydro fleet has delivered from measured data.

One-off data preparation, deliberately not part of the Snakemake workflow. Installed
hydro capacity is not deliverable capacity: head falls with the reservoir level,
plants in a cascade cannot all run at full output together, releases follow
irrigation and minimum-flow rules, and units are out for maintenance. The ceiling is
the highest hourly output of all hydro plants (reservoir, run-of-river and pumped
storage) over several years, a property of the fleet, not of one year. It is written
to ``data/custom/hydro_output_ceiling.csv`` (MW, mainland Spain).

Pumped storage is reported apart only from 2022, so the ceiling is the total.

References
----------
- [Energy-Charts API](https://api.energy-charts.info/)
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from scripts.retrieve_generation import retrieve_country

logger = logging.getLogger(__name__)

HYDRO = ["hydro_reservoir", "hydro_run_of_river", "pumped_storage"]
FIRST_YEAR, LAST_YEAR = 2019, 2025  # the years of SOURCE_ID
SOURCE_ID = "energy_charts_public_power_2019_2025"


def annual_maxima(first_year: int, last_year: int) -> pd.Series:
    """Return the highest hourly Spanish hydro output (MW) of each year."""
    maxima = {
        year: retrieve_country("ES", year)[HYDRO].sum(axis=1).max()
        for year in range(first_year, last_year + 1)
    }
    return pd.Series(maxima)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2024, help="data year of the row")
    args = parser.parse_args(argv)

    maxima = annual_maxima(FIRST_YEAR, LAST_YEAR)
    by_year = "; ".join(f"{y}: {v / 1e3:.1f}" for y, v in maxima.items())
    note = (
        f"Highest hourly output of all Spanish hydro plants (reservoir + run-of-river "
        f"+ pumped storage) in {FIRST_YEAR}-{LAST_YEAR}; rounded to 10 MW. "
        f"Annual maxima in GW: {by_year}. "
        f"Derived with scripts/retrieve_hydro_ceiling.py."
    )
    row = {
        "country": "ES_peninsular",
        "year": args.year,
        "carrier_or_metric": "hydro_output_ceiling",
        "value": round(float(maxima.max()), -1),
        "unit": "MW",
        "source_id": SOURCE_ID,
        "note": note,
    }
    root = Path(__file__).resolve().parents[1]
    out_path = root / "data" / "custom" / "hydro_output_ceiling.csv"
    pd.DataFrame([row]).to_csv(out_path, index=False)
    logger.info("Wrote a ceiling of %.0f MW to %s.", row["value"], out_path)


if __name__ == "__main__":
    main()
