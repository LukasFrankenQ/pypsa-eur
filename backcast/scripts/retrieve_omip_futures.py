# SPDX-FileCopyrightText: 2026 Iegor Riepin
#
# SPDX-License-Identifier: MIT
"""
Retrieve the week or day baseload futures prices of OMIP for every week or day of a year.

One-off data preparation, deliberately not part of the Snakemake workflow. OMIP, the
derivatives exchange of the Iberian electricity market (MIBEL), lists baseload futures
on the Spanish (``FTB``) and French (``FFB``) day-ahead price; a week contract pays the
average day-ahead price of one ISO week, a day contract that of one day. Its daily
bulletin (no key required) gives the end-of-day reference price of every listed
contract. What the market expected, public before delivery:

- ``--contract week``: for each week touching the year, the reference price on the last
  trading day before the week starts; writes ``data/custom/omip_week_ahead_<year>.csv``.
- ``--contract day``: for each day of the year and the first of the next (the last UTC
  hour of the year is local New Year's Day), the reference price on the latest
  bulletin at least two days before delivery (the bulletin of the day before closes
  after OMIE's day-ahead auction at noon); writes
  ``data/custom/omip_day_contract_<year>.csv``.

Prices in EUR/MWh.

References
----------
- [OMIP market data](https://www.omip.pt/en/dados-mercado)
"""

from __future__ import annotations

import argparse
import logging
import re
import time
from datetime import date, timedelta
from functools import cache
from pathlib import Path

import pandas as pd

from scripts.retrieve_security_gas_floor import download

logger = logging.getLogger(__name__)

URL = "https://www.omip.pt/en/dados-mercado?date={day}&product=EL&zone={zone}&instrument={instrument}"
INSTRUMENTS = {"ES": "FTB", "FR": "FFB"}
ROW = re.compile(
    r"Trading last day:</label> \d{4}-\d{2}-\d{2}</div>.*?</span>([A-Z]{3} [^<]+)</strong></td>(.*?)</tr>",
    re.S,
)
CELL = re.compile(r"<td[^>]*>([^<]*)</td>")
# position of the reference price of the bulletin's day among a row's cells
REFERENCE_CELL = 13
LOOKBACK_DAYS = 10
# OMIE's auction closes at noon of the day before delivery, after that day's bulletin
DAY_LEAD_DAYS = 2
WEEKDAYS = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")


@cache
def reference_prices(day: date, zone: str) -> dict[str, str]:
    """Return {contract: reference price} of one daily bulletin."""
    url = URL.format(day=day.isoformat(), zone=zone, instrument=INSTRUMENTS[zone])
    page = download(url).decode("utf-8")
    time.sleep(1)
    return {
        name.strip(): CELL.findall(rest)[REFERENCE_CELL].strip()
        for name, rest in ROW.findall(page)
    }


def week_ahead(zone: str, iso_year: int, iso_week: int) -> dict[str, object]:
    """Return the reference price of a week contract on its last trading day before delivery."""
    monday = date.fromisocalendar(iso_year, iso_week, 1)
    contract = f"{INSTRUMENTS[zone]} Wk{iso_week:02d}-{iso_year % 100:02d}"
    # Friday first: weekend bulletins carry no new prices
    for back in range(3, LOOKBACK_DAYS + 1):
        day = monday - timedelta(days=back)
        if day.weekday() >= 5:
            continue
        price = reference_prices(day, zone).get(contract, "n.a.")
        if price not in ("n.a.", ""):
            return {
                "zone": zone,
                "iso_year": iso_year,
                "iso_week": iso_week,
                "week_start": monday,
                "contract": contract,
                "trade_date": day,
                "eur_mwh": float(price.replace(",", "")),
            }
    raise ValueError(
        f"No reference price for {contract} in the {LOOKBACK_DAYS} days before {monday}."
    )


def day_contract(zone: str, day: date) -> dict[str, object]:
    """Return the reference price of a day contract on the latest bulletin before the auction."""
    contract = (
        f"{INSTRUMENTS[zone]} D {WEEKDAYS[day.weekday()]}{day.day:02d}"
        f"{day:%b}-{day.year % 100:02d}"
    )
    for back in range(DAY_LEAD_DAYS, LOOKBACK_DAYS + 1):
        trade = day - timedelta(days=back)
        if trade.weekday() >= 5:
            continue
        price = reference_prices(trade, zone).get(contract, "n.a.")
        if price not in ("n.a.", ""):
            return {
                "zone": zone,
                "day": day,
                "contract": contract,
                "trade_date": trade,
                "eur_mwh": float(price.replace(",", "")),
            }
    raise ValueError(
        f"No reference price for {contract} in the {LOOKBACK_DAYS} days before {day}."
    )


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--contract", choices=("week", "day"), default="week")
    args = parser.parse_args(argv)

    days = pd.date_range(f"{args.year}-01-01", f"{args.year}-12-31", freq="D")
    if args.contract == "week":
        weeks = sorted({tuple(d.isocalendar())[:2] for d in days})
        rows = [week_ahead(zone, y, w) for zone in INSTRUMENTS for y, w in weeks]
        name = f"omip_week_ahead_{args.year}.csv"
    else:
        local = days.union([pd.Timestamp(f"{args.year + 1}-01-01")])
        rows = [day_contract(zone, d.date()) for zone in INSTRUMENTS for d in local]
        name = f"omip_day_contract_{args.year}.csv"
    out_path = Path(__file__).resolve().parents[1] / "data" / "custom" / name
    pd.DataFrame(rows).to_csv(out_path, index=False)
    logger.info("Wrote %d %s prices to %s.", len(rows), args.contract, out_path)


if __name__ == "__main__":
    main()
