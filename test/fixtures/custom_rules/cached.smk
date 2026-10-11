# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT


# A cache-eligible rule the caching tests run as a real cluster job.
rule custom_rules_cached_marker:
    cache: True
    output:
        "results/custom_rules_cached_marker.txt",
    shell:
        "echo cached > {output}"
