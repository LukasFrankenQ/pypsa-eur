# SPDX-FileCopyrightText: 2026 Iegor Riepin
#
# SPDX-License-Identifier: MIT
"""
Derive the minimum output of the Aboño 2 coal unit from OMIE's programs.

One-off data preparation, deliberately not part of the Snakemake workflow. Aboño 2
(Asturias) burns coal together with the residual blast-furnace gases of the
neighboring ArcelorMittal steelworks and supplies the industrial load of its 220 kV
zone, so the system operator keeps it running whatever the day-ahead price. The floor
is the output at which the operator schedules it most often in the program after
constraints (``pdvd``): its minimum stable output. It is written to
``data/custom/coal_floor.csv`` (MW, mainland Spain).

References
----------
- [OMIE file access](https://www.omie.es/es/file-access-list)
- [Informe de impacto ambiental, conversión a gas del grupo 2 de la Central Térmica
  de Aboño, BOE-A-2024-10144](https://www.boe.es/diario_boe/txt.php?id=BOE-A-2024-10144)
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from scripts.retrieve_security_gas_floor import SOURCE_ID, unit_program

logger = logging.getLogger(__name__)

UNIT = "ABO2"


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2024)
    args = parser.parse_args(argv)

    output = unit_program("pdvd", args.year, {UNIT})
    scheduled = output[output > 0.0].round()
    floor = float(scheduled.mode().iloc[0])
    note = (
        f"Output at which the system operator most often schedules Aboño 2 in the "
        f"program after constraints (PDVD) of {args.year}: {(scheduled == floor).sum()} "
        f"of the {len(scheduled)} hours it is scheduled (of {len(output)} hours); its "
        f"minimum stable output. Derived with scripts/retrieve_coal_floor.py."
    )
    row = {
        "country": "ES_peninsular",
        "year": args.year,
        "carrier_or_metric": "coal_floor",
        "value": floor,
        "unit": "MW",
        "source_id": SOURCE_ID,
        "note": note,
    }
    root = Path(__file__).resolve().parents[1]
    out_path = root / "data" / "custom" / "coal_floor.csv"
    pd.DataFrame([row]).to_csv(out_path, index=False)
    logger.info("Wrote a floor of %.0f MW to %s.", floor, out_path)


if __name__ == "__main__":
    main()
