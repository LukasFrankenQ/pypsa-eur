# SPDX-FileCopyrightText: 2026 Iegor Riepin
#
# SPDX-License-Identifier: MIT
"""
Plot the modeled Spanish price of the back-cast against OMIE's day-ahead price.

A scorecard on top, then one panel per month: the hourly OMIE day-ahead price and the
model's Spanish price (nodal prices weighted by load) of a solved network, both in
UTC, with the gap between them filled by its sign. Weekends are shaded and every
panel spans 31 days. The scorecard compares the two price series over the hours the
model covers (level, capture prices, distribution, cheap and expensive hours, daily
spread, hourly fit) and is also written as a CSV, so every model change gets a number.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import matplotlib.colors as mcolors
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
import pypsa
from matplotlib.patches import Patch

from scripts.validate_network import es_hourly_price, mainland_buses

logger = logging.getLogger(__name__)

STYLE = Path(__file__).with_name("plots.mplstyle")
OMIE_COLOR = "#444444"
MODEL_COLOR = "#D55E00"
# the gap between the two prices, by its sign (Okabe-Ito vermilion and blue)
ABOVE_COLOR = (*mcolors.to_rgb(MODEL_COLOR), 0.12)
BELOW_COLOR = (*mcolors.to_rgb("#0072B2"), 0.1)
WEEKEND_COLOR = "#f1f1f1"
PANEL_HEIGHT_IN = 1.8
PANEL_DAYS = 31  # every panel spans the longest month, so a day has one width
SCORECARD_HEIGHT_IN = 0.9
SOLAR = ("solar", "solar-hsat")
WIND = ("onwind",)
QUANTILES = {"p5": 0.05, "p25": 0.25, "median": 0.50, "p75": 0.75, "p95": 0.95}
CHEAP, EXPENSIVE = 1.0, 100.0  # EUR/MWh
EUR = "EUR/MWh"
# scorecard metrics shown in the figure -> column header; the CSV holds them all
HEADERS = {
    "mae": "Mean abs.\nerror",
    "rmae": "rMAE",
    "mean": "Mean",
    "solar_capture": "Solar\ncapture",
    "wind_capture": "Wind\ncapture",
    "p5": "P5",
    "median": "Median",
    "p95": "P95",
    "share_below_1": f"Hours\n< {CHEAP:.0f}",
    "share_above_100": f"Hours\n> {EXPENSIVE:.0f}",
    "daily_spread": "Daily\nspread",
}


def model_series(n: pypsa.Network) -> pd.DataFrame:
    """
    Hourly price of mainland Spain with the load, solar and wind output that weight it.

    Hours without a snapshot (the dropped leap day) stay empty.
    """
    price, load = es_hourly_price(n)
    gens = n.generators[n.generators["bus"].isin(mainland_buses(n))]
    output = n.generators_t.p[gens.index].T.groupby(gens["carrier"]).sum().T
    series = pd.DataFrame(
        {
            "price": price,
            "load": load,
            "solar": output.reindex(columns=SOLAR).sum(axis=1),
            "wind": output.reindex(columns=WIND).sum(axis=1),
        }
    )
    return series.reindex(pd.date_range(price.index[0], price.index[-1], freq="h"))


def _price_metrics(price: pd.Series, weights: pd.DataFrame) -> dict[str, float]:
    """Level, capture prices, distribution and volatility of one hourly price series."""
    daily = price.groupby(price.index.date)
    out = {"mean": price.mean()}
    for name, column in [
        ("load_weighted_mean", "load"),
        ("solar_capture", "solar"),
        ("wind_capture", "wind"),
    ]:
        out[name] = (price * weights[column]).sum() / weights[column].sum()
    out.update({name: price.quantile(q) for name, q in QUANTILES.items()})
    out["share_below_1"] = 100.0 * (price < CHEAP).mean()
    out["share_above_100"] = 100.0 * (price > EXPENSIVE).mean()
    out["daily_spread"] = (daily.max() - daily.min()).mean()
    return out


def naive_forecast(price: pd.Series) -> pd.Series:
    """
    Naive forecast of Lago et al. (2021): the same hour a day or a week earlier.

    The same hour of the previous day on Tuesday to Friday, of the previous week
    otherwise (weekdays in UTC). Hours without that earlier hour, as in the first
    days, have no forecast.
    """
    midweek = price.index.dayofweek.isin([1, 2, 3, 4])
    day = price.shift(freq="24h").reindex(price.index)
    week = price.shift(freq="168h").reindex(price.index)
    return day.where(midweek, week)


def scorecard(
    model: pd.DataFrame, omie: pd.Series, measured: pd.DataFrame
) -> pd.DataFrame:
    """
    Compare the model price with OMIE over the hours the model covers.

    Returns one row per metric with columns ``model``, ``omie`` and ``unit``. The model
    price is weighted by the model's load, solar and wind output, OMIE by the measured
    ones (``measured``, same columns), so each capture price is what that market's own
    plants earned. The fit metrics
    (correlation, mean absolute error, root mean square error) have no OMIE value.
    ``naive_mae`` is the error of the naive forecast of the OMIE price itself, and
    ``rmae`` the model's error relative to it over the hours that have a naive
    forecast: below 1, the model beats repeating past prices.

    References
    ----------
    - [Lago et al. (2021)](https://doi.org/10.1016/j.apenergy.2021.116983)
    """
    model = model.dropna()
    omie = omie.reindex(model.index)
    if omie.isna().any():
        raise ValueError("OMIE prices do not cover every model hour.")
    measured = measured.reindex(model.index)
    if measured.isna().any().any():
        raise ValueError("Measured output does not cover every model hour.")
    card = pd.DataFrame(
        {
            "model": _price_metrics(model["price"], model),
            "omie": _price_metrics(omie, measured),
        }
    )
    card["unit"] = EUR
    card.loc[["share_below_1", "share_above_100"], "unit"] = "% of hours"
    error = model["price"] - omie
    mae = error.abs().mean()
    naive_error = (naive_forecast(omie) - omie).abs().dropna()
    nan = float("nan")
    fit = pd.DataFrame(
        {
            "correlation": [model["price"].corr(omie), nan, "-"],
            "mae": [mae, nan, EUR],
            "rmse": [(error**2).mean() ** 0.5, nan, EUR],
            "rmae": [
                error.abs()[naive_error.index].mean() / naive_error.mean(),
                nan,
                "-",
            ],
            "naive_mae": [nan, naive_error.mean(), EUR],
        },
        index=["model", "omie", "unit"],
    ).T.astype({"model": float, "omie": float})
    return pd.concat([card, fit]).rename_axis("metric")


def _draw_scorecard(ax: plt.Axes, card: pd.DataFrame) -> None:
    """
    Draw the scorecard as a table: one column per metric, a row per price series.

    The errors of the model against OMIE come first, in the model row.
    """
    ax.axis("off")
    rows = {"model": ("Model", MODEL_COLOR), "omie": ("OMIE", OMIE_COLOR)}
    xs = [0.09 + 0.91 * (k + 0.5) / len(HEADERS) for k in range(len(HEADERS))]
    ys = {"header": 0.75, "model": 0.35, "omie": 0.08}
    for x, (metric, header) in zip(xs, HEADERS.items(), strict=True):
        unit = "%" if card.at[metric, "unit"].startswith("%") else ""
        ax.text(x, ys["header"], header, ha="center", va="center", color="#666666")
        for row, (_, color) in rows.items():
            if pd.notna(card.at[metric, row]):
                decimals = {"rmae": 2, "mae": 1}.get(metric, 0)
                value = f"{card.at[metric, row]:.{decimals}f}{unit}"
                ax.text(x, ys[row], value, ha="center", va="center", color=color)
    for row, (label, color) in rows.items():
        ax.text(0.0, ys[row], label, va="center", color=color, fontweight="bold")
    ax.set(xlim=(0, 1), ylim=(0, 1))


def plot_price_backcast(
    model: pd.Series,
    omie: pd.Series,
    card: pd.DataFrame,
    months: list[int],
) -> plt.Figure:
    """Draw the scorecard and one price panel per month."""
    year = model.index[0].year
    names = [f"month_{month}" for month in months]
    fig = plt.figure(
        figsize=(10.0, SCORECARD_HEIGHT_IN + PANEL_HEIGHT_IN * len(months))
    )
    axes = fig.subplot_mosaic(
        [["scorecard"], *[[name] for name in names]],
        height_ratios=[SCORECARD_HEIGHT_IN] + [PANEL_HEIGHT_IN] * len(months),
    )
    _draw_scorecard(axes["scorecard"], card)
    for name, month in zip(names, months, strict=True):
        ax = axes[name]
        m = model[model.index.month == month]
        o = omie[omie.index.month == month]
        # unclipped, so that prices just below zero show under the axis line
        ax.plot(
            o.index,
            o,
            color=OMIE_COLOR,
            lw=0.6,
            clip_on=False,
            label="OMIE day-ahead (hourly)",
        )
        ax.plot(
            m.index,
            m,
            color=MODEL_COLOR,
            lw=0.9,
            clip_on=False,
            label="PyPSA model (hourly)",
        )
        gap = o.reindex(m.index)
        for where, color, label in (
            (m >= gap, ABOVE_COLOR, "Model above OMIE"),
            (m < gap, BELOW_COLOR, "Model below OMIE"),
        ):
            ax.fill_between(
                m.index,
                m,
                gap,
                where=where,
                interpolate=True,
                color=color,
                lw=0,
                label=label,
            )
        start = m.index[0].normalize()
        end = m.index[-1] + pd.Timedelta(hours=1)
        days = pd.date_range(start, end, freq="D", inclusive="left")
        # from the day before, so that a month starting on a Sunday is shaded too
        for saturday in pd.date_range(start - pd.Timedelta(days=1), end, freq="W-SAT"):
            ax.axvspan(
                saturday,
                min(saturday + pd.Timedelta(days=2), end),
                color=WEEKEND_COLOR,
                lw=0,
                zorder=0,
            )
        ax.text(
            0.004,
            0.95,
            f"{start:%B} {year}",
            transform=ax.transAxes,
            va="top",
            fontweight="bold",
        )
        ax.set_ylabel(EUR)
        ax.sharey(axes[names[0]])
        ax.set_xlim(start, start + pd.Timedelta(days=PANEL_DAYS))
        ax.spines["bottom"].set_bounds(mdates.date2num(start), mdates.date2num(end))
        # the day of the month under the middle of each day, a tick mark at midnight
        ax.set_xticks(days + pd.Timedelta(hours=12))
        ax.set_xticks(days, minor=True)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%-d"))
        ax.tick_params(axis="x", which="minor", length=2.5, color="#999999")
    axes[names[0]].set_ylim(bottom=0.0)
    handles, labels = axes[names[0]].get_legend_handles_labels()
    handles.append(Patch(color=WEEKEND_COLOR))
    labels.append("Weekend")
    axes[names[0]].legend(
        handles,
        labels,
        loc="lower right",
        bbox_to_anchor=(1.0, 1.0),
        ncol=5,
        borderaxespad=0.0,
    )
    fig.suptitle(
        f"Spanish day-ahead price, {year} back-cast: PyPSA model vs OMIE",
        x=0.0,
        ha="left",
    )
    return fig


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--prices", type=Path, required=True, help="OMIE prices CSV.")
    parser.add_argument(
        "--generation", type=Path, required=True, help="Measured generation CSV."
    )
    parser.add_argument("--months", type=int, nargs="+", default=list(range(1, 13)))
    parser.add_argument(
        "--output", type=Path, nargs="+", required=True, help="Figure file(s)."
    )
    parser.add_argument("--output-scorecard", type=Path, required=True)
    args = parser.parse_args(argv)

    n = pypsa.Network(str(args.network))
    model = model_series(n)
    omie = pd.read_csv(args.prices, index_col="snapshot", parse_dates=True)["price_es"]
    generation = pd.read_csv(args.generation, index_col="snapshot", parse_dates=True)
    card = scorecard(model, omie, generation[generation["country"] == "ES"])
    logger.info("Price scorecard:\n%s", card.round(2).to_string())
    args.output_scorecard.parent.mkdir(parents=True, exist_ok=True)
    card.round(3).to_csv(args.output_scorecard)

    plt.style.use(STYLE)
    fig = plot_price_backcast(model["price"], omie, card, args.months)
    for path in args.output:
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi="figure")
    plt.close(fig)


if __name__ == "__main__":
    main()
