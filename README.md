# tiny-agent

A tiny agent that works the inbox of one factory order. It shows how four parts split the work:

- **Jev** ([TypeSafe](https://docs.typesafe.ai)) decides what each email is, with a probability, in under a second.
- **Code** does the exact tasks: read a price, compare it to a target, compute days late, archive.
- **Claude** writes when writing is needed: a summary and a reply draft.
- **A human** approves anything that would leave the building. Nothing is sent automatically.

```
email ─► JEV: quote / delay / question / other   (with a probability)
                │
   confident, and a rule fits?        unsure, or a reply is needed?
                ▼                                   ▼
              CODE                               CLAUDE
   read price, days late, archive        summary + draft reply
                └──────────► approval queue ◄──────┘
```

## Example run

| Email | Jev decides | Who acts | Result |
|---|---|---|---|
| Quotation | quote, p 1.00, 0.6 s | code | USD 4.35 is under the 4.40 target → "accept?" waits for approval |
| Shipping update | delay, p 1.00, 0.2 s | code | ships 14 days late → "tell the customer?" waits |
| Color question | question, p 1.00, 0.3 s | Claude | drafts a reply (21 s) |
| Two offers | question, p 0.87, 0.4 s | Claude | summarizes both offers, drafts a reply (46 s) |
| Holiday notice | other, p 0.97, 0.6 s | code | archived |

Full output: [`last-run.txt`](last-run.txt).

## Run it

```sh
uv venv && uv pip install -r requirements.txt
export TYPESAFE_API_KEY=...   # from https://console.typesafe.ai
.venv/bin/python agent.py
```

You also need the [Claude Code](https://code.claude.com) CLI (`claude`), logged in.

## Files

- [`agent.py`](agent.py): the whole loop, about 200 lines.
- [`inbox/`](inbox): five simulated factory emails.
- [`order.json`](order.json): the order's starting state.

The order, the factory and the emails are invented. The routing thresholds (`ACT_ALONE = 0.80`, `FLOOR = 0.50`) are starting points, not tuned on real data.
