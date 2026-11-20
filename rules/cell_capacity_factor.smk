# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT


localrules:
    cell_capacity_factors,


rule build_cell_capacity_factor:
    """Calculates the average capacity factor of a renewable technology for every cutout grid cell."""
    input:
        code_dependencies=code_dependencies("scripts/build_cell_capacity_factor.py"),
        cutout=lambda w: input_cutout(
            w, config_provider("renewable", w.technology, "cutout")(w)
        ),
    output:
        resources("cell_capacity_factor_{technology}.nc"),
    log:
        logs("build_cell_capacity_factor_{technology}.log"),
    wildcard_constraints:
        technology="(?!hydro).*",  # Any technology other than hydro
    cache: True
    threads: config["atlite"].get("nprocesses", 4)
    resources:
        mem_mb=config["atlite"].get("nprocesses", 4) * 25000,
    params:
        renewable=lambda w: config_provider("renewable", w.technology)(w),
        technology="{technology}",
    message:
        "Building the per-cell capacity factor for {wildcards.technology} technology"
    script:
        scripts("build_cell_capacity_factor.py")


rule cell_capacity_factors:
    """Builds the per-cell capacity factor of every renewable technology."""
    input:
        lambda w: expand(
            resources("cell_capacity_factor_{technology}.nc"),
            run=config["run"]["name"],
            technology=[
                t
                for t in config_provider("electricity", "renewable_carriers")(w)
                if t != "hydro"
            ],
        ),
    message:
        "Collecting per-cell capacity factor files"
