# SPDX-FileCopyrightText: 2026 Iegor Riepin
#
# SPDX-License-Identifier: MIT
"""
Derive the French natural hydro output of a year from RTE's eCO2mix data.

One-off data preparation, deliberately not part of the Snakemake workflow. The raw
extract, the sums of the ``hydraulique_fil_eau_eclusee`` (run-of-river and pondage) and
``hydraulique_lacs`` (lakes) columns of eCO2mix's definitive national data, is vendored
in ``data/custom/sources/``; this script turns it into the ``gen_hydro`` row of
``data/custom/fr_actuals.csv`` (TWh), the French target of the ``hydro_inflow`` patch.
Pumped-storage output (``hydraulique_step_turbinage``) is left out: it is not natural
inflow. eCO2mix holds one value per half hour. With ``--download``, the extract is
first fetched from the ODRE open-data API (no key required).

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
COLUMNS = ("hydraulique_fil_eau_eclusee", "hydraulique_lacs")
HOURS_PER_VALUE = 0.5


def download_raw(year: int, raw_path: Path) -> None:
    """Fetch the sums and value counts of the natural hydro columns of ``year``."""
    select = ", ".join(
        f"sum({c}) as {c}, count({c}) as n_{c}"
        for c in (*COLUMNS, "hydraulique_step_turbinage")
    )
    params = urlencode(
        {
            "select": select,
            "where": f"date_heure >= date'{year}-01-01' AND date_heure < date'{year + 1}-01-01'",
            "limit": 1,
        }
    )
    logger.info("Downloading eCO2mix %d hydro output from %s", year, API_URL)
    urlretrieve(f"{API_URL}?{params}", raw_path)


def natural_hydro_twh(raw_path: Path, year: int) -> tuple[float, dict[str, float]]:
    """Return the natural hydro output (TWh) and its parts from the raw extract."""
    result = json.loads(raw_path.read_text())["results"][0]
    hours = len(
        pd.date_range(f"{year}-01-01", f"{year + 1}-01-01", freq="h", inclusive="left")
    )
    parts = {}
    for c in COLUMNS:
        if result[f"n_{c}"] != hours / HOURS_PER_VALUE:
            raise ValueError(
                f"{c}: {result[f'n_{c}']} values, expected one per half hour."
            )
        parts[c] = result[c] * HOURS_PER_VALUE / 1e6
    return sum(parts.values()), parts


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
        / f"eco2mix_national_cons_def_{args.year}_hydro.json"
    )
    if args.download:
        download_raw(args.year, raw_path)
    total, parts = natural_hydro_twh(raw_path, args.year)
    ror, lakes = (parts[c] for c in COLUMNS)

    out_path = root / "data" / "custom" / "fr_actuals.csv"
    actuals = pd.read_csv(out_path)
    actuals = actuals[actuals["carrier_or_metric"] != "gen_hydro"]
    row = {
        "country": "FR",
        "year": args.year,
        "carrier_or_metric": "gen_hydro",
        "value": round(total, 2),
        "unit": "TWh",
        "source_id": f"rte_eco2mix_{args.year}_hydro",
        "note": (
            f"France {args.year} natural hydro output: run-of-river and pondage "
            f"{ror:.2f} + lakes {lakes:.2f} TWh (eCO2mix definitive data via ODRE; "
            "pumped-storage output excluded); target of the hydro inflow patch. "
            "Derived with scripts/retrieve_fr_hydro.py."
        ),
    }
    actuals = pd.concat([actuals, pd.DataFrame([row])], ignore_index=True)
    actuals.to_csv(out_path, index=False)
    logger.info(
        "FR natural hydro %d: %.2f TWh written to %s.", args.year, total, out_path
    )


if __name__ == "__main__":
    main()
