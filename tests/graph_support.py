"""Shared setup for the graph tests: the anonymised base filing, settings, deps and a runner.

The base filing is the spec's eval-note filing: balances, 800 + 500 = 1,300, cash 500 - 300 + 100
= 300 = 900 - 600. Ids and the clock are fixed, so nothing here depends on a random value.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

from app.config import Settings
from app.graph import Deps, NodeFn, build_graph, resume_run, start_run
from app.ingest import IngestResult, ingest_text
from app.mapping import load_mapping_table
from app.state import RunSummary
from app.tools.model_client import ModelClient
from tests.scripted_model import ScriptedModel

CLOCK = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)

IS_PAGE = """--- page 3 --- CONSOLIDATED INCOME STATEMENT (USD thousands)      FY2023   FY2022
Revenue                         5,000    4,500
Cost of sales                  (3,000)  (2,800)
Gross profit                    2,000    1,700
Operating expenses             (1,200)  (1,100)
Operating profit                  800      600
Interest expense                 (100)    (100)
Profit before tax                 700      500
Income tax                       (200)    (150)
Net income                        500      350
"""
BS_PAGE = """--- page 5 --- CONSOLIDATED BALANCE SHEET (USD thousands)     FY2023   FY2022
Cash and cash equivalents         900      600
Trade receivables                 600      500
Inventory                         500      400
Total current assets            2,000    1,500
Property, plant and equipment   1,500    1,300
Total assets                    3,500    2,800
Trade payables                    700      600
Borrowings                      1,000      900
Total liabilities               1,700    1,500
Share capital                     500      500
Retained earnings               1,300      800
Total equity                    1,800    1,300
Total liabilities and equity    3,500    2,800
"""
CF_PAGE = """--- page 7 --- CONSOLIDATED CASH FLOW STATEMENT (USD thousands)   FY2023
Net income                        500
Depreciation and amortisation     100
Increase in receivables          (100)
Increase in inventory            (100)
Increase in payables              100
Net cash from operating activities 500
Purchase of property, plant and equipment (300)
Net cash from investing activities (300)
Net cash from financing activities 100
Net change in cash                300
"""
BASE_FILING = IS_PAGE + BS_PAGE + CF_PAGE
# The balance sheet no longer balances by 50: equity is short in the printed totals.
UNBALANCED_FILING = (
    IS_PAGE
    + BS_PAGE.replace(
        "Retained earnings               1,300", "Retained earnings               1,250"
    )
    .replace("Total equity                    1,800", "Total equity                    1,750")
    .replace("Total liabilities and equity    3,500", "Total liabilities and equity    3,450")
    + CF_PAGE
)
REQUEST = "Spread the filing below into the standard IS/BS/CF workbook."


def settings_for(tmp_path: Path, **overrides: str) -> Settings:
    env = {
        "COGNITIA_OPENROUTER_API_KEY": "test-no-key",
        "SPREAD_CHECKPOINT_DB": str(tmp_path / "checkpoints.sqlite"),
        "SPREAD_OUTPUT_DIR": str(tmp_path / "out"),
        "SPREAD_MAX_TOKENS_PER_RUN": "200000",
        "SPREAD_MODEL": "test/scripted",
        **overrides,
    }
    return Settings.from_env(env)


def deps_for(
    tmp_path: Path,
    filing: str | None = BASE_FILING,
    *,
    client: ModelClient | None = None,
    load: Callable[[], IngestResult] | None = None,
    request: str = REQUEST,
    route_factory: Any = None,
    **settings: str,
) -> Deps:
    return Deps(
        settings=settings_for(tmp_path, **settings),
        client=client or ScriptedModel(),
        table=load_mapping_table(),
        request_text=request,
        load=load or (lambda: ingest_text(filing or "")),
        now=lambda: CLOCK,
        route_factory=route_factory,
    )


def run_graph(
    deps: Deps, run_id: str = "run-test", *, wrap: Callable[[str, NodeFn], NodeFn] | None = None
) -> tuple[dict[str, Any], list[str]]:
    deps.settings.checkpoint_db.parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(deps.settings.checkpoint_db)) as saver:
        return start_run(build_graph(deps, saver, wrap=wrap), run_id, "company-a")


def resume_graph(
    deps: Deps, run_id: str = "run-test", *, wrap: Callable[[str, NodeFn], NodeFn] | None = None
) -> tuple[dict[str, Any], list[str]]:
    with SqliteSaver.from_conn_string(str(deps.settings.checkpoint_db)) as saver:
        return resume_run(build_graph(deps, saver, wrap=wrap), run_id)


def summary_of(state: dict[str, Any]) -> RunSummary:
    from app.graph import build_summary

    return build_summary(state)
