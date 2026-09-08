"""Frozen enum constants for the v1 pipeline conformance package.

Values mirror DATASET_CONSTRUCTION_PIPELINE_V1.md sections 2 (core objects,
four independent statuses), 2.1 (evidence inference matrix), 5 (semantic /
authorization statuses) and 8-9 (physical statuses).  Enums are plain tuples so
fixtures and schemas stay JSON-serializable.
"""

OBJECT_TYPES: tuple[str, ...] = (
    "CorpusCard",
    "EvidenceUnit",
    "EvidenceBundle",
    "CanonicalResponsibility",
    "Query",
    "Contract",
    "OpportunityPredicate",
    "PhysicalProcess",
    "Episode",
)

RELEASE_STATUSES: tuple[str, ...] = ("provisional", "frozen", "invalidated", "released")

SEMANTIC_STATUSES: tuple[str, ...] = (
    "provisional_ai_pilot",
    "human_validated",
    "critical_case_validated",
    "rejected_or_unresolved",
    "semantic_unknown",
    "out_of_scope",
)

AUTHORIZATION_STATUSES: tuple[str, ...] = (
    "authorized_agent_control",
    "authorized_automation_only",
    "manual_only",
    "refused",
    "unknown",
)

PHYSICAL_STATUSES: tuple[str, ...] = (
    "unassigned",
    "candidate_binding",
    "positive_opportunity",
    "boundary_opportunity",
    "certified_no_opportunity",
    "unsupported",
)

AUTHORSHIP_VALUES: tuple[str, ...] = (
    "authored",
    "copied",
    "suggested",
    "default",
    "active",
    "abandoned",
    "unknown",
)

SOURCE_TYPES: tuple[str, ...] = (
    "direct_interview_diary_quotation",
    "participant_authored_desired_automation",
    "naturally_authored_routine",
    "interaction_log",
    "caregiver_researcher_authored_rule",
    "default_copied_synthetic_rule",
)

# Section 2.1 inference matrix (informational copy; authoritative copy is the
# machine-readable fixture fixtures/inference_matrix.json).
PROPOSITION_CODES: tuple[str, ...] = (
    "reported_purpose",
    "beneficiary",
    "desired_state",
    "failure_experience",
    "failure_meaning",
    "preference",
    "refusal",
    "recurrence",
    "persistence",
    "accountability",
    "ownership",
    "override_release",
    "delegation_acceptance",
    "deployment",
    "consent",
    "authorized_action",
    "configured_mechanism",
    "context",
    "trigger",
    "platform_capability",
    "test_fixture",
    "observed_action_recurrence",
    "override_behavior",
)

# Section 5: delegation/action authorization.
AUTHORIZATION_REQUIREMENT = {
    "authorized_agent_control": "nonempty bounded action set required",
}

# Section 2: allowed release-status transitions.
RELEASE_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "provisional": ("frozen", "invalidated"),
    "frozen": ("released", "invalidated"),
    "invalidated": (),
    "released": (),
}

EPISODE_SPLITS: tuple[str, ...] = ("train", "dev", "test", "none")

TERMINAL_VERDICTS: tuple[str, ...] = (
    "released",
    "continues_beyond_window",
    "censored_pending",
    "violated_at_truncation",
)

ANTI_GAMING_RULES: tuple[str, ...] = ("terminal_guard_band", "terminal_viability")

TRACKS: tuple[str, ...] = (
    "seen_responsibility_unseen_process",
    "unseen_paraphrase",
    "held_out_responsibility_family",
)
