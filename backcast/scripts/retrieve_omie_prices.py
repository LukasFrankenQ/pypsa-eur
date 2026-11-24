# SPDX-FileCopyrightText: 2026 Iegor Riepin
#
# SPDX-License-Identifier: MIT
"""
Retrieve OMIE's hourly day-ahead prices into the price-benchmark CSV.

One-off data preparation, deliberately not part of the Snakemake workflow: fetches
the daily ``marginalpdbc`` files of the Iberian market operator (no key required)
and writes ``data/custom/omie_day_ahead_<year>.csv`` (columns ``snapshot``,
``price_es``, ``price_pt``; EUR/MWh; ``snapshot`` is the start of the hour in UTC,
as the network snapshots are). OMIE numbers the hours of each local day
(Europe/Madrid), with 23 or 25 hours on the clock-change days.

References
----------
- [OMIE day-ahead market prices](https://www.omie.es/es/file-access-list?parents=/Mercado%20Diario/1.%20Precios&dir=Precios%20horarios%20del%20mercado%20diario%20en%20Espa%C3%B1a&realdir=marginalpdbc)
"""

from __future__ import annotations

import argparse
import logging
from io import StringIO
from pathlib import Path

import pandas as pd

from scripts.retrieve_security_gas_floor import download

logger = logging.getLogger(__name__)

FILE_URL = (
    "https://www.omie.es/es/file-download"
    "?parents=marginalpdbc&filename=marginalpdbc_{day:%Y%m%d}.1"
)
TZ = "Europe/Madrid"
COLUMNS = ["year", "month", "day", "hour", "price_pt", "price_es"]


def parse_day(text: str, day: pd.Timestamp) -> pd.DataFrame:
    """Return one local day's prices from a ``marginalpdbc`` file, indexed in UTC."""
    rows = [line for line in text.splitlines() if line[:4].isdigit()]
    df = pd.read_csv(StringIO("\n".join(rows)), sep=";", header=None, usecols=range(6))
    df.columns = COLUMNS
    if list(df["hour"]) != list(range(1, len(df) + 1)) or len(df) not in (23, 24, 25):
        raise ValueError(f"Unexpected hours in the OMIE file of {day:%Y-%m-%d}.")
    local = pd.date_range(day, periods=len(df), freq="h", tz=TZ)
    df.index = local.tz_convert("UTC").tz_localize(None).rename("snapshot")
    return df[["price_es", "price_pt"]]


def download_day(day: pd.Timestamp) -> pd.DataFrame:
    """Fetch and parse the prices of one local day, retrying when throttled."""
    return parse_day(download(FILE_URL.format(day=day)).decode("latin-1"), day)


def retrieve_prices(year: int) -> pd.DataFrame:
    """Return the hourly prices of every UTC hour of ``year``."""
    # the last UTC hour of the year belongs to 1 January of the next local year
    days = pd.date_range(f"{year}-01-01", f"{year + 1}-01-01", freq="D")
    logger.info("Downloading %d OMIE day-ahead files for %d.", len(days), year)
    prices = pd.concat(map(download_day, days)).sort_index()
    prices = prices[prices.index.year == year]
    hours = pd.date_range(f"{year}-01-01", f"{year + 1}-01-01", freq="h")[:-1]
    if not prices.index.equals(hours):
        raise ValueError(f"OMIE prices do not cover every hour of {year}.")
    return prices


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2024)
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    out_path = root / "data" / "custom" / f"omie_day_ahead_{args.year}.csv"
    prices = retrieve_prices(args.year)
    prices.to_csv(out_path)
    logger.info(
        "Wrote %d hours to %s (mean ES price %.2f EUR/MWh).",
        len(prices),
        out_path,
        prices["price_es"].mean(),
    )


if __name__ == "__main__":
    main()
