# knowledge/

This folder is context, not data. The report and the reconciliation are pure
arithmetic over `fin_daily`, the bank statement, Stripe and the PMS - nothing
here changes a single number. What lives here shapes the words around those
numbers: who reads them, what they already know, and how the email signs off.

## What to put here

| File | What it holds |
|---|---|
| `property.md` | Who reads this agent's output and what it will not do - useful context for anyone (including your Claude session) working on it. |
| `faq.md` | Questions you or your owners are likely to ask about how a number was produced. |
| `signature.md` | The sign-off on the owner report and the F&B digest email. Plain text. |

Copy the `.example.md` files, rename them without `.example`, and fill them in:

```bash
cp knowledge/property.example.md knowledge/property.md
cp knowledge/faq.example.md      knowledge/faq.md
cp knowledge/signature.example.md knowledge/signature.md
```

`knowledge/*.md` is gitignored (the `.example.md` files are not), because your
property notes are yours.

## How to write it

**Short sentences, concrete facts, no marketing language.** Nobody but your own
team reads this - the report's numbers come from `fin_daily`, not from here.

**Say what this agent does NOT do.** "It never edits the ledger or the PMS,
only flags" is worth repeating in your own words for anyone new who opens this
repo, including your own Claude session six months from now.

**Keep the signature current.** It is the only free text your owners and
outlet managers actually see outside the numbers themselves.

## Keeping it current

When a policy changes - a new GL account, a different recipient, a renamed
outlet - change it here first, and check whether `config/agent.yaml` needs the
matching update (recipients, outlets, GL codes).
