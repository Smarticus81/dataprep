"""
Schema registry — canonical source of truth for all target files.

Each CanonicalSchema defines:
  - the output filename
  - required and optional fields with type rules
  - accepted source aliases for each field
  - enum constraints
  - placeholder token detection
  - whether absence of the target is blocking for PSUR generation
  - row-level uniqueness rules (tabular targets)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set, Tuple


@dataclass(frozen=True)
class FieldDef:
    name: str
    required: bool
    dtype: str                                  # "str", "date", "int", "float", "bool", "enum"
    enum_values: Tuple[str, ...] = ()           # canonical enum set if dtype == "enum"
    aliases: Tuple[str, ...] = ()               # accepted source aliases (lower-strip compared)
    allow_null: bool = False
    description: str = ""


@dataclass(frozen=True)
class CanonicalSchema:
    filename: str
    canonical_type: str
    is_tabular: bool                            # True -> CSV output; False -> JSON output
    fields: Tuple[FieldDef, ...]
    absence_is_blocking: bool
    uniqueness_fields: Tuple[str, ...] = ()     # tabular uniqueness constraint
    placeholder_tokens: Tuple[str, ...] = (
        "tbd", "n/a", "na", "none", "pending", "unknown", "xxx", "?", "-", "",
        "not applicable", "to be determined", "to be confirmed", "tbc",
    )

    def required_fields(self) -> List[FieldDef]:
        return [f for f in self.fields if f.required]

    def optional_fields(self) -> List[FieldDef]:
        return [f for f in self.fields if not f.required]

    def field_by_name(self, name: str) -> Optional[FieldDef]:
        for f in self.fields:
            if f.name == name:
                return f
        return None

    def all_aliases(self) -> Dict[str, str]:
        """Return {alias_lower: canonical_field_name} for every alias."""
        out: Dict[str, str] = {}
        for fdef in self.fields:
            out[fdef.name.lower()] = fdef.name
            for alias in fdef.aliases:
                out[alias.lower()] = fdef.name
        return out


# ---------------------------------------------------------------------------
# Field definitions — grouped by canonical target
# ---------------------------------------------------------------------------

def _f(name: str, required: bool, dtype: str, aliases: Tuple[str, ...] = (),
       enum_values: Tuple[str, ...] = (), allow_null: bool = False,
       description: str = "") -> FieldDef:
    return FieldDef(
        name=name,
        required=required,
        dtype=dtype,
        aliases=aliases,
        enum_values=enum_values,
        allow_null=allow_null,
        description=description,
    )


# ---------------------------------------------------------------------------
# 1. sales.csv
# ---------------------------------------------------------------------------

SALES_SCHEMA = CanonicalSchema(
    filename="sales.csv",
    canonical_type="sales",
    is_tabular=True,
    absence_is_blocking=True,
    fields=(
        _f("date", True, "date",
           aliases=("ship date", "shipped date", "shipment date", "invoice date",
                    "dispatch date", "order date", "sale date", "transaction date")),
        _f("quantity", True, "int",
           aliases=("qty", "units", "units sold", "units shipped", "volume",
                    "quantity sold", "quantity shipped", "no. of units")),
        _f("country", True, "str",
           aliases=("country of destination", "destination country", "ship to country",
                    "market", "territory", "country name")),
        _f("region", False, "str",
           aliases=("sales region", "geo region", "region name", "geography", "geo")),
        _f("product", False, "str",
           aliases=("product name", "product description", "item", "item description",
                    "catalog item", "article")),
        _f("device_model", False, "str",
           aliases=("model", "model no", "model number", "model no.", "catalog no",
                    "catalog number", "catalogue number", "part no", "part number",
                    "sku", "reference number", "ref no")),
    ),
)


# ---------------------------------------------------------------------------
# 2. complaints.csv
# ---------------------------------------------------------------------------

COMPLAINTS_SCHEMA = CanonicalSchema(
    filename="complaints.csv",
    canonical_type="complaints",
    is_tabular=True,
    absence_is_blocking=True,
    uniqueness_fields=("complaint_number",),
    fields=(
        _f("complaint_number", True, "str",
           aliases=("complaint no", "complaint #", "complaint id", "record id",
                    "case number", "case id", "event id", "complaint reference",
                    "complaint ref", "report number", "report id", "ticket number",
                    "complaint no.", "ref no", "reference id", "tracking number")),
        _f("date", True, "date",
           aliases=("date received", "received date", "date of complaint",
                    "complaint date", "date reported", "report date",
                    "date of event", "event date", "date filed")),
        _f("description", True, "str",
           aliases=("complaint description", "event description", "narrative",
                    "problem description", "issue description", "details",
                    "complaint text", "description of event", "event narrative",
                    "failure description")),
        _f("serious", True, "bool",
           aliases=("serious event", "serious complaint", "serious adverse event",
                    "serious incident", "sae", "is serious", "serious?",
                    "serious (y/n)", "serious y/n")),
        _f("region", False, "str",
           aliases=("complaint region", "region of origin", "market region")),
        _f("country", False, "str",
           aliases=("country", "country of origin", "country of report",
                    "country of complaint", "reporting country")),
        _f("device_model", False, "str",
           aliases=("model", "model no", "model number", "model no.", "catalog number",
                    "device model", "part number", "device reference")),
        _f("harm", False, "str",
           aliases=("harm description", "patient harm", "harm type", "type of harm",
                    "harm category", "injury description")),
        _f("imdrf_code", False, "str",
           aliases=("imdrf", "imdrf code", "imdrf annex a", "problem code",
                    "device problem code", "meddra code", "meddra")),
        _f("patient_outcome", False, "str",
           aliases=("outcome", "patient outcome", "patient status", "result",
                    "clinical outcome", "outcome description")),
        _f("reportable", False, "bool",
           aliases=("mfr reportable", "mdr reportable", "reportable", "regulatory reportable",
                    "report required", "mandatory report", "is reportable",
                    "reportable (y/n)", "reportable y/n", "vigilance report required")),
        _f("regulatory_report_reference", False, "str",
           aliases=("mdr number", "vigilance report no", "regulatory report no",
                    "report reference", "regulatory report reference",
                    "mhra report reference", "competent authority report")),
        _f("lot_or_batch", False, "str",
           aliases=("lot", "batch", "lot number", "batch number", "lot/batch",
                    "lot no", "batch no", "serial number", "serial no")),
    ),
)


# ---------------------------------------------------------------------------
# 3. capa.csv
# ---------------------------------------------------------------------------

CAPA_SCHEMA = CanonicalSchema(
    filename="capa.csv",
    canonical_type="capa",
    is_tabular=True,
    absence_is_blocking=False,
    uniqueness_fields=("capa_number",),
    fields=(
        _f("capa_number", True, "str",
           aliases=("capa no", "capa id", "capa #", "capa reference", "capa ref",
                    "action number", "action id", "nc number", "nonconformance id",
                    "corrective action number", "preventive action number")),
        _f("title", True, "str",
           aliases=("capa title", "description", "capa description", "subject",
                    "action title", "brief description")),
        _f("status", True, "enum",
           enum_values=("open", "closed", "in_progress", "pending_verification",
                        "cancelled", "on_hold"),
           aliases=("capa status", "action status", "current status", "state")),
        _f("open_date", True, "date",
           aliases=("date opened", "date created", "creation date", "open date",
                    "initiation date", "date initiated", "start date")),
        _f("close_date", False, "date", allow_null=True,
           aliases=("date closed", "closure date", "completion date", "closed date",
                    "date completed", "resolved date")),
        _f("root_cause", False, "str",
           aliases=("root cause", "root cause analysis", "rca", "cause",
                    "underlying cause", "root cause description")),
        _f("type", False, "enum",
           enum_values=("corrective", "preventive", "both"),
           aliases=("capa type", "action type", "ca/pa type", "type of action")),
        _f("target_completion_date", False, "date", allow_null=True,
           aliases=("target date", "due date", "completion target",
                    "target closure date", "planned completion")),
        _f("effectiveness", False, "str",
           aliases=("effectiveness", "effectiveness check", "verification of effectiveness",
                    "effectiveness result", "effectiveness assessment")),
        _f("escalation_or_recovery_plan", False, "str",
           aliases=("escalation plan", "recovery plan", "escalation", "recovery",
                    "contingency plan", "escalation or recovery")),
    ),
)


# ---------------------------------------------------------------------------
# 4. fsca.csv
# ---------------------------------------------------------------------------

FSCA_SCHEMA = CanonicalSchema(
    filename="fsca.csv",
    canonical_type="fsca",
    is_tabular=True,
    absence_is_blocking=False,
    uniqueness_fields=("action_id",),
    fields=(
        _f("action_id", True, "str",
           aliases=("fsca no", "fsca id", "fsca #", "fsca number", "fsca ref",
                    "field action id", "field safety corrective action id",
                    "recall id", "recall number", "action reference")),
        _f("reason", True, "str",
           aliases=("reason for fsca", "reason for action", "fsca reason",
                    "reason for recall", "safety issue", "reason")),
        _f("device_model", True, "str",
           aliases=("model", "model number", "device model", "model no",
                    "catalog number", "affected model")),
        _f("device_name", True, "str",
           aliases=("device name", "product name", "device description",
                    "affected device name")),
        _f("date_initiated", True, "date",
           aliases=("date initiated", "initiation date", "fsca initiation date",
                    "date of initiation", "start date", "date started",
                    "date action initiated")),
        _f("status", True, "enum",
           enum_values=("open", "closed", "ongoing", "completed"),
           aliases=("fsca status", "action status", "current status", "state")),
        _f("final_fsn_date", False, "date", allow_null=True,
           aliases=("fsn date", "field safety notice date", "final notice date",
                    "date of fsn", "fsn issued date", "notice date")),
        _f("regions_affected", False, "str",
           aliases=("regions affected", "affected regions", "countries affected",
                    "affected countries", "markets affected")),
        _f("uk_market_affected", False, "bool",
           aliases=("uk affected", "uk market affected", "uk market", "affects uk",
                    "uk?")),
        _f("date_reported_to_mhra", False, "date", allow_null=True,
           aliases=("mhra report date", "date reported to mhra", "mhra notification date",
                    "date notified to mhra", "competent authority report date")),
        _f("effectiveness", False, "str",
           aliases=("effectiveness", "effectiveness assessment", "effectiveness check",
                    "corrective action effectiveness", "fsca effectiveness")),
    ),
)


# ---------------------------------------------------------------------------
# 5. device_context.json
# ---------------------------------------------------------------------------

DEVICE_CONTEXT_SCHEMA = CanonicalSchema(
    filename="device_context.json",
    canonical_type="device_context",
    is_tabular=False,
    absence_is_blocking=True,
    fields=(
        _f("device_trade_names", True, "str",
           aliases=("device name", "trade name", "product name", "brand name",
                    "device trade name", "commercial name")),
        _f("device_description", True, "str",
           aliases=("device description", "description", "product description",
                    "device summary", "general description")),
        _f("intended_purpose", True, "str",
           aliases=("intended purpose", "intended use", "purpose", "device purpose",
                    "medical purpose", "clinical purpose")),
        _f("indications_for_use", True, "str",
           aliases=("indications", "indications for use", "clinical indications",
                    "intended indications", "indications and intended use")),
        _f("target_patient_population", True, "str",
           aliases=("patient population", "target population", "intended patient",
                    "patient group", "target patient population")),
        _f("intended_user_profile", True, "str",
           aliases=("intended user", "user profile", "user group",
                    "intended users", "operator profile")),
        _f("basic_udi_di_or_device_family_name", True, "str",
           aliases=("basic udi-di", "udi-di", "udi", "device family", "device family name",
                    "basic udi", "udi number", "universal device identifier")),
        _f("model_or_catalog_numbers", True, "str",
           aliases=("model number", "catalog number", "catalogue number", "part number",
                    "reference number", "model no", "cat no", "ref no")),
        _f("eu_mdr_classification_and_rule", True, "str",
           aliases=("mdr class", "eu class", "classification", "eu classification",
                    "mdr classification", "eu mdr class", "classification rule",
                    "eu mdr classification", "device class")),
        _f("notified_body_name_and_id", True, "str",
           aliases=("notified body", "nb", "nb name", "notified body id",
                    "notified body number", "nb number")),
        _f("sterility_status", True, "str",
           aliases=("sterility", "sterile", "sterility status", "sterile product",
                    "sterility condition", "sterilisation")),
        _f("single_use_or_reusable", True, "str",
           aliases=("single use", "reusable", "single-use", "reuse",
                    "single use or reusable", "disposable", "re-usable")),
        _f("contraindications", False, "str",
           aliases=("contraindications", "contraindicated", "warnings",
                    "contraindication", "clinical contraindications")),
        _f("uk_mdr_classification_and_rule", False, "str",
           aliases=("uk class", "ukca class", "uk mdr class", "uk classification",
                    "uk mdr classification")),
        _f("uk_responsible_person", False, "str",
           aliases=("uk responsible person", "ukrp", "uk rep", "uk rp",
                    "uk authorised representative")),
        _f("ukca_marking_status", False, "str",
           aliases=("ukca", "ukca marking", "ukca status", "ukca marked")),
        _f("emdn_code", False, "str",
           aliases=("emdn", "emdn code", "european medical device nomenclature")),
        _f("gmdn_code", False, "str",
           aliases=("gmdn", "gmdn code", "global medical device nomenclature",
                    "gmdn term")),
        _f("date_of_first_ce_marking_or_doc", False, "date",
           aliases=("first ce marking date", "ce marking date", "doc date",
                    "date of first ce mark", "initial certification date",
                    "date of ce certificate")),
        _f("cer_document_number_and_version", False, "str",
           aliases=("cer number", "cer document number", "cer version",
                    "clinical evaluation report number", "cer ref")),
        _f("cer_date_or_last_update", False, "date",
           aliases=("cer date", "cer update date", "cer last update",
                    "clinical evaluation report date")),
        _f("device_lifetime", False, "str",
           aliases=("device lifetime", "service life", "useful life",
                    "expected lifetime", "product lifetime")),
        _f("market_history", False, "str",
           aliases=("market history", "date first marketed", "market entry",
                    "introduction to market", "market launch date")),
        _f("pms_plan_document", False, "str",
           aliases=("pms plan", "pms plan reference", "pms plan document number")),
        _f("pmcf_plan_document", False, "str",
           aliases=("pmcf plan", "pmcf plan reference", "pmcf plan document number")),
        _f("risk_management_file_document_number", False, "str",
           aliases=("rmf number", "risk management file", "rmf document number",
                    "risk management file number", "risk management plan")),
        _f("ifu_document", False, "str",
           aliases=("ifu", "ifu number", "instructions for use", "ifu document number",
                    "ifu reference")),
        _f("other_associated_documents", False, "str",
           aliases=("associated documents", "related documents", "other documents")),
    ),
)


# ---------------------------------------------------------------------------
# 6. ract.json
# ---------------------------------------------------------------------------

RACT_HAZARD_FIELDS = (
    _f("hazard_id", True, "str",
       aliases=("hazard id", "id", "hazard number", "hazard no", "risk id")),
    _f("hazard_description", True, "str",
       aliases=("hazard", "hazard description", "hazardous situation",
                "hazard name", "hazard situation")),
    _f("harm", True, "str",
       aliases=("harm", "harm description", "potential harm", "harm category")),
    _f("severity", True, "str",
       aliases=("severity", "severity level", "severity rating",
                "severity score", "severity pre-mitigation")),
    _f("probability_before", True, "str",
       aliases=("probability before", "probability pre", "pre-mitigation probability",
                "p1", "occurrence before", "likelihood before")),
    _f("risk_level_before", True, "str",
       aliases=("risk level before", "risk before", "pre-mitigation risk",
                "initial risk", "risk prior to control")),
    _f("risk_control", True, "str",
       aliases=("risk control", "control measure", "mitigation", "risk mitigation",
                "control", "mitigation measure")),
    _f("probability_after", True, "str",
       aliases=("probability after", "probability post", "post-mitigation probability",
                "p2", "occurrence after", "likelihood after", "residual probability")),
    _f("risk_level_after", True, "str",
       aliases=("risk level after", "risk after", "post-mitigation risk",
                "residual risk", "risk post-control")),
    _f("expected_rate", True, "str",
       aliases=("expected rate", "expected frequency", "expected occurrence rate",
                "anticipated rate")),
    _f("max_expected_rate", True, "str",
       aliases=("max expected rate", "maximum expected rate", "upper bound rate",
                "max rate", "maximum rate")),
    _f("hazard_category", False, "str",
       aliases=("hazard category", "hazard type", "category")),
    _f("imdrf_code", False, "str",
       aliases=("imdrf", "imdrf code", "device problem code")),
    _f("medical_device_problem", False, "str",
       aliases=("medical device problem", "device problem", "problem description")),
)

RACT_SCHEMA = CanonicalSchema(
    filename="ract.json",
    canonical_type="ract",
    is_tabular=False,
    absence_is_blocking=True,
    fields=RACT_HAZARD_FIELDS,
)


# ---------------------------------------------------------------------------
# 7. previous_psur.json
# ---------------------------------------------------------------------------

PREVIOUS_PSUR_SCHEMA = CanonicalSchema(
    filename="previous_psur.json",
    canonical_type="previous_psur",
    is_tabular=False,
    absence_is_blocking=True,
    fields=(
        _f("period", True, "str",
           aliases=("reporting period", "psur period", "period covered", "review period")),
        _f("cadence", True, "str",
           aliases=("reporting cadence", "psur cadence", "cadence", "frequency",
                    "reporting frequency")),
        _f("device_name", True, "str",
           aliases=("device name", "product name", "device")),
        _f("manufacturer", True, "str",
           aliases=("manufacturer", "manufacturer name", "mfr", "company",
                    "legal manufacturer")),
        _f("prior_actions", True, "str",
           aliases=("prior actions", "previous actions", "actions from last psur",
                    "actions taken", "follow-up actions")),
        _f("complaint_summary", True, "str",
           aliases=("complaint summary", "complaints summary", "complaint overview")),
        _f("serious_incidents_count", True, "int",
           aliases=("serious incidents", "number of serious incidents",
                    "count of serious incidents", "serious event count")),
        _f("trend_data", False, "str",
           aliases=("trend data", "trending", "trend analysis")),
        _f("sales_data", False, "str",
           aliases=("sales data", "sales summary", "units sold")),
        _f("notified_body_review", False, "str",
           aliases=("nb review", "notified body review", "nb feedback")),
        _f("sections", False, "str",
           aliases=("sections", "report sections", "psur sections")),
    ),
)


# ---------------------------------------------------------------------------
# 8. pms_plan.json
# ---------------------------------------------------------------------------

PMS_PLAN_SCHEMA = CanonicalSchema(
    filename="pms_plan.json",
    canonical_type="pms_plan",
    is_tabular=False,
    absence_is_blocking=True,
    fields=(
        _f("device_name", True, "str",
           aliases=("device name", "product name", "device")),
        _f("device_classification", True, "str",
           aliases=("classification", "device class", "mdr class")),
        _f("proactive_activities", True, "str",
           aliases=("proactive activities", "proactive pms", "proactive surveillance")),
        _f("reactive_activities", True, "str",
           aliases=("reactive activities", "reactive pms", "reactive surveillance",
                    "complaint handling", "vigilance")),
        _f("psur_cadence", True, "str",
           aliases=("psur cadence", "cadence", "reporting frequency",
                    "psur frequency", "reporting cadence")),
        _f("pms_plan_version", False, "str",
           aliases=("version", "pms plan version", "plan version", "revision")),
        _f("pms_plan_date", False, "date",
           aliases=("pms plan date", "plan date", "effective date", "date of plan")),
        _f("trend_reporting_thresholds", False, "str",
           aliases=("trend thresholds", "trending thresholds",
                    "trend reporting thresholds", "signal thresholds")),
        _f("complaint_handling_summary", False, "str",
           aliases=("complaint handling", "complaint process", "complaint handling summary")),
        _f("pmcf_plan_reference", False, "str",
           aliases=("pmcf reference", "pmcf plan reference", "pmcf plan")),
        _f("pmcf_activities", False, "str",
           aliases=("pmcf activities", "pmcf tasks", "post-market clinical follow-up")),
        _f("associated_documents", False, "str",
           aliases=("associated documents", "related documents", "linked documents")),
    ),
)


# ---------------------------------------------------------------------------
# 9. pmcf.json
# ---------------------------------------------------------------------------

PMCF_SCHEMA = CanonicalSchema(
    filename="pmcf.json",
    canonical_type="pmcf",
    is_tabular=False,
    absence_is_blocking=False,
    fields=(
        _f("pmcf_plan_reference", True, "str",
           aliases=("pmcf plan", "pmcf reference", "pmcf plan reference",
                    "pmcf plan number", "plan reference")),
        _f("activities", True, "str",
           aliases=("pmcf activities", "activities", "planned activities",
                    "pmcf tasks", "clinical data activities")),
        _f("pmcf_evaluation_report_reference", False, "str",
           aliases=("pmcf evaluation report", "evaluation report reference",
                    "pmcfer reference", "pmcf evaluation report number")),
        _f("enrollment_recovery_plan", False, "str",
           aliases=("enrollment plan", "recruitment plan", "recovery plan",
                    "enrollment recovery plan")),
    ),
)


# ---------------------------------------------------------------------------
# 10. literature_search.json
# ---------------------------------------------------------------------------

LITERATURE_SCHEMA = CanonicalSchema(
    filename="literature_search.json",
    canonical_type="literature",
    is_tabular=False,
    absence_is_blocking=False,
    fields=(
        _f("reference", False, "str",
           aliases=("reference", "search reference", "literature reference",
                    "search report reference")),
        _f("search_period", False, "str",
           aliases=("search period", "search dates", "period searched",
                    "date range searched")),
        _f("methodology", False, "str",
           aliases=("methodology", "search methodology", "search strategy",
                    "search method")),
        _f("records_screened", False, "int",
           aliases=("records screened", "articles screened", "records identified",
                    "total records", "hits")),
        _f("relevant_articles_identified", False, "int",
           aliases=("relevant articles", "relevant records", "included articles",
                    "articles included", "relevant publications")),
        _f("summary_of_new_data_performance_or_safety", False, "str",
           aliases=("summary", "findings", "new findings", "safety summary",
                    "performance summary", "new data summary")),
        _f("newly_observed_uses", False, "str",
           aliases=("new uses", "newly observed uses", "off-label use",
                    "new indications observed")),
        _f("previously_unassessed_risks", False, "str",
           aliases=("new risks", "unassessed risks", "previously unidentified risks",
                    "new hazards")),
        _f("state_of_the_art_changes", False, "str",
           aliases=("sota changes", "state of the art", "sota", "soa changes")),
        _f("comparison_with_similar_devices", False, "str",
           aliases=("comparators", "similar devices", "comparable devices",
                    "device comparison")),
    ),
)


# ---------------------------------------------------------------------------
# 11. external_events.csv
# ---------------------------------------------------------------------------

EXTERNAL_EVENTS_SCHEMA = CanonicalSchema(
    filename="external_events.csv",
    canonical_type="external_db",
    is_tabular=True,
    absence_is_blocking=False,
    uniqueness_fields=("event_id",),
    fields=(
        _f("event_id", True, "str",
           aliases=("event id", "record id", "id", "reference", "maude id",
                    "database id", "submission id")),
        _f("date", True, "date",
           aliases=("date", "event date", "date of event", "report date",
                    "date received")),
        _f("device_model", True, "str",
           aliases=("model", "device model", "model number", "device")),
        _f("device_name", True, "str",
           aliases=("device name", "product name", "brand name")),
        _f("external_source", True, "str",
           aliases=("source", "database", "data source", "external source",
                    "database name", "source database", "registry")),
        _f("description", True, "str",
           aliases=("description", "event description", "problem description",
                    "report text", "event narrative", "details")),
        _f("narrative", True, "str",
           aliases=("narrative", "full narrative", "detailed description",
                    "manufacturer narrative", "event details")),
        _f("event_type", True, "str",
           aliases=("event type", "type", "report type", "event category",
                    "type of event")),
        _f("serious", True, "bool",
           aliases=("serious", "is serious", "serious event", "serious (y/n)")),
        _f("outcome", True, "str",
           aliases=("outcome", "patient outcome", "result", "clinical outcome")),
    ),
)


# ---------------------------------------------------------------------------
# 12. coding_dictionary.json
# ---------------------------------------------------------------------------

CODING_DICTIONARY_SCHEMA = CanonicalSchema(
    filename="coding_dictionary.json",
    canonical_type="coding_dictionary",
    is_tabular=False,
    absence_is_blocking=False,
    fields=(
        _f("AnnexA", False, "str",
           aliases=("annex a", "annex_a", "imdrf annex a", "problem codes")),
        _f("AnnexF", False, "str",
           aliases=("annex f", "annex_f", "imdrf annex f", "outcome codes")),
    ),
)


# ---------------------------------------------------------------------------
# Registry lookup
# ---------------------------------------------------------------------------

ALL_SCHEMAS: Dict[str, CanonicalSchema] = {
    "sales": SALES_SCHEMA,
    "complaints": COMPLAINTS_SCHEMA,
    "capa": CAPA_SCHEMA,
    "fsca": FSCA_SCHEMA,
    "device_context": DEVICE_CONTEXT_SCHEMA,
    "ract": RACT_SCHEMA,
    "previous_psur": PREVIOUS_PSUR_SCHEMA,
    "pms_plan": PMS_PLAN_SCHEMA,
    "pmcf": PMCF_SCHEMA,
    "literature": LITERATURE_SCHEMA,
    "external_db": EXTERNAL_EVENTS_SCHEMA,
    "coding_dictionary": CODING_DICTIONARY_SCHEMA,
}

# Canonical types that are "core" for PSUR generation
CORE_REQUIRED_TYPES: FrozenSet[str] = frozenset({
    "sales", "complaints", "device_context", "ract", "previous_psur", "pms_plan",
})

# Types that are strongly recommended but not always blocking
STRONGLY_RECOMMENDED_TYPES: FrozenSet[str] = frozenset({
    "capa", "fsca", "pmcf", "literature", "external_db", "coding_dictionary",
})


def get_schema(canonical_type: str) -> Optional[CanonicalSchema]:
    return ALL_SCHEMAS.get(canonical_type)
