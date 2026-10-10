# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
Geothermal cost and potential models shared by the EGS and the heat-only rules.

* `egs_costs`: the enhanced geothermal (EGS) power cost model of Ricks & Jenkins,
  "Pathways to national-scale adoption of enhanced geothermal power through
  experience-driven cost reductions", Joule 2025, supplementary code
  `Costing_and_Supply_Curves/EGS_Costs.py` (doi:10.5281/zenodo.15485307, CC-BY-4.0),
  vectorised. It is GETEM-derived and in 2021 USD: surface-plant cost and gross output
  per injector fitted per depth as functions of the production temperature (binary/ORC
  plants, linear extrapolation above 200 °C), quadratic well costs in depth,
  stimulation, exploration, interest during construction, and GETEM O&M with the NREL
  ATB 2023 derating. Parameters default to the paper's central deep-EGS case.
* `doublet_costs`: a heat-only geothermal doublet with the parameters of the TU Delft
  `module_geothermal` (Egberink, Limberger et al. 2026, Apache-2.0): the ThermoGIS
  drilling-cost curve in EUR2020, pump cost per doublet, stimulation cost for
  petrothermal (stimulated) doublets and the conventional/stimulated flow rates.
"""

import numpy as np

# specific heat of geothermal brine, kJ/(kg K)
CP_BRINE = 4.2

EGS_DEPTHS_KM = (2.5, 3.5, 4.5, 5.5, 6.5)

# Per-depth fit coefficients of the source model.
# gross power per kg/s of flow (kW), quadratic in production temperature up to 200 °C
_PWR = {
    2.5: (0.005526, -1.3473, 124.43),
    3.5: (0.005767, -1.4473, 136.4),
    4.5: (0.005777, -1.4566, 139.59),
    5.5: (0.005362, -1.331, 132.57),
    6.5: (0.004806, -1.1589, 121.77),
}
# surface plant cost (USD/kW) = k1 exp(k2 T) + k3 up to 200 °C
_PLANT = {
    2.5: (58770, -0.02232, 2006),
    3.5: (70160, -0.02273, 1994),
    4.5: (74180, -0.02236, 1961),
    5.5: (70590, -0.02085, 1791),
    6.5: (55080, -0.01768, 1358),
}
# lateral-length adjustment of the well cost: (cost with 2286 m lateral - with 1786 m), depth factor
_LATERAL = {
    2.5: (4953350 - 4394664, 0.886),
    3.5: (8630763 - 7753212, 0.899),
    4.5: (10385865 - 9508314, 0.913),
    5.5: (18356326 - 17040291, 0.926),
    6.5: (20988394 - 19672360, 0.940),
}

_EGS_PARAMS = dict(
    pre_survey_cost=250_000,
    test_drilling_cost=3_300_000,
    pre_survey_years=3,
    test_drilling_years=0.5,
    drilling_years=1,
    construction_years=1,
    pre_survey_interest=1.15,
    test_drilling_interest=1.15,
    drilling_interest=1.1,
    construction_interest=1.08,
    wf_maint=0.015,
    pp_maint=0.018,
    tandi=0.0075,
    drill_success_rate=0.9,
    stim_cost=3_480_000,
    plantcost_adj=1.491 / 1.408,
    laborcost_adj=1.556 / 1.449,
    om_derating=0.77,
)


def ambient_temperature_shift(t_prod_c, t_amb_k):
    """
    Shift of the effective resource temperature for an ambient temperature
    other than 283.15 K (binary-plant performance fit of the source model).
    """
    t_prod_c = np.asarray(t_prod_c, dtype=float)
    t_amb_k = np.asarray(t_amb_k, dtype=float)
    temp_diff = t_amb_k - 283.15
    t = t_prod_c + 273.15
    a, b, c, gpb = 4.01465, -0.01204, 0.00001605, 288.15
    cold = temp_diff <= 0
    c1 = np.where(cold, 0.002746, 0.002713)
    c0 = np.where(cold, -0.083806, -0.091841)
    d1 = np.where(cold, 0.002713, 0.002676)
    d0 = np.where(cold, -0.091841, -0.1012)
    eta_ll = c1 * t + c0
    eta_ul = d1 * t + d0
    lg = np.log(t / gpb)
    poly = (
        (t - gpb) * (a - b * gpb)
        - a * gpb * lg
        + 0.5 * (t**2 - gpb**2) * (b - c * gpb)
        + c / 3 * (t**3 - gpb**3)
    )
    dpdt_amb = -(
        eta_ll
        * (
            -a * lg
            - gpb * (b - c * gpb)
            - b * (t - gpb)
            + b * gpb
            - 0.5 * c * (t**2 - gpb**2)
            - c * gpb**2
        )
        + (eta_ul / 10 - eta_ll / 10) * poly
    )
    dpdt_prod = c1 * poly + (c1 * t + c0) * (
        -a * gpb / t + a + t * (b - c * gpb) - b * gpb + c * t**2
    )
    return -(dpdt_amb / dpdt_prod) * temp_diff


def wellbore_temperature_loss(depth_km):
    """Temperature lost between reservoir and wellhead (°C), source model fit."""
    return 0.6889 * np.asarray(depth_km, dtype=float) - 0.1333


def egs_well_costs(depth_km, lateral_length_m=2286, drill_cost_multiplier=1.0):
    """
    Drilling cost of one (cased, uncased) horizontal EGS well with
    `lateral_length_m` of lateral at vertical depth `depth_km`, USD2021,
    stimulation excluded.
    """
    d = float(depth_km)
    uncased = (399766 * d**2 + 25250 * d + 2439840) * drill_cost_multiplier
    cased = (413193 * d**2 + 152907 * d + 2932844) * drill_cost_multiplier
    dl, fac = _LATERAL[d]
    lat_adj = (
        (dl * (2286 - lateral_length_m) / 500 + 163.8 * (2286 - lateral_length_m))
        * 0.87
        * fac
        * drill_cost_multiplier
    )
    return cased - lat_adj, uncased - 0.6 * lat_adj


def egs_costs(
    reservoir_temperature_c,
    air_temperature_k,
    depth_km,
    producers_per_injector=1.5,
    stimulate_producers=True,
    lateral_length_m=2286,
    flow_derating=0.77,
    drill_cost_multiplier=1.0,
    plant_size_mw=50,
    max_temperature_c=None,
    reinjection_temperature_c=70.0,
):
    """
    Costs and output of one EGS injector unit at `depth_km` (one of `EGS_DEPTHS_KM`).

    Parameters
    ----------
    reservoir_temperature_c : array-like
        Rock temperature at the depth, °C.
    air_temperature_k : array-like
        Annual mean ambient air temperature, K.
    depth_km : float
    max_temperature_c : float, optional
        Cap on the production temperature fed to the plant fits, which are
        binary-cycle fits to 200 °C and linear extrapolations above.
    reinjection_temperature_c : float
        Brine temperature after the plant; sets the thermal output and hence the
        plant's conversion efficiency.

    Returns
    -------
    dict of arrays, USD2021
        `gross_mw` (electric, per injector, excluding wellfield pumping),
        `thermal_mw` (heat extracted per injector), `plant_capex` and
        `well_capex` (USD/kW gross electric, interest during construction
        included), `fom` (USD/kW-yr gross electric), `mw_per_km2` (gross
        electric technical density).
    """
    p = _EGS_PARAMS
    d = float(depth_km)
    if d not in _PWR:
        raise ValueError(f"depth {d} km not in the fitted set {EGS_DEPTHS_KM}")
    t_res = np.asarray(reservoir_temperature_c, dtype=float)
    t_air = np.broadcast_to(np.asarray(air_temperature_k, dtype=float), t_res.shape)

    mass_flow = 160 * 0.925 * (lateral_length_m / 2286) * flow_derating  # kg/s
    wellfield_area_km2 = 1.39 * (lateral_length_m / 2286) * flow_derating

    t_prod = t_res - wellbore_temperature_loss(d)
    t_adj = t_prod + ambient_temperature_shift(t_prod, t_air)
    if max_temperature_c is not None:
        t_adj = np.minimum(t_adj, max_temperature_c)
        t_prod = np.minimum(t_prod, max_temperature_c)

    a, b, c = _PWR[d]
    t_lo = np.minimum(t_adj, 200)
    gross_kw_per_flow = (
        a * t_lo**2 + b * t_lo + c + np.where(t_adj > 200, 0.862 * (t_adj - 200), 0.0)
    )
    gross_mw = gross_kw_per_flow * mass_flow / 1e3
    thermal_mw = mass_flow * CP_BRINE * (t_prod - reinjection_temperature_c) / 1e3

    size_mult = (plant_size_mw / 10) ** (-0.244)
    k1, k2, k3 = _PLANT[d]
    plant = (
        k1 * np.exp(k2 * t_lo)
        + k3
        + np.where(
            t_adj > 200,
            55630 * (np.exp(-0.02319 * t_adj) - np.exp(-0.02319 * 200)),
            0.0,
        )
    )
    plant_cost = plant * size_mult * 1e3 * p["plantcost_adj"]  # USD/MW

    cased, uncased = egs_well_costs(d, lateral_length_m, drill_cost_multiplier)
    stim = p["stim_cost"] * lateral_length_m / 2286
    if stimulate_producers:
        wells = cased * (1 + producers_per_injector) / p[
            "drill_success_rate"
        ] + stim * (1 + producers_per_injector)
    else:
        wells = (cased + uncased * producers_per_injector) / p[
            "drill_success_rate"
        ] + stim
    with np.errstate(divide="ignore", invalid="ignore"):
        wellfield_cost = wells / gross_mw  # USD/MW

    labor = (
        (
            0.25 * plant_size_mw**0.525 * 20 * 8760
            + 0.15 * plant_size_mw**0.65 * (24 + 24 + 17.5) * 2000
            + 0.075 * plant_size_mw**0.65 * (40 + 30 + 12) * 2000
        )
        / plant_size_mw
        * 1.8
        * 1.37
        * p["om_derating"]
        * p["laborcost_adj"]
    )
    fom = (
        plant_cost * (p["pp_maint"] + p["tandi"]) * p["om_derating"]
        + wellfield_cost * (p["wf_maint"] + p["tandi"]) * p["om_derating"]
        + labor
    )

    exploration = (
        p["pre_survey_cost"]
        / plant_size_mw
        * p["pre_survey_interest"] ** p["pre_survey_years"]
        + p["test_drilling_cost"] / plant_size_mw
    ) * p["test_drilling_interest"] ** p["test_drilling_years"]
    well_capex = (
        (exploration + wellfield_cost)
        * p["drilling_interest"] ** p["drilling_years"]
        * p["construction_interest"] ** p["construction_years"]
    )
    plant_capex = plant_cost * p["construction_interest"] ** p["construction_years"]

    return {
        "production_temperature": t_prod,
        "gross_mw": gross_mw,
        "thermal_mw": thermal_mw,
        "plant_capex": plant_capex / 1e3,
        "well_capex": well_capex / 1e3,
        "fom": fom / 1e3,
        "mw_per_km2": gross_mw / wellfield_area_km2,
    }


def drilling_cost(depth_m, constant=250_000.0, linear=700.0, quadratic=0.2):
    """
    Cost of one vertical geothermal well (EUR2020) at `depth_m`, ThermoGIS
    quadratic drilling-cost curve as configured in the TU Delft
    `module_geothermal`.
    """
    depth_m = np.asarray(depth_m, dtype=float)
    return constant + linear * depth_m + quadratic * depth_m**2


def doublet_costs(
    reservoir_temperature_c,
    depth_km,
    stimulated,
    reinjection_temperature_c=40.0,
    flow_hydrothermal=100.0,
    flow_stimulated=60.0,
    pump_cost=0.6e6,
    stimulation_cost=1.03e6,
    drilling=None,
):
    """
    Heat output and investment of one heat-only geothermal doublet.

    Parameters
    ----------
    reservoir_temperature_c : array-like
        Rock temperature at the depth, °C.
    depth_km : float
    stimulated : array-like of bool
        True where the reservoir must be stimulated (petrothermal), which
        lowers the flow and adds the stimulation cost.
    drilling : dict, optional
        ThermoGIS curve coefficients passed to `drilling_cost`.

    Returns
    -------
    dict of arrays
        `production_temperature` (°C at the wellhead), `thermal_mw` per
        doublet and `capex` (EUR2020 per doublet).
    """
    t_res = np.asarray(reservoir_temperature_c, dtype=float)
    stimulated = np.broadcast_to(np.asarray(stimulated, dtype=bool), t_res.shape)
    t_prod = t_res - wellbore_temperature_loss(depth_km)
    flow = np.where(stimulated, flow_stimulated, flow_hydrothermal)
    thermal_mw = flow * CP_BRINE * (t_prod - reinjection_temperature_c) / 1e3
    capex = (
        2 * drilling_cost(depth_km * 1e3, **(drilling or {}))
        + pump_cost
        + np.where(stimulated, stimulation_cost, 0.0)
    )
    return {
        "production_temperature": t_prod,
        "thermal_mw": thermal_mw,
        "capex": capex,
    }


def annuity(lifetime, discount_rate):
    """Capital recovery factor."""
    if discount_rate > 0:
        return discount_rate / (1.0 - 1.0 / (1.0 + discount_rate) ** lifetime)
    return 1 / lifetime


def bin_supply_curve(
    frame, cost_column, capacity_column, bins, group="bus", extra=("capex",)
):
    """
    Aggregate cells into supply-curve steps per `group`: one row per (group, bin)
    with summed capacity and capacity-weighted means of `cost_column` and every
    column in `extra`. Cells above the last bin edge are dropped.
    """
    frame = frame.loc[(frame[capacity_column] > 0) & frame[cost_column].notna()]
    frame = frame.loc[frame[cost_column] <= bins[-1]].copy()
    edges = [-np.inf, *bins]
    frame["step"] = np.digitize(frame[cost_column], edges[1:], right=True)
    weighted = [cost_column, *extra]
    w = frame[capacity_column]
    agg = frame[weighted].mul(w, axis=0)
    agg[capacity_column] = w
    agg[[group, "step"]] = frame[[group, "step"]]
    out = agg.groupby([group, "step"]).sum()
    out[weighted] = out[weighted].div(out[capacity_column], axis=0)
    return out.reset_index()
