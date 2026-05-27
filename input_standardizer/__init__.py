"""
PSUR Input Standardizer
=======================

Transforms messy real-world raw input packages into clean, canonical,
auditable input packages for downstream PSUR generation.

Programmatic usage::

    from input_standardizer.service import standardize

    result = standardize(input_dir="Data/", output_dir="Output/")
    print(result.ready)          # True or False
    print(result.output_dir)     # path to canonical package

CLI usage::

    python -m input_standardizer.cli

"""

__version__ = "1.0.0"
