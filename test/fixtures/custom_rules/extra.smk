# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

# Fixture rule file listed under `custom_rules` by config.custom_rules.yaml.


rule custom_rules_marker:
    output:
        "results/custom_rules_marker.txt",
    shell:
        "echo custom > {output}"
