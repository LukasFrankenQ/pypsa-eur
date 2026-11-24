# Calibration & Validation Data Sources (`data/custom/`)

This document catalogs every primary data source (`source_id`) cited across the
calibration and validation CSV files in `data/custom/`:

1. [`national_capacities.csv`](../data/custom/national_capacities.csv): Installed
   generation and storage capacities (`MW`, `GWh` for pure and mixed pumped hydro
   storage (PHS) and reservoir hydro energy, and `per_unit` for PHS round-trip
   efficiency and the Spanish nuclear minimum output) by carrier for Spain (`ES`
   National, `ES_peninsular`, `ES_baleares`), Portugal (`PT`), and France (`FR`) in
   **2024**, plus the 2023 wind and solar rows of the average-operating-capacity rule.
   Spanish pumped-storage and solar-thermal parameters are kept under `ES_peninsular`,
   where all of these plants stand. A few rows document a derivation or an alternative
   figure that the model does not apply; their notes say so.
2. [`annual_demand.csv`](../data/custom/annual_demand.csv): Annual electricity demand
   (`TWh`) for Spain (`ES`, `ES_peninsular`, `ES_baleares`), Portugal (`PT`), and France
   (`FR`) in **2024**.
3. [`ntc.csv`](../data/custom/ntc.csv): Commercial net transfer capacities (`MW`) for
   `ES-FR`, `ES-PT`, `ES-MA`, `ES1-ES4` (the Rómulo HVDC link between the mainland and
   Mallorca, keyed in Redeia's zone codes), `FR-BE`, `FR-DE`, `FR-CH`, `FR-IT`, and
   `FR-GB` in **2024**.
4. [`prices.csv`](../data/custom/prices.csv): realized 2024 carbon prices of the EU
   emissions trading system (ETS; ICAP), MIBGAS PVB gas prices and CIF-ARA coal prices
   (IEA), lignite/oil/nuclear/biomass fuel prices from `PyPSA/technology-data v0.15.0`
   (nuclear converted to `EUR/MWh_th`, since its EIA source is per kWh of net
   generation), the mean day-ahead price of OMIE (the Iberian electricity market
   operator), the Portuguese gas network premium (`1.10 EUR/MWh_th`, ERSE) and the ratio
   of lower to higher heating value of natural gas (`0.901`, MITECO).
5. [`validation_2024_es.csv`](../data/custom/validation_2024_es.csv): Complete 2024
   Spanish electricity system validation benchmark, which also supplies the Spanish
   targets of the patches (wind, solar, hydro and solar-thermal energy, cogeneration
   output and CO2, biomass and waste output, exports to Morocco and Andorra, the 2024
   curtailment): generation by technology (`TWh`), busbar demand (`TWh`), gross and net
   physical cross-border exchanges per border (`TWh`), power-sector CO2 emissions by
   technology (`MtCO2`), and average day-ahead market price (`EUR/MWh`) across National
   (`ES`), Peninsular (`ES_peninsular`), Balearic (`ES_baleares`), and Modeled 2-Zone
   (`ES_mainland_baleares`) scopes.
6. [`fr_boundary_flows_2024.csv`](../data/custom/fr_boundary_flows_2024.csv): Measured
   hourly French external border flows (GB, IT, CH, DE+BE; MW, hour start in UTC,
   positive = import into France), derived from the vendored RTE eCO2mix extract via
   `scripts/retrieve_fr_boundary_flows.py`.
7. [`pt_actuals.csv`](../data/custom/pt_actuals.csv): Portuguese annual wind, hydro, and
   bioenergy generation (TWh) from Ember Yearly Electricity Data, used for the wind
   correction, hydro inflow, and biomass availability patches. The vendored extract
   covers the wind rows; the hydro and bioenergy values come from the same Ember
   release, cited by URL.
8. [`fr_nuclear_monthly_2024.csv`](../data/custom/fr_nuclear_monthly_2024.csv): Monthly
   mean French nuclear output in 2024 (`MW`, eCO2mix via ODRE), the seasonal shape for
   the `fr_nuclear_profile` patch (normalized to the upstream annual availability
   factor, so annual energy capability is preserved), derived from the vendored eCO2mix
   extract with `scripts/retrieve_fr_nuclear.py`.
9. [`omie_day_ahead_2024.csv`](../data/custom/omie_day_ahead_2024.csv): Hourly OMIE
   day-ahead prices of 2024 for Spain and Portugal (`EUR/MWh`, hour start in UTC), the
   benchmark of the price back-cast (read only by the evaluation), retrieved with
   `scripts/retrieve_omie_prices.py`.
10. [`daily_fuel_prices_2024.csv`](../data/custom/daily_fuel_prices_2024.csv): Daily
    2024 gas price at the Spanish hub (MIBGAS PVB day-ahead, `EUR/MWh_th`, by delivery
    day in Spanish local time) and EU allowance price (EEX primary auctions via Instrat,
    `EUR/tCO2`, last auction carried forward), retrieved with
    `scripts/retrieve_daily_fuel_prices.py`.
11. [`omip_week_ahead_2024.csv`](../data/custom/omip_week_ahead_2024.csv): Week-ahead
    baseload futures prices of OMIP for Spain (`FTB`) and France (`FFB`) for every ISO
    week touching 2024 (`EUR/MWh`): the reference price on the last trading day before
    each week, public before delivery; the water value of the `water_value` patch with
    `water_value_contract: week`, retrieved with
    `scripts/retrieve_omip_futures.py --contract week`.
12. [`generation_hourly_2024.csv`](../data/custom/generation_hourly_2024.csv): Measured
    hourly generation by technology, pumping and load of Spain, Portugal and France in
    2024 (`MW`, hour start in UTC), used for the reservoir floor (Spanish rows), the
    monthly water balance of `hydro_inflow` (all three countries), the natural inflow of
    Portuguese reversible plants and diagnostics, retrieved with
    `scripts/retrieve_generation.py`.
13. [`fr_actuals.csv`](../data/custom/fr_actuals.csv): French 2024 onshore wind and
    bioenergy generation (`TWh`, RTE *Bilan électrique 2024*) and natural hydro output
    (`TWh`, eCO2mix definitive data, pumped storage excluded;
    `scripts/retrieve_fr_hydro.py`), used for the wind correction, biomass availability
    and hydro inflow patches.
14. [`security_gas_floor.csv`](../data/custom/security_gas_floor.csv): Gas output the
    Spanish system operator requires for grid security (`MW`, mainland Spain): the mean
    gas output in OMIE's program after constraints in the hours of 2024 when the
    day-ahead program holds no gas, derived with
    `scripts/retrieve_security_gas_floor.py`.
15. [`hydro_output_ceiling.csv`](../data/custom/hydro_output_ceiling.csv): Highest
    hourly output of all Spanish hydro plants in 2019–2025 (`MW`: reservoir,
    run-of-river and pumped storage), the limit of the `es_hydro_availability` patch,
    derived with `scripts/retrieve_hydro_ceiling.py`.
16. [`coal_floor.csv`](../data/custom/coal_floor.csv): Minimum stable output of the
    Aboño 2 coal unit (`MW`): the output at which the system operator most often
    schedules it in OMIE's program after constraints of 2024, derived with
    `scripts/retrieve_coal_floor.py`.
17. [`solar_tracking.csv`](../data/custom/solar_tracking.csv): Share of mainland Spain's
    solar PV capacity on horizontal single-axis trackers (`0.6`), derived on the 2024
    back-cast and cross-checked with the literature; used by the `solar_tracking` patch.
18. [`pt_cogeneration_monthly_2024.csv`](../data/custom/pt_cogeneration_monthly_2024.csv):
    Monthly output of natural-gas cogeneration in mainland Portugal in 2024 (`GWh`,
    1,538 GWh in the year; REN Data Hub monthly balance), the fixed profile of
    Portuguese cogeneration in the `chp_profile` patch, retrieved with
    `scripts/retrieve_pt_cogeneration.py`.
19. [`es_unit_capacities.csv`](../data/custom/es_unit_capacities.csv): Net capacity
    (`MW`) of the mainland Spanish nuclear and coal units by OMIE unit code, used by
    `scripts/retrieve_unit_outages.py`.
20. [`es_unit_outages_2024.csv`](../data/custom/es_unit_outages_2024.csv): Hourly
    unavailable capacity of mainland Spanish nuclear, combined-cycle and coal plants in
    2024 (`MW`, hour start in UTC) from the unavailabilities declared to OMIE, the input
    of the `es_unit_outages` patch, derived with `scripts/retrieve_unit_outages.py`.
21. [`reservoir_levels_weekly.csv`](../data/custom/reservoir_levels_weekly.csv): Weekly
    energy stored in the hydro reservoirs of Spain, Portugal and France, 2015–2025
    (`GWh`, week's average, dated by the Monday that starts the week), as the TSOs
    publish it on the ENTSO-E Transparency Platform (Regulation 543/2013, Art.
    16(1)(d)); the monthly water balance of the `hydro_inflow` patch, derived from the
    vendored Energy-Charts extracts with `scripts/retrieve_reservoir_levels.py`.
22. [`omip_day_contract_2024.csv`](../data/custom/omip_day_contract_2024.csv): Day
    baseload futures prices of OMIP for Spain (`FTB`) and France (`FFB`) for every day
    of 2024 (`EUR/MWh`): the reference price on the latest bulletin at least two days
    before delivery, before OMIE's day-ahead auction (three to six days over weekends
    and holidays); the water value of the `water_value` patch (default
    `water_value_contract: day`), retrieved with
    `scripts/retrieve_omip_futures.py --contract day`.

---

## 1. Master Registry of `source_id` Entries

| `source_id` | Title | Publisher | Primary URL | Retrieval Date | License / Terms | Vendored File in `data/custom/sources/` | SHA-256 of Vendored File | Used In CSVs |
|---|---|---|---|---|---|---|---|---|
| `redeia_ise_2025_potencia_instalada` | *Informe del Sistema Eléctrico Español 2025 — Potencia instalada por tecnología y ámbito geográfico (2021–2025)* | Redeia / Red Eléctrica de España (REE) | `https://www.sistemaelectrico-ree.es/es/informe-del-sistema-electrico` | 2026-04-20 | Redeia Open Data / Attribution | `redeia_ise_2025_potencia_instalada.json` | `cd1245cd8f006993fb6f31231d131960cccff5d368fbc2cbe33d4e54777cb1ac` | `national_capacities.csv`, `es_unit_capacities.csv` |
| `redeia_ise_2025_potencia_autoconsumo` | *Informe del Sistema Eléctrico Español 2025 — Potencia instalada de autoconsumo fotovoltaico por CC.AA. (2021–2025)* | Redeia / Red Eléctrica de España (REE) | `https://www.sistemaelectrico-ree.es/es/informe-del-sistema-electrico` | 2026-04-20 | Redeia Open Data / Attribution | `redeia_ise_2025_potencia_autoconsumo.json` | `6adc1057bdb9399cb6332ca606cdf943638dca21a0148758763d5a9336229898` | `national_capacities.csv` |
| `redeia_ise_2025_evolucion_demanda` | *Informe del Sistema Eléctrico Español 2025 — Evolución de la demanda y generación por tecnología (2021–2025)* | Redeia / Red Eléctrica de España (REE) | `https://www.sistemaelectrico-ree.es/es/informe-del-sistema-electrico` | 2026-04-20 | Redeia Open Data / Attribution | `redeia_ise_2025_evolucion_demanda.json` | `1ea835860c675049fabb34d679b97a64e50d04bf83f97cc815962a4eefdbfc61` | `validation_2024_es.csv` |
| `redeia_ise_2025_interconexiones` | *Informe del Sistema Eléctrico Español 2025 / Capacidades comerciales de interconexión internacional (ES-FR, ES-PT, ES-MA)* | Redeia / Red Eléctrica de España (REE) | `https://www.ree.es/es/actividades/gestor-de-la-red-y-transportista/interconexiones-internacionales` | 2026-04-20 | Redeia Open Data / Attribution | — (NTC values from REE's published commercial interconnection capacities at the URL; no distributable file) | — | `ntc.csv` |
| `ree_romulo` | *Proyecto Rómulo: enlace submarino Península–Baleares* (HVDC, ±250 kV, 2 × 200 MW = 400 MW, Morvedre–Santa Ponsa, in service since 2012) | Red Eléctrica de España (REE) | `https://www.ree.es/sites/default/files/01_ACTIVIDADES/Documentos/folleto_romulo_es.pdf` | 2026-11-12 | REE public information | — | — | `ntc.csv` |
| `redata_2024_estructura_generacion` | *REData API — Estructura de generación nacional 2024 (MWh)* | Red Eléctrica de España (REData API) | `https://apidatos.ree.es/es/datos/generacion/estructura-generacion?start_date=2024-01-01T00:00&end_date=2024-12-31T23:59&time_trunc=year` | 2026-04-20 | Redeia Open Data / Attribution | `redata_2024_estructura_generacion.json` | `00828dff7d7a141725af3b38ad27c7e3211cad874d8d75a871b09575197a6506` | `validation_2024_es.csv` |
| `redata_2024_estructura_generacion_peninsular` | *REData API — Estructura de generación peninsular (`geo_id=8741`) 2024 (MWh)* | Red Eléctrica de España (REData API) | `https://apidatos.ree.es/es/datos/generacion/estructura-generacion?start_date=2024-01-01T00:00&end_date=2024-12-31T23:59&time_trunc=year&geo_trunc=electric_system&geo_limit=peninsular&geo_ids=8741` | 2026-04-20 | Redeia Open Data / Attribution | `redata_2024_estructura_generacion_peninsular.json` | `7359731ef0a8d9360331c69370a0cc569dd5b5dc407c8edfcd1e61ec6a9f5d73` | `validation_2024_es.csv` |
| `redata_2024_estructura_generacion_baleares` | *REData API — Estructura de generación Baleares (`geo_id=8743`) 2024 (MWh)* | Red Eléctrica de España (REData API) | `https://apidatos.ree.es/es/datos/generacion/estructura-generacion?start_date=2024-01-01T00:00&end_date=2024-12-31T23:59&time_trunc=year&geo_trunc=electric_system&geo_limit=baleares&geo_ids=8743` | 2026-04-20 | Redeia Open Data / Attribution | `redata_2024_estructura_generacion_baleares.json` | `3df88e743c8cb7c2d36f445c3738643e4bf2c40fa0a85e210266121bccf5551c` | `validation_2024_es.csv` |
| `redata_2024_demanda_evolucion` | *REData API — Evolución de la demanda nacional en barras de central 2024 (MWh)* | Red Eléctrica de España (REData API) | `https://apidatos.ree.es/es/datos/demanda/evolucion?start_date=2024-01-01T00:00&end_date=2024-12-31T23:59&time_trunc=year` | 2026-04-20 | Redeia Open Data / Attribution | `redata_2024_demanda_evolucion.json` | `3a2dae49c2691c0bd96d1768b4f8c7e554e1cd6a6f1896267d3b3404dde74f5d` | `annual_demand.csv`, `validation_2024_es.csv` |
| `redata_2024_demanda_evolucion_peninsular` | *REData API — Evolución de la demanda peninsular (`geo_id=8741`) 2024 (MWh)* | Red Eléctrica de España (REData API) | `https://apidatos.ree.es/es/datos/demanda/evolucion?start_date=2024-01-01T00:00&end_date=2024-12-31T23:59&time_trunc=year&geo_trunc=electric_system&geo_limit=peninsular&geo_ids=8741` | 2026-04-20 | Redeia Open Data / Attribution | `redata_2024_demanda_evolucion_peninsular.json` | `c5520f49b7c1961f9571dfc783013c90431cb739dde9172536f762f3ba71b16c` | `annual_demand.csv`, `validation_2024_es.csv` |
| `redata_2024_demanda_evolucion_baleares` | *REData API — Evolución de la demanda Baleares (`geo_id=8743`) 2024 (MWh)* | Red Eléctrica de España (REData API) | `https://apidatos.ree.es/es/datos/demanda/evolucion?start_date=2024-01-01T00:00&end_date=2024-12-31T23:59&time_trunc=year&geo_trunc=electric_system&geo_limit=baleares&geo_ids=8743` | 2026-04-20 | Redeia Open Data / Attribution | `redata_2024_demanda_evolucion_baleares.json` | `26663aa084a4aba6495243ab249509ff66b87a4a67700b66d75d5bd0dbc823a4` | `annual_demand.csv`, `validation_2024_es.csv` |
| `redata_2024_frontera_fisicos` | *REData API — Intercambios físicos internacionales por frontera 2024 (MWh)* | Red Eléctrica de España (REData API) | `https://apidatos.ree.es/es/datos/intercambios/frontera-fisicos?start_date=2024-01-01T00:00&end_date=2024-12-31T23:59&time_trunc=year` | 2026-04-20 | Redeia Open Data / Attribution | `redata_2024_frontera_fisicos.json` | `3214f5fb91b09abd99aabba3155fe998fe1c5899f51eccc9152691add372afe1` | `validation_2024_es.csv` |
| `redata_2024_intercambios_francia` | *REData API — Intercambios físicos frontera Francia 2024 (Exportación, Importación y Saldo, MWh)* | Red Eléctrica de España (REData API) | `https://apidatos.ree.es/es/datos/intercambios/francia-frontera?start_date=2024-01-01T00:00&end_date=2024-12-31T23:59&time_trunc=year` | 2026-04-20 | Redeia Open Data / Attribution | `redata_2024_intercambios_francia.json` | `51155917e938f66d7f78a365c31eb9b25bd95a59b3fe6767db0e4969faf491de` | `validation_2024_es.csv` |
| `redata_2024_intercambios_portugal` | *REData API — Intercambios físicos frontera Portugal 2024 (Exportación, Importación y Saldo, MWh)* | Red Eléctrica de España (REData API) | `https://apidatos.ree.es/es/datos/intercambios/portugal-frontera?start_date=2024-01-01T00:00&end_date=2024-12-31T23:59&time_trunc=year` | 2026-04-20 | Redeia Open Data / Attribution | `redata_2024_intercambios_portugal.json` | `632685eba153b765bdaf3bde9527136ac05a22215a3604da91a76d4073572668` | `validation_2024_es.csv` |
| `redata_2024_intercambios_marruecos` | *REData API — Intercambios físicos frontera Marruecos 2024 (Exportación, Importación y Saldo, MWh)* | Red Eléctrica de España (REData API) | `https://apidatos.ree.es/es/datos/intercambios/marruecos-frontera?start_date=2024-01-01T00:00&end_date=2024-12-31T23:59&time_trunc=year` | 2026-04-20 | Redeia Open Data / Attribution | `redata_2024_intercambios_marruecos.json` | `ee9912eb609d63da1f3b0e2f80eba88691eccfaf6c36d74ceb8e51dbb335a646` | `validation_2024_es.csv` |
| `redata_2024_intercambios_andorra` | *REData API — Intercambios físicos frontera Andorra 2024 (Exportación, Importación y Saldo, MWh)* | Red Eléctrica de España (REData API) | `https://apidatos.ree.es/es/datos/intercambios/andorra-frontera?start_date=2024-01-01T00:00&end_date=2024-12-31T23:59&time_trunc=year` | 2026-04-20 | Redeia Open Data / Attribution | `redata_2024_intercambios_andorra.json` | `e958d86a8832110ae8c88897ecffd29109f5c11c9a856bed9600e851d2a2de27` | `validation_2024_es.csv` |
| `redata_2024_enlace_baleares` | *REData API — Enlace submarino HVDC Península–Baleares 2024 (MWh)* | Red Eléctrica de España (REData API) | `https://apidatos.ree.es/es/datos/intercambios/enlace-baleares?start_date=2024-01-01T00:00&end_date=2024-12-31T23:59&time_trunc=year` | 2026-04-20 | Redeia Open Data / Attribution | `redata_2024_enlace_baleares.json` | `30da423f8d9b62d5bbf36b7298c26d8d0b44cfa596f77006b504aefe041f1d61` | `validation_2024_es.csv` |
| `redata_2024_emisiones_co2_mensual` | *REData API — Emisiones de CO2 asociadas a la generación nacional por tecnología 2024 (tCO2 eq.)* | Red Eléctrica de España (REData API) | `https://apidatos.ree.es/es/datos/generacion/no-renovables-detalle-emisiones-CO2?start_date=2024-01-01T00:00&end_date=2024-12-31T23:59&time_trunc=month` | 2026-04-20 | Redeia Open Data / Attribution | `redata_2024_emisiones_co2_mensual.json` | `93ba3158b3d181875e0105435b9db2f7ba889a0765306a4e226dfbbb9d12a2e3` | `validation_2024_es.csv` |
| `redata_2024_emisiones_co2_peninsular_mensual` | *REData API — Emisiones de CO2 asociadas a la generación peninsular (`geo_id=8741`) 2024 (tCO2 eq.)* | Red Eléctrica de España (REData API) | `https://apidatos.ree.es/es/datos/generacion/no-renovables-detalle-emisiones-CO2?start_date=2024-01-01T00:00&end_date=2024-12-31T23:59&time_trunc=month&geo_trunc=electric_system&geo_limit=peninsular&geo_ids=8741` | 2026-04-20 | Redeia Open Data / Attribution | `redata_2024_emisiones_co2_peninsular_mensual.json` | `3009c71b7968cbfd6d4e74ed211421050ec50afceeb121863227a78a1c2ab7cb` | `validation_2024_es.csv` |
| `redata_2024_emisiones_co2_baleares_mensual` | *REData API — Emisiones de CO2 asociadas a la generación Baleares (`geo_id=8743`) 2024 (tCO2 eq.)* | Red Eléctrica de España (REData API) | `https://apidatos.ree.es/es/datos/generacion/no-renovables-detalle-emisiones-CO2?start_date=2024-01-01T00:00&end_date=2024-12-31T23:59&time_trunc=month&geo_trunc=electric_system&geo_limit=baleares&geo_ids=8743` | 2026-04-20 | Redeia Open Data / Attribution | `redata_2024_emisiones_co2_baleares_mensual.json` | `feddba1ccd963c7582d5fafde0d4a998d716b38bfad5624d3a537666e0c9263e` | `validation_2024_es.csv` |
| `miteco_pniec_2023_2030` | *Actualización del Plan Nacional Integrado de Energía y Clima (PNIEC) 2023–2030 (Real Decreto 986/2024, BOE-A-2024-19885, Tabla 2.9 p. 122)* | Ministerio para la Transición Ecológica y el Reto Demográfico (MITECO) | `https://www.miteco.gob.es/content/dam/miteco/es/energia/files-1/pniec-2023-2030/PNIEC_2024_240924.pdf` | 2026-04-20 | Public Domain / BOE Official Publication | `miteco_pniec_2023_2030_240924.pdf` | `b8c3c30979827cccb0d833598374816a243d0c88597515a8d6130e8d7cc72a30` | `national_capacities.csv` |
| `jrc_hydro_power_database` | *JRC Hydro-power Plants Database (`jrc-hydro-power-plant-database.csv`, v10)* | European Commission Joint Research Centre (JRC) | `https://raw.githubusercontent.com/energy-modelling-toolkit/hydro-power-database/master/data/jrc-hydro-power-plant-database.csv` | 2026-04-20 | CC BY 4.0 | `jrc_hydro_power_plant_database.csv` | `62e9c5c2394b74183ff60be053c41ea28bb90035af5734a2712f0aaa76cba9e5` | `national_capacities.csv` |
| `ren_datahub_balanco_mensal` | *Eletricidade — Balanço Mensal, janeiro a dezembro 2024 and dezembro 2023 (Potência Instalada: Bombagem 3 585 MW, Solar 2 620 MW end-2023 and 3 883 MW end-2024; Produção: Gás Natural, Cogeração)* | REN — Redes Energéticas Nacionais (Data Hub) | `https://datahub.ren.pt/pt/eletricidade/balanco-mensal/?date=2024-12-31` | 2026-11-12 | REN Data Hub public data | — | — | `national_capacities.csv`, `pt_cogeneration_monthly_2024.csv` |
| `huclin_2024_cajamar_almacenamiento` | *El rol de las tecnologías de almacenamiento en la transición energética hacia un sector eléctrico descarbonizado (Chaves, Huclin & Ramos 2025, IIT Comillas, Mediterráneo Económico 39, Tabla 3 p. 72 & p. 77, citing Huclin 2024)* | Fundación Cajamar / Instituto de Investigación Tecnológica (IIT), Universidad Pontificia Comillas | `https://www.publicacionescajamar.es/publicacionescajamar/public/pdf/publicaciones-periodicas/mediterraneo-economico/39/39-la-estrategia-para-la-transicion-energetica.pdf` | 2026-04-20 | All rights reserved (Fundación Cajamar): cited, not redistributed; retrieved edition SHA-256 815709cc69a82a3f32be1a8fbd88748b9de578949e86915466ff81fb5366f71d | — | — | `national_capacities.csv` |
| `ren_dados_tecnicos_2024` | *Dados Técnicos 2024 — Sistema Eletroprodutor Nacional (Potência instalada e consumo 2024)* | REN — Redes Energéticas Nacionais / APREN / DGEG | `https://datahub.ren.pt/` | 2026-04-20 | REN DataHub Attribution | — (Official REN 2024 summary statistics) | — | `national_capacities.csv`, `annual_demand.csv` |
| `ember_yearly_electricity_data` | *Yearly Electricity Data (Portugal wind generation and capacity extract)* | Ember | `https://ember-energy.org/data/yearly-electricity-data/` | 2026-10-18 | CC-BY-4.0 | `ember_yearly_pt_wind.csv` | `6f1e3405714efc2a79a8798abcdc26b8e0f09e74b2ed61d8be970b4babb2bb81` | `pt_actuals.csv` (vendored extract of the PRT wind rows; the hydro and bioenergy rows cite the same release by URL) |
| `rte_eco2mix_2024_exchanges` | *eCO2mix données nationales consolidées et définitives — échanges commerciaux par frontière, 2024* | RTE / ODRE open data | `https://odre.opendatasoft.com/explore/dataset/eco2mix-national-cons-def/` | 2026-10-18 | Licence Ouverte Etalab 2.0 / RTE | `eco2mix_national_cons_def_2024_exchanges.csv` | `73a4e9e1fbe680a4f732487b5cf53181a2f4774ef89593e91ae888a52aa4d4dd` | `fr_boundary_flows_2024.csv` (derived via `scripts/retrieve_fr_boundary_flows.py`: hourly means of half-hourly commercial exchanges; GB, IT, CH, DE+BE combined; positive = import into FR; ES excluded, modeled endogenously) |
| `miteco_factores_ets2` | *Factores de cálculo para el RCDE-2 (paso de PCS a PCI del gas natural: 0,901)* | Ministerio para la Transición Ecológica y el Reto Demográfico (MITECO) | `https://www.miteco.gob.es/content/dam/miteco/es/cambio-climatico/temas/comercio-de-derechos-de-emision/rcde-2/251022%20FACTORES%20C%C3%81LCULO%20ETS2_web.pdf` | 2026-11-12 | Spanish government publication | — (one value, cited by URL) | — | `prices.csv` (heating-value basis of gas trade: PD-01 sec. 6.2, `https://www.boe.es/boe/dias/2019/03/14/pdfs/BOE-A-2019-3691.pdf`; of efficiencies: Danish Energy Agency technology catalog, `https://ens.dk/media/3273/download`) |
| `erse_tep_gas_2025_2026` | *Tarifas e preços de gás para o ano gás 2025-2026 (UGS parcela I, Quadro 3-13; URT entrada/saída, Quadros 3-17/3-18; Tapada do Outeiro em salvaguarda de segurança de abastecimento, sec. 0.2.1)* | ERSE — Entidade Reguladora dos Serviços Energéticos | `https://www.erse.pt/media/ssbndzo1/tep-g%C3%A1s-2025-2026.pdf` | 2026-10-19 | ERSE public regulatory decision | `erse_tep_gas_2025_2026.pdf` | `1095a68eda84cf0a32fc0719036c1fd0d3e7e654838ba2e2bc822247597d0843` | `prices.csv`, `national_capacities.csv` |
| `rte_eco2mix_2024_nuclear` | *eCO2mix données nationales consolidées et définitives — production nucléaire mensuelle moyenne, 2024* | RTE / ODRE open data | `https://odre.opendatasoft.com/explore/dataset/eco2mix-national-cons-def/` | 2026-10-19 | Licence Ouverte Etalab 2.0 / RTE | `eco2mix_national_cons_def_2024_nuclear_monthly.json` | `9f0c860b84fb7854dbc58a1b9ced2376b6d8f9fe29d57f99e2fd111f23b7d353` | `fr_nuclear_monthly_2024.csv` |
| `rte_eco2mix_2024_hydro` | *eCO2mix données nationales consolidées et définitives — production hydraulique 2024 (fil de l'eau et éclusée, lacs, STEP turbinage), sums of half-hourly values* | RTE / ODRE open data | `https://odre.opendatasoft.com/explore/dataset/eco2mix-national-cons-def/` | 2026-11-15 | Licence Ouverte Etalab 2.0 / RTE | `eco2mix_national_cons_def_2024_hydro.json` | `64be3ff35d09edb550a88215461ef3a244305eeddfbbebcb45fa351e5b57c98f` | `fr_actuals.csv` |
| `rte_pad_water_reserves` | *Stock hydraulique hebdomadaire (lake-type plants), 2017–2026, GWh by ISO week (RTE Analyses et données, hydropower)* | RTE | `https://analysesetdonnees.rte-france.com/system/files/graphDownloads/RTE_PAD_water_reserves.csv` | 2026-11-13 | RTE open data | `rte_pad_water_reserves.csv` | `284c4cfbc1c3f661c85925d185bc614feecd2adb42038dcd05ae13d980988b81` | `national_capacities.csv` |
| `ren_datahub_daily_balance` | *Eletricidade — Balanço Diário, Water Balance: stored energy of all reservoirs (GWh and % of maximum), daily 2023-12 to 2025-01* | REN — Redes Energéticas Nacionais (Data Hub) | `https://datahub.ren.pt/en/electricity/daily-balance/?date=2024-06-30` | 2026-11-13 | REN Data Hub public data | — | — | `national_capacities.csv` |
| `rte_bilan_electrique_2024` | *Bilan électrique 2024 — Consommation, production et échanges (2024 figures as reported in the vendored Bilan 2025 document)* | RTE — Réseau de Transport d'Électricité | `https://analysesetdonnees.rte-france.com/bilan-electrique-2024/synthese` | 2026-04-20 | Licence Ouverte Etalab 2.0 / RTE | `rte_bilan_electrique_2025_principaux_resultats.pdf` | `40eae4b5d59da1d5096f0e0b562439e3c946b267749cb8ae16b1a967d55d10cc` | `annual_demand.csv`, `national_capacities.csv`, `fr_actuals.csv` (figures not in the vendored document are quoted from the full report, `https://assets.rte-france.com/analyse-et-donnees/2025-09/BE2024%20-%20Rapport%20Complet.pdf`, retrieved 2026-11-12) |
| `odre_parc_prod_par_filiere` | *Parc annuel de production par filière (parc_solaire, end of 2023)* | RTE / ODRE open data | `https://odre.opendatasoft.com/explore/dataset/parc-prod-par-filiere/` | 2026-11-12 | Licence Ouverte Etalab 2.0 / RTE | — (one value, cited by URL) | — | `national_capacities.csv` |
| `omie_programas_pdbf_pdvd` | *Programa Diario Base de Funcionamiento (PDBF) and Programa Diario Viable Definitivo (PDVD), hourly programs by unit, 2024; Listado de unidades* | OMIE — Operador del Mercado Ibérico de Energía | `https://www.omie.es/es/file-access-list` | 2026-11-12 | OMIE public market results | — (233 MB; retrieved by script) | — | `security_gas_floor.csv`, `coal_floor.csv` (derived via `scripts/retrieve_security_gas_floor.py` and `scripts/retrieve_coal_floor.py`; the unit list is the current one, units renamed since 2024 are matched by a trailing R) |
| `rte_bilan_electrique_2025` | *Bilan électrique 2025 — Principaux résultats (Consommation, parc installé 2024–2025, production et échanges)* | RTE — Réseau de Transport d'Électricité | `https://assets.rte-france.com/analyse-et-donnees/2026-03/BE2025%20-%20Principaux%20R%C3%A9sultats.pdf` | 2026-04-20 | Licence Ouverte Etalab 2.0 / RTE | `rte_bilan_electrique_2025_principaux_resultats.pdf` | `40eae4b5d59da1d5096f0e0b562439e3c946b267749cb8ae16b1a967d55d10cc` | `national_capacities.csv` |
| `entsoe_tyndp_2024_ntc` | *ENTSO-E TYNDP 2024 — Reference Grid and Cross-Border Commercial Transfer Capacities (2024, 2025)* | ENTSO-E | `https://2024.entsos-tyndp-scenarios.eu/` | 2026-04-20 | CC BY 4.0 | — | — | `ntc.csv` |
| `mibgas_data_2024` | *MIBGAS Data 2024 — Trading Data PVB&VTP (day-ahead product `GDAES_D+1`, reference price by delivery day)* | MIBGAS S.A. | `https://www.mibgas.es/en/file-access/MIBGAS_Data_2024.xlsx?path=AGNO_2024/XLS` | 2026-11-10 | MIBGAS public market data | — (fetched by the retrieval script; not vendored) | — | `prices.csv`, `daily_fuel_prices_2024.csv` |
| `instrat_eua_auctions` | *EU ETS prices — clearing prices of the EEX primary auctions of EU allowances* | Instrat Foundation (energy.instrat.pl), from EEX auction results | `https://energy.instrat.pl/en/prices/eu-ets/` | 2026-11-10 | Instrat open data, attribution (terms at the source) | — (fetched by the retrieval script; not vendored) | — | `daily_fuel_prices_2024.csv` |
| `icap_ets_2025` | *ICAP Allowance Price Explorer & Emissions Trading Worldwide Status Report 2025/2026 (EU ETS 2024–2025 EUA Spot/Auction Settlement)* | International Carbon Action Partnership (ICAP) / EEX | `https://icapcarbonaction.com/en/ets-prices` | 2026-04-20 | Attribution | — | — | `prices.csv` |
| `omie_2024_informe_anual` | *Evolución del mercado de electricidad — Informe anual 2024 (Precio medio aritmético mercado diario España y Portugal)* | OMIE — Operador del Mercado Ibérico de Energía (Polo Español) | `https://www.omie.es/es/publicaciones/informe-anual` | 2026-04-20 | OMIE Public Market Data | — | — | `prices.csv`, `validation_2024_es.csv` |
| `omie_marginalpdbc_2024` | *Precios horarios del mercado diario en España y Portugal (`marginalpdbc`), 2024* | OMIE — Operador del Mercado Ibérico de Energía (Polo Español) | `https://www.omie.es/es/file-access-list?parents=/Mercado%20Diario/1.%20Precios&dir=Precios%20horarios%20del%20mercado%20diario%20en%20Espa%C3%B1a&realdir=marginalpdbc` | 2026-11-10 | OMIE Public Market Data | — (daily files fetched by the retrieval script; not vendored) | — | `omie_day_ahead_2024.csv` |
| `deloitte_appa_macro_2024` | *Estudio del Impacto Macroeconómico de las Energías Renovables en España 2024 (Deloitte for APPA Renovables), p. 153, chart 8.4: renewable energy offered in OMIE's day-ahead market and not matched, 6.1 TWh, 6.5% of the offer* | APPA Renovables / Deloitte | `https://www.appa.es/wp-content/uploads/2025/10/Estudio-del-Impacto-Macroeconomico-de-las-Energias-Renovables-2024.pdf` | 2026-11-13 | Public report | — | — | `validation_2024_es.csv` |
| `appa_informe_anual_2024` | *Informe anual Renovables 2024, Sistema eléctrico: servicios de ajuste, pp. 25-26: renewable energy scheduled down for technical constraints, 1 600 GWh (PDBF) and 448.1 GWh (real time); source REE* | APPA Renovables | `https://www.appa.es/wp-content/uploads/2025/02/Informe-anual-Energias-Renovables-2024.pdf` | 2026-11-13 | Public report | — | — | `validation_2024_es.csv` |
| `omie_indisp_cab_2024` | *Indisponibilidades de unidades (`indisp`) and cabeceras de ofertas (`cab`) of the day-ahead market, monthly archives 2024* | OMIE — Operador del Mercado Ibérico de Energía (Polo Español) | `https://www.omie.es/es/file-access-list?parents=/Mercado%20Diario/7.%20Indisponibilidades&dir=Indisponibilidades&realdir=indisp` | 2026-11-15 | OMIE Public Market Data | — (monthly archives fetched by the retrieval script; not vendored) | — | `es_unit_outages_2024.csv` |
| `foronuclear_resultados_2024` | *Resultados nucleares de 2024 y perspectivas para 2025 (net capacity by unit, p. 26)* | Foro de la Industria Nuclear Española | `https://www.foronuclear.org/wp-content/uploads/2025/04/Informe_Resultados_Nucleares_2024.pdf` | 2026-11-15 | Public report | — | — | `es_unit_capacities.csv` |
| `csn_pdt_cinu_gener_2404_45_1` | *Propuesta de dictamen técnico CSN/PDT/CINU/GENER/2404/45.1, ITC on flexible operation of Spanish nuclear plants, p. 3: load reductions 'hasta el entorno del 60% o 70%' at the request of the dispatch centres* | Consejo de Seguridad Nuclear (CSN) | `https://sede.csn.gob.es/documents/10182/2567023/1705+-+II.02.+INUC_Web_CSN_PDT_CINU_GENER_2404_45.1_Web_+FIRMA+INFORME.pdf/9b220f73-a24b-c478-0a00-3dea917d68af` | 2026-11-15 | Public regulatory document | — | — | `national_capacities.csv` |
| `cnmc_inf_de_228_22` | *INF/DE/228/22: informe sobre la transmisión de titularidad de los grupos 1 y 2 de la central térmica de Aboño (net capacities pp. 2-3)* | CNMC | `https://www.cnmc.es/sites/default/files/4587562_0.pdf` | 2026-11-15 | Public report | — | — | `es_unit_capacities.csv` |
| `cnmc_inf_de_531_23` | *INF/DE/531/23: informe sobre la transmisión de titularidad de la central térmica Soto de Ribera 3 (net capacity p. 1)* | CNMC | `https://www.cnmc.es/sites/default/files/5256679.pdf` | 2026-11-15 | Public report | — | — | `es_unit_capacities.csv` |
| `omip_futures_2024` | *OMIP daily market bulletins, baseload futures (`FTB` Spain, `FFB` France): reference prices of the week and day contracts, 2023-12 to 2024-12* | OMIP — Polo Português, S.G.M.R. (MIBEL derivatives exchange) | `https://www.omip.pt/en/dados-mercado` | 2026-11-15 | OMIP public market data | — (daily bulletins fetched by the retrieval script; not vendored) | — | `omip_week_ahead_2024.csv`, `omip_day_contract_2024.csv` |
| `energy_charts_public_power_2024` | *Measured generation by production type, Spain, Portugal and France, 2024 (Energy-Charts API, ENTSO-E Transparency data)* | Fraunhofer ISE, Energy-Charts | `https://api.energy-charts.info/public_power` | 2026-11-11 | Energy-Charts open data, attribution (terms at the source) | — (fetched by the retrieval script; not vendored) | — | `generation_hourly_2024.csv` |
| `energy_charts_filling_level_es` | *Filling level of water reservoirs and hydro storage plants, Spain, weekly 2015–2026 (ENTSO-E Transparency 16.1.D, as charted by Energy-Charts)* | Fraunhofer ISE, Energy-Charts (data: ENTSO-E) | `https://energy-charts.info/charts/filling_level/data/es/year_storage.json` | 2026-11-15 | Energy-Charts open data, attribution (terms at the source) | `energy_charts_filling_level_es.json` | `54aa29622be5cb0e229570279282e631f6e1e6fede29ba2fa0c059e8ff27625a` | `reservoir_levels_weekly.csv` |
| `energy_charts_filling_level_pt` | *Filling level of water reservoirs and hydro storage plants, Portugal, weekly 2015–2026 (ENTSO-E Transparency 16.1.D, as charted by Energy-Charts)* | Fraunhofer ISE, Energy-Charts (data: ENTSO-E) | `https://energy-charts.info/charts/filling_level/data/pt/year_storage.json` | 2026-11-15 | Energy-Charts open data, attribution (terms at the source) | `energy_charts_filling_level_pt.json` | `cc757addab29f4a709ffb962d53d334bb0b7a39fe8d550de5ed5e94bae4cd8ec` | `reservoir_levels_weekly.csv` |
| `energy_charts_filling_level_fr` | *Filling level of water reservoirs and hydro storage plants, France, weekly 2015–2026 (ENTSO-E Transparency 16.1.D, as charted by Energy-Charts)* | Fraunhofer ISE, Energy-Charts (data: ENTSO-E) | `https://energy-charts.info/charts/filling_level/data/fr/year_storage.json` | 2026-11-15 | Energy-Charts open data, attribution (terms at the source) | `energy_charts_filling_level_fr.json` | `5817a1af9422256955beb7f6623d28ef00382dacc5eeb2d06c2781926eccb163` | `reservoir_levels_weekly.csv` |
| `energy_charts_public_power_2019_2025` | *Measured generation by production type, Spain, 2019–2025 (Energy-Charts API, ENTSO-E Transparency data)* | Fraunhofer ISE, Energy-Charts | `https://api.energy-charts.info/public_power?country=es` | 2026-11-12 | Energy-Charts open data, attribution (terms at the source) | — (fetched by the retrieval script; not vendored) | — | `hydro_output_ceiling.csv` (derived via `scripts/retrieve_hydro_ceiling.py`: highest hourly sum of reservoir, run-of-river and pumped-storage output per year) |
| `iea_coal_2025` | *Coal 2025 — Prices and costs (CIF ARA 6 000 kcal/kg annual averages: USD 101/t in 2025, −10% y-o-y)* | International Energy Agency (IEA) | `https://www.iea.org/reports/coal-2025/prices-and-costs` | 2026-10-20 | CC BY 4.0 (IEA) | — | — | `prices.csv` |
| `pypsa_tech_data_v015_2025` | *PyPSA technology-data v0.15.0 — `costs_2025.csv` (2025 fuel cost assumptions, currency year 2025)* | PyPSA Contributors | `https://raw.githubusercontent.com/PyPSA/technology-data/v0.15.0/outputs/costs_2025.csv` | 2026-04-20 | MIT / CC BY 4.0 | `pypsa_technology_data_v0.15.0_costs_2025.csv` | `a4492c081545352d575474898c58524a4d5502c119fedefac7afe94f53afb683` | `prices.csv` |
| `eia_epa_table_8_4` | *EIA Electric Power Annual, Table 8.4 — Average power plant operating expenses for major U.S. investor-owned electric utilities, 2014–2024 (mills per kWh, weighted by net generation)* | U.S. Energy Information Administration | `https://www.eia.gov/electricity/annual/html/epa_08_04.html` | 2026-10-21 | U.S. Government public domain | — (unit check only: shows the technology-data nuclear fuel figure is per MWh_el) | — | `prices.csv` |
| `study_solar_tracking_2024` | *Share of mainland Spain's solar PV capacity on single-axis trackers (`0.6`), derived on the 2024 back-cast: the mix of the fixed-tilt and tracker profiles of atlite that best reproduces the measured hourly solar output of 2024; cross-checked with Victoria & Andresen (2019), arXiv:1807.10044, and market reports* | Study authors (see `doc/validation_log.md`, §7, step 19) | — | 2026-11-12 | Internal Study Derivation | — | — | `solar_tracking.csv` |

---

## 2. Spanish Pumped Hydro Storage (PHS) and Run-of-River (RoR) Characterization

A raw PyPSA-Eur build parameterizes Spanish pumped hydro (`PHS`) from the JRC
Hydro-power Plants Database, which yields hundreds of hours of storage energy
(comparable to seasonal natural-inflow reservoir dams rather than diurnal/weekly pumped
storage), while run-of-river hydro (`ror`) comes out far below the Spanish small-hydro
fleet.

Cross-referencing **Redeia (*Informe del Sistema Eléctrico 2025*)**, **MITECO (*PNIEC
2023–2030*, RD 986/2024)**, the **JRC Hydro-power Plants Database
(`jrc_hydro_power_database`)**, and **Chaves, Huclin & Ramos (2025) / Huclin (2024)
(`huclin_2024_cajamar_almacenamiento`, IIT Comillas, *Mediterráneo Económico 39*, Table
3 p. 72 & p. 77)** resolves this discrepancy:

1. **Exact origin of the `~4,000 GWh` JRC storage-energy artifact**:
   - In `jrc-hydro-power-plant-database.csv` (`jrc_hydro_power_database`), 15 Spanish
     plants are tagged `HPSS` (pumped storage), with total
     `storage_capacity_MWh = 3,956,590 MWh` (`3,956.59 GWh`).
   - However, **`3,897.12 GWh` (`98.5%` of the JRC total)** belongs to just **five
     open-loop mixed-PHS (`OLPSH` / *bombeo mixto*) plants whose upper reservoirs are
     massive multi-annual river-cascade dams**: *Villarino / Almendra* (`3,120.00 GWh`),
     *Valdecañas* (`664.00 GWh`), *Pintado* (`77.00 GWh`), *Aldeadávila* (`35.00 GWh`),
     plus *Guillena* (`1.12 GWh`).
   - Because the study models **mixed PHS (`2,687.0 MW`) inside `reservoir hydro`
     (`14,909.79 MW` in 2024 for `ES_peninsular`)** following Redeia's convention, only
     **pure PHS (`3,331.4 MW` in 2024)** is modeled as a closed-loop `PHS`
     `StorageUnit`.
2. **Pure PHS Storage Energy (`GWh`), Pumping Capacity (`MW`), and Round-Trip Efficiency
   (`per_unit`)**:
   - **`huclin_2024_cajamar_almacenamiento` (Chaves, Huclin & Ramos 2025 / Huclin 2024,
     IIT Comillas, Table 3 p. 72 & p. 77)**:
     - Existing Spanish closed-loop pure PHS (`CLPSH` / *bombeo puro*): storage energy =
       **`120.000 GWh`** (`quality: sourced`; `~34.6–36.0 h` at full turbine output),
       pumping capacity = **`3,552.0 MW`** (`quality: sourced`), and round-trip cycle
       efficiency = **`0.750`** (`75.0%`, `quality: sourced`).
   - **`jrc_hydro_power_database` (JRC Plant-Level Head-Pond Sum)**:
     - Directly reported `storage_capacity_MWh` across the 5 Spanish pure-PHS plants
       with non-zero head-pond energy in JRC (*Cortes–La Muela* `24.50 GWh` + *Moralets*
       `27.00 GWh` + *Aguayo* `3.70 GWh` + *Estany Gento–Sallente* `3.27 GWh` + *Tajo de
       la Encantada* `1.00 GWh`) = **`59.470 GWh`** (`quality: sourced`; `17.85 h` at
       `3,331.4 MW`). Adding the physical upper-reservoir energy
       (`hm³ × head × 9.81 × 0.80 / 3600`) for *Bolarque II*
       (`7.0 hm³ × 269 m = 4.10 GWh`) and *Ibón de Ip* (`5.3 hm³ × 940 m = 10.86 GWh`)
       yields **`74.430 GWh`** (`quality: derived`; `22.34 h` at `3,331.4 MW`).
     - Pure PHS pumping capacity in JRC (*La Muela I–II* `1,390 MW` + *Tajo de la
       Encantada* `420 MW` + *Sallente* `400 MW` + *Aguayo* `360 MW` + *Moralets*
       `219 MW` + *Bolarque II* `208 MW` + *Tanes* `110 MW` + *Montamara* `100 MW` +
       *Ip* `99 MW`) = **`3,306.000 MW`** (`quality: sourced`).
3. **Applied in the model** (`phs_split`): 120 GWh of storage energy and 0.75 round-trip
   efficiency (Chaves, Huclin & Ramos), and the JRC pumping capacity of 3,306 MW.

---

## 3. National vs. Modeled-Scope Clarifications

The Spain-wide calibration and validation CSVs (`national_capacities.csv`,
`annual_demand.csv`, `validation_2024_es.csv`) use these geographic keys (REE
electric-system labels, not model bus prefixes: in the network, `ES0*` is the peninsula
and `ES1 0AC` the Balearics):
- `ES`: Spain National total (Peninsular + Balearic Islands + Canary Islands + Ceuta +
  Melilla)
- `ES_peninsular`: Peninsular Spain (`ES1`, `geo_id=8741`)
- `ES_baleares`: Balearic Islands (`ES4`, `geo_id=8743`)
- `ES_mainland_baleares`: Sum of `ES_peninsular + ES_baleares` (`ES1 + ES4`), the
  model's Spanish scope (the Canary Islands, Ceuta and Melilla are not in the model);
  used only in `validation_2024_es.csv`, the other tables give the two zones.

Official Redeia and MITECO primary data require three scope distinctions that commonly
cited national ballpark figures blur:

1. **2024 power-sector CO2 emissions**:
   - **National (`ES`)** 2024 electricity-generation CO2 reported by REData
     (`no-renovables-detalle-emisiones-CO2`) is **`27.045 MtCO2`**
     (`27,044,848 tCO2 eq.`), of which **`5.140 MtCO2`** (`19.0%`) is emitted in the
     **Canary Islands, Ceuta, and Melilla**.
   - For the modeled scope (**`ES_mainland_baleares` = `ES1 + ES4`**), 2024 power-sector
     CO2 is **`21.905 MtCO2`** (`19.990 MtCO2` on the Peninsula `ES1` + `1.915 MtCO2` on
     the Balearic Islands `ES4`).
2. **2024 Spanish electricity demand**:
   - **National (`ES`)** busbar demand (`Demanda en b.c.`) in 2024 is **`249.000 TWh`**,
     whereas **Modeled (`ES_mainland_baleares` = `ES1 + ES4`)** 2024 busbar demand is
     **`239.692 TWh`** (`233.628 TWh` Peninsular `ES1` + `6.064 TWh` Balearic `ES4`).
3. **2024 Spanish CCGT generation**:
   - **National (`ES`)** 2024 CCGT generation is **`35.755 TWh`**, whereas **Modeled
     (`ES_mainland_baleares` = `ES1 + ES4`)** 2024 CCGT generation is **`32.019 TWh`**
     (`29.107 TWh` Peninsular `ES1` + `2.913 TWh` Balearic `ES4`).
