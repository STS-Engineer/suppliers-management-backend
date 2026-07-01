"""Shared business constants used across multiple features."""

# Relations whose panel_decision qualifies them as "on the panel"
PANEL_ACTIVE_DECISIONS: tuple[str, ...] = (
    "panel_add",
    "panel_add_committee_validated",
)

# Evaluation frequency → maximum days before the relation is overdue
EVAL_FREQUENCY_DAYS: dict[str, int] = {
    "Quarterly": 91,
    "Semi-Annual": 182,
    "Annual": 365,
}
