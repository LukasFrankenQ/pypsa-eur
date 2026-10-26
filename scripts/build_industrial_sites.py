# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
Locate today's industrial sites and their CO2 emissions.

Description
-----------
Builds one table of industrial sites, ``industrial_sites.csv``, with the columns
``site_id``, ``name``, ``country``, ``lat``, ``lon``, ``sector``, ``activity``,
``nace``, ``co2_t`` (tonnes of CO2 equivalent in ``year``), ``year``, ``source``
and ``location`` (how the point was placed: ``address`` geocoded, ``field`` or
``facility`` from the Norwegian Offshore Directorate, ``reported`` grid
reference). A second table, ``industrial_sites_left_out.csv``, sums per country
what was left out and why (``power and heat``, ``not industry``, ``unclassified
combustion``, ``no location``, ``outside its country``).

Industry means mining and manufacturing (NACE sections B and C). Power and heat
plants are left out: an installation registered for combustion counts as
industry only when its NACE code is outside section D and no ENTSO-E power plant
is linked to it.

Sources:

- the EU ETS verified emissions of the latest complete year, with the
  installations' activity types, geocoded addresses, NACE codes (from the
  carbon leakage lists) and their link to ENTSO-E power plants, from the EU ETS
  data package of Abrell (CC-BY-4.0, https://doi.org/10.5281/zenodo.23208374),
  which compiles the EU Transaction Log;
- the Norwegian Offshore Directorate's facilities, which place the offshore
  fields and onshore gas plants that the EUTL lists at their operator's office;
- for Great Britain, which left the EU ETS, the NAEI point sources (Open
  Government Licence), keeping the industrial sites of at least 10 kt of CO2
  in its latest year, about the size the EU ETS starts at.

Each point must lie within 10 km of its country's land or sea; the rest are left
out. The table covers the configured countries.
"""

import json
import logging
import re

import geopandas as gpd
import numpy as np
import pandas as pd

from scripts._helpers import configure_logging, set_scenario_config

logger = logging.getLogger(__name__)

COLUMNS = [
    "site_id",
    "name",
    "country",
    "lat",
    "lon",
    "sector",
    "activity",
    "nace",
    "co2_t",
    "year",
    "source",
    "location",
]

STEEL = "Iron and steel"
CEMENT = "Cement and lime"
MINERALS = "Glass, ceramics and other minerals"
NON_FERROUS = "Non-ferrous metals"
CHEMICALS = "Chemicals"
REFINING = "Refining"
PAPER = "Pulp and paper"
FOOD = "Food and drink"
OIL_GAS = "Oil and gas extraction"
OTHER = "Other industry"

SECTORS = [
    STEEL,
    CEMENT,
    MINERALS,
    NON_FERROUS,
    CHEMICALS,
    REFINING,
    PAPER,
    FOOD,
    OIL_GAS,
    OTHER,
]

POWER = "power and heat"
NOT_INDUSTRY = "not industry"
UNCLASSIFIED = "unclassified combustion"
NO_LOCATION = "no location"
OUTSIDE = "outside its country"

# EU ETS activity types (Annex I of Directive 2003/87/EC; 1-9 are the codes of
# the second trading period)
ETS_ACTIVITY_SECTORS = {
    2: REFINING,
    3: STEEL,
    4: STEEL,
    5: STEEL,
    6: CEMENT,
    7: MINERALS,
    8: MINERALS,
    9: PAPER,
    21: REFINING,
    22: STEEL,
    23: STEEL,
    24: STEEL,
    25: STEEL,
    26: NON_FERROUS,
    27: NON_FERROUS,
    28: NON_FERROUS,
    29: CEMENT,
    30: CEMENT,
    31: MINERALS,
    32: MINERALS,
    33: MINERALS,
    34: MINERALS,
    35: PAPER,
    36: PAPER,
    37: CHEMICALS,
    38: CHEMICALS,
    39: CHEMICALS,
    40: CHEMICALS,
    41: CHEMICALS,
    42: CHEMICALS,
    43: CHEMICALS,
    44: CHEMICALS,
    45: OTHER,
    46: OTHER,
    47: OTHER,
}
# Combustion and opted-in installations, classified by their NACE code
ETS_BY_NACE = {1, 20, 99}
# Aviation, maritime and the ETS2 fuel suppliers have no site
ETS_NO_SITE = {10, 50, 70}

# NAEI point source sectors that are industry
NAEI_SECTORS = {
    "Iron & steel industries": STEEL,
    "Cement": CEMENT,
    "Lime": CEMENT,
    "Other mineral industries": MINERALS,
    "Non-ferrous metal industries": NON_FERROUS,
    "Chemical industry": CHEMICALS,
    "Processing & distribution of petroleum products": REFINING,
    "Paper, printing & publishing industries": PAPER,
    "Food, drink & tobacco industry": FOOD,
    "Oil & gas exploration and production": OIL_GAS,
    "Other industries": OTHER,
    "Other fuel production": OTHER,
    "Vehicles": OTHER,
    "Electrical engineering": OTHER,
    "Mechanical engineering": OTHER,
    "Textiles, clothing, leather & footwear": OTHER,
}
NAEI_POWER = {"Major power producers", "Minor power producers"}
CARBON_TO_CO2 = 44.0 / 12.0

# EUTL installation names that name a Norwegian facility by another name
NO_ALIASES = {"HAMMERFEST LNG": "MELKØYA"}
# Sodir facility kinds that are no emitting site
NO_PLACELESS_KINDS = {
    "LANDFALL",
    "LOADING SYSTEM",
    "OFFSHORE WIND TURBINE",
    "DIVE SUPPORT VESSEL",
}
# Words of a Norwegian installation name that only say what it is
NO_SUFFIXES = re.compile(r"(FELTET|OMRÅDET|FELT)\b")

BUFFER_M = 10_000.0
# The smallest NAEI site kept, in tonnes of CO2
MIN_SITE_T = 10_000.0


def nace_division(nace) -> int | None:
    """The NACE Rev. 2 division of a code like ``24.10`` or ``6.20``."""
    if nace is None or (isinstance(nace, float) and np.isnan(nace)):
        return None
    head = str(nace).strip().split(".")[0]
    return int(head) if head.isdigit() else None


def nace_sector(nace) -> tuple[str | None, str | None]:
    """The sector of a NACE code, or None and why it is left out."""
    division = nace_division(nace)
    if division is None:
        return None, UNCLASSIFIED
    if division == 35:
        return None, POWER
    if not 5 <= division <= 33:
        return None, NOT_INDUSTRY
    group = str(nace).strip().split(".")[1][:1] if "." in str(nace) else ""
    if division in (6, 9):
        return OIL_GAS, None
    if division in (10, 11, 12):
        return FOOD, None
    if division == 17:
        return PAPER, None
    if division == 19:
        return REFINING, None
    if division in (20, 21):
        return CHEMICALS, None
    if division == 23:
        return (CEMENT if group == "5" else MINERALS), None
    if division == 24:
        non_ferrous = group == "4" or str(nace).strip() in ("24.53", "24.54")
        return (NON_FERROUS if non_ferrous else STEEL), None
    return OTHER, None


def ets_sector(activity, nace, power_plant: bool) -> tuple[str | None, str | None]:
    """The sector of an EU ETS installation, or None and why it is left out."""
    activity = int(activity) if pd.notna(activity) and str(activity) != "" else None
    if activity is None or activity in ETS_NO_SITE:
        return None, NOT_INDUSTRY
    if activity in ETS_BY_NACE:
        if power_plant:
            return None, POWER
        return nace_sector(nace)
    return ETS_ACTIVITY_SECTORS.get(activity, OTHER), None


def latest_year(compliance: pd.DataFrame) -> int:
    """The latest year most installations have verified emissions for."""
    reported = compliance[compliance["verified"] > 0].groupby("year").size()
    return int(reported[reported >= 0.5 * reported.max()].index.max())


def ets_sites(
    installations: pd.DataFrame,
    compliance: pd.DataFrame,
    locations: pd.DataFrame,
    nace: pd.DataFrame,
    plant_links: pd.DataFrame,
    year: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    The EU ETS installations of `year` with their sector and location, and
    what was left out of them.
    """
    compliance = compliance.assign(
        verified=pd.to_numeric(compliance["verified"], errors="coerce")
    )
    year = year or latest_year(compliance)
    emitted = compliance[(compliance["year"] == year) & (compliance["verified"] > 0)]
    emitted = emitted.groupby("installation_id")["verified"].sum()

    df = installations.set_index("id").reindex(emitted.index)
    df["co2_t"] = emitted
    # aviation, maritime and the ETS2 fuel suppliers are no site, nor are the
    # operators the installation table does not list
    activity = pd.to_numeric(df["activity_id"], errors="coerce")
    df = df[activity.notna() & ~activity.isin(ETS_NO_SITE)]
    df = df[df["registry_id"] != "XI"]  # Northern Ireland: the NAEI covers it
    codes = nace.set_index("installation_id")
    df["nace"] = codes["nace_2020"].fillna(codes["nace_2015"]).reindex(df.index)
    linked = df.index.isin(plant_links["installation_id"])
    classified = [
        ets_sector(a, n, p)
        for a, n, p in zip(df["activity_id"], df["nace"], linked, strict=True)
    ]
    df["sector"] = [s for s, _ in classified]
    df["reason"] = [r for _, r in classified]

    at = locations.set_index("installation_id")
    df["lat"] = at["latitude"].reindex(df.index)
    df["lon"] = at["longitude"].reindex(df.index)
    df.loc[df["sector"].notna() & df["lat"].isna(), "reason"] = NO_LOCATION

    sites = pd.DataFrame(
        {
            "site_id": df.index,
            "name": df["name"],
            "country": df["registry_id"],
            "lat": df["lat"],
            "lon": df["lon"],
            "sector": df["sector"],
            "activity": df["activity_name"],
            "nace": df["nace"],
            "co2_t": df["co2_t"],
            "year": year,
            "source": "eu_ets",
            "location": "address",
            "reason": df["reason"],
        }
    ).reset_index(drop=True)
    kept = sites["reason"].isna()
    return sites.loc[kept, COLUMNS], left_out(sites[~kept])


def _norwegian(text: str) -> str:
    text = NO_SUFFIXES.sub("", str(text).upper())
    return re.sub(r"[^0-9A-ZÆØÅ ]+", " ", text).strip()


def locate_on_facilities(
    sites: pd.DataFrame, facilities: gpd.GeoDataFrame
) -> pd.DataFrame:
    """
    Place the Norwegian installations that name an offshore field or a
    Sodir facility at that field's surface facilities, or that facility.
    """
    facilities = facilities[
        facilities.geometry.apply(lambda g: g is not None and not g.is_empty)
        & ~facilities["fclKind"].fillna("").str.upper().isin(NO_PLACELESS_KINDS)
    ]
    places: dict[str, tuple[list, str]] = {}
    for kind, key in (("facility", "fclName"), ("field", "fclBelongsToName")):
        for name, group in facilities.groupby(key):
            norm = _norwegian(name)
            if len(norm) >= 3:
                places[norm] = (list(group.geometry), kind)
    # the longest name first, so SLEIPNER VEST wins over SLEIPNER
    names = sorted(places, key=len, reverse=True)

    sites = sites.copy()
    norway = sites.index[sites["country"] == "NO"]
    for i in norway:
        raw = str(sites.at[i, "name"]).upper()
        alias = next((v for a, v in NO_ALIASES.items() if a in raw), None)
        name = _norwegian(alias or raw)
        exact = [n for n in names if re.search(rf"\b{re.escape(n)}\b", name)]
        # else a field the name only shortens: SLEIPNER for SLEIPNER VEST and ØST
        words = [w for w in name.split() if len(w) >= 4]
        chosen = exact[:1] or [
            n for n in names if any(n.startswith(w + " ") for w in words)
        ]
        if not chosen:
            continue
        points = [p for n in chosen for p in places[n][0]]
        sites.at[i, "lon"] = float(np.mean([p.x for p in points]))
        sites.at[i, "lat"] = float(np.mean([p.y for p in points]))
        sites.at[i, "location"] = places[chosen[0]][1]
    return sites


def naei_sites(
    ghgs: pd.DataFrame, min_site_t: float, year: int | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    The industrial NAEI point sources of `year` emitting at least
    `min_site_t` tonnes of CO2, and what was left out of them.
    """
    co2 = ghgs[ghgs["Pollutant_Name"].str.startswith("Carbon Dioxide")]
    year = year or int(co2["Year"].max())
    co2 = co2[(co2["Year"] == year) & (co2["Emission"] > 0)]
    co2 = co2.groupby("PlantID", as_index=False).agg(
        Site=("Site", "first"),
        Operator=("Operator", "first"),
        Sector=("Sector", "first"),
        Easting=("Easting", "first"),
        Northing=("Northing", "first"),
        Emission=("Emission", "sum"),
    )
    co2["co2_t"] = co2["Emission"] * CARBON_TO_CO2
    points = gpd.GeoSeries(
        gpd.points_from_xy(co2["Easting"], co2["Northing"]), crs="EPSG:27700"
    ).to_crs("EPSG:4326")
    sector = co2["Sector"].map(NAEI_SECTORS)
    reason = pd.Series(np.where(co2["Sector"].isin(NAEI_POWER), POWER, NOT_INDUSTRY))
    reason[sector.notna()] = None
    sites = pd.DataFrame(
        {
            "site_id": "GB_NAEI_" + co2["PlantID"].astype(str),
            "name": co2["Operator"].astype(str) + ", " + co2["Site"].astype(str),
            "country": "GB",
            "lat": points.y.to_numpy(),
            "lon": points.x.to_numpy(),
            "sector": sector,
            "activity": co2["Sector"],
            "nace": None,
            "co2_t": co2["co2_t"],
            "year": year,
            "source": "uk_naei",
            "location": "reported",
            "reason": reason,
        }
    )
    kept = sites["reason"].isna() & (sites["co2_t"] >= min_site_t)
    industry_below = sites["reason"].isna() & ~kept
    sites.loc[industry_below, "reason"] = f"below {min_site_t / 1e3:g} kt"
    return sites.loc[kept, COLUMNS], left_out(sites[~kept])


def within_countries(
    sites: pd.DataFrame, shapes: gpd.GeoDataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The sites within BUFFER_M of their country's land or sea, and the rest."""
    area = shapes.to_crs(3035).dissolve(by="name").buffer(BUFFER_M)
    points = gpd.GeoSeries(
        gpd.points_from_xy(sites["lon"], sites["lat"]), index=sites.index, crs=4326
    ).to_crs(3035)
    inside = np.array(
        [
            country in area.index and area[country].contains(point)
            for country, point in zip(sites["country"], points, strict=True)
        ],
        dtype=bool,
    )
    outside = sites[~inside].assign(reason=OUTSIDE)
    return sites[inside], left_out(outside)


def left_out(rows: pd.DataFrame) -> pd.DataFrame:
    """Sites and tonnes left out, per country and reason."""
    return (
        rows.groupby(["country", "reason"])
        .agg(sites=("site_id", "size"), co2_t=("co2_t", "sum"))
        .reset_index()
    )


def build_industrial_sites(
    *,
    countries: list[str],
    ets: dict[str, pd.DataFrame],
    facilities: gpd.GeoDataFrame,
    naei: pd.DataFrame | None,
    shapes: gpd.GeoDataFrame,
    min_site_t: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """All industrial sites of `countries` and what was left out."""
    eu, eu_out = ets_sites(**ets)
    eu = locate_on_facilities(eu, facilities)
    parts, out = [eu], [eu_out]
    if naei is not None and "GB" in countries:
        gb, gb_out = naei_sites(naei, min_site_t)
        parts.append(gb)
        out.append(gb_out)
    sites = pd.concat(parts, ignore_index=True)
    sites = sites[sites["country"].isin(countries)]
    sites, outside = within_countries(sites, shapes)
    out.append(outside)
    left = pd.concat(out, ignore_index=True)
    left = left[left["country"].isin(countries)]
    left = left.groupby(["country", "reason"], as_index=False)[["sites", "co2_t"]].sum()
    sites = sites.sort_values(["country", "co2_t"], ascending=[True, False])
    return sites.reset_index(drop=True), left


if __name__ == "__main__":
    if "snakemake" not in globals():
        from scripts._helpers import mock_snakemake

        snakemake = mock_snakemake("build_industrial_sites")

    configure_logging(snakemake)
    set_scenario_config(snakemake)

    inputs = snakemake.input
    ets = {
        "installations": pd.read_csv(
            inputs.eutl_installations, dtype={"activity_id": str}
        ),
        "compliance": pd.read_csv(
            inputs.eutl_compliance, usecols=["installation_id", "year", "verified"]
        ),
        "locations": pd.read_csv(inputs.eutl_locations),
        "nace": pd.read_csv(inputs.eutl_nace, dtype=str),
        "plant_links": pd.read_csv(inputs.eutl_plant_links),
    }
    with open(inputs.sodir_facilities) as f:
        facilities = gpd.GeoDataFrame.from_features(json.load(f)["features"], crs=4326)
    naei = pd.read_excel(inputs.naei_point_sources, sheet_name="GHGs")
    shapes = pd.concat(
        [gpd.read_file(inputs.country_shapes), gpd.read_file(inputs.offshore_shapes)]
    )

    sites, left = build_industrial_sites(
        countries=snakemake.params.countries,
        ets=ets,
        facilities=facilities,
        naei=naei,
        shapes=gpd.GeoDataFrame(shapes, geometry="geometry", crs=4326),
        min_site_t=MIN_SITE_T,
    )
    for (country, reason), row in left.set_index(["country", "reason"]).iterrows():
        logger.info(
            f"{country}: left out {row.sites} sites, {row.co2_t / 1e6:.2f} Mt ({reason})"
        )
    logger.info(
        f"{len(sites)} industrial sites, {sites['co2_t'].sum() / 1e6:.1f} Mt CO2"
    )
    sites.to_csv(snakemake.output.sites, index=False)
    left.to_csv(snakemake.output.left_out, index=False)
