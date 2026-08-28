# FAQ (fixture data)

Sample only, for `make demo` and the tests. A real clone fills in
`knowledge/faq.example.md` -> `knowledge/faq.md` with its own answers.

**Q: Why did the owner report change definitions for ADR and RevPAR?**
A: They are not recomputed here. Both are the arithmetic mean of the daily
values already in `fin_daily`, matching how the property's own finance team
already tracks them. See `docs/how-it-works.md` "Design decisions".

**Q: Why does the reconciliation only cover 7 days, not 120?**
A: The three-way match (bank, Stripe, PMS folio) needs all three sources
lined up; widening the window alone does not turn it into a two-way check
against a finance sheet. See `docs/how-it-works.md` "Design decisions" #1.

**Q: Can the agent post the FX difference to the ledger by itself?**
A: No, not in this template, even when the in-tolerance rule is on. A human
always presses `post_fx`. See `docs/how-it-works.md` "Design decisions" #4.
