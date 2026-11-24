# SPDX-FileCopyrightText: 2026 Iegor Riepin
#
# SPDX-License-Identifier: MIT
"""
Retrieve the monthly output of Portuguese gas cogeneration from REN's Data Hub.

One-off data preparation, deliberately not part of the Snakemake workflow. REN
publishes a monthly electricity balance of mainland Portugal (no key required). Its
production table splits natural gas into combined-cycle plants and cogeneration; the
cogeneration row of each month is written to
``data/custom/pt_cogeneration_monthly_{year}.csv`` (GWh).

Renewable cogeneration is part of the biomass row of the same table and of the
bioenergy output the model already holds, so it is left out here.

References
----------
- [REN Data Hub, Balanço Mensal](https://datahub.ren.pt/pt/eletricidade/balanco-mensal/)
"""

from __future__ import annotations

import argparse
import logging
from io import StringIO
from pathlib import Path

import pandas as pd

from scripts.retrieve_security_gas_floor import download

logger = logging.getLogger(__name__)

BALANCE_URL = "https://datahub.ren.pt/pt/eletricidade/balanco-mensal/?date={date}"
SOURCE_ID = "ren_datahub_balanco_mensal"


def gas_cogeneration(year: int, month: int) -> tuple[float, float]:
    """Return the output of gas cogeneration (GWh) in one month and in the year to date."""
    month_end = pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)
    page = download(BALANCE_URL.format(date=month_end.date())).decode("utf-8")
    production = pd.read_html(StringIO(page), thousands=" ")[0]
    labels = production[0].astype(str).str.strip()
    # rows are nested by position: cogeneration follows the combined-cycle row
    row = labels.index[labels == "Ciclo Combinado"][0] + 1
    if labels[row] != "Cogeração":
        raise ValueError(f"Unexpected layout of the balance of {month_end.date()}.")
    return float(production.at[row, 2]), float(production.at[row, 3])


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2024)
    args = parser.parse_args(argv)

    months = range(1, 13)
    output = [gas_cogeneration(args.year, month) for month in months]
    monthly = [value for value, _ in output]
    annual = output[-1][1]
    if abs(sum(monthly) - annual) > 0.01 * annual:
        raise ValueError(
            f"Monthly values sum to {sum(monthly):.0f} GWh, REN reports {annual:.0f}."
        )
    note = (
        f"Output of natural-gas cogeneration in mainland Portugal by month of "
        f"{args.year} (REN Data Hub, Balanço Mensal, row Gás Natural / Cogeração; "
        f"REN reports {annual:.0f} GWh for the year, the rounded months sum to "
        f"{sum(monthly):.0f}). "
        f"Retrieved with scripts/retrieve_pt_cogeneration.py."
    )
    rows = pd.DataFrame(
        {
            "country": "PT",
            "year": args.year,
            "month": months,
            "carrier_or_metric": "chp_generation_monthly",
            "value": monthly,
            "unit": "GWh",
            "source_id": SOURCE_ID,
            "note": note,
        }
    )
    root = Path(__file__).resolve().parents[1]
    out_path = root / "data" / "custom" / f"pt_cogeneration_monthly_{args.year}.csv"
    rows.to_csv(out_path, index=False)
    logger.info("Wrote %.0f GWh over 12 months to %s.", sum(monthly), out_path)


if __name__ == "__main__":
    main()
