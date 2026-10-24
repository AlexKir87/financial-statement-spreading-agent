"""Entry point for the reconciliation node: spread in, check results and open issues out."""

from collections.abc import Sequence
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.reconcile.checks import STATEMENT_MISSING, Spread, run_checks
from app.reconcile.rules import DEFAULT_RULES, ReconRules
from app.state import CHECK_ORDER, CheckResult, OpenIssue


class ReconciliationReport(BaseModel):
    """`results` follow the canonical check order, then period order; `open_issues` explain
    every `not_applicable` result, `statement_missing_in_source` first. Neither carries a
    figure that is not in the input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    results: tuple[CheckResult, ...]
    open_issues: tuple[OpenIssue, ...]


def run_reconciliations(
    spread: Spread,
    *,
    periods: Sequence[str],
    rules: ReconRules = DEFAULT_RULES,
    tolerance: Decimal = Decimal(1),
) -> ReconciliationReport:
    """Run the six checks. `periods` lists every period oldest first; "prior period" means
    the previous entry, unless every period is a canonical key (`app/periods.py`): then each check
    runs on its own kind of period and the prior balance sheet is found by date. `tolerance`
    is in spread units, so 1 is one unit of reported scale (`Settings.rounding_tolerance_units`
    defaults to 2)."""
    if tolerance < 0:
        raise ValueError("tolerance must not be negative")
    if not periods:
        raise ValueError("periods must not be empty")
    if len(set(periods)) != len(periods):
        raise ValueError("periods must be unique")
    stray = sorted(set(spread) - set(periods))
    if stray:
        raise ValueError("spread has periods missing from `periods`: " + ", ".join(stray))
    outputs = run_checks(
        spread,
        periods,
        subtotals=rules.subtotals,
        sections=rules.sections,
        re_other_movement_ids=rules.re_other_movement_ids,
        re_column_id=rules.re_column_id,
        tolerance=tolerance,
    )
    issues = [i for key in CHECK_ORDER for i in outputs[key].open_issues]
    # A missing statement is what makes a run `delivered_with_exceptions`, so it comes first.
    issues.sort(key=lambda i: i.code != STATEMENT_MISSING)  # stable: check order is kept
    return ReconciliationReport(
        results=tuple(r for key in CHECK_ORDER for r in outputs[key].results),
        open_issues=tuple(issues),
    )
