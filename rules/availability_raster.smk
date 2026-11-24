# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT


localrules:
    availability_rasters,


rule build_availability_raster:
    input:
        code_dependencies=code_dependencies("scripts/build_availability_raster.py"),
        corine=ancient(rules.retrieve_corine.output["tif_file"]),
        natura=lambda w: (
            f"{NATURA_DATASET['folder']}/natura.tiff"
            if config_provider("renewable", w.technology, "natura")(w)
            else []
        ),
        luisa=lambda w: (
            rules.retrieve_luisa_land_cover.output["tif"]
            if config_provider("renewable", w.technology, "luisa")(w)
            else []
        ),
        gebco=ancient(
            lambda w: (
                rules.retrieve_gebco.output["gebco"]
                if (
                    config_provider("renewable", w.technology)(w).get("max_depth")
                    or config_provider("renewable", w.technology)(w).get("min_depth")
                )
                else []
            )
        ),
        ship_density=lambda w: (
            resources("shipdensity_raster.tif")
            if "ship_threshold" in config_provider("renewable", w.technology)(w).keys()
            else []
        ),
        country_shapes=resources("country_shapes.geojson"),
        offshore_shapes=resources("offshore_shapes.geojson"),
        regions=lambda w: (
            resources("onshore_regions.geojson")
            if w.technology in ("onwind", "solar", "solar-hsat")
            else resources("offshore_regions.geojson")
        ),
        cutout=lambda w: input_cutout(
            w, config_provider("renewable", w.technology, "cutout")(w)
        ),
    output:
        resources("availability_raster_{technology}.tif"),
    log:
        logs("build_availability_raster_{technology}.log"),
    wildcard_constraints:
        technology="(?!hydro).*",  # Any technology other than hydro
    cache: True
    threads: 1
    resources:
        mem_mb=25000,
    params:
        renewable=lambda w: config_provider("renewable", w.technology)(w),
        technology="{technology}",
    message:
        "Building the fine eligibility raster for {wildcards.technology} technology"
    script:
        scripts("build_availability_raster.py")


rule availability_rasters:
    input:
        lambda w: expand(
            resources("availability_raster_{technology}.tif"),
            run=config["run"]["name"],
            technology=[
                t
                for t in config_provider("electricity", "renewable_carriers")(w)
                if t != "hydro"
            ],
        ),
    message:
        "Collecting fine eligibility rasters"
