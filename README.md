# tiny-agent

**Who uses it:** Irina, to see how an LLM, TypeSafe's Jev and plain code split the work in one agent.
**Question it answers:** what does each part of an agent do, step by step, on a realistic task?
**Delete by:** 2026-11-02.
**Data:** simulated. The five factory emails in `inbox/` and order 1042 are invented. Nothing is sent anywhere.

## What it does

One agent works an order's inbox. For each email:

1. **Jev decides** what the email is (quote, delay, question, other) and returns a probability.
2. **Code decides who acts** from that probability (`policy()` in `agent.py`):
   - Confident, and a fixed rule fits → **code** does the exact task (read the price, compare it to the target, compute days late, archive).
   - Unsure, or a reply has to be written → **Claude** (`claude -p`) summarizes the email and drafts a reply.
   - Code that can't read a value with certainty hands the email to Claude.
3. Anything that leaves the building (accept a price, reply, tell the customer) waits in an **approval queue**.
4. The order's state (step, price, ship date) is updated after each email and saved to `run/state.json`.

Thresholds (`ACT_ALONE = 0.80`, `FLOOR = 0.50`) are starting points, not tested on real data.

## Run

```sh
uv venv -q && uv pip install -q typesafe-sdk==0.7.1 httpx2==2.13.1
.venv/bin/python agent.py
```

Needs `TYPESAFE_API_KEY` (in `~/.zshenv`) and a logged-in `claude` CLI. The last run's output is in `last-run.txt`.
