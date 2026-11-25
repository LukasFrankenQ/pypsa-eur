# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Retirement year of existing power plants without a known retirement year."""

import numpy as np
import pandas as pd

from scripts.add_existing_baseyear import estimate_retirement_year

COSTS = pd.DataFrame({"lifetime": {"nuclear": 40.0, "CCGT": 25.0}})


def _powerplants() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Fueltype": ["nuclear", "nuclear", "CCGT", "oil"],
            "DateIn": [1980.0, 1975.0, 2000.0, 1990.0],
            "DateOut": [np.nan, 2035.0, np.nan, np.nan],
        },
        index=["reactor", "reactor with date", "gas plant", "oil plant"],
    )


def test_missing_retirement_year_uses_cost_data_lifetime():
    retirement = estimate_retirement_year(_powerplants(), COSTS, {})

    assert retirement["reactor"] == 2020
    assert retirement["gas plant"] == 2025
    # no lifetime in the cost data
    assert retirement["oil plant"] == 2020


def test_default_lifetime_overrides_cost_data_for_listed_technologies_only():
    retirement = estimate_retirement_year(_powerplants(), COSTS, {"nuclear": 60})

    assert retirement["reactor"] == 2040
    assert retirement["gas plant"] == 2025
    # a known retirement year is kept
    assert retirement["reactor with date"] == 2035
