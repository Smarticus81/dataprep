"""
Mappers package.

Each sub-module maps one canonical domain target.
All mapper run() functions share the same signature:

    run(candidates, store, use_llm=True) -> MapperOutput

The device_context mapper has an extended signature to accept separate
structured and document candidates.
"""

from . import (
    capa_mapper,
    coding_dictionary_mapper,
    complaints_mapper,
    device_context_mapper,
    external_db_mapper,
    fsca_mapper,
    literature_mapper,
    pmcf_mapper,
    pms_plan_mapper,
    previous_psur_mapper,
    ract_mapper,
    sales_mapper,
)

__all__ = [
    "sales_mapper",
    "complaints_mapper",
    "capa_mapper",
    "fsca_mapper",
    "device_context_mapper",
    "ract_mapper",
    "previous_psur_mapper",
    "pms_plan_mapper",
    "pmcf_mapper",
    "literature_mapper",
    "external_db_mapper",
    "coding_dictionary_mapper",
]
