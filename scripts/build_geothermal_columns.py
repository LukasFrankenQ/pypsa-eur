# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
Map the Limberger et al. 3-D subsurface temperature model of Europe onto the
network's onshore regions, as the shared input of the enhanced geothermal (EGS)
and heat-only geothermal potentials.

The temperature model (Limberger et al. 2014, Geothermal Energy Science 2:55;
2026 release on Zenodo 22149195, CC-BY-4.0) holds measured temperatures where they
exist and crustal conduction elsewhere, on 10 km columns in EPSG:3857 (about 3-6 km
on the ground in Europe) from 0 to 10 km depth in 250 m steps. Each column whose
centre lies in an onshore region is kept on its native grid and carries:

- `temperature` (°C) at the configured depths,
- `area` (km²) on the ground, (10 km cos φ)²,
- `land_share`: the share of its weather cell available for onshore wind
  (`availability_matrix_onwind.nc`), the land the EGS wellfields may use,
- `urban_population` of its weather cell (thousand inhabitants, `pop_layout_urban.nc`)
  and that cell's `cell_area` (km²),
- `dh_area` (km²) and `dh_cap` (MW_th): its overlap with the Fraunhofer
  district-heating areas (Manz et al. 2024, buffered by
  `sector.district_heating.dh_areas.buffer`) and the share of their demand
  divided by the full-load hours; NaN in countries without such areas.

Inputs
------
- `data/limberger_temperature/<version>/temperature_voxel.nc`
- `resources/<run_name>/onshore_regions.geojson`
- `resources/<run_name>/availability_matrix_onwind.nc`
- `resources/<run_name>/pop_layout_urban.nc`
- `data/dh_areas/<version>/dh_areas.gpkg`

Outputs
-------
- `resources/<run_name>/geothermal_columns.nc`
"""

import logging

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr
from shapely.geometry import box

from scripts._helpers import configure_logging, set_scenario_config

logger = logging.getLogger(__name__)

# country codes of the district-heating areas that differ from the network's
DH_COUNTRY_CODES = {"EL": "GR", "UK": "GB"}


def select_columns(
    voxel: xr.DataArray, regions: gpd.GeoDataFrame, depths_m: list[float]
) -> gpd.GeoDataFrame:
    """
    Columns of the voxel whose centre lies in an onshore region, with their
    temperature at `depths_m` as columns `T<depth>`.
    """
    minx, miny, maxx, maxy = regions.to_crs(3857).total_bounds
    sub = voxel.sel(
        x=slice(minx - 1e4, maxx + 1e4),
        y=slice(miny - 1e4, maxy + 1e4)
        if voxel.y[0] < voxel.y[-1]
        else slice(maxy + 1e4, miny - 1e4),
    ).sel(depth_m=depths_m)
    frame = (
        sub.transpose("y", "x", "depth_m")
        .to_series()
        .unstack("depth_m")
        .dropna(how="all")
    )
    frame.columns = [f"T{int(d)}" for d in frame.columns]
    frame = frame.reset_index()
    points = gpd.GeoDataFrame(
        frame, geometry=gpd.points_from_xy(frame.x, frame.y), crs=3857
    ).to_crs(4326)
    joined = gpd.sjoin(
        points, regions[["name", "geometry"]], how="inner", predicate="within"
    )
    joined = joined.drop(columns="index_right").rename(columns={"name": "bus"})
    joined = joined[~joined.index.duplicated()]
    joined["lon"] = joined.geometry.x
    joined["lat"] = joined.geometry.y
    return joined.reset_index(drop=True)


def column_area(lat: pd.Series, spacing_m: float) -> pd.Series:
    """Ground area (km²) of a square Mercator column at latitude `lat`."""
    return (spacing_m / 1e3 * np.cos(np.radians(lat))) ** 2


def sample_cells(field: xr.DataArray, lon: pd.Series, lat: pd.Series) -> np.ndarray:
    """Value of a weather-cell field at the cells nearest to the columns."""
    return field.sel(
        x=xr.DataArray(lon.values, dims="column"),
        y=xr.DataArray(lat.values, dims="column"),
        method="nearest",
    ).values


def cell_area(field: xr.DataArray, lat: pd.Series) -> np.ndarray:
    """Ground area (km²) of the weather cells of `field` at latitude `lat`."""
    dx = float(abs(field.x[1] - field.x[0]))
    dy = float(abs(field.y[1] - field.y[0]))
    return (dx * 111.32 * np.cos(np.radians(lat.values))) * (dy * 110.57)


def district_heating_overlap(
    columns: gpd.GeoDataFrame,
    dh_areas: gpd.GeoDataFrame,
    spacing_m: float,
    buffer_m: float,
    full_load_hours: float,
) -> pd.DataFrame:
    """
    Overlap (km²) of each column with the buffered district-heating areas and
    the capacity (MW_th) their demand can absorb, each area's demand split by
    the share of its buffered footprint inside the column.
    """
    half = spacing_m / 2
    squares = gpd.GeoDataFrame(
        geometry=[
            box(x - half, y - half, x + half, y + half)
            for x, y in zip(columns.x, columns.y)
        ],
        index=columns.index,
        crs=3857,
    ).to_crs(3035)
    areas = dh_areas.to_crs(3035)[["Dem_GWh", "geometry"]].copy()
    areas["geometry"] = areas.buffer(buffer_m)
    areas["area_total"] = areas.area
    areas["area_id"] = np.arange(len(areas))
    pieces = gpd.overlay(squares.reset_index(names="column"), areas, how="intersection")
    pieces["overlap"] = pieces.area
    # an area's buffered footprint may overlap itself after buffering neighbours:
    # demand is split by the piece's share of that area
    pieces["cap"] = (
        pieces["Dem_GWh"]
        * pieces["overlap"]
        / pieces["area_total"]
        * 1e3
        / full_load_hours
    )
    per_column = pieces.groupby("column")[["overlap", "cap"]].sum()
    # several buffered areas may overlap: the land counted is at most the column
    land = (
        gpd.GeoDataFrame(pieces[["column"]], geometry=pieces.geometry, crs=3035)
        .dissolve(by="column")
        .area
    )
    out = pd.DataFrame(
        {
            "dh_area": land.reindex(columns.index).fillna(0.0) / 1e6,
            "dh_cap": per_column["cap"].reindex(columns.index).fillna(0.0),
        }
    )
    return out


if __name__ == "__main__":
    if "snakemake" not in globals():
        from scripts._helpers import mock_snakemake

        snakemake = mock_snakemake("build_geothermal_columns")

    configure_logging(snakemake)
    set_scenario_config(snakemake)
    params = snakemake.params

    regions = gpd.read_file(snakemake.input.regions).to_crs(4326)
    voxel = xr.open_dataset(snakemake.input.voxel)["temperature"]
    spacing_m = float(abs(voxel.x[1] - voxel.x[0]))

    depths_km = sorted(set(params.depths_egs) | set(params.depths_heat))
    depths_m = [round(d * 1e3) for d in depths_km]
    missing = set(depths_m) - set(voxel.depth_m.values.astype(int))
    if missing:
        raise ValueError(
            f"Depths {sorted(missing)} m are not levels of the temperature model "
            f"(250 m steps)."
        )

    columns = select_columns(voxel, regions, depths_m)
    columns["country"] = columns.bus.str[:2]
    columns["area"] = column_area(columns.lat, spacing_m)

    availability = xr.open_dataarray(snakemake.input.availability_matrix)
    land = availability.sum("bus").clip(0, 1)
    columns["land_share"] = sample_cells(land, columns.lon, columns.lat)

    urban = xr.open_dataarray(snakemake.input.pop_layout_urban)
    columns["urban_population"] = sample_cells(urban, columns.lon, columns.lat)
    columns["cell_area"] = cell_area(urban, columns.lat)

    dh_areas = gpd.read_file(snakemake.input.dh_areas)
    dh_areas["country"] = dh_areas.country.replace(DH_COUNTRY_CODES)
    dh_countries = set(dh_areas.country)
    dh = district_heating_overlap(
        columns,
        dh_areas,
        spacing_m=spacing_m,
        buffer_m=params.dh_area_buffer,
        full_load_hours=params.full_load_hours,
    )
    has_dh = columns.country.isin(dh_countries)
    columns["dh_area"] = dh["dh_area"].where(has_dh)
    columns["dh_cap"] = dh["dh_cap"].where(has_dh)

    covered = set(columns.bus)
    uncovered = sorted(set(regions.name) - covered)
    if uncovered:
        logger.info(
            f"No column of the temperature model lies in the regions {uncovered}: "
            "no geothermal potential there."
        )
    logger.info(
        f"{len(columns)} temperature columns in {len(covered)} regions; district-heating "
        f"areas cover {sorted(dh_countries & set(columns.country))}, urban cells the rest."
    )

    temperature = xr.DataArray(
        columns[[f"T{d}" for d in depths_m]].values,
        dims=("column", "depth"),
        coords={"depth": depths_km},
        name="temperature",
    )
    attrs = ["bus", "country", "lon", "lat", "x", "y", "area", "land_share"]
    attrs += ["urban_population", "cell_area", "dh_area", "dh_cap"]
    ds = xr.Dataset(
        {
            a: (
                "column",
                columns[a].to_numpy(dtype=str)
                if a in ("bus", "country")
                else columns[a].to_numpy(dtype=float),
            )
            for a in attrs
        }
        | {"temperature": temperature}
    )
    ds["temperature"].attrs["units"] = "degC"
    ds.attrs["source"] = (
        "Limberger et al. 2014, Geothermal Energy Science 2:55; Zenodo 22149195"
    )
    ds.to_netcdf(snakemake.output.columns)
