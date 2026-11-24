# SPDX-FileCopyrightText: 2026 Iegor Riepin
#
# SPDX-License-Identifier: MIT
"""
Derive the monthly mean French nuclear output from RTE's eCO2mix data.

One-off data preparation, deliberately not part of the Snakemake workflow. The raw
extract, the monthly mean of the ``nucleaire`` column of eCO2mix's definitive
national data, is vendored in ``data/custom/sources/``; this script converts it to
``data/custom/fr_nuclear_monthly_<year>.csv`` (MW), the seasonal shape of the
``fr_nuclear_profile`` patch. With ``--download``, the extract is first fetched from
the ODRE open-data API (no key required).

References
----------
- [RTE eCO2mix definitive national data](https://odre.opendatasoft.com/explore/dataset/eco2mix-national-cons-def/)
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlretrieve

import pandas as pd

logger = logging.getLogger(__name__)

API_URL = (
    "https://odre.opendatasoft.com/api/explore/v2.1/catalog/datasets/"
    "eco2mix-national-cons-def/records"
)


def download_raw(year: int, raw_path: Path) -> None:
    """Fetch the monthly mean nuclear output of ``year`` from the ODRE API."""
    params = urlencode(
        {
            "select": "avg(nucleaire) as nuc_mw",
            "where": f"date_heure >= date'{year}-01-01' AND date_heure < date'{year + 1}-01-01'",
            "group_by": "date_format(date_heure, 'yyyy-MM') as month",
            "order_by": "month",
            "limit": 12,
        }
    )
    logger.info("Downloading eCO2mix %d nuclear output from %s", year, API_URL)
    urlretrieve(f"{API_URL}?{params}", raw_path)


def monthly_output(raw_path: Path, year: int) -> pd.DataFrame:
    """Return the monthly mean nuclear output rows of the raw extract."""
    results = json.loads(raw_path.read_text())["results"]
    mw = pd.Series({r["month"]: r["nuc_mw"] for r in results}).sort_index()
    if list(mw.index) != [f"{year}-{m:02d}" for m in range(1, 13)]:
        raise ValueError(f"The extract does not hold the twelve months of {year}.")
    return pd.DataFrame(
        {
            "country": "FR",
            "year": year,
            "month": range(1, 13),
            "carrier_or_metric": "nuclear_generation_monthly_mean",
            "value": mw.round(1).to_numpy(),
            "unit": "MW",
            "source_id": f"rte_eco2mix_{year}_nuclear",
            "note": (
                f"Monthly mean French nuclear output {year} (eCO2mix via ODRE; shape "
                "for the fr_nuclear_profile patch normalized to the upstream annual "
                "availability factor)"
            ),
        }
    )


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    raw_path = (
        root
        / "data"
        / "custom"
        / "sources"
        / f"eco2mix_national_cons_def_{args.year}_nuclear_monthly.json"
    )
    if args.download:
        download_raw(args.year, raw_path)
    rows = monthly_output(raw_path, args.year)
    out_path = root / "data" / "custom" / f"fr_nuclear_monthly_{args.year}.csv"
    rows.to_csv(out_path, index=False)
    logger.info("Wrote 12 monthly values to %s.", out_path)


if __name__ == "__main__":
    main()
