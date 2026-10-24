"""The six reconciliation checks as pure functions (the spec "The six reconciliation checks").

Every check reads `spread` and returns `CheckResult`s plus the `OpenIssue`s a
`not_applicable` result requires. Nothing here calls a model or the network, and nothing
returns or stores a balancing figure: a result carries only values already in `spread` and
the exact difference between two sides.

For every result `difference = actual - expected`, where `expected` is the figure the
source states or the primary statement gives, and `actual` is what the other lines add up
to. The sign is kept so a reader can see which side is larger. A difference within
`tolerance` (two units of the reported scale by default) passes, and the exact difference is still
recorded. Spread values share one scale per run, so the tolerance is in spread units.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from app import periods as period_keys
from app.reconcile import rules as lines
from app.reconcile.rules import SumRule
from app.state import CheckKey, CheckResult, OpenIssue, Term

Spread = Mapping[str, Mapping[str, Decimal]]

STATEMENT_MISSING = "statement_missing_in_source"
NOT_APPLICABLE = "check_not_applicable"


@dataclass(frozen=True)
class CheckOutput:
    results: tuple[CheckResult, ...]
    open_issues: tuple[OpenIssue, ...]


@dataclass(frozen=True)
class _Side:
    value: Decimal
    terms: tuple[Term, ...]


def _cell(spread: Spread, period: str, line_id: str) -> Term | None:
    value = spread.get(period, {}).get(line_id)
    return None if value is None else Term(line_id=line_id, period=period, value=value)


def _statement_absent(spread: Spread, line_id: str) -> bool:
    """True when no period has any line of the statement `line_id` belongs to (is_, bs_, cf_)."""
    prefix = line_id.split("_", 1)[0] + "_"
    return not any(i.startswith(prefix) for cells in spread.values() for i in cells)


def _not_applicable(
    spread: Spread,
    key: CheckKey,
    period: str,
    missing: Sequence[tuple[str, str]] = (),
    why: str | None = None,
) -> tuple[CheckResult, OpenIssue]:
    """`not_applicable` always comes with an open issue that says why, never silently.

    `missing` lists the (line id, period) cells the check needed. When one of them belongs to
    a statement that has no line in any period, the issue code is `statement_missing_in_source`.
    """
    gone = any(_statement_absent(spread, line_id) for line_id, _ in missing)
    detail = why or "missing: " + ", ".join(f"{line_id} ({when})" for line_id, when in missing)
    issue = OpenIssue(
        code=STATEMENT_MISSING if gone else NOT_APPLICABLE, where=f"{key}:{period}", detail=detail
    )
    return CheckResult(key=key, period=period, status="not_applicable"), issue


def _compared(
    key: CheckKey, period: str, expected: _Side, actual: _Side, tolerance: Decimal
) -> CheckResult:
    difference = actual.value - expected.value
    return CheckResult(
        key=key,
        period=period,
        status="pass" if abs(difference) <= tolerance else "fail",
        expected=expected.value,
        actual=actual.value,
        difference=difference,
        terms=(*expected.terms, *actual.terms),
    )


def _total(terms: Sequence[Term]) -> _Side:
    return _Side(value=sum((t.value for t in terms), Decimal(0)), terms=tuple(terms))


# --- Single-comparison checks ----------------------------------------------------------

Outcome = tuple[CheckResult, OpenIssue | None]


def _bs_balances(spread: Spread, period: str, tolerance: Decimal) -> Outcome:
    assets = _cell(spread, period, lines.BS_TOTAL_ASSETS)
    liabilities = _cell(spread, period, lines.BS_TOTAL_LIABILITIES)
    equity = _cell(spread, period, lines.BS_TOTAL_EQUITY)
    combined = _cell(spread, period, lines.BS_TOTAL_LIABILITIES_AND_EQUITY)
    if liabilities is not None and equity is not None:
        right: _Side | None = _total([liabilities, equity])
    elif combined is not None:
        right = _total([combined])
    else:
        right = None
    if assets is None or right is None:
        missing = [(lines.BS_TOTAL_ASSETS, period)] if assets is None else []
        if right is None:
            missing += [
                (line_id, period)
                for line_id, cell in (
                    (lines.BS_TOTAL_LIABILITIES, liabilities),
                    (lines.BS_TOTAL_EQUITY, equity),
                )
                if cell is None
            ]
        return _not_applicable(spread, "bs_balances", period, missing)
    return _compared("bs_balances", period, _total([assets]), right, tolerance), None


def _prior_and_now(periods: Sequence[str], index: int) -> tuple[str | None, str]:
    """The balance sheet period before `periods[index]`, and the one it ends on.

    With canonical keys the prior balance sheet is found by date (the one that closes the day
    before the duration starts) and the closing one is the date the duration ends on; with any
    other labels the previous entry in the list is the prior period and the period itself is
    the closing one.
    """
    period = periods[index]
    span = period_keys.parse_key(period) if period_keys.all_canonical(periods) else None
    if span is None:
        return (periods[index - 1] if index else None), period
    instants = [s.end for k in periods if (s := period_keys.parse_key(k)) and s.start is None]
    before = period_keys.prior_balance_date(span, instants)
    return (None if before is None else before.isoformat()), span.end.isoformat()


def _no_prior_why(periods: Sequence[str], index: int) -> str:
    if period_keys.all_canonical(periods):
        return "no prior balance sheet printed for this period"
    return "no prior balance sheet for the first period"


def _cf_cash_reconciles(
    spread: Spread, periods: Sequence[str], index: int, tolerance: Decimal
) -> Outcome:
    period = periods[index]
    prior, closing = _prior_and_now(periods, index)
    if prior is None:
        return _not_applicable(
            spread,
            "cf_cash_reconciles",
            period,
            [(lines.BS_CASH, "the prior period")],
            why=_no_prior_why(periods, index),
        )
    wanted = (
        (lines.CF_NET_CHANGE_IN_CASH, period),
        (lines.BS_CASH, closing),
        (lines.BS_CASH, prior),
    )
    cells = [_cell(spread, when, line_id) for line_id, when in wanted]
    cash_flow, now, before = cells
    if cash_flow is None or now is None or before is None:
        missing = [w for w, c in zip(wanted, cells, strict=True) if c is None]
        return _not_applicable(spread, "cf_cash_reconciles", period, missing)
    expected = _Side(now.value - before.value, (before, now))
    return _compared("cf_cash_reconciles", period, expected, _total([cash_flow]), tolerance), None


def _re_rollforward(
    spread: Spread,
    periods: Sequence[str],
    index: int,
    tolerance: Decimal,
    other_ids: Sequence[str],
    column_id: str | None = None,
) -> Outcome:
    period = periods[index]
    prior, closing_period = _prior_and_now(periods, index)
    if prior is None:
        return _not_applicable(
            spread,
            "re_rollforward",
            period,
            [(lines.BS_RETAINED_EARNINGS, "the prior period")],
            why=_no_prior_why(periods, index),
        )
    wanted = (
        (lines.BS_RETAINED_EARNINGS, prior),
        (lines.BS_RETAINED_EARNINGS, closing_period),
        (lines.IS_NET_INCOME, period),
    )
    cells = [_cell(spread, when, line_id) for line_id, when in wanted]
    opening, closing, income = cells
    column = _cell(spread, period, column_id) if column_id else None
    if column is not None and opening is not None and closing is not None:
        # The equity statement's retained earnings column lists every movement, net income and
        # dividends included, so nothing else is added (plan step 26).
        actual = _total([opening, column])
        return _compared("re_rollforward", period, _total([closing]), actual, tolerance), None
    if opening is None or closing is None or income is None:
        missing = [w for w, c in zip(wanted, cells, strict=True) if c is None]
        return _not_applicable(spread, "re_rollforward", period, missing)
    if _statement_absent(spread, lines.CF_DIVIDENDS_PAID) and not any(
        _cell(spread, period, i) for i in other_ids
    ):
        return _not_applicable(
            spread,
            "re_rollforward",
            period,
            why="no cash flow statement, so dividends paid are unknown",
        )
    movements = [
        cell
        for line_id in (lines.CF_DIVIDENDS_PAID, *other_ids)
        if (cell := _cell(spread, period, line_id)) is not None
    ]
    actual = _total([opening, income, *movements])
    return _compared("re_rollforward", period, _total([closing]), actual, tolerance), None


def _ni_pl_equals_cf(spread: Spread, period: str, tolerance: Decimal) -> Outcome:
    income = _cell(spread, period, lines.IS_NET_INCOME)
    cash_flow = _cell(spread, period, lines.CF_NET_INCOME) or _cell(
        spread, period, lines.CF_STARTING_PROFIT
    )
    if income is None or cash_flow is None:
        missing = [(lines.IS_NET_INCOME, period)] if income is None else []
        if cash_flow is None:
            missing.append((lines.CF_NET_INCOME, period))
        return _not_applicable(spread, "ni_pl_equals_cf", period, missing)
    return _compared(
        "ni_pl_equals_cf", period, _total([income]), _total([cash_flow]), tolerance
    ), None


# --- Sum-rule checks -------------------------------------------------------------------


def _expand(
    spread: Spread,
    period: str,
    line_id: str,
    by_total: Mapping[str, SumRule],
    seen: tuple[str, ...],
) -> list[Term]:
    """The cell for `line_id`, or, when an intermediate subtotal was not printed, the cells
    it is built from, so an omitted subtotal never counts as zero."""
    cell = _cell(spread, period, line_id)
    if cell is not None:
        return [cell]
    rule = by_total.get(line_id)
    if rule is None or line_id in seen:
        return []
    return [
        t
        for c in rule.component_ids
        for t in _expand(spread, period, c, by_total, (*seen, line_id))
    ]


def _sum_check(
    spread: Spread, key: CheckKey, rules: Sequence[SumRule], period: str, tolerance: Decimal
) -> tuple[list[CheckResult], OpenIssue | None]:
    """One result per period when every applicable rule passes, one per failing rule else.

    A rule applies when its total is in the period and at least one component is. A
    component that is absent counts as zero, so a lost line shows as a difference. A total
    printed with no detail at all is skipped: there is nothing in the spread to compare.
    """
    by_total = {r.total_id: r for r in rules}
    outcomes: list[CheckResult] = []
    for rule in rules:
        total = _cell(spread, period, rule.total_id)
        parts = [c for i in rule.component_ids for c in _expand(spread, period, i, by_total, ())]
        if total is None or not parts:
            continue
        outcomes.append(_compared(key, period, _total([total]), _total(parts), tolerance))
    if not outcomes:
        absent = [(r.total_id, period) for r in rules if _statement_absent(spread, r.total_id)]
        everything_absent = len(absent) == len(rules)
        result, issue = _not_applicable(
            spread,
            key,
            period,
            absent if everything_absent else (),
            "no printed total with detail lines in this period",
        )
        return [result], issue
    failing = [o for o in outcomes if o.status == "fail"]
    if failing:
        return failing, None
    return [max(outcomes, key=lambda o: abs(o.difference or Decimal(0)))], None


# --- Entry for the engine --------------------------------------------------------------


def run_checks(
    spread: Spread,
    periods: Sequence[str],
    *,
    subtotals: Sequence[SumRule],
    sections: Sequence[SumRule],
    re_other_movement_ids: Sequence[str],
    tolerance: Decimal,
    re_column_id: str | None = None,
) -> dict[CheckKey, CheckOutput]:
    """Run the six checks over `periods` (oldest first); keys follow the canonical order."""
    single: dict[CheckKey, Callable[[int], Outcome]] = {
        "bs_balances": lambda i: _bs_balances(spread, periods[i], tolerance),
        "cf_cash_reconciles": lambda i: _cf_cash_reconciles(spread, periods, i, tolerance),
        "re_rollforward": lambda i: _re_rollforward(
            spread, periods, i, tolerance, re_other_movement_ids, re_column_id
        ),
        "ni_pl_equals_cf": lambda i: _ni_pl_equals_cf(spread, periods[i], tolerance),
    }
    sums: dict[CheckKey, Sequence[SumRule]] = {
        "subtotals_match_source": subtotals,
        "lines_complete": sections,
    }
    canonical = period_keys.all_canonical(periods)
    kinds = {  # canonical keys: a check runs only on the kind of period it belongs to
        "bs_balances": "instant",
        "cf_cash_reconciles": "duration",
        "re_rollforward": "duration",
        "ni_pl_equals_cf": "duration",
    }

    def applies(key: CheckKey, i: int) -> bool:
        if not canonical:
            return True
        span = period_keys.parse_key(periods[i])
        return span is not None and (span.start is None) == (kinds[key] == "instant")

    # With canonical keys a check may have no period of its kind at all (no balance sheet date,
    # no duration). It still gets one `not_applicable` result with its open issue, and a
    # statement absent from the filing is `statement_missing_in_source`, as on the older path.
    needs: dict[CheckKey, tuple[str, ...]] = {
        "bs_balances": (lines.BS_TOTAL_ASSETS,),
        "cf_cash_reconciles": (lines.CF_NET_CHANGE_IN_CASH, lines.BS_CASH),
        "re_rollforward": (lines.BS_RETAINED_EARNINGS, lines.IS_NET_INCOME),
        "ni_pl_equals_cf": (lines.IS_NET_INCOME, lines.CF_NET_INCOME),
    }
    out: dict[CheckKey, CheckOutput] = {}
    for key, check in single.items():
        indexes = [i for i in range(len(periods)) if applies(key, i)]
        if canonical and not indexes:
            result, issue = _not_applicable(
                spread,
                key,
                periods[-1],
                [(line_id, "any period") for line_id in needs[key]],
                "no balance sheet date among the periods"
                if kinds[key] == "instant"
                else "no duration period (income statement or cash flow column) among the periods",
            )
            out[key] = CheckOutput(results=(result,), open_issues=(issue,))
            continue
        pairs = [check(i) for i in indexes]
        out[key] = CheckOutput(
            results=tuple(r for r, _ in pairs),
            open_issues=tuple(i for _, i in pairs if i is not None),
        )
    for key, rule_set in sums.items():
        results: list[CheckResult] = []
        issues: list[OpenIssue] = []
        for period in periods:
            rs, sum_issue = _sum_check(spread, key, rule_set, period, tolerance)
            results.extend(rs)
            if sum_issue is not None:
                issues.append(sum_issue)
        out[key] = CheckOutput(results=tuple(results), open_issues=tuple(issues))
    return out
