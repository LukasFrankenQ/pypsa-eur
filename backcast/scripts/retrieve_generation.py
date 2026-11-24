# SPDX-FileCopyrightText: 2026 Iegor Riepin
#
# SPDX-License-Identifier: MIT
"""
Retrieve measured hourly generation by technology into a CSV.

One-off data preparation, deliberately not part of the Snakemake workflow: fetches
the measured generation of Spain, Portugal and France from the Energy-Charts API
(ENTSO-E data, no key required) and writes
``data/custom/generation_hourly_<year>.csv``: one row per hour and country, in MW,
``snapshot`` being the start of the hour in UTC, as the network snapshots are.
Pumping is positive. Technologies a country does not report are zero.

References
----------
- [Energy-Charts API](https://api.energy-charts.info/)
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from urllib.request import urlopen

import pandas as pd

logger = logging.getLogger(__name__)

# the API counts days in local time, so the last UTC hour needs the next day
API_URL = (
    "https://api.energy-charts.info/public_power"
    "?country={country}&start={year}-01-01&end={next_year}-01-01"
)
COUNTRIES = ("ES", "PT", "FR")
# column -> (Energy-Charts production types, sign)
COLUMNS: dict[str, tuple[tuple[str, ...], float]] = {
    "nuclear": (("Nuclear",), 1.0),
    "hydro_reservoir": (("Hydro water reservoir",), 1.0),
    "hydro_run_of_river": (("Hydro Run-of-River",), 1.0),
    "pumped_storage": (("Hydro pumped storage",), 1.0),
    "pumping": (("Hydro pumped storage consumption",), -1.0),
    "gas": (("Fossil gas",), 1.0),
    "coal": (("Fossil hard coal",), 1.0),
    "biomass_waste": (("Biomass", "Waste", "Other renewables"), 1.0),
    "wind": (("Wind onshore", "Wind offshore"), 1.0),
    "solar": (("Solar",), 1.0),
    "load": (("Load",), 1.0),
}


def retrieve_country(country: str, year: int) -> pd.DataFrame:
    """Return the hourly generation of one country for every UTC hour of ``year``."""
    url = API_URL.format(country=country.lower(), year=year, next_year=year + 1)
    with urlopen(url, timeout=300) as response:
        raw = json.load(response)
    index = pd.to_datetime(raw["unix_seconds"], unit="s")
    types = {t["name"]: t["data"] for t in raw["production_types"]}
    measured = pd.DataFrame(types, index=index).astype(float).resample("h").mean()
    hours = pd.date_range(f"{year}-01-01", f"{year + 1}-01-01", freq="h")[:-1]
    # types the API does not report count as zero; gaps in reported ones must not
    reported = [n for names, _ in COLUMNS.values() for n in names if n in measured]
    if measured[reported].reindex(hours).isna().any().any():
        raise ValueError(f"Generation of {country} has gaps in {year}.")
    out = pd.DataFrame(
        {
            column: sign * measured.reindex(columns=list(names)).sum(axis=1)
            for column, (names, sign) in COLUMNS.items()
        }
    ).reindex(hours)
    if out.isna().any().any():
        raise ValueError(
            f"Generation of {country} does not cover every hour of {year}."
        )
    out.insert(0, "country", country)
    return out.rename_axis("snapshot")


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2024)
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    out_path = root / "data" / "custom" / f"generation_hourly_{args.year}.csv"
    generation = pd.concat(retrieve_country(c, args.year) for c in COUNTRIES)
    generation.round(0).to_csv(out_path, float_format="%.0f")
    annual = generation.groupby("country").sum(numeric_only=True) / 1e6
    logger.info(
        "Wrote %d rows to %s; TWh:\n%s", len(generation), out_path, annual.round(1)
    )


if __name__ == "__main__":
    main()
