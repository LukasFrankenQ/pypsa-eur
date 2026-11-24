# Validation Log: 2024 Back-cast

The solved network of 2024 is compared against Redeia actuals in one report
(`results/validation/validation_{variant}.csv` and `.yaml`, from
`scripts/validate_network.py`). The **raw** variant is the fresh PyPSA-Eur build with
only the France boundary closure; the **patched** variant adds the cited corrections
below, under the [rules of the README](../README.md#the-rules-of-the-game). No output is
pinned to its historical value, except where marked "by construction".

Abbreviations: OMIE, the Iberian electricity market operator; MAE, mean absolute error
of the hourly price against OMIE's; rMAE, MAE relative to a naive forecast; CF, capacity
factor; CCGT and OCGT, combined- and open-cycle gas turbines; CHP, cogeneration; CSP,
concentrated solar power; PHS, pumped hydro storage; VOM, variable operation and
maintenance cost; ETS, EU emissions trading system; NTC, net transfer capacity; HVDC,
high-voltage direct current; GEM, the Global Energy Monitor plant tracker; ERA5, the
ECMWF reanalysis of the weather; EIA, the US Energy Information Administration; JRC, the
European Commission's Joint Research Centre; ENTSO-E, the European network of
transmission system operators; EEX, the European Energy Exchange; PVB, the Spanish gas
hub (punto virtual de balance); UGS and URT, the Portuguese gas network tariffs for
system use and transport; UTC, coordinated universal time; REE (Red Eléctrica), the
Spanish system operator, part of Redeia.

<a id="raw-findings"></a>

## 1. What a fresh PyPSA-Eur v2026.09.0 build gets wrong for Spain

| Finding | Raw vs actual | Cause |
|---|---|---|
| Wind −21% (−13% with resource classes, §2) | 46.8 vs 59.5 TWh; fleet CF 16.8% vs ~22% | (i) the fleet profile averages over all eligible land, not where turbines stand; (ii) ERA5 underestimates Iberian ridge winds; (iii) GEM regional allocation is off (10.2 GW in Aragón vs ~5.5 real) |
| Solar +46% | 41.3 GW vs 32.5 GW grid-connected | the inventory counts total PV including ~9 GW of self-consumption, which does not belong above busbar demand |
| Hydro −30% | output 24.3 vs 34.9 TWh | upstream normalizes inflow to EIA statistics, which end before 2024 (a wet year), giving an average year |
| No CO2 price | CCGT marginal cost ≈ 81 EUR/MWh (gas + VOM only) | the composed network has no ETS price (lignite at 28 EUR/MWh would run baseload) |
| Nuclear fuel 3× too expensive | nuclear marginal cost 27.3 EUR/MWh_el, above biomass (20.0) | technology-data v0.15.0 labels its nuclear fuel price (7.45) EUR/MWh_th, but the EIA source reports it per kWh of net generation, so dividing by η = 0.326 counts the efficiency twice |
| CHP missing | 0 vs 16.4 TWh, ~6 Mt CO2 | electricity-only PyPSA-Eur has no cogeneration |
| CSP missing | 0 vs 4.1 TWh | carrier not built by default |
| Borders over-trade | France pushes 28.0 TWh into Spain | ES-FR corridor capability 5.2 GW (3 AC lines × 0.7 + 2 × 1 GW INELFE) vs 2.8 GW commercial NTC |
| PHS and hydro artifacts | PHS 7.4 GW at 295 h; reservoir 1,402 h; run-of-river 0.5 vs 2.2 GW | JRC hydro database counts river-cascade dams as storage |
| Biomass and waste unconstrained | ~98% CF vs ~39% real | fuel-limited plants without an availability cap |
| Phantom lignite (ES 0.51 GW, FR 0.98 GW) and mainland oil | lignite runs 4.1 TWh baseload (coal group 4.1 vs 3.0 TWh) | stale inventory: no operating lignite in Spain since 2022, nor in France |
| Demand −3.4% | ENTSO-E vs Redeia busbar definition | a closure, not an error |
| Portugal | hydro 10.3 vs 14.9 TWh; biomass ~100% CF; wind CF 14.1% vs 29.2%, with 4.2 of 5.6 GW in the worst CF bin | the same EIA gap and biomass issue; the ERA5 bias compounded by wrong GEM plant coordinates |

## 2. Config-level fix first: wind resource classes

`renewable.onwind.resource_classes: 5` splits each region into 5 capacity-factor bins,
and existing plants are joined to the bin where they stand, so the fleet profile
reflects actual siting. Spain's fleet CF moves from 16.8% to 18.5%, about a third of the
bias; France lands at 24.1%, above the realized 21.8% (42.8 TWh, RTE); Portugal stays at
14.1% (ERA5 and plant coordinates). The wind factor below covers only the residual.

<a id="patches"></a>

## 3. The patch set (`prepare_network.py`; each switchable, all inputs cited)

| Patch | What it does | Data |
|---|---|---|
| `boundary_flows` | France's external borders (GB, IT, CH, DE+BE) fixed to measured hourly 2024 flows; on in every variant (a closure, not a calibration) | RTE eCO2mix extract, vendored; −86.1 TWh net checks against RTE's ≈ −89 TWh total minus Spain |
| `national_capacities` | Fleet totals per carrier and scope (ES peninsular, Balearics, PT, FR) set pro rata to published actuals; creates missing units with technology-data efficiency and CO2 intensity (ES coal, Balearic OCGT, France's 1.5 GW OCGT); zeroes the phantom lignite. Portugal's CCGT is the market-dispatched fleet (2,839 MW, without Tapada do Outeiro, §5). Wind and solar use average operating capacity (the mean of year-end 2023 and 2024), with output growing month by month ([step 27](#step-27)). French solar is the total including self-consumption, which RTE's consumption and solar output include; Portuguese solar is the grid-connected fleet of REN's balance ([step 24](#step-24)). Turbine capacities of pumped storage and reservoirs: mainland Spain as Redeia books them (mixed pumped storage with the reservoirs), Portugal and France as REN and RTE report them ([step 23](#step-23)); Portuguese and French reservoirs take their cited energy capacity (REN 3.27 TWh, RTE 3.18 TWh; [step 25](#step-25)) | `national_capacities.csv` (Redeia, MITECO PNIEC, JRC, Chaves, Huclin & Ramos, RTE, REN, ERSE) |
| `wind_correction` | Onwind profiles scaled for the residual ERA5 bias, capped at 1.0. The factor is derived at run time: the factor at which the capped profiles hold the cited generation, in Spain before curtailment ([steps 17](#step-17), [28](#step-28)) | ES: Redeia, curtailment: Deloitte for APPA, APPA; PT: `pt_actuals.csv` (Ember extract, vendored); FR: `fr_actuals.csv` (RTE) |
| `solar_tracking` | 60% of mainland Spain's solar PV capacity moves from the fixed panels of upstream (35° tilt) to the single-axis tracker profile that PyPSA-Eur builds with atlite (`solar-hsat`), per node and resource class; one factor, derived at run time, then holds the cited solar PV energy before curtailment ([steps 19](#step-19), [28](#step-28)) | `solar_tracking.csv` (share derived on the back-cast, cross-checked with [Victoria & Andresen 2019](https://arxiv.org/abs/1807.10044)); `validation_2024_es.csv` (Redeia) |
| `phs_split` | Pure PHS: 120 GWh of storage and 0.75 round-trip efficiency (Chaves, Huclin & Ramos), 3,306 MW of pumping (JRC); mixed PHS stays inside reservoir hydro, as Redeia reports it | `national_capacities.csv` |
| `pumped_storage_limits` | Pumped storage pumps at its pumping capacity (France 4,300 MW, Portugal 3,585 MW); upstream lets the units pump at turbine capacity (6.1 and 4.7 GW). Portuguese storage energy set to the plants' reported 453 GWh (upstream 1,701 GWh) ([steps 6](#step-6), [8](#step-8), [10](#step-10)) | `national_capacities.csv` (RTE, REN, JRC) |
| `chp_profile` | Cogeneration as a fixed profile, distributed by nodal load. Spain: the monthly shape of the vendored Redeia cogeneration CO2 series, scaled to the published annual output of the mainland (16.38 TWh, 5,533 MW) and of the Balearics (0.04 TWh, 12 MW), 0.366 tCO2/MWh_el. Portugal: the monthly output of gas cogeneration in REN's balance (1.54 TWh, 640 MW; [step 20](#step-20)). By construction | `national_capacities.csv` (capacity), `validation_2024_es.csv` (Redeia output and CO2), vendored REData JSON (shape); `pt_cogeneration_monthly_2024.csv` (REN Data Hub) |
| `csp` | 2.3 GW of CSP with a solar profile rescaled to the realized 2024 capacity factor. By construction (no storage dispatch modeled) | `national_capacities.csv`, `validation_2024_es.csv` |
| `biomass_availability` | Biomass and waste `p_max_pu` capped at the realized capacity factor (ES 0.391, PT 0.563, FR 0.521) | cited generation ÷ capacity |
| `prices` | Thermal marginal cost = VOM + fuel/η + ETS × intensity/η, with realized hub prices (2024: MIBGAS PVB gas 34.71, CIF-ARA coal 14.83, ETS 65.24). The gas price is per MWh on higher heating value and is converted to the lower heating value of the efficiencies and emission factors (÷ 0.901: 38.52 EUR/MWh_th; [step 15](#step-15)). Nuclear fuel 2.43 EUR/MWh_th, the technology-data figure read per MWh_el as its EIA source reports it (marginal cost 11.9 instead of 27.3 EUR/MWh_el; §1). Portuguese gas plants also pay the gas network premium (+1.10 EUR/MWh_th, ERSE; §5) | `prices.csv`, technology-data (VOM), ERSE gas tariffs 2025–26, EIA Table 8.4 |
| `daily_fuel_prices` | On top of `prices`: every hour of a local day takes that day's gas price at the Spanish hub and EU allowance price. All gas plants of the model use the Spanish hub price (Portugal with its network premium); cogeneration keeps its fixed profile ([step 3](#step-3)) | `daily_fuel_prices_2024.csv` (MIBGAS; EEX auctions via Instrat) |
| `biomass_waste_fixed` | Spanish and Portuguese biomass and waste plants run at a fixed output, the realized capacity factor of `biomass_availability`: they burn the fuel they have, whatever the price. Their variable cost is kept ([step 5](#step-5)) | none |
| `demand` | Loads of each zone scaled to cited demand (mainland Spain 233.6, Balearics 6.1 and PT 51.4 TWh busbar; FR 442.2 TWh, RTE gross consumption) | `annual_demand.csv` |
| `hydro_inflow` | Spanish, Portuguese and French reservoir inflow scaled to observed 2024 natural hydro energy after turbine losses (ES ×1.270, PT ×2.419, FR ×0.899), closing the EIA gap; each month takes its share of the year from the water balance (measured output plus the change in stored energy, [step 29](#step-29)); Portuguese reversible plants receive the natural water they turbined (34% of the reservoir inflow) ([steps 16](#step-16), [25](#step-25)) | `validation_2024_es.csv`, `pt_actuals.csv`, `fr_actuals.csv` (eCO2mix), `generation_hourly_2024.csv`, `reservoir_levels_weekly.csv` (ENTSO-E) |
| `water_value` | Reservoir water in Spain, Portugal and France is valued at OMIP's baseload futures price for the day (Spain's contract for Portugal), the reference price on the latest bulletin before OMIE's auction, two or more days ahead: market information public before delivery. Releases cost it; spilled water forgoes it (price × turbine efficiency). Inflow, energy capacity and the cyclic level stay. `backcast.water_value_contract: week` takes the week-ahead contract instead ([steps 30](#step-30), [32](#step-32)) | `omip_day_contract_2024.csv`, `omip_week_ahead_2024.csv` (OMIP) |
| `reservoir_floor` | A share of Spanish reservoir hydro runs as must-run: per calendar month, the 10th percentile of its measured hourly output. The turbine capacity of the reservoir units falls by the floor, and their inflow by the water its energy takes through the turbines. A heuristic from measured output ([steps 7](#step-7), [22](#step-22)) | `generation_hourly_2024.csv` (ENTSO-E via Energy-Charts) |
| `es_hydro_availability` | Spanish reservoir plants take an hourly upper limit that keeps all hydro output under 11.8 GW, the highest the fleet delivered in 2019–2025 (20.4 GW installed). Energy is untouched ([step 12](#step-12)) | `hydro_output_ceiling.csv` (Energy-Charts, derived with `scripts/retrieve_hydro_ceiling.py`) |
| `fr_nuclear_profile` | French nuclear availability shaped by the measured 2024 monthly output (36.6 GW in June to 49.3 GW in December), preserving the annual factor (0.715). Kept for physical realism, although it does not help the net-exports gate (§5) | `fr_nuclear_monthly_2024.csv` (eCO2mix via ODRE, vendored) |
| `es_unit_outages` | Mainland nuclear, combined-cycle and coal plants follow, hour by hour, the outages their owners declared to OMIE in 2024 (unavailable 6.2, 14.5 and 3.3 TWh); shutdowns for market reasons are not declared, so the model decides them. Nuclear keeps 60% of its available capacity, the depth of market-driven load reductions (CSN). Replaces the monthly nuclear availability of step 9 ([steps 26](#step-26), [31](#step-31)) | `es_unit_outages_2024.csv` (OMIE `indisp`), `es_unit_capacities.csv` (Foro Nuclear, CNMC, Redeia), `national_capacities.csv` (CSN) |
| `security_gas_floor` | Mainland gas plants keep 1,060 MW running, shared by capacity: the output the system operator orders for grid security when the market schedules no gas. A plant at its minimum does not set the price ([step 11](#step-11)) | `security_gas_floor.csv` (OMIE unit programs, derived with `scripts/retrieve_security_gas_floor.py`) |
| `coal_floor` | Mainland coal keeps 243 MW running, the minimum stable output of Aboño 2: the unit burns the blast-furnace gases of the ArcelorMittal steelworks and supplies its 220 kV zone, so it runs whatever the price ([step 13](#step-13)) | `coal_floor.csv` (OMIE unit programs, derived with `scripts/retrieve_coal_floor.py`); BOE-A-2024-10144 |
| `start_up_costs` | Gas and coal plants in all three countries carry the start-up cost of the upstream unit-commitment table (combined cycle 60, open cycle 24, coal 49 EUR per MW started), and so do Spanish nuclear plants (250 EUR per MW); the solve charges it in linear form, on each MW of added output ([steps 14](#step-14), [18](#step-18)) | `pypsa-eur/data/unit_commitment.csv` (DIW, IRENA, Agora Energiewende) |
| `ma_ad_exports` | Fixed flat Spanish exports to Morocco (2.54 TWh) and Andorra (0.24 TWh) at the nearest nodes | `validation_2024_es.csv` |
| `bidding_zones` | Applied after the data patches, which work on the nodes: the nodes merge into the bidding zones the day-ahead market clears (mainland Spain, Balearics, Portugal, France), the storage units of each zone merge into one per carrier, and lines and HVDC links give way to one transport link per border at the published NTC (ES→FR and FR→ES 2.8 GW, ES→PT 4.2 GW, PT→ES 3.5 GW, peninsula–Balearics 0.4 GW) ([steps 1](#step-1), [25](#step-25)) | `ntc.csv` |
| `drop_empty_generators` | Removes generators below 1 MW: resource classes and carriers without a fleet (100 of 353, 3.6 MW in total). Results unchanged | none |

<a id="gates"></a>

## 4. Result: 8 of 8 gates pass

| Gate | Raw | Patched | Status |
|---|---|---|---|
| Wind (±15%) | −13.2% | +0.7% | PASS |
| Solar PV (±15%) | +46.2% | +8.9% | PASS (available energy by construction, steps 19 and 28) |
| Nuclear (±10%) | +4.3% | +3.7% | PASS |
| Hydro (±20%) | −30.4% | 0.0% | PASS (energy by construction, steps 16 and 25) |
| CCGT+CHP (±25%) | −60.5% | −8.6% | PASS |
| CO2 (±20%) | −36.5% | −2.7% | PASS |
| Load shedding (< 0.01%) | 0 | 0 | PASS |
| Net exports (sign and ±50%) | −196% | +13.4 vs +10.2 TWh (+30.9%) | PASS |

Values of the current model (`results/validation/validation_patched.csv`), hourly solve.

## 5. The net-exports gate

The gate was first closed on the 15-node model of step 0; the current values are in §4
and the remaining border errors in §6. Three findings of that work still hold.

1. **French nuclear profile: kept as physics, not for the gate.** Shaping French nuclear
   by the measured monthly output made the gate *worse*: more winter nuclear lets France
   export more cheap energy into Spain, displacing Spanish CCGT.
2. **Transmission capacity does not limit the totals.** Measured 2024 exchanges with
   France reached 3.6 GW both ways, above the published NTC of 2.8 GW; a cap tight
   enough to force the totals (~1.7 GW) would contradict the measured flows.
3. **Portuguese gas misordering: fixed with cited inputs.** Portugal's cheapest CCGT
   undercut Spain's cheapest through an inventory-noise efficiency edge, so the model
   ran Portuguese gas at 12.9 TWh against ~3 TWh real, suppressing ES→PT exports and
   Spanish CCGT and CO2. Two corrections from the ERSE gas tariff decision 2025–26
   (`erse_tep_gas_2025_2026`): (a) the Portuguese **gas network premium**, +1.10
   EUR/MWh_th on top of the MIBGAS hub price (UGS parcela I 0.71 + firm entry/exit URT ≈
   0.39), a lower bound since Sines LNG regasification is not counted; (b) **Tapada do
   Outeiro (990 MW)** ended its power purchase agreement in Q1 2024 and is held in
   security-of-supply reserve until 2026-03-31, so it leaves the market-dispatched
   fleet.

<a id="caveats"></a>

## 6. Known caveats

- By construction, not validation successes: the hydro energy available (steps 16, 22,
  25), wind and solar energy available (steps 17, 19, 28), cogeneration, solar thermal,
  biomass and waste output.
- Curtailment: the model curtails about half of the 9.4 TWh Spain curtailed in 2024
  (step 28), all of it wind, while solar runs above its published output: wind and solar
  bid alike, so the model has no reason to cut one before the other.
- The wind factors and the tracker share are derived in-sample, on 2024.
- Coal runs about 16% above its published output. Mainland coal above Aboño 2's minimum
  enters the merit order at 107 EUR/MWh, where the other units offered at 128 to 137
  EUR/MWh in 2024 or did not offer (OMIE day-ahead offers), and the two Balearic units,
  an emergency reserve, run far too much. Oil and gas turbines produce almost nothing.
- Nuclear runs about 4% above its published output: a linear model cannot switch whole
  units off for market reasons (Almaraz I and Cofrentes in March 2024), so the 60%
  minimum of step 31 holds them on.
- The net-exports gate passes on the total; its parts carry compensating errors: Spain
  imports slightly too much from France and exports about a third too much to Portugal.
- The price is compressed: hours when OMIE cleared at 1 to 60 EUR/MWh are about 11
  EUR/MWh too high, hours above 130 about 21 too low, and the model has too few hours
  above 100 (step table of §7). By hour of the day, nights and solar hours are too high,
  evening peaks too low. Reservoirs set the price in most hours at one water value per
  day plus the scarcity value the model gives the year's water; in those hours the
  level is right on average, the hourly shape is not.

<a id="steps"></a>

## 7. Price back-cast, step by step

Each step is a switchable patch or a correction inside one, kept when it has a cited
physical or market reason and the gates of §4 hold; the MAE of the hourly price
(`results/validation/price_scorecard_patched.csv`) is reported for each, not used to
choose them. rMAE is the MAE relative to a naive forecast of the OMIE price (21.7
EUR/MWh). The daily spread is the mean of each day's highest minus lowest hourly price.
OMIE itself has a mean of 63.2 EUR/MWh, 12.1% of hours below 1 EUR/MWh, 25.4% above 100
and a daily spread of 72 EUR/MWh.

| Step | Change | MAE | rMAE | Mean | Hours < 1 | Hours > 100 | Gates |
|---|---|---|---|---|---|---|---|
| raw | Fresh PyPSA-Eur build with only the France boundary closure (no CO2 price; gas plateau at 81 EUR/MWh) | 31.9 | 1.47 | 69.0 | 0% | 0% | 3/8 |
| 0 | Starting point: 15 nodes with physical flows, borders capped at NTC | 33.2 | 1.53 | 81.8 | 0.6% | 0% | 8/8 |
| 1 | `bidding_zones`: one node per bidding zone, borders as NTC transport links | 34.1 | 1.58 | 83.0 | 1.6% | 0% | 8/8 |
| 2 | Annual gas price corrected: 38.08 → 34.71 EUR/MWh_th | 32.8 | 1.51 | 77.9 | 1.6% | 0% | 8/8 |
| 3 | `daily_fuel_prices`: daily gas and EU allowance prices | 27.9 | 1.29 | 76.9 | 1.6% | 9.6% | 8/8 |
| 4 | `water_value`: reservoir releases cost the monthly mean day-ahead price | 22.5 | 1.04 | 70.9 | 1.5% | 13.5% | 8/8 |
| 5 | `biomass_waste_fixed`: Spanish and Portuguese biomass and waste at a fixed output | 22.3 | 1.03 | 70.6 | 1.8% | 13.5% | 8/8 |
| 6 | `pumped_storage_limits`: French pumping at its cited capacity | 22.3 | 1.03 | 70.6 | 1.8% | 13.5% | 8/8 |
| 7 | `reservoir_floor`: must-run share of Spanish reservoir hydro | 21.7 | 1.00 | 70.3 | 2.6% | 13.9% | 8/8 |
| 8 | `pumped_storage_limits`: Portuguese pumping capacity and storage energy | 21.1 | 0.97 | 69.6 | 3.0% | 15.0% | 8/8 |
| 9 | `es_nuclear_availability`: Spanish nuclear outage calendar of 2024 | 20.5 | 0.95 | 68.6 | 3.0% | 13.1% | 8/8 |
| 10 | French inputs corrected to RTE's 2024 actuals (load, solar, wind, bioenergy, pumping) | 20.4 | 0.94 | 68.5 | 3.0% | 13.2% | 8/8 |
| 11 | `security_gas_floor`: gas the system operator requires for security runs outside the merit order | 19.0 | 0.88 | 65.9 | 3.7% | 12.8% | 8/8 |
| 12 | `es_hydro_availability`: Spanish hydro limited to the highest output the fleet has delivered | 18.7 | 0.86 | 66.6 | 3.7% | 13.6% | 8/8 |
| 13 | `coal_floor`: Aboño 2 runs at its minimum output (steelworks gases, 220 kV zone) | 18.4 | 0.85 | 65.3 | 3.9% | 13.5% | 8/8 |
| 14 | `start_up_costs`: linearized start-up costs of gas and coal plants | 18.4 | 0.85 | 68.0 | 3.9% | 17.0% | 8/8 |
| 15 | Gas price converted to the lower heating value (÷ 0.901) | 19.0 | 0.88 | 73.3 | 3.9% | 31.0% | 8/8 |
| 16 | `hydro_inflow`: inflow scaled to the observed energy after turbine losses | 18.4 | 0.85 | 72.3 | 3.9% | 24.5% | 8/8 |
| 17 | `wind_correction`: factor solved so that the capped profiles hold the cited wind energy | 17.7 | 0.82 | 71.3 | 4.3% | 23.9% | 8/8 |
| 18 | `start_up_costs`: Spanish nuclear plants take the start-up cost of the same table | 17.5 | 0.81 | 70.6 | 10.5% | 23.9% | 8/8 |
| 19 | `solar_tracking`: 60% of the Spanish solar fleet on single-axis trackers, cited solar energy held | 17.7 | 0.82 | 71.3 | 8.3% | 21.2% | 8/8 |
| 20 | `chp_profile`: Portuguese gas cogeneration at its monthly output (REN) | 17.4 | 0.81 | 70.8 | 8.4% | 21.2% | 8/8 |
| 21 | Spanish demand and cogeneration per zone; the price compared with OMIE is the mainland price | 17.2 | 0.80 | 70.4 | 8.5% | 21.1% | 8/8 |
| 22 | `reservoir_floor`: the floor takes its water after turbine losses | 17.3 | 0.80 | 70.6 | 8.5% | 20.9% | 8/8 |
| 23 | `national_capacities`: Portuguese and French pumped storage and reservoirs at their cited turbine capacity | 17.3 | 0.80 | 70.6 | 8.6% | 21.1% | 8/8 |
| 24 | `national_capacities`: Portuguese solar at REN's grid-connected fleet | 17.4 | 0.80 | 70.8 | 8.4% | 21.1% | 8/8 |
| 25 | Hydro structure: one storage unit per zone and carrier; Portuguese and French reservoir energy and French inflow as cited; Portuguese reversible plants take their natural inflow | 17.4 | 0.80 | 70.0 | 8.3% | 20.5% | 8/8 |
| 26 | `es_unit_outages`: mainland nuclear, gas and coal outages declared to OMIE, hour by hour | 17.7 | 0.82 | 70.8 | 8.0% | 20.6% | 8/8 |
| 27 | `national_capacities`: wind and solar output grows month by month with the fleet | 17.5 | 0.81 | 70.4 | 8.1% | 20.5% | 8/8 |
| 28 | Spanish wind and solar targets include the energy curtailed in 2024 | 15.7 | 0.72 | 66.6 | 10.4% | 20.8% | 8/8 |
| 29 | `hydro_inflow`: reservoir inflow by month from the water balance | 15.6 | 0.72 | 66.1 | 10.4% | 20.9% | 8/8 |
| 30 | `water_value`: reservoir water valued at OMIP week-ahead futures | 15.7 | 0.72 | 65.8 | 10.5% | 21.8% | 8/8 |
| 31 | `es_unit_outages`: Spanish nuclear keeps 60% of its available capacity (CSN) | 15.4 | 0.71 | 65.3 | 14.7% | 21.8% | 8/8 |
| 32 | `water_value`: OMIP day contract from the latest bulletin before OMIE's auction | 14.0 | 0.65 | 64.6 | 14.7% | 20.5% | 8/8 |

<a id="step-1"></a>**Step 1, bidding zones.** A structural choice, kept although the MAE
rises slightly. The day-ahead market clears one price per zone and leaves the grid
inside a zone to redispatch. In the 15-node model the internal lines bind in at most 3%
of hours, and lifting their limits leaves the price unchanged (MAE 33.2). With zones,
Spain and Portugal share a price in 93% of hours (15 nodes: 69%; OMIE: 94%), and the
solve takes a quarter of the time (14 s against 55 s in the solver), which later steps
need.

<a id="step-2"></a>**Step 2, annual gas price.** A data correction. The MIBGAS annual
report gives 38.08 EUR/MWh for the 2024 day-ahead product in its text, but its own
monthly table averages 34.73, and the daily MIBGAS data by delivery day average 34.71.
The gas plateau of the model drops from 95 to 89 EUR/MWh. Dispatch is unchanged, since
every gas plant moves alike.

<a id="step-3"></a>**Step 3, daily fuel prices.** Thermal marginal costs follow the
daily MIBGAS day-ahead price at the Spanish hub (22 to 52 EUR/MWh_th over 2024; February
25.5, December 46.1 on average) and the daily EU allowance auction price (50 to 75
EUR/t). Gas alone brings the MAE to 28.3, the allowance price to 27.9. The correlation
with OMIE rises from 0.57 to 0.74: the model now follows the winter rise and has its
first hours above 100 EUR/MWh.

<a id="step-4"></a>**Step 4, water value.** A heuristic taken from the back-cast year
itself, replaced in step 30. Without it, perfect foresight gives water one value for the
whole year, near the gas cost: the model holds water back in the wet spring and releases
it in November and December, when gas is dearest (hydro in April 2.5 TWh against 4.1
actual; in December 4.9 against 2.5). Releases of reservoir hydro in Spain, Portugal and
France now cost the mean day-ahead price of the calendar month in their zone (OMIE;
Energy-Charts for France). Inflow, energy capacity and the cyclic level stay, so the
model still allocates the year's water and adds its own scarcity value on top. All three
zones are needed: with Spain alone the MAE is 23.5, because French and Portuguese
reservoirs keep setting the Iberian price through the borders.

<a id="step-5"></a>**Step 5, biomass and waste output.** These plants burn the fuel they
have: waste has to be incinerated, and biomass is paid per MWh under support schemes.
They do not back down when the price falls below their fuel cost (20 and 36 EUR/MWh in
the model). Their output in Spain and Portugal is now fixed at the realized capacity
factor; the variable cost stays, so the system cost still counts it. Measured data
confirm the behavior: in the hours with an OMIE price below 1 EUR/MWh, Spanish biomass
and waste ran at 0.6 GW, against 0.2 GW in the model before. Their output now equals the
published 5.8 TWh. French biomass and waste stay dispatchable, capped at their realized
capacity factor from step 10.

<a id="step-6"></a>**Step 6, French pumping.** A data correction with no visible effect
on the MAE (lower by 0.01). The upstream pumped-storage units pump at their full turbine
capacity (6.1 GW in France). The JRC database gives 3.5 GW of pumping capacity (a lower
bound: Revin reports none), and the measured maximum of 2024 is 3.6 GW. RTE's 4.3 GW
replaces the JRC sum in step 10; Portugal follows in step 8.

<a id="step-7"></a>**Step 7, reservoir floor.** A heuristic from measured output, not a
cited limit. Part of what is booked as reservoir hydro does not respond to price:
minimum river flows, and cascade plants with little storage. In the hours with an OMIE
price below 1 EUR/MWh, Spanish reservoir hydro ran at 2.3 GW on average, against 0.3 GW
in the model. The floor of a calendar month is the 10th percentile of the measured
hourly reservoir output (0.4 GW in summer to 3.3 GW in March; 11.1 TWh a year, 38% of
the reservoir inflow). It runs as must-run; the reservoir units keep the rest of the
capacity and of the water. The 5th and the 25th percentile give the same MAE (21.7 and
21.6 in a test with floors in all three zones), and floors in Portugal and France add
nothing. Hours below 1 EUR/MWh rise only to 2.6%: in surplus hours the price still stops
at the nuclear cost of 12 EUR/MWh, because nuclear plants back down in the model as they
do not in reality (steps 18 and 31).

<a id="step-8"></a>**Step 8, Portuguese pumped storage.** A data correction. In the
spring hours with an OMIE price below 10 EUR/MWh, Portugal pumped 0.9 GW on average
(measured), against 3.0 GW in the model, which soaked up the Iberian surplus through the
border. Two inputs were off. Pumping capacity: 4.7 GW upstream (the turbine capacity)
against 3,585 MW installed (REN, December 2024; the historic pumping peak is 3,011 MW).
Storage energy: 1,701 GWh upstream against 453 GWh reported by the plants in the JRC
database, a lower bound, since three plants (Venda Nova III, Foz Tua, Gouvães) report
none. The energy is what matters: with the pumping capacity alone the MAE does not move.
Portuguese pumping falls from 7.4 to 6.0 TWh a year (4.7 TWh measured, REN).

<a id="step-9"></a>**Step 9, Spanish nuclear availability.** Replaced in step 26.
Upstream gives Spanish nuclear one flat availability of 0.87 (6.2 of 7.1 GW). In 2024
the whole fleet was online in January and from July to September (7.0 GW measured),
while 4.7 to 4.9 GW ran from March to May and 5.0 GW in November, through refueling
outages and shutdowns at low prices. The availability of a month is now the highest
measured hourly output of that month over the installed capacity (0.75 to 1.00);
dispatch stays free. It is an upper reading: a unit out for part of a month still counts
for all of it, so nuclear output is 54.3 TWh against 52.4 actual.

<a id="step-10"></a>**Step 10, French inputs.** Data corrections inside the existing
patches, with almost no effect on the Spanish price (MAE lower by 0.1). Five French
inputs were off against RTE's *Bilan électrique 2024*. Load followed the
weather-corrected consumption (449.2 TWh) and now follows the gross one (442.2 TWh).
Solar stayed at the upstream fleet (13.2 GW, 15.6 TWh), because the French capacity row
carried a label the patch does not read; with the average operating capacity of 2024
(22.0 GW) it gives 25.9 TWh (24.8 actual). Offshore wind stayed at the upstream 2.0 GW
and is now the average operating capacity (1.2 GW, 4.5 TWh against 4.0 actual); onshore
wind is averaged the same way and its profile takes a bias factor of 0.907, derived as
for Spain and Portugal (42.8 TWh). Biomass and waste ran at 80% of capacity (16.2 TWh)
and are now capped at the realized 52% (10.5 TWh; 8.5 TWh dispatched). Pumping capacity
moves from the JRC plant sum (3,513 MW, a lower bound) to RTE's 4.3 GW; French pumping
is 7.4 TWh, as measured. French hydro inflow follows in step 25. Net imports from France
stay at 8.4 TWh (3.0 actual): the French volumes were not what drives them. The offshore
row also sets the Spanish prototypes to their 2 MW (20 MW upstream).

<a id="step-11"></a>**Step 11, security gas floor.** A requirement of the system
operator, not a market result. After the day-ahead market clears, Red Eléctrica checks
the schedule against its security criteria and orders plants up where it fails them
(phase 1 of the technical-constraint solution, operating procedure P.O. 3.2 in its 2024
version, [BOE-A-2024-6215 and
BOE-A-2024-11959](https://ree.es/sites/default/files/2024-08/PO_3_2_BOE-A-2024-6215-BOE-A-2024-11959.pdf)).
The procedure names the causes: the security criteria in normal operation and after a
contingency, and too little reserve for balancing, for voltage control in the
transmission grid or for restoring supply. In 2024 the operator scheduled 13.6 TWh up in
this phase (Red Eléctrica, *Servicios de ajuste e intercambios internacionales*, 2025
edition, p. 4: 13,588 GWh). OMIE's unit programs show the size for gas: 6.7 TWh in the
day-ahead program, 15.0 TWh after constraints, 29.1 TWh generated. Until this step every
gas plant of the model sat in the merit order, so gas set the price whenever it ran.

The floor is the gas the operator scheduled in the hours when the market itself
scheduled none (61% of the hours of 2024): 1.06 GW on average, about seven plants at
minimum output. Three things mark it as a requirement. It is never zero (0.1% of those
hours). It does not follow the price (correlation 0.12). It follows load, as voltage
control on a lightly loaded grid needs: about 1.5 GW at 21 GW of load and 0.8 GW at 30
GW. No figure published by the operator was found, so the number is estimated from the
programs of 2024, once. Each mainland gas plant takes a minimum output in proportion to
its capacity; a plant at its minimum is not marginal, and output above it stays in the
merit order.

The MAE falls from 20.4 to 19.0, most in the months where the model was too expensive
(the bias of February to June falls from 9 to 16 to 5 to 12 EUR/MWh). Gas runs above the
floor in 35% of hours; the day-ahead program held gas in 39%. The hourly redispatch
itself (program after constraints minus day-ahead program) was tested as the floor and
not used: it gives 19.3 and depends on how the market cleared (the hours without market
gas averaged 41 EUR/MWh, the others 101), so the series would carry the result into the
inputs. A floor that follows load gives 19.0 as well, so the constant stands.

<a id="step-12"></a>**Step 12, Spanish hydro availability.** Installed capacity is not
deliverable capacity. Mainland Spain has 20.4 GW of hydro turbines (17.1 GW of reservoir
and run-of-river plants, 3.3 GW of pure pumped storage), and the model used them all:
reservoir plus run-of-river output reached 12 to 16 GW in February to June and in
October, where the measured maximum of 2024 is 8.4 GW. The model shaved every evening
peak with capacity that is not there. The combined output of all Spanish hydro plants
never passed 11.8 GW in 2019–2025 (annual maxima 10.6, 10.7, 11.8, 9.8, 11.5, 11.7 and
11.8 GW; 2022 was dry). Head falls with the reservoir level, plants in a cascade cannot
all run at full output together, releases follow irrigation and minimum-flow rules, and
units are out for maintenance. The limit also held when the price asked for every
turbine: in the 1% most expensive hours of 2024 (OMIE above 158 EUR/MWh) hydro output
reached 11.4 GW.

The ceiling is that record, 11.8 GW, a property of the fleet taken from seven years and
not from the back-cast year. Reservoir plants take an hourly upper limit that keeps
reservoir plus run-of-river output under the ceiling less the 3.3 GW of pure pumped
storage (8.5 GW), assumed at capacity when the limit binds. Energy is untouched. The MAE
falls from 19.0 to 18.7 and the daily spread rises from 40 to 43 EUR/MWh. The gain is in
the shape of the day, not in the level: the bias of March to June rises by 1 to 4
EUR/MWh, because water that could no longer be released at the peak is not all used in
those months. The monthly maxima of 2024 were tested as the limit and fit slightly
better (MAE 18.6), but they are that year's dispatch, a market result; the multi-year
record is not. Before 2022 the data report pumped storage inside the hydro total, so the
ceiling is defined on the total.

<a id="step-13"></a>**Step 13, coal floor.** Spanish coal output in 2024 is one unit
that runs for reasons the power price does not touch. Aboño 2 (Asturias) is coupled to
the ArcelorMittal steelworks next to it: it burns coal together with the residual
blast-furnace gases of the works. Those gases cannot be vented for their carbon
monoxide, so without the unit they would be flared. The unit also supplies the
industrial load of its 220 kV zone: the environmental report on its conversion to gas
(BOE-A-2024-10144) rules out closing it as "not technically viable" for that reason. The
system operator therefore orders it up after the day-ahead market. OMIE's unit programs
hold Aboño 2 in 1,307 hours of the day-ahead program (0.34 TWh) and in 7,566 hours of
the program after constraints (2.28 TWh); the other coal units (Aboño 1, Soto 3, Los
Barrios) add 0.16 TWh. In the model coal sat in the merit order at 107 EUR/MWh and
produced 1.2 TWh against 3.0 actual.

The floor is the unit's minimum stable output, 243 MW: the level at which the operator
schedules it most often (3,588 of the 7,566 hours). Measured Spanish coal output never
fell below 144 MW in 2024 and stood at or above 244 MW in 95% of hours. The mainland
coal fleet takes that minimum output; at its minimum it does not set the price. Coal
output moves to 3.1 TWh (3.0 actual) and its emissions to 3.0 Mt (2.9 actual), neither
fitted. The MAE falls from 18.7 to 18.4. The mean measured coal output (340 MW) as the
floor fits slightly better (18.3) but is an outcome, and it overshoots the volume (3.9
TWh). Aboño 2 was converted to gas in 2025, so the floor belongs to the 2024 fleet.

<a id="step-14"></a>**Step 14, start-up costs.** A step for the shape of the day, not
for the MAE, which stays at 18.4. In January and December the model sat on the gas plant
cost in 70% of hours; OMIE did so in 8 to 9% of them, and moved between 0.7 times that
cost at night and 1.3 times in the evening. The daily spread was 59 and 66 EUR/MWh in
OMIE against 28 and 42 in the model. The model also burnt far more gas at the evening
peak than was measured (15 GW against 7.5 in January and 10 in December), because with a
flat price neither pumped storage nor reservoir hydro has a reason to wait for the
evening: in January the three countries' storage and hydro delivered 7.5 GW less at the
peak than measured, and Spanish gas filled the gap.

Starting a thermal plant costs fuel and wear before it sells any energy. A plant called
for a few evening hours must earn that back in those hours, and a plant kept on
overnight accepts a price below its fuel cost to avoid a new start. A start-up cost is
paid per MW of capacity started, not per MWh. The model does not track which capacity is
on: it takes the capacity a gas or coal plant starts in an hour to be the increase of
its output over the previous hour. This keeps the problem linear and the solve time
unchanged (the linearized unit commitment, with its commitment variables, took five
minutes). It is an approximation: exact when a plant starts and goes to full output, too
low when it starts to part load, too high when a running plant ramps up. The cost is the
one PyPSA-Eur ships in `data/unit_commitment.csv` (DIW, IRENA, Agora Energiewende): 60
EUR/MW for combined-cycle plants, 24 for open-cycle turbines, 49 for coal. Spanish gas
plants declare about the same in their own day-ahead offers: the fixed term of their
minimum-income condition has a median of 69 EUR per MW offered in 2024 (OMIE offer
headers `cab`, 52 units and 15,456 unit-days, a fixed term on 22% of them; quartiles 40
to 86 EUR/MW; own aggregation, not part of the workflow). Under market rule 28.1.2.1
(BOE-A-2024-11958) such an offer only stands if the day's revenue covers a fixed amount
in EUR plus a variable amount per MWh.

The daily spread rises from 42 to 55 EUR/MWh and the hours above 100 EUR/MWh from 13.5%
to 17.0%. At the January evening peak Spanish gas falls from 15.2 to about 12 GW, hydro
and pumped storage move to the peak, and the exchange with France turns from export to
import, as measured. The MAE does not fall because the mean price rises by 2.7 EUR/MWh
(68.0 against 63.2 in OMIE): the months that were too expensive get more expensive (the
bias of February to June rises by about 2). With the cost on gas plants alone the MAE is
18.3; coal is kept because its output stays closer to the actual (3.2 TWh against 3.7
without, 3.0 actual).

<a id="step-15"></a>**Step 15, heating value of the gas price.** A unit correction that
raises the MAE, kept because it is right. Gas is traded per MWh on higher heating value:
the Spanish gas system rules measure energy on it (PD-01, section 6.2: "referido al
poder calorífico superior"), as does the EU network code (Regulation 2015/703, article
13: "kWh (based on GCV)"); MIBGAS's own rules do not state the basis. Plant efficiencies
are on lower heating value (Danish Energy Agency technology catalog, the source of
technology-data: "expressed in percent at lower calorific heat value"), and so are the
emission factors (IPCC 2006). The model divided one by the other. The ministry's
conversion factor for natural gas is 0.901 (MITECO), so the fuel cost of gas plants was
about 11% too low: 34.71 EUR/MWh_th becomes 38.52 on lower heating value, and the
cheapest Spanish gas plant moves from 88.6 to 94.9 EUR/MWh on average. The Portuguese
network premium is converted the same way. A cross-check points the same way: the
emissions and output of Spanish combined-cycle plants in 2024 (12.0 Mt, 32.0 TWh) imply
an operating efficiency of about 54%, below the 57% of the model.

Winter improves: the bias of November and December moves from −7 and −13 to 0 and −6
EUR/MWh, and their MAE from 17.0 and 21.6 to 15.1 and 17.4. Every month from February to
October gets worse (bias +11 to +19), and the mean price is 73.3 against 63.2 in OMIE;
the MAE over the year rises from 18.4 to 19.0. The gas cost that was too low had been
hiding another error: gas sets the price of the model in too many hours outside winter.
With the mean bias removed the MAE would be 17.5, with the monthly bias removed 16.3.

<a id="step-16"></a>**Step 16, hydro inflow after turbine losses.** A correction inside
`hydro_inflow`. The patch scales reservoir inflow so that the model can produce the
hydro energy observed in 2024, but it compared the observed energy, which is turbine
output, with the inflow before the dispatch efficiency of the reservoir units (0.9).
Spanish hydro therefore produced 32.4 TWh against 34.9 observed, and gas filled the gap
(34.9 TWh from combined-cycle plants against 32.0). The inflow is now counted after that
efficiency (ES ×1.270, PT ×2.419). Spanish hydro reaches 34.8 TWh and combined-cycle
output 32.4 TWh; the hours above 100 EUR/MWh fall from 31.0% to 24.5% (OMIE 25.4%), the
mean price from 73.3 to 72.3 and the MAE from 19.0 to 18.4. (The Spanish hydro energy of
this step was 1.1 TWh too high; step 22 corrects it. Step 25 removes the spill.)

<a id="step-17"></a>**Step 17, wind energy after the cap.** A correction inside
`wind_correction`. The factor was cited generation over modeled availability, and the
scaled profiles were then capped at full output; the energy above the cap was lost: 1.3
TWh in Spain and 1.9 TWh in Portugal, which gas and imports filled. The factor is now
the one at which the capped profiles hold the cited generation (ES ×1.219 instead of
×1.187, PT ×2.524 instead of ×2.053; France is below the cap and unchanged). Spanish
wind output is 58.1 TWh against 59.5 cited (the rest is curtailed in the model); the MAE
falls from 18.4 to 17.7.

One factor per country is a simple correction of the wind-speed bias of ERA5. It lifts
every hour alike and flattens the windiest hours at full output: the Spanish fleet peaks
at 96% of its capacity in the model and at 65% in the measured data (open item
[C5](decision_log.md#c-open-items)). The Portuguese factor above 2 shows that the
profile itself is poor there (plant coordinates).

The step corrects the energy, not the hourly shape. Against measured hourly wind the fit
does not change (correlation 0.89 in Spain and 0.90 in Portugal before and after). The
model lifts the calm and normal hours, where it had too little wind, and adds to the
windiest fifth of hours, where it already had too much (Spain +2.5 GW before, +2.8 GW
after).

<a id="step-18"></a>**Step 18, start-up cost of Spanish nuclear plants.** An extension
of `start_up_costs`, for the hours of surplus. Spanish reactors run at constant output:
in 2024 the output of the fleet moved by 0.14 GW within the average day and fell below
90% of the day's maximum in 3% of hours. The model cycled them like a flexible plant:
2.2 GW within the average day, below 90% of the day's maximum in 14% of hours. In the
1,057 hours when OMIE cleared below 1 EUR/MWh the reactors produced 5.4 GW; in the model
they backed down to 2.8 GW and held the price at their fuel cost of 12 EUR/MWh.

The reactors now carry the start-up cost of the same upstream table, 250 EUR/MW, in the
same linear form as gas and coal. The figure is for a full start: a reactor that backs
down and returns pays it on the part it returns. That stands in for the inflexibility of
a reactor; it is not a measured cost of modulation. French reactors are left free
because they follow load: their output moves by 5.0 GW within the average day. With the
cost on them too the MAE is 17.4, but their output goes flat (0.2 GW within the day) and
its hourly error against the measured output rises from 2.6 to 3.0 GW.

Spanish nuclear output now moves by 0.11 GW within the average day and stands at 5.2 GW
in the hours when OMIE cleared below 1 EUR/MWh. The hours below 1 EUR/MWh rise from 4.3%
to 10.5%. The model is below 1 EUR/MWh in 57% of the hours when OMIE was (28% before);
of its own hours below 1 EUR/MWh, 65% are such hours in OMIE (79% before). The MAE falls
from 17.7 to 17.5; the root mean square error rises from 23.5 to 23.6 and the
correlation falls from 0.88 to 0.87, because a missed hour at zero is a larger miss than
one at 12 EUR/MWh. Nuclear output rises from 52.8 to 56.4 TWh (52.4 actual): the
availability of step 9 counts a unit that is out for part of a month as available for
the whole month, which the free dispatch had hidden (step 26 replaces it).

The step exposes an error of the inputs in winter. In January, February and December the
model is below 1 EUR/MWh in 239 hours (56 in OMIE), and in 99 of them OMIE is above 10
EUR/MWh (38 on average). These hours fall at midday (77 of 99) and in the windiest fifth
of hours (58 of 99): the model has 3.1 GW more solar output than was measured in them
and 2.3 GW more wind (step 19 takes up the solar part). The value of the cost is not the
cause: between 100 and 500 EUR/MW the hours below 1 EUR/MWh stay at 10 to 11% of the
year, and 150 to 250 EUR/MW reproduce the measured movement of the fleet within the day
(0.20 and 0.11 GW against 0.14). A linearized unit commitment with the parameters of the
same table (minimum output 50%) gives the same MAE and lets the reactors move by 1.3 GW
within the day, so it is not kept.

<a id="step-19"></a>**Step 19, solar trackers.** A correction of the solar input; it
does not lower the MAE. Upstream holds the Spanish solar fleet as fixed panels at 35° of
tilt. The measured output has the shape of a tracker fleet, a flat top at midday and
wide shoulders: at noon in January the fleet delivered 9.1 GW where the model had 15.6
GW, and at 17:00 UTC in July 13.4 GW where the model had 6.8 GW (photovoltaic and solar
thermal output together). A horizontal single-axis tracker lies flat at noon, so it
yields less than a tilted panel under the low winter sun and more in the mornings and
evenings of summer.

Scaling each month's solar output to the measured one was considered first and rejected:
it moves numbers without a physical reason. No primary source states the share of
trackers in the Spanish fleet. It is derived on the back-cast: the mix of the fixed and
the tracker profile of atlite that best reproduces the measured hourly solar output of
2024 is 60% trackers (least squares over the daylight hours; 55% from March to August,
26% from November to February; own aggregation, not part of the workflow). Three sources
support the order of magnitude. [Victoria & Andresen
2019](https://arxiv.org/abs/1807.10044) find that 70% fixed panels and 30% trackers
"roughly represent the current configuration of PV plants in Spain" for the fleet of
2016 (4.7 GW). A market report puts trackers on roughly 70% of the capacity built in
2024 ([Mordor
Intelligence](https://www.mordorintelligence.com/industry-reports/spain-solar-energy-market),
not verified against primary data), and Spain took more than half of the European demand
for trackers in 2023 ([Wood Mackenzie, via PV
Tech](https://www.pv-tech.org/woodmac-solar-pv-trackers-hit-92gw-shipments-2023/)). If
70% of all capacity built since 2016 went on trackers, the fleet of 2024 held about 63%
(an assumption: the 70% is reported for 2024 alone).

The share moves from the fixed panels to the tracker profile node by node and resource
class by resource class. Trackers yield 18% more per MW in atlite, so the fleet would
hold 50.3 TWh against 43.7 TWh of cited generation; one factor (×0.869) on both profiles
holds the cited energy, as for wind. Without a factor the fixed fleet had held 45.3 TWh.

The solar input moves to the measured one: its hourly error falls from 1.58 to 0.95 GW
and its correlation rises from 0.945 to 0.975. The hours when the model is below 1
EUR/MWh and OMIE above 10 fall from 99 to 53 in winter and from 150 to 77 over the year.

The MAE rises from 17.5 to 17.7, for two reasons. The cited energy is 1.6 TWh below what
the fixed fleet held, in a model whose prices are too high: with the energy of before, a
split of 50 to 75% gives 17.1 to 17.2. And the peaked midday output of fixed panels had
hidden an error of the price level. The bias at midday (10 to 13 UTC) rises from +1 to
+3 EUR/MWh to +11 to +13, while that of the morning (7 to 8 UTC) falls from +14 to +18
to +9 to +12: the bias is now about the same through the day, which points to the price
in hours without gas and not to the solar shape.

<a id="step-20"></a>**Step 20, Portuguese cogeneration.** An extension of `chp_profile`
to Portugal. The model held no Portuguese cogeneration. REN's monthly balance of 2024
reports 4.86 TWh from natural gas in mainland Portugal: 3.33 TWh from combined-cycle
plants and 1.54 TWh from cogeneration (640 MW). The model produced 0.41 TWh from gas in
Portugal, and Spain filled the gap: it exported 15.8 TWh to Portugal where 10.5 TWh were
measured. Water was scarcer in Spain for it, and the model adds that scarcity to the
price in every hour when a reservoir is the marginal plant (60% of hours, about 20
EUR/MWh on top of the water value).

Portuguese gas cogeneration now runs as a fixed profile at its monthly output in REN's
balance (110 to 170 GWh a month), as Spanish cogeneration does: it follows the heat
demand of its industrial host, not the electricity price. Renewable cogeneration is part
of the bioenergy output the model already holds. Exports to Portugal fall from 15.8 to
14.2 TWh (10.5 actual) and net exports from 11.2 to 10.0 TWh (10.2); the MAE falls from
17.7 to 17.4 and the MAE of the Portuguese price to 18.8. Portuguese combined-cycle
plants stay far below the 3.33 TWh of REN's balance (open item
[C4](decision_log.md#c-open-items)).

<a id="step-21"></a>**Step 21, mainland and Balearics apart.** Corrections inside
`demand`, `chp_profile` and the validation. Spanish demand was scaled with one factor to
the sum of the mainland and the Balearics, and Spanish cogeneration was spread over both
by load. The sum was right and the parts were not: the mainland held 236.0 TWh of demand
against 233.6 published, the Balearics 3.7 against 6.1, and the Balearics 85 MW of
cogeneration against 12. Both are now set per zone from the rows Redeia publishes for
each.

The price compared with OMIE was the load-weighted mean of the two zones. OMIE's Spanish
price is that of the mainland market; generation in the Balearics is dispatched apart,
outside the system of offers ([Real Decreto
738/2015](https://www.boe.es/buscar/act.php?id=BOE-A-2015-8646)). The comparison now
takes the mainland zone alone. With the Balearic demand right, the mean of the two zones
would count the 483 hours when the mainland is below 1 EUR/MWh and the Balearics above
50 as hours above 1 EUR/MWh.

The MAE falls from 17.4 to 17.2 and rMAE from 0.81 to 0.80, because the mainland carries
2.4 TWh less demand. Balearic combined-cycle output rises from 0.4 to 1.9 TWh (2.9
actual) and net exports stand at 10.2 TWh (10.2 actual). Two things get worse: Balearic
coal produces 0.38 TWh (0.06 actual), and the link to the Balearics carries 2.9 TWh (1.6
actual).

Three smaller corrections ride along. A wind or solar unit is no longer created where
upstream holds none, since it would have no weather profile: the 3.6 MW of Balearic wind
ran at full output all year (Redeia reports no Balearic wind output). In the validation
report the mean price is the mean over the hours, as OMIE's is (70.4 against 63.0), and
capacity factors use the hours of the calendar year on both sides. rMAE compares the two
errors over the hours that have a naive forecast.

<a id="step-22"></a>**Step 22, water of the reservoir floor.** A correction inside
`reservoir_floor`. Step 16 counted the reservoir inflow after the turbine efficiency
(0.9), but the floor of step 7 still took its energy (11.1 TWh a year) out of the inflow
one for one, so it removed too little water. Spanish hydro could produce 36.1 TWh
against the 34.9 TWh observed; the model spilled 1.4 TWh, and the hydro gate (−0.4%) hid
the excess. The floor now takes the water its energy needs through the turbines, and the
three parts add up to the observed energy: 18.1 TWh from the reservoirs, 11.1 from the
floor, 5.7 from run-of-river plants. The MAE rises from 17.2 to 17.3.

<a id="step-23"></a>**Step 23, Portuguese and French hydro capacities.** A correction
inside `national_capacities`. The turbine capacities of pumped storage and reservoirs
were only read for mainland Spain: the Portuguese and French rows carry other labels,
because REN and RTE report all pumped storage as one class where Redeia books mixed
plants with the reservoirs. Both countries kept the upstream values. They now take the
cited ones:

| Turbine capacity (MW) | Upstream | Cited |
|---|---|---|
| Portugal, pumped storage | 4,742 | 3,532 (REN) |
| Portugal, reservoirs | 1,764 | 1,740 (REN) |
| France, pumped storage | 6,076 | 5,100 (RTE) |
| France, reservoirs | 8,613 | 10,100 (RTE) |

The energy capacity stays as it was (a reservoir does not change with its turbines), and
so does the pumping capacity, which `pumped_storage_limits` sets. With less pumped
storage to turn out at the peak, the daily spread rises from 51 to 54 EUR/MWh and the
95th percentile from 121.7 to 122.9 EUR/MWh (136.6 in OMIE). The MAE stays at 17.3. For
mainland Spain the energy capacity still grows with the turbine capacity that step 0 set
(open item [C8](decision_log.md#c-open-items)).

<a id="step-24"></a>**Step 24, Portuguese solar.** A correction inside
`national_capacities`. Portugal had no `solar PV` row, only the total including
self-consumption (5,130 MW), so its solar kept the upstream 3,921 MW and produced 5.95
TWh against 4.91 TWh in REN's balance. Portuguese demand in the model is grid
consumption, so the fleet is the grid-connected one REN reports: 2,620 MW at the end of
2023 and 3,883 MW at the end of 2024 (REN Data Hub, monthly balance), 3,252 MW on
average over the year. Portuguese solar now produces 4.94 TWh (4.91 published).

The MAE rises from 17.3 to 17.4 and the mean price from 70.6 to 70.8: less Portuguese
solar means more exports from Spain to Portugal, 15.1 TWh against 14.3 before (10.5
actual). As with the trackers of step 19, the input is now right and the price level
error of the hours without gas shows more.

<a id="step-25"></a>**Step 25, hydro structure.** Four corrections of how reservoirs and
pumped storage are represented.

- *One storage unit per zone and carrier* (`bidding_zones`). Upstream gives each plant
  its own storage (plants without reservoir data share the country's remaining energy)
  and shares the country's inflow by turbine capacity; clustering sums the plants per
  node. One Spanish node held 16.5 of the 19.8 TWh on 2 GW of turbines, others less than
  an hour of storage with a full share of inflow, so they spilled 1.24 TWh a year.
  Inside a zone without a grid the split has no physical meaning; the units of each zone
  now merge (capacities, energy and inflow summed, availabilities and costs weighted by
  turbine capacity). Spanish hydro output is now exactly the published 34.95 TWh.
- *French reservoirs*. Energy capacity 3.18 TWh instead of the upstream 8.0: the highest
  weekly lake stock RTE reports (2024 week 42; RTE publishes no capacity, so this is a
  lower bound). Inflow scaled, as in Spain and Portugal, to the 2024 natural hydro
  output of eCO2mix, 68.3 TWh (run-of-river and pondage 47.9, lakes 20.4; pumped storage
  left out), factor 0.899. The upstream 8 TWh let the model empty French lakes in winter
  and fill them in summer, against an observed stock between 1.3 and 3.2 TWh.
- *Portuguese reservoirs*. Energy capacity 3.27 TWh, REN's reference storage capacity
  (stored GWh over % of maximum, Data Hub), of which 2.81 TWh in the reservoir unit and
  0.45 TWh in the reversible units (instead of 1.6 + 0.45).
- *Portuguese reversible plants on river reservoirs* (Alqueva, Venda Nova, Frades, Baixo
  Sabor and others) turbine natural inflow as well as pumped water, as Spain's mixed
  plants do. Their measured 2024 output beyond the returned pumped energy (6.41 TWh
  minus 0.75 × 4.80 TWh) is natural water, 2.8 TWh; that share of the reservoir inflow
  (34%) moves to them. Before, all Portuguese natural water had to pass 1.74 GW of
  conventional turbines and 1.7 TWh spilled.

Spill falls to zero in Spain and Portugal; France spills 1.5 TWh, its 2024 inflow timing
against a 3.2 TWh reservoir. Net imports from France fall from 7.0 to 4.6 TWh (3.0
actual); exports to Portugal stay at 13.4 TWh (10.5). The MAE moves from 17.45 to 17.40
and the mean price from 70.8 to 70.0. The new Portuguese capacity alone costs about 0.2
of MAE (scratch runs): a larger Portuguese store keeps more water for the hours without
gas, where the model's level is already too high.

<a id="step-26"></a>**Step 26, declared outages.** Plants must declare every
unavailability to the market operator, and OMIE publishes the declarations (`indisp`):
unit, start, end and the capacity still available. `scripts/retrieve_unit_outages.py`
takes the latest version of each declaration and, per unit and hour, the capacity
declared unavailable; net capacities come from Foro Nuclear (nuclear, p. 26 of its 2024
results), CNMC reports (coal) and OMIE's offer headers (combined cycles, 24.6 GW, as in
the model). Shutdowns for market reasons are not declared, so the model still decides
them. The patch replaces the monthly nuclear availability of step 9, which counted a
unit out for part of a month as available for all of it, and adds combined cycles and
coal.

Unavailable in 2024: nuclear 6.2 TWh (refueling in April-May and October-December; the
first half of December has 5.1 GW available, as measured), combined cycles 14.5 TWh (0.5
to 2.7 GW by month) and coal 3.3 TWh (Aboño 1 under its running-hours limit most of the
year, boiler and condenser leaks at Los Barrios in November-December). Nuclear output
falls from 55.9 to 53.6 TWh (52.4 published), combined cycles rise from 30.6 to 32.6 TWh
(32.0), coal falls from 4.29 to 3.77 TWh (3.03).

The MAE rises from 17.4 to 17.7 and the mean price from 70.0 to 70.8: the extra nuclear
of the monthly availability had been lowering the price in the hours where the model's
level is too high. As in steps 19 and 24, the inputs are now right and the remaining
error shows.

<a id="step-27"></a>**Step 27, fleet growth within the year.** A correction inside
`national_capacities`. Wind and solar used the average operating capacity, the mean of
the year-end 2023 and 2024 figures, in every hour. The fleets grew during 2024 (mainland
PV 25.7 to 32.2 GW, French PV 19.4 to 24.5 GW, Portuguese PV 2.6 to 3.9 GW, mainland
wind 30.2 to 31.5 GW, French wind 21.8 to 23.0 GW), so January had less and December
more capacity than the average. Each month now scales the fleet's profiles by the
capacity in operation at mid-month, linear between the two year-end figures, over the
average; the energy patches that follow still hold the cited annual output.

Mainland solar against measured output moves from +28% to +15% in January and from +6%
to +17% in December; summer stays about 8% low. The same winter-high, summer-low pattern
shows in France and Portugal, so what remains is the shape of the upstream solar profile
(open item [C6](decision_log.md#c-open-items)), not the fleet. The MAE falls from 17.7
to 17.5.

<a id="step-28"></a>**Step 28, curtailed energy.** A correction inside `wind_correction`
and `solar_tracking`. Their Spanish targets were REE's published output, which is
measured after curtailment: offers not matched in the day-ahead market and energy the
system operator schedules down for technical constraints. The model was given too little
wind and solar energy, and it lacked it in exactly the surplus hours that set prices
near zero. Two published figures give the energy available: 6.5% of the renewable energy
offered in OMIE's day-ahead market was not matched (Deloitte for APPA, 2024 study, p.
153; its 6.1 TWh imply an offer of 94 TWh, less than the fleet's output, so the share is
used), and 2.05 TWh were scheduled down for technical constraints (APPA annual report,
pp. 25-26, from REE). Available energy = (output + technical) / (1 − 0.065): 8.3% of the
available wind and solar energy was curtailed, applied to both alike (an estimate from
OMIE's offer files gives 10.7% for wind and 6.0% for solar). The wind target rises from
59.5 to 64.9 TWh, the solar target from 43.7 to 47.6 TWh.

The MAE falls from 17.5 to 15.7, the mean price from 70.4 to 66.6, and the share of
hours below 1 EUR/MWh rises from 8.1% to 10.4% (12.1%). Net imports from France fall to
3.3 TWh (3.0). The model curtails less than reality did (3.7 against 9.4 TWh), so its
wind and solar output now exceeds the published one.

<a id="step-29"></a>**Step 29, inflow by month.** New information: the energy stored in
the reservoirs, which every transmission system operator must publish each week (ENTSO-E
Transparency Platform, Regulation 543/2013, Art. 16(1)(d); Spain, Portugal and France,
2015–2025, via Energy-Charts). It shows what upstream's runoff model cannot: water
leaving the reservoirs without passing a turbine, for irrigation and by evaporation, and
water arriving faster in a wet winter. A month's net inflow is the measured reservoir
output plus the change in stored energy over the month (the weekly averages interpolated
to the month boundaries): operators' release decisions cancel in this balance, so what
remains is the water that arrived, the weather minus the withdrawals, not a market
outcome. The year's inflow keeps the cited total of step 16; each month takes its share
from the balance and keeps its hourly shape from the runoff model, and a month with a
net withdrawal takes it evenly. Portuguese natural output of reversible plants counts.

Spanish inflow by month becomes 5.6, 3.9 and 7.4 TWh from January to March (upstream
2.3, 1.7, 3.2) and −0.2 and −0.6 TWh in July and August (0.7, 0.45); France and Portugal
change less. The MAE falls from 15.7 to 15.6. Spill is not observed, so a wet spring's
inflow is slightly understated; only the shape is used.

<a id="step-30"></a>**Step 30, water value from futures.** A reservoir owner values
water at the price it expects to sell it for later. The Iberian forward market states
that expectation in public: OMIP, the derivatives exchange of MIBEL, lists baseload
futures on the Spanish and French day-ahead price, and a week contract pays the average
day-ahead price of one ISO week. Its reference price on the last trading day before the
week starts (OMIP's daily bulletin, `scripts/retrieve_omip_futures.py`) is market
information every participant had before delivery, so using it is fair back-casting: it
is an ex-ante expectation, not the realized price the model is judged against. The
prepare step now reads no realized day-ahead price at all. Spanish and Portuguese
reservoirs take the Spanish contract (MIBEL clears both at one price in most hours),
French reservoirs the French one; across 2024 the Spanish values run from 9 to 122
EUR/MWh, the French from 19 to 124. In most weeks the reference price is OMIP's
assessment from bids, offers and related contracts rather than a trade.

Releases of reservoir hydro cost the week's value. Spilled water forgoes it too (price ×
turbine efficiency); without that, the optimizer would rather spill than generate
whenever the price is below the water value, which no operator does. Inflow, energy
capacity and the cyclic level stay, so the model still allocates the year's water. The
MAE moves from 15.6 to 15.7 (rMAE 0.72) and the correlation with OMIE is 0.89; hydro
output stays at the published 34.95 TWh.

<a id="step-31"></a>**Step 31, nuclear minimum output.** A correction inside
`es_unit_outages`. Spanish reactors run base load, at their licensed power, and cut
output for the market only to about 60-70% of it (CSN/PDT/CINU/GENER/2404/45.1, p. 3:
"con bajadas de hasta el entorno del 60% o 70%"; Trillo held about 61% for 79 days in
2024). Without that limit the model treats them as fully flexible at their fuel cost
(about 12 EUR/MWh): in surplus hours it backed nuclear down before it curtailed wind and
solar, the reverse of 2024. Nuclear now keeps 60% of its available capacity. Wind
curtailment rises from 3.8 to 5.0 TWh (wind output 59.9 TWh, 59.5 published), and
February nuclear matches measured output. Whole units switched off for market reasons
cannot be represented in a linear model: Almaraz I and Cofrentes stopped for weeks in
March 2024, where the model keeps them at 60% (March +1.0 GW, nuclear 54.3 against 52.4
TWh in the year). The MAE falls from 15.7 to 15.4; hours below 1 EUR/MWh rise from 10.5%
to 14.7% (12.1%).

<a id="step-32"></a>**Step 32, water value from the day contract.** The week-ahead
contract of step 30 fixes one value for seven days on the Friday before; it cannot see
the weather and demand of the days it covers. OMIP also lists day contracts (`FTB D`
Spain, `FFB D` France) for the next days. Each delivery day now takes its contract's
reference price on the latest bulletin at least two days before delivery: the bulletin
of the day before closes after OMIE's auction at noon, so it is left out. Over weekends
and holidays the lead grows to three to six days. Same source and the same ex-ante
argument as step 30. On their own, the day contracts miss OMIE's daily mean price by
12.6 EUR/MWh on average, the week contracts by 18.3. As for the weeks, most day
contracts trade no volume, so the reference price is OMIP's assessment.
`backcast.water_value_contract: week` restores step 30.

The MAE falls from 15.4 to 14.0 (rMAE 0.65, correlation 0.91); from 21 to 30 October,
the worst stretch of the year, it falls from 26 to 23. More Spanish gas runs (CCGT and
CHP −8.6% against −11.6%) and Spain imports less from France (3.4 TWh, 3.0 actual), but
exports to Portugal stay too high (14.0 TWh, 10.5 actual). What remains is the scarcity
value above the water value and the flat price within each day (§6).
