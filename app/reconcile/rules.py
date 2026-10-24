"""Line ids and sum rules the six checks read (the spec "The six reconciliation checks").

The rules are data, not logic: step 7 may load them from `mapping/standard_lines.csv`
without touching `checks.py`. The ids below are the provisional standard set the eval cases
already use (`bs_total_assets`, `is_net_income`, `cf_net_change_in_cash`).

Sign convention (the engine relies on it): spread values keep the sign the source prints,
so a cost or an outflow is negative (cost of sales `-3000`, dividends paid `-100`) and a
total is the plain sum of its components. The one exception is made upstream, in `build_spread`:
income statement costs printed as positive numbers are flipped to negative when the printed
subtotals prove that is how the filing is built.
"""

from pydantic import BaseModel, ConfigDict, Field

from app.state import LineId

# Lines the single-line checks read.
BS_TOTAL_ASSETS = "bs_total_assets"
BS_TOTAL_LIABILITIES = "bs_total_liabilities"
BS_TOTAL_EQUITY = "bs_total_equity"
BS_TOTAL_LIABILITIES_AND_EQUITY = "bs_total_liabilities_and_equity"
BS_CASH = "bs_cash"
BS_RETAINED_EARNINGS = "bs_retained_earnings"
IS_NET_INCOME = "is_net_income"
CF_NET_INCOME = "cf_net_income"
CF_STARTING_PROFIT = "cf_starting_profit"
CF_NET_CHANGE_IN_CASH = "cf_net_change_in_cash"
CF_DIVIDENDS_PAID = "cf_dividends_paid"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SumRule(_Model):
    """`total_id` should equal the sum of `component_ids` (signed, as printed)."""

    total_id: LineId
    component_ids: tuple[LineId, ...] = Field(min_length=1)


class ReconRules(_Model):
    # Each printed subtotal against the lines it is built from, subtotals included
    # (gross profit from revenue and cost of sales, net income from profit before tax).
    subtotals: tuple[SumRule, ...]
    # Each section total against its detail lines only, so a lost or doubled detail
    # line shows even when the printed subtotals chain correctly.
    sections: tuple[SumRule, ...]
    # Optional stated movements in retained earnings besides net income and dividends.
    re_other_movement_ids: tuple[LineId, ...] = ()
    # The sum of every movement in the retained earnings column of the equity statement (net
    # income and dividends included). When a period has it, it replaces net income, dividends
    # paid and the other stated movements in the rollforward.
    re_column_id: LineId | None = None


_BS_ASSET_DETAIL = (
    "bs_cash",
    "bs_receivables",
    "bs_inventory",
    "bs_other_current_assets",
    "bs_ppe",
    "bs_intangibles",
    "bs_other_noncurrent_assets",
)
_BS_LIABILITY_DETAIL = ("bs_payables", "bs_borrowings", "bs_other_liabilities")
_BS_EQUITY_DETAIL = ("bs_share_capital", "bs_retained_earnings", "bs_other_equity")
_CF_OPERATING_DETAIL = (
    "cf_net_income",
    "cf_starting_profit",
    "cf_depreciation_amortisation",
    "cf_change_receivables",
    "cf_change_inventory",
    "cf_change_payables",
    "cf_other_operating",
)
_CF_INVESTING_DETAIL = ("cf_capex", "cf_other_investing")
_CF_FINANCING_DETAIL = ("cf_debt_proceeds_repayments", "cf_dividends_paid", "cf_other_financing")

DEFAULT_RULES = ReconRules(
    subtotals=(
        SumRule(total_id="is_gross_profit", component_ids=("is_revenue", "is_cost_of_sales")),
        SumRule(
            total_id="is_operating_profit",
            component_ids=("is_gross_profit", "is_operating_expenses", "is_operating_income_items"),
        ),
        SumRule(
            total_id="is_profit_before_tax",
            component_ids=("is_operating_profit", "is_interest_expense", "is_non_operating_items"),
        ),
        SumRule(total_id="is_net_income", component_ids=("is_profit_before_tax", "is_income_tax")),
        SumRule(
            total_id="bs_total_current_assets",
            component_ids=("bs_cash", "bs_receivables", "bs_inventory", "bs_other_current_assets"),
        ),
        SumRule(
            total_id=BS_TOTAL_ASSETS,
            component_ids=(
                "bs_total_current_assets",
                "bs_ppe",
                "bs_intangibles",
                "bs_other_noncurrent_assets",
            ),
        ),
        SumRule(total_id=BS_TOTAL_LIABILITIES, component_ids=_BS_LIABILITY_DETAIL),
        SumRule(total_id=BS_TOTAL_EQUITY, component_ids=_BS_EQUITY_DETAIL),
        SumRule(
            total_id=BS_TOTAL_LIABILITIES_AND_EQUITY,
            component_ids=(BS_TOTAL_LIABILITIES, BS_TOTAL_EQUITY),
        ),
        SumRule(
            total_id=CF_NET_CHANGE_IN_CASH,
            component_ids=(
                "cf_net_cash_operating",
                "cf_net_cash_investing",
                "cf_net_cash_financing",
                "cf_fx_effect",
            ),
        ),
    ),
    sections=(
        SumRule(
            total_id=IS_NET_INCOME,
            component_ids=(
                "is_revenue",
                "is_cost_of_sales",
                "is_operating_expenses",
                "is_operating_income_items",
                "is_interest_expense",
                "is_income_tax",
                "is_non_operating_items",
                "is_other_items",
            ),
        ),
        SumRule(total_id=BS_TOTAL_ASSETS, component_ids=_BS_ASSET_DETAIL),
        SumRule(total_id=BS_TOTAL_LIABILITIES, component_ids=_BS_LIABILITY_DETAIL),
        SumRule(total_id=BS_TOTAL_EQUITY, component_ids=_BS_EQUITY_DETAIL),
        SumRule(total_id="cf_net_cash_operating", component_ids=_CF_OPERATING_DETAIL),
        SumRule(total_id="cf_net_cash_investing", component_ids=_CF_INVESTING_DETAIL),
        SumRule(total_id="cf_net_cash_financing", component_ids=_CF_FINANCING_DETAIL),
    ),
    re_other_movement_ids=("bs_re_other_movements",),
    re_column_id="bs_re_column_movements",
)
