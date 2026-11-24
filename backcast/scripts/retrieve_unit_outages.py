# SPDX-FileCopyrightText: 2026 Iegor Riepin
#
# SPDX-License-Identifier: MIT
"""
Derive the hourly unavailable capacity of mainland Spanish nuclear, coal and gas plants.

One-off data preparation, deliberately not part of the Snakemake workflow. OMIE
publishes every unavailability its sellers declare (``indisp``, monthly archives of
daily snapshots, no key required): unit, start, end and the capacity still available.
The latest version of each declaration counts. A unit's unavailable capacity in an hour
is its net capacity minus the lowest capacity declared available for that hour.
Shutdowns for market reasons are not declared, so the model decides them.

Net capacities: nuclear and coal units from ``data/custom/es_unit_capacities.csv``;
combined-cycle units from the maximum capacity in OMIE's day-ahead offer headers
(``cab``), mainland units only. Writes ``data/custom/es_unit_outages_<year>.csv``
(``snapshot`` in UTC, unavailable MW per technology).

References
----------
- [OMIE unavailabilities](https://www.omie.es/es/file-access-list?parents=/Mercado%20Diario/7.%20Indisponibilidades&dir=Indisponibilidades&realdir=indisp)
- [OMIE offer headers](https://www.omie.es/es/file-access-list?parents=/Mercado%20Diario/2.%20Ofertas&dir=Cabeceras%20de%20ofertas&realdir=cab)
"""

from __future__ import annotations

import argparse
import io
import logging
import zipfile
from pathlib import Path

import pandas as pd

from scripts.retrieve_security_gas_floor import PROGRAM_URL, download

logger = logging.getLogger(__name__)

TZ = "Europe/Madrid"
# Portuguese combined-cycle plants share OMIE's offer headers
PORTUGUESE_CCGT = ("LARES", "RIBATEJO")


def archives(kind: str, year: int) -> list[zipfile.ZipFile]:
    """Download the monthly archives of one OMIE file type."""
    return [
        zipfile.ZipFile(
            io.BytesIO(download(PROGRAM_URL.format(kind=kind, year=year, month=m)))
        )
        for m in range(1, 13)
    ]


def ccgt_capacities(cab: list[zipfile.ZipFile]) -> pd.Series:
    """Return the net capacity (MW) of mainland combined cycles from the offer headers."""
    pmax = {}
    for z in cab:
        for name in z.namelist():
            for line in z.read(name).decode("latin-1").splitlines():
                label = line[17:47].strip()
                if not label.startswith("C.C.") or label.startswith(
                    tuple(f"C.C. {p}" for p in PORTUGUESE_CCGT)
                ):
                    continue
                unit = line[10:17].strip().removesuffix("R")
                pmax[unit] = max(pmax.get(unit, 0.0), float(line.split()[-4]))
    return pd.Series(pmax)


def declarations(indisp: list[zipfile.ZipFile], units: pd.Index) -> pd.DataFrame:
    """Return the latest version of every declaration for ``units`` (local times)."""
    rows = []
    for z in indisp:
        for name in sorted(
            z.namelist(), key=lambda s: (s.split(".")[0], int(s.split(".")[1]))
        ):
            for line in z.read(name).decode("latin-1").splitlines()[2:]:
                p = line.split(";")
                if len(p) < 15 or p[13] not in units:
                    continue
                rows.append(
                    {
                        "id": p[0],
                        "unit": p[13],
                        "start": pd.Timestamp(f"{p[1]}-{p[2]}-{p[3]} {p[4]}:{p[5]}"),
                        "end": pd.Timestamp(f"{p[7]}-{p[8]}-{p[9]} {p[10]}:{p[11]}"),
                        "available": float(p[14].replace(",", ".")),
                    }
                )
    return pd.DataFrame(rows).drop_duplicates("id", keep="last")


def to_utc(times: pd.Series) -> pd.Series:
    """Convert OMIE's local times to naive UTC."""
    local = times.dt.tz_localize(TZ, ambiguous=False, nonexistent="shift_forward")
    return local.dt.tz_convert("UTC").dt.tz_localize(None)


def unavailable(events: pd.DataFrame, capacity: pd.Series, year: int) -> pd.DataFrame:
    """Hourly unavailable MW per unit: capacity minus the lowest available declared."""
    hours = pd.date_range(
        f"{year}-01-01", f"{year + 1}-01-01", freq="h", inclusive="left"
    )
    start, end = (
        to_utc(events["start"]).dt.floor("h"),
        to_utc(events["end"]).dt.ceil("h"),
    )
    out = pd.DataFrame(0.0, index=hours, columns=capacity.index)
    for (_, e), s, t in zip(events.iterrows(), start, end):
        lost = min(max(capacity[e.unit] - e.available, 0.0), capacity[e.unit])
        window = (hours >= s) & (hours < t)
        out.loc[window, e.unit] = out.loc[window, e.unit].clip(lower=lost)
    return out


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2024)
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    table = pd.read_csv(root / "data" / "custom" / "es_unit_capacities.csv")
    ccgt = ccgt_capacities(archives("cab", args.year))
    capacity = pd.concat([table.set_index("omie_code")["net_mw"], ccgt])
    technology = pd.concat(
        [
            table.set_index("omie_code")["technology"],
            pd.Series("CCGT", index=ccgt.index),
        ]
    )
    events = declarations(archives("indisp", args.year), capacity.index)
    per_unit = unavailable(events, capacity, args.year)
    out = per_unit.T.groupby(technology).sum().T.round(1)
    out.index.name = "snapshot"
    out_path = root / "data" / "custom" / f"es_unit_outages_{args.year}.csv"
    out.to_csv(out_path)
    for tech, mw in out.items():
        cap = capacity[technology == tech].sum()
        logger.info(
            "%s: %.0f MW, %d declarations, %.1f TWh of %.1f TWh unavailable.",
            tech,
            cap,
            events["unit"].map(technology).eq(tech).sum(),
            mw.sum() / 1e6,
            cap * len(out) / 1e6,
        )
    logger.info("Wrote %s.", out_path)


if __name__ == "__main__":
    main()
