# SPDX-FileCopyrightText: 2026 Iegor Riepin
#
# SPDX-License-Identifier: MIT
"""Tests for the price back-cast: OMIE retrieval, the dispatch solve and its prices."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pypsa
import pytest

from scripts.plot_price_backcast import (
    main,
    model_series,
    naive_forecast,
    scorecard,
)
from scripts.retrieve_omie_prices import parse_day
from scripts.solve_network import solve_network
from scripts.validate_network import es_hourly_price


def _omie_file(day: str, hours: int) -> str:
    y, m, d = day.split("-")
    rows = [f"{y};{m};{d};{h};{h}.5;{h};" for h in range(1, hours + 1)]
    return "\n".join(["MARGINALPDBC;", *rows, "*"])


@pytest.mark.parametrize(
    ("day", "hours", "first_utc"),
    [
        ("2024-01-15", 24, "2024-01-14 23:00"),
        ("2024-03-31", 23, "2024-03-30 23:00"),  # clocks go forward
        ("2024-10-27", 25, "2024-10-26 22:00"),  # clocks go back
    ],
)
def test_parse_day_maps_local_hours_to_utc(
    day: str, hours: int, first_utc: str
) -> None:
    prices = parse_day(_omie_file(day, hours), pd.Timestamp(day))
    expected = pd.date_range(first_utc, periods=hours, freq="h")
    assert prices.index.equals(expected)
    assert prices["price_es"].tolist() == list(range(1, hours + 1))
    assert prices["price_pt"].iloc[0] == 1.5


def test_parse_day_rejects_missing_hours() -> None:
    with pytest.raises(ValueError, match="Unexpected hours"):
        parse_day(_omie_file("2024-01-15", 20), pd.Timestamp("2024-01-15"))


@pytest.fixture
def solved(toy_base: pypsa.Network) -> pypsa.Network:
    """The toy network with time-varying loads, as built networks carry them, solved."""
    loads = toy_base.loads.index
    toy_base.loads_t.p_set = pd.DataFrame(
        500.0, index=toy_base.snapshots, columns=loads
    )
    return solve_network(toy_base)


def test_solved_price_is_the_marginal_plant(solved: pypsa.Network) -> None:
    n = solved
    price, load = es_hourly_price(n)
    assert (load == 1000.0).all()
    assert price.max() == pytest.approx(70.0)  # CCGT sets the price when it runs
    assert n.generators_t.p.filter(like="load shedding").sum().sum() == 0.0


def test_model_series_keep_gaps_empty(solved: pypsa.Network) -> None:
    n = solved
    n.set_snapshots(n.snapshots.delete(range(24, 30)))  # six hours without snapshots
    hourly = model_series(n)
    price, _ = es_hourly_price(n)
    assert len(hourly) == 48
    assert hourly.iloc[24:30].isna().all().all()
    assert hourly["price"].dropna().equals(price.rename("price").rename_axis(None))


def test_scorecard_of_a_perfect_and_a_shifted_model(solved: pypsa.Network) -> None:
    model = model_series(solved)
    perfect = scorecard(model, model["price"], model)
    prices = perfect.dropna()
    assert (prices["model"] == prices["omie"]).all()
    assert perfect.loc[["mae", "rmse"], "model"].tolist() == [0.0, 0.0]

    shifted = scorecard(model, model["price"] + 10.0, model)
    assert shifted.at["mean", "omie"] - shifted.at["mean", "model"] == pytest.approx(
        10.0
    )
    assert shifted.at["solar_capture", "omie"] - shifted.at[
        "solar_capture", "model"
    ] == pytest.approx(10.0)
    assert shifted.at["mae", "model"] == pytest.approx(10.0)
    assert shifted.at["daily_spread", "omie"] == pytest.approx(
        shifted.at["daily_spread", "model"]
    )


def test_naive_forecast_repeats_yesterday_midweek_and_last_week_otherwise() -> None:
    hours = pd.date_range("2024-01-01", periods=14 * 24, freq="h")  # starts on a Monday
    price = pd.Series(range(len(hours)), index=hours, dtype=float)
    lag = price - naive_forecast(price)
    assert (
        lag["2024-01-01"].isna().all() and lag["2024-01-06":"2024-01-07"].isna().all()
    )
    assert (lag["2024-01-02":"2024-01-05"] == 24.0).all()
    assert (lag["2024-01-09":"2024-01-12"] == 24.0).all()  # Tuesday to Friday
    assert (lag["2024-01-08"] == 168.0).all() and (lag["2024-01-13":] == 168.0).all()


def test_rmae_is_the_error_relative_to_the_naive_forecast() -> None:
    hours = pd.date_range("2024-01-01", periods=14 * 24, freq="h")
    omie = pd.Series(range(len(hours)), index=hours, dtype=float)
    model = pd.DataFrame({"price": omie + 12.0, "load": 1.0, "solar": 1.0, "wind": 1.0})
    card = scorecard(model, omie, model)
    naive_mae = (
        24.0 * 8 + 168.0 * 3
    ) / 11  # 8 midweek days and 3 others have a forecast
    assert card.at["naive_mae", "omie"] == pytest.approx(naive_mae)
    assert card.at["rmae", "model"] == pytest.approx(12.0 / naive_mae)


def test_omie_capture_price_is_weighted_by_measured_output(
    solved: pypsa.Network,
) -> None:
    model = model_series(solved)
    omie = pd.Series(range(len(model)), index=model.index, dtype=float)
    measured = model.assign(solar=0.0)
    measured.loc[measured.index[5], "solar"] = 1.0  # all measured solar in one hour
    card = scorecard(model, omie, measured)
    assert card.at["solar_capture", "omie"] == 5.0


@pytest.mark.parametrize(
    ("omie_hours", "measured_hours", "match"),
    [(slice(1, None), slice(None), "OMIE"), (slice(None), slice(1, None), "Measured")],
)
def test_scorecard_needs_omie_and_measured_output_in_every_model_hour(
    solved: pypsa.Network, omie_hours: slice, measured_hours: slice, match: str
) -> None:
    model = model_series(solved)
    with pytest.raises(ValueError, match=match):
        scorecard(model, model["price"].iloc[omie_hours], model.iloc[measured_hours])


def test_price_figure_and_scorecard_are_written(
    solved: pypsa.Network, tmp_path: Path
) -> None:
    n = solved
    n.export_to_netcdf(tmp_path / "solved.nc")
    omie = pd.DataFrame({"price_es": 50.0, "price_pt": 50.0}, index=n.snapshots)
    omie.rename_axis("snapshot").to_csv(tmp_path / "omie.csv")
    generation = pd.DataFrame(
        {"country": "ES", "load": 1.0, "solar": 1.0, "wind": 1.0}, index=n.snapshots
    )
    generation.rename_axis("snapshot").to_csv(tmp_path / "generation.csv")
    args = {
        "--network": tmp_path / "solved.nc",
        "--prices": tmp_path / "omie.csv",
        "--generation": tmp_path / "generation.csv",
        "--output": tmp_path / "p.pdf",
        "--output-scorecard": tmp_path / "card.csv",
    }
    main([str(a) for pair in args.items() for a in pair] + ["--months", "1"])
    assert (tmp_path / "p.pdf").stat().st_size > 0
    card = pd.read_csv(tmp_path / "card.csv", index_col="metric")
    assert card.at["mean", "omie"] == 50.0
