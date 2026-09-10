# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT


def solar_capacity_per_sqkm_provider(w):
    """Return capacity_per_sqkm for the solar techs the extra functionality compares."""
    renewable = config_provider("renewable", default={})(w)
    return {
        tech: renewable[tech]["capacity_per_sqkm"]
        for tech in ("solar", "solar-hsat")
        if tech in renewable and "capacity_per_sqkm" in renewable[tech]
    }


rule solve_network:
    """Optimises investment and dispatch for one planning horizon to minimise total system cost."""
    cache: True
    input:
        code_dependencies=code_dependencies(
            "scripts/solve_network.py", "scripts/_benchmark.py"
        ),
        network=resources("networks/composed_{horizon}.nc"),
    output:
        network=RESULTS + "networks/solved_{horizon}.nc",
        model=(
            RESULTS + "models/solved_{horizon}.nc"
            if config["solving"]["options"]["store_model"]
            else []
        ),
    log:
        solver=normpath(RESULTS + "logs/solve_network/solver_{horizon}.log"),
        memory=RESULTS + "logs/solve_network/memory_{horizon}.log",
        python=RESULTS + "logs/solve_network/python_{horizon}.log",
    shadow:
        shadow_config
    threads: solver_threads
    resources:
        mem_mb=config_provider("solving", "mem_mb"),
        runtime=config_provider("solving", "runtime", default="6h"),
    params:
        solving=config_provider("solving"),
        foresight=config_provider("foresight"),
        planning_horizons=config_provider("planning_horizons"),
        sector=config_provider("sector"),
        co2_sequestration_potential=config_provider(
            "sector", "co2_sequestration_potential"
        ),
        solar_capacity_per_sqkm=solar_capacity_per_sqkm_provider,
        custom_extra_functionality=input_custom_extra_functionality,
    script:
        scripts("solve_network.py")


rule solve_operations_network:
    """Re-optimises dispatch only, holding the capacities from the solved network fixed."""
    cache: True
    input:
        code_dependencies=code_dependencies(
            "scripts/solve_operations_network.py",
            "scripts/solve_network.py",
            "scripts/_benchmark.py",
        ),
        network=RESULTS + "networks/solved_{horizon}.nc",
    output:
        network=RESULTS + "networks/operations_{horizon}.nc",
    log:
        solver=normpath(RESULTS + "logs/solve_operations_network/solver_{horizon}.log"),
        memory=RESULTS + "logs/solve_operations_network/memory_{horizon}.log",
        python=RESULTS + "logs/solve_operations_network/python_{horizon}.log",
    shadow:
        shadow_config
    threads: solver_threads
    resources:
        mem_mb=config_provider("solving", "mem_mb"),
        runtime=config_provider("solving", "runtime", default="6h"),
    params:
        solving=config_provider("solving"),
        foresight=config_provider("foresight"),
        planning_horizons=config_provider("planning_horizons"),
        sector=config_provider("sector"),
        co2_sequestration_potential=config_provider(
            "sector", "co2_sequestration_potential"
        ),
        solar_capacity_per_sqkm=solar_capacity_per_sqkm_provider,
        custom_extra_functionality=input_custom_extra_functionality,
    script:
        scripts("solve_operations_network.py")
