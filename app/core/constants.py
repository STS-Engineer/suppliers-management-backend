"""Shared business constants used across multiple features."""

# Relations whose panel_decision qualifies them as "on the panel".
# Both committee-validated forms are included: `panel_add_exec_committee` is
# written by the Monday import / grade→status derivation, while
# `panel_add_committee_validated` is written by the committee-review workflow.
PANEL_ACTIVE_DECISIONS: tuple[str, ...] = (
    "panel_add",
    "panel_add_exec_committee",
    "panel_add_committee_validated",
)

# Evaluation frequency → maximum days before the relation is overdue
EVAL_FREQUENCY_DAYS: dict[str, int] = {
    "Quarterly": 91,
    "Semi-Annual": 182,
    "Annual": 365,
}
