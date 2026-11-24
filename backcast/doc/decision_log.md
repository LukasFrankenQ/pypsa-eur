# Decision Log

Scope and architecture decisions of the back-cast, each with its reason. The model was
built for the [Spain data center decree
study](https://github.com/pypsalabs/spain-datacenter-decree) and is reduced here to its
2024 back-cast. Validation findings, the patches they led to and the evidence behind
each step are in [validation_log.md](validation_log.md).

## A. Scope

| Topic | Decision |
|---|---|
| Study question | How well can a cost-based dispatch model of **the 2024 Spanish system** reproduce the hourly day-ahead price published by OMIE, the Iberian electricity market operator? The fleet is frozen: no capacity expansion, dispatch only |
| Fleet year | 2024 actuals (Redeia) |
| Geography | Four bidding zones: mainland Spain, the Balearics, Portugal and France, as the day-ahead market clears them. PyPSA-Eur builds 15 nodes (ES 9 / FR 4 / PT 2), which the cited corrections work on; the `bidding_zones` patch then merges them. France's non-Iberian borders are closed with measured flows (see B) |
| Weather year | **2024**: weather, fleet data and system outcomes all close, so the weather-dependent closures (measured French border flows, observed hydro inflow, the wind-bias factor) are in-sample |
| Temporal resolution | Hourly snapshots (8,760 steps) |

## B. Architecture

| Topic | Decision |
|---|---|
| Base model | **PyPSA-Eur v2026.09.0 as a pinned git submodule** (commit c8209014), with no upstream file modified: every study choice lives in this overlay, so a diff against vanilla PyPSA-Eur shows them all. The pin is the gitlink in the commit tree (`.gitmodules` carries only path and URL) |
| Workflow | Two steps. (1) The upstream network build runs inside the submodule with `--configfile ../config/config.spain.yaml`. (2) The back-cast workflow (`Snakefile`, a directed acyclic graph of rules) takes over from `composed_*.nc`: `prepare_network` (patch engine) → `solve_network` (dispatch) → `validate_network`, `plot_validation` and `plot_price_backcast` |
| Variants | The back-cast is the year 2024, its weather and its data, in two variants: `raw` (fresh PyPSA-Eur build with only the France boundary closure) and `patched` (all cited corrections). The wind-bias factor is derived at run time on the back-cast |
| Validation design | The solved 2024 network is compared against published actuals in one report per variant (generation, demand, capacity factors, CO2 and intensity, flows, mean price), and its hourly price of mainland Spain (the market OMIE clears; the Balearic system is dispatched apart, Real Decreto 738/2015) against OMIE's hourly day-ahead price in one figure |
| Background corrections | `prepare_network.py` applies an ordered list of cited corrections ("patches"), each switchable in `backcast.patches`; named variants (`raw`, `patched`) select sets through the `{variant}` wildcard. The rule: **build fresh, validate, correct only what the validation demands, cite every correction.** The raw variant keeps only the France boundary closure, so the raw and patched reports together show the effect of every patch |
| France boundary | **No pan-European model.** France's external borders (GB, IT, CH, DE+BE combined) are fixed to measured hourly 2024 flows (RTE eCO2mix via ODRE), as fixed generators and loads at the nearest French node. For the validation year, measured flows beat any coarse Europe-wide model and need no second model kept consistent. Limitation: French external trade does not respond to Iberian prices |
| Interconnectors | One transport link per border at the published commercial net transfer capacity (NTC), directional (`ntc.csv`); no flow physics between or inside zones, as in the market coupling. The `raw` variant keeps PyPSA-Eur's 15 nodes with linearized power flow |
| Storage per zone | **One storage unit per zone and carrier**: with no grid inside a zone, upstream's split of storage and inflow among nodes has no meaning and makes small nodes spill. Portuguese reversible plants on river reservoirs keep their natural inflow; their water carries no water value, so pumped arbitrage stays undistorted ([step 25](validation_log.md#step-25)) |
| Operator security requirement | **Gas the operator orders up for security runs outside the merit order**: a constant 1,060 MW on mainland gas, from OMIE's programs and not the hourly redispatch, which would carry the market result ([step 11](validation_log.md#step-11)) |
| Start-up costs | **A linear start-up cost, not unit commitment**, so the problem stays linear. Gas, coal and Spanish nuclear plants carry the cost of PyPSA-Eur's `data/unit_commitment.csv`; French reactors follow load and are left free ([steps 14](validation_log.md#step-14), [18](validation_log.md#step-18)) |
| Coal floor | **Aboño 2 runs at its minimum output**: it burns steelworks gases and supplies its 220 kV zone, so the operator keeps it on whatever the price ([step 13](validation_log.md#step-13)) |
| Hydro availability | **Installed capacity is not deliverable capacity.** The record output of seven years, not of the back-cast year, caps Spanish reservoir and run-of-river output, so the cap carries no market result of 2024 ([step 12](validation_log.md#step-12)) |
| Solar trackers | **Capacity times the atlite profile, with the mounting of the real fleet**: 60% of mainland Spain's solar on the tracker profile PyPSA-Eur already builds. Scaling each month to its measured output was rejected: it moves numbers without a physical reason ([step 19](validation_log.md#step-19)) |
| Curtailed energy | **Wind and solar availability includes the energy curtailed**: published output is after curtailment ([step 28](validation_log.md#step-28)) |
| Water value | **Reservoir water is valued at the futures price of the day**, quoted on OMIP before OMIE's auction: ex-ante market information, not the realized price the model is judged against. The prepare step reads no realized day-ahead price at all; `backcast.water_value_contract` switches to the week contract ([steps 30](validation_log.md#step-30), [32](validation_log.md#step-32)) |
| Nuclear minimum | **Online nuclear keeps 60% of its available capacity**, the depth of market-driven load reductions (CSN). Shutdowns of whole units for market reasons are not representable in a linear model ([step 31](validation_log.md#step-31)) |
| Commodity prices | Dispatch-year prices are **realized market averages** for the price-setting commodities: EU emission allowances (EUA) 65.24 EUR/tCO2 (ICAP), MIBGAS PVB gas 34.71 EUR/MWh_th (the hub Iberian plants buy at; converted from higher to lower heating value, [step 15](validation_log.md#step-15)), coal 14.83 delivered to Amsterdam-Rotterdam-Antwerp (CIF ARA; IEA). In the patched variant gas and allowance prices follow their daily values ([step 3](validation_log.md#step-3)). Oil and biomass fuels stay on technology-data v0.15.0 (minor, not price-setting, no single benchmark). Nuclear fuel keeps the technology-data figure, read per MWh_el as its EIA source reports it ([§1](validation_log.md#raw-findings)) |
| Technology costs | technology-data v0.15.0 (EUR2025) at cost year 2025, the planning horizon of the upstream build: variable operating costs, efficiencies and CO2 intensities |
| Resource classes | `resource_classes: 5` for onwind, solar and solar-hsat: each region is split into 5 capacity-factor bins with their own profiles and potentials, and existing plants are joined to the bin where they stand. This fixes the layout bias at the config level (a single regional profile averages over all eligible land, while real plants occupy the good sites). The residual ERA5 wind bias is handled in validation |
| Solver | HiGHS dual simplex (open, reproducible, no license), 2 threads per solve (the `solve_network` rule), set in `backcast.solving.solver`; per-solver options live in `scripts/solve_network.py`. An hourly solve needs ~11 GB |
| Environment | No own environment: everything runs in the submodule's pixi environment (manifest and lock of the pinned commit), so both workflow steps use exactly the versions PyPSA-Eur was tested with |
| Snakemake style | Rules call their scripts through `shell:` + `PYTHONPATH` and a CLI (the `script:` directive breaks on `from __future__` imports). Each rule lists its scripts and every `data/custom` file it reads as inputs, so code and data edits retrigger the workflow |
| Data policy | Every number a script uses comes from a cited file: the small tables in `data/custom/*.csv` carry a `source_id` per row, the hourly, daily and weekly series one per file, all registered in `doc/data_sources.md` (URL, retrieval date, license, and the sha256 of the primary file where vendored). The `scripts/retrieve_*.py` scripts derive the series and the OMIE-based floors and are rerunnable; start-up costs come from PyPSA-Eur's `data/unit_commitment.csv`. One vendored file, the Redeia monthly CO2 JSON that shapes the CHP profile, is read directly by a patch and declared as a Snakemake input |

## C. Open items

What the step log leaves open, for the next steps (validation log
[§6](validation_log.md#caveats) and [§7](validation_log.md#steps)).

| # | Item |
|---|---|
| C2 | The price is compressed: hours when OMIE cleared at 1 to 60 EUR/MWh are about 11 too high, hours above 130 about 21 too low; nights and solar hours too high, evening peaks too low. Reservoirs set the price in most hours at one water value per day; in the hours where they are marginal the level is right on average, the hourly shape is not |
| C3 | Coal runs too much after the declared outages (steps 26 and 31): mainland units offered above their fuel and carbon cost, and the Balearic reserve units run too much |
| C4 | Portuguese combined-cycle plants produce a sixth of their measured output; part of it ran after the day-ahead market (step 20) |
| C5 | Wind peaks: the model fleet peaks near its full capacity, the measured at 65%; a quantile correction of the wind speeds would keep the hourly shape (step 17) |
| C6 | Solar profile seasonality: after fleet growth (step 27) mainland solar is 15-17% above measured output in December-January and 8% below in summer, with the same pattern in France and Portugal: the upstream profile (35° tilt or irradiance), not the fleet |
| C7 | French reactors back down without limit, so the French price never falls below their fuel cost; a minimum output from the fleet's record would be the physical fix (step 18) |
| C8 | Spanish reservoir energy capacity grows with the turbine capacity set in step 0 (19.8 TWh; REE's hydroelectric reserve capacity is 18.5 TWh); no effect on the MAE (step 23). Portuguese and French reservoirs now take cited capacities (step 25) |
