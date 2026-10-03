import os as _os
_OUT = _os.environ.get("PROBE_OUT", "context_claude/development/20261003-research-review"); _os.makedirs(_OUT, exist_ok=True)
import pandas as pd
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 60); pd.set_option("display.max_rows", 400); pd.set_option("display.max_colwidth", 80)
r = pd.read_csv("reports_claude/scan/_ledger/views/stage_rulers.csv")
print(r.groupby(["stage","metric","status"]).size().to_string())
al = r[r.session.astype(str).str.upper().isin(["ALL","all"])]
print("\n=== ALL rows"); print(al.to_string())
print("\n=== sessions:", sorted(r.session.astype(str).unique())[-25:])
# per-metric summary across sessions for MATURE daily rows
d = r[(r.status=="MATURE") & ~r.session.astype(str).str.upper().isin(["ALL"])]
s = d.groupby(["stage","metric"]).agg(n=("value","size"), mean=("value","mean"), med=("value","median"), pos=("value", lambda x:(x>0).mean()), sd=("value","std")).round(5)
s["t"]=(s["mean"]/(s["sd"]/s["n"]**0.5)).round(2)
print("\n=== daily MATURE rows summary"); print(s.to_string())
