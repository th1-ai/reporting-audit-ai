# Property facts - Hotel Aurora (fixture data)

This is sample data for `make demo` and the tests. It is a shorter copy of the
same invented property described in `knowledge/property.example.md`; a real
clone of this repo fills in `knowledge/property.md` with its own facts.

- Name: Hotel Aurora
- Address: 1 Example Street, 1000-001 Lisbon, Portugal
- Phone: +351 200 000 000
- Email: reservations@example.com
- Website: https://example.com
- 42 rooms, one restaurant (Salt Restaurant), reception staffed 07:00 to 23:00
- Currency: EUR. Owner report and reconciliation are both in EUR.

## Who reads this agent's output

- The owners: the Monday one-page report (revenue, GOP, occupancy, ADR, RevPAR).
- The finance team: the weekly reconciliation queue and its action buttons.
- Salt Restaurant's outlet manager: the daily till exceptions digest, when the
  F&B Sales Audit sub-agent is enabled.

## What this agent does not touch

- It never edits the finance ledger or the PMS itself. The five reconciliation
  actions are buttons a human presses; three of the seven action keys log only.
- It never sets a rate, never books a room, never contacts a guest.
