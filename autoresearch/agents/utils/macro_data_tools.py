from typing import Annotated

from langchain_core.tools import tool

from autoresearch.dataflows.interface import route_to_vendor


@tool
def get_macro_indicators(
    indicator: Annotated[
        str,
        "Macro indicator: a friendly alias such as 'cpi', 'core_pce', "
        "'unemployment', 'fed_funds_rate', '10y_treasury', 'yield_curve', "
        "'real_gdp', 'vix', or a raw FRED series ID such as 'CPIAUCSL'.",
    ],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format; the end of the window"],
    look_back_days: Annotated[
        int | None, "Trailing window length in days; omit for a 1-year window"
    ] = None,
    *,
    vintage_date: Annotated[str | None, "FRED information vintage YYYY-MM-DD; defaults to curr_date"] = None,
    knowledge_cutoff: Annotated[str | None, "Research cutoff date or timezone-aware ISO timestamp"] = None,
) -> str:
    """
    Retrieve a macroeconomic indicator time series from FRED (Federal Reserve
    Economic Data): policy rates, Treasury yields, inflation, labor, and growth.
    Returns the series title, units, frequency, the latest value, the change
    over the window, and a recent observation table. Uses the configured
    macro_data vendor.

    Args:
        indicator (str): Friendly alias or raw FRED series ID
        curr_date (str): Current date in yyyy-mm-dd format
        look_back_days (int): Trailing window length; omit for a 1-year window
        vintage_date (str): Historical FRED information date; defaults to curr_date
        knowledge_cutoff (str): Date or timezone-aware ISO research cutoff

    Returns:
        str: A formatted markdown report of the macro series
    """
    return route_to_vendor(
        "get_macro_indicators", indicator, curr_date, look_back_days,
        vintage_date=vintage_date, knowledge_cutoff=knowledge_cutoff,
    )
