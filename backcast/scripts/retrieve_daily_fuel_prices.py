# SPDX-FileCopyrightText: 2026 Iegor Riepin
#
# SPDX-License-Identifier: MIT
"""
Retrieve daily gas and CO2 prices into the daily fuel price CSV.

One-off data preparation, deliberately not part of the Snakemake workflow. Writes
``data/custom/daily_fuel_prices_<year>.csv`` with one row per calendar day:

- ``gas_pvb``: MIBGAS day-ahead product for delivery at the Spanish virtual balancing
  point (``GDAES_D+1``, reference price, EUR/MWh_th), by delivery day. The year's
  first day is traded in the previous year's file and takes the second day's price.
- ``co2_eua``: clearing price of the EEX primary auctions of EU allowances (EUR/tCO2),
  as collected by Instrat; days without an auction keep the last auction's price.

References
----------
- [MIBGAS data files](https://www.mibgas.es/en/file-access)
- [Instrat energy data, EU ETS prices](https://energy.instrat.pl/en/prices/eu-ets/)
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

MIBGAS_URL = (
    "https://www.mibgas.es/en/file-access/MIBGAS_Data_{year}.xlsx?path=AGNO_{year}/XLS"
)
MIBGAS_SHEET = "Trading Data PVB&VTP"
MIBGAS_PRODUCT = "GDAES_D+1"
INSTRAT_URL = "https://energy-api.instrat.pl/api/prices/co2?all=1"
INSTRAT_INDEX = "t3pa"  # EEX primary auctions of EU allowances
BROWSER = {"User-Agent": "Mozilla/5.0"}


def gas_prices(year: int) -> pd.Series:
    """Day-ahead gas price at the Spanish hub per delivery day of ``year``."""
    trades = pd.read_excel(
        MIBGAS_URL.format(year=year),
        sheet_name=MIBGAS_SHEET,
        storage_options=BROWSER,
    )
    trades.columns = [c.split("\n")[0].strip() for c in trades.columns]
    day_ahead = trades[trades["Product"] == MIBGAS_PRODUCT]
    return day_ahead.set_index("First Day Delivery")["Reference Price"].sort_index()


def co2_prices() -> pd.Series:
    """Mean clearing price of the EU allowance auctions per auction day, all years."""
    auctions = pd.read_json(INSTRAT_URL, storage_options=BROWSER)
    auctions = auctions[auctions["indeks"] == INSTRAT_INDEX]
    days = pd.to_datetime(auctions["date"]).dt.tz_localize(None).dt.normalize()
    return auctions["price"].groupby(days).mean()  # some days have two auctions


def daily_fuel_prices(year: int) -> pd.DataFrame:
    """Gas and CO2 price of every calendar day of ``year``."""
    days = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D", name="date")
    gas = gas_prices(year).reindex(days)
    if pd.isna(gas.iloc[0]):  # traded in the previous year's file
        gas.iloc[0] = gas.iloc[1]
    prices = pd.DataFrame(
        {
            "gas_pvb": gas,
            "co2_eua": co2_prices().reindex(days, method="ffill"),
        }
    )
    if prices.isna().any().any():
        raise ValueError(f"Daily fuel prices do not cover every day of {year}.")
    return prices


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2024)
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    out_path = root / "data" / "custom" / f"daily_fuel_prices_{args.year}.csv"
    prices = daily_fuel_prices(args.year)
    prices.to_csv(out_path)
    logger.info(
        "Wrote %d days to %s (mean gas %.2f EUR/MWh_th, CO2 %.2f EUR/t).",
        len(prices),
        out_path,
        prices["gas_pvb"].mean(),
        prices["co2_eua"].mean(),
    )


if __name__ == "__main__":
    main()
