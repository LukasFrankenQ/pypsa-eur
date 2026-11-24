# SPDX-FileCopyrightText: 2026 Iegor Riepin
#
# SPDX-License-Identifier: MIT
"""
Retrieve the weekly energy stored in the hydro reservoirs of Spain, Portugal and France.

One-off data preparation, deliberately not part of the Snakemake workflow. Under EU
Regulation 543/2013 (Art. 16(1)(d)) every transmission system operator publishes the
aggregated weekly filling of its water reservoirs and hydro storage plants on the
ENTSO-E Transparency Platform. Energy-Charts (Fraunhofer ISE) serves the series as the
JSON behind its public filling-level chart (no key required), which is vendored in
``data/custom/sources/``; this script turns it into
``data/custom/reservoir_levels_weekly.csv`` (GWh, one row per country and week, dated
by the Monday that starts the week; the value is the week's average). With
``--download``, the JSON files are fetched first.

References
----------
- [Energy-Charts, filling level of storage reservoirs](https://energy-charts.info/charts/filling_level/chart.htm?l=en&c=ES)
- [Regulation (EU) No 543/2013, Art. 16(1)(d)](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32013R0543)
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

from scripts.retrieve_security_gas_floor import download

logger = logging.getLogger(__name__)

URL = "https://energy-charts.info/charts/filling_level/data/{code}/year_storage.json"
COUNTRIES = ("ES", "PT", "FR")
FIRST_YEAR, LAST_YEAR = 2015, 2025


def weekly_levels(raw_path: Path, country: str) -> pd.DataFrame:
    """Return the weekly stored energy (GWh) of one country from its chart JSON."""
    series = json.loads(raw_path.read_text())[0]
    weeks = pd.to_datetime(series["xAxisValues"], unit="ms")
    gwh = pd.Series(series["data"], index=weeks, dtype=float).dropna() * 1e3
    gwh = gwh[(gwh.index.year >= FIRST_YEAR - 1) & (gwh.index.year <= LAST_YEAR)]
    return pd.DataFrame(
        {"country": country, "week_start": gwh.index.date, "stored_gwh": gwh.round(1)}
    )


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    frames = []
    for country in COUNTRIES:
        code = country.lower()
        raw_path = (
            root
            / "data"
            / "custom"
            / "sources"
            / f"energy_charts_filling_level_{code}.json"
        )
        if args.download:
            raw_path.write_bytes(download(URL.format(code=code)))
        frames.append(weekly_levels(raw_path, country))
    levels = pd.concat(frames, ignore_index=True)
    out_path = root / "data" / "custom" / "reservoir_levels_weekly.csv"
    levels.to_csv(out_path, index=False)
    logger.info(
        "Wrote %d weekly levels (%s) to %s.",
        len(levels),
        ", ".join(f"{c} {levels.country.eq(c).sum()}" for c in COUNTRIES),
        out_path,
    )


if __name__ == "__main__":
    main()
