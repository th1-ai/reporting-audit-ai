# Measuring the benefit

## The business case, from the roster

**Reporting & Audit AI ("The Auditor").** Owners get a true weekly pulse
without anyone building a deck, and revenue leakage gets caught while it's
still fixable. ROI: **-95% weekly reporting hours** (labor).

**F&B Sales Audit AI ("The Pit Boss").** Nobody can manually review voids
and discounts across a portfolio of restaurants - which is exactly why
margin quietly leaks there. ROI: **+3% F&B margin recovered** (revenue).

## What actually produces the -95%

Before: someone - usually a general manager or a controller - pulls
occupancy, revenue and cost figures from three or four places, builds a
deck or a spreadsheet by hand, writes the commentary, and sends it. Call it
2-4 hours a week for a single property, more for a portfolio.

After: `python3 tools/run.py --once --report` runs in seconds. What is left
for a human is reading the draft and pressing approve, or editing it - a few
minutes, not hours. The 95% figure is a claim about the hours removed from
building the report, not a claim that reconciliation or oversight time goes
away too - a controller still needs to work the reconciliation queue.

**Track it with `make report`**, which reads `fin_runs` (one row per job)
and the review queue directly - no re-computation, just what happened:

```bash
make report
```

Shows, per week: the report's headline figures, the reconciliation
headline, the human edit rate on drafts (`edited / (edited+sent)`), and the
LLM spend (which should be near zero - the only call is the cosmetic note).

## What actually produces the +3% (once the Pit Boss is on)

The claim is about margin *recovered* - flagged leakage that gets acted on,
not flagged leakage on its own. This template is honest that it does not
close that loop for you: `tools/fnb_audit.py` computes flags and
`tools/report.py` counts them, but nothing here prices "recovered" against
an outcome (a server retrained, a policy tightened, a pattern that stopped
recurring). See "Design decisions" #9-equivalent in the spec this was built
from - the ROI claim only becomes measurable once a hotel adds a resolution
step. A reasonable first step: track, outside this repo, whether a flagged
shift's void rate comes back down the following week.

## What to measure, concretely

| Metric | Where | What it tells you |
|---|---|---|
| Reports drafted vs. sent | `make report` | Is the report actually reaching owners, or stuck in the queue? |
| Human edit rate | `make report` | Falling over time means the drafts are getting closer to what the hotel actually wants to say. |
| Reconciliation: clean+explained vs. attention | the reconciliation headline every run | A rising "clean" share means fewer real exceptions, or the rules genuinely explaining more (check which). |
| Amount in question (EUR) | the reconciliation headline | The euros actually at risk each week - the number to report upward. |
| F&B: flags per week, void value | `make report` (once the sub-agent is on) | Whether the leakage pattern this agent exists to catch is shrinking or just moving shift to shift. |

## Honest caveats

- The owner report's ADR and RevPAR are means of daily values, not revenue
  divided by rooms sold - see `docs/how-it-works.md` "Design decisions" #3.
  If your finance team tracks the second definition, the two will not match
  exactly on an uneven week.
- The reconciliation window is 7 days, not the roster's "120 days" framing -
  see "Design decisions" #1. Widening `recon.window_days` does not turn it
  into the 120-day check the roster describes.
- The F&B margin-recovery ROI has no counter in this template - see above.
  Do not report "+3%" as an achieved number without your own tracking behind
  it.
- Web traffic only appears in the owner report if `data/imports/web_traffic.csv`
  is connected; the roster promise is otherwise honestly flagged as not yet
  wired, not silently dropped.
