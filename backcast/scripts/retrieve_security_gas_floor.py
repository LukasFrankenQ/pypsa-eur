# SPDX-FileCopyrightText: 2026 Iegor Riepin
#
# SPDX-License-Identifier: MIT
"""
Derive the gas output the system operator requires for security from OMIE's programs.

One-off data preparation, deliberately not part of the Snakemake workflow. OMIE
publishes two hourly schedules per unit (no key required, 90 days after delivery):

- ``pdbf``: the day-ahead program, the result of the day-ahead market plus bilateral
  contracts;
- ``pdvd``: the program after Red Eléctrica has solved the technical constraints of
  the day-ahead program (operating procedure P.O. 3.2), ordering units up where the
  market schedule fails its security criteria.

In the hours when the day-ahead program holds no Spanish gas plant, all gas in the
program after constraints is there by order of the operator. Its mean is the floor
written to ``data/custom/security_gas_floor.csv`` (MW, mainland Spain).

References
----------
- [OMIE file access](https://www.omie.es/es/file-access-list)
- [P.O. 3.2 Restricciones técnicas, version in force in 2024 (BOE-A-2024-6215, BOE-A-2024-11959)](https://ree.es/sites/default/files/2024-08/PO_3_2_BOE-A-2024-6215-BOE-A-2024-11959.pdf)
"""

from __future__ import annotations

import argparse
import logging
import time
import zipfile
from io import BytesIO
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import pandas as pd

logger = logging.getLogger(__name__)

PROGRAM_URL = (
    "https://www.omie.es/es/file-download"
    "?parents={kind}&filename={kind}_{year}{month:02d}.zip"
)
UNITS_URL = "https://www.omie.es/sites/default/files/dados/listados/LISTA_UNIDADES.XLS"
ATTEMPTS = 5  # OMIE throttles bursts of requests
SOURCE_ID = "omie_programas_pdbf_pdvd"


def download(url: str) -> bytes:
    """Fetch one file, retrying when throttled."""
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with urlopen(request, timeout=300) as response:
                return response.read()
        except (URLError, TimeoutError):
            if attempt == ATTEMPTS:
                raise
            time.sleep(10 * attempt)


def spanish_gas_units() -> set[str]:
    """Return the codes of the combined-cycle units of the Spanish zone."""
    units = pd.read_excel(BytesIO(download(UNITS_URL)), header=None, skiprows=4)
    units = units.iloc[:, [0, 5, 6]].set_axis(["unit", "zone", "technology"], axis=1)
    spanish_gas = (units["technology"] == "Ciclo Combinado") & (
        units["zone"] == "ZONA ESPAÑOLA"
    )
    codes = set(units.loc[spanish_gas, "unit"])
    # the list is the current one: units renamed since carry a trailing R
    return codes | {code[:-1] for code in codes if code.endswith("R")}


def unit_program(kind: str, year: int, units: set[str]) -> pd.Series:
    """Return the hourly output (MW) of ``units`` in one program, by day and hour."""
    days = []
    for month in range(1, 13):
        url = PROGRAM_URL.format(kind=kind, year=year, month=month)
        archive = zipfile.ZipFile(BytesIO(download(url)))
        for name in sorted(archive.namelist()):
            day = pd.read_csv(
                BytesIO(archive.read(name)),
                sep=";",
                skiprows=1,
                header=None,
                usecols=[0, 1, 2, 3, 4, 5],
                names=["year", "month", "day", "hour", "unit", "mw"],
                encoding="latin-1",
            ).dropna(subset=["unit"])
            hours = ["year", "month", "day", "hour"]
            output = day[day["unit"].isin(units)].groupby(hours)["mw"].sum()
            every_hour = pd.MultiIndex.from_frame(day[hours].drop_duplicates())
            days.append(output.reindex(every_hour, fill_value=0.0))
        logger.info("Read %s of %d-%02d.", kind, year, month)
    return pd.concat(days)


def security_floor(year: int) -> tuple[float, float]:
    """Return the floor (MW) and the share of hours it is observed in."""
    units = spanish_gas_units()
    day_ahead = unit_program("pdbf", year, units)
    after_constraints = unit_program("pdvd", year, units).reindex(
        day_ahead.index, fill_value=0.0
    )
    no_market_gas = day_ahead < 1.0
    logger.info(
        "Gas in the day-ahead program %.1f TWh, after constraints %.1f TWh.",
        day_ahead.sum() / 1e6,
        after_constraints.sum() / 1e6,
    )
    return float(after_constraints[no_market_gas].mean()), float(no_market_gas.mean())


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2024)
    args = parser.parse_args(argv)

    floor, share = security_floor(args.year)
    note = (
        f"Mean gas output in the program after constraints (PDVD) in the "
        f"{share:.0%} of hours of {args.year} when the day-ahead program (PDBF) "
        f"holds no Spanish combined-cycle unit; rounded to 10 MW. "
        f"Derived with scripts/retrieve_security_gas_floor.py."
    )
    row = {
        "country": "ES_peninsular",
        "year": args.year,
        "carrier_or_metric": "security_gas_floor",
        "value": round(floor, -1),
        "unit": "MW",
        "source_id": SOURCE_ID,
        "note": note,
    }
    root = Path(__file__).resolve().parents[1]
    out_path = root / "data" / "custom" / "security_gas_floor.csv"
    pd.DataFrame([row]).to_csv(out_path, index=False)
    logger.info("Wrote a floor of %.0f MW to %s.", row["value"], out_path)


if __name__ == "__main__":
    main()
