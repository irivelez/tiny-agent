#!/usr/bin/env python3
"""tiny-agent: one agent loop where Jev decides, code acts and Claude handles open-ended work.

Who uses it: Irina, to see how an LLM, TypeSafe's Jev and plain code split the work in one agent.
Question it answers: what does each part of an agent do, step by step, on a realistic task?
Delete by: 2026-11-02.
Data: SIMULATED factory emails about an invented order (inbox/). Nothing is sent anywhere;
every outward action waits in an approval queue.

The loop has the same shape as any agent:
    state = load()                     # memory: the agent's notebook (order.json)
    for email in inbox:                # each new event
        j = jev_decide(email, state)   # DECIDE: a typed answer with probabilities
        who = policy(j)                # thresholds pick who acts
        result = act(who, email)       # ACT: a code task, or Claude for open-ended work
        state.update(result)           # REMEMBER
        say(...)                       # OBSERVE: every step is printed and saved
"""
import asyncio
import json
import re
import ssl
import subprocess
import time
from datetime import date
from pathlib import Path

import httpx2
from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul

HERE = Path(__file__).parent
RUN = HERE / "run"

# Starting thresholds, not yet tested on real data. TypeSafe's docs: tune them on your own cases.
ACT_ALONE = 0.80  # confidence Jev needs before code acts without help
FLOOR = 0.50      # below this, treat the decision as unknown

# DECIDE: two questions, answered together in one Jev call.
QUESTIONS = {
    "kind": Choice(
        instructions="What does this email from the factory mainly do for our order `order.id`?",
        criteria={
            "quote": "States a unit price or price terms for our order",
            "delay": "Reports a later production or ship date for our order",
            "question": "Asks us to decide or confirm something before they can continue",
            "other": "Greetings, office closures, ads, or anything not about our order",
        },
    ),
    "needs_reply": Noul(
        instructions="Does the factory need an answer from us before they can continue with order `order.id`?"
    ),
}


def load_emails():
    emails = []
    for p in sorted((HERE / "inbox").glob("*.txt")):
        head, body = p.read_text().split("\n\n", 1)
        h = dict(line.split(": ", 1) for line in head.splitlines())
        emails.append({"file": p.name, "from": h["From"], "subject": h["Subject"], "body": body.strip()})
    return emails


async def jev_decide(client, email, state):
    t0 = time.perf_counter()
    r = await client.system_one(
        state={
            "email": {"from": email["from"], "subject": email["subject"], "body": email["body"]},
            "order": {"id": state["order"], "item": state["item"], "quantity": state["qty"],
                      "promised_ship_date": state["promised_ship"]},
        },
        questions=QUESTIONS,
    )
    k = r.choices["kind"]
    return {"kind": k.choice, "p": dict(k.probabilities), "conf": k.confidence,
            "needs_reply": r.nouls["needs_reply"].noul, "ms": int((time.perf_counter() - t0) * 1000)}


def policy(j):
    """Code owns the rules: who acts on this email?"""
    if j["conf"] < FLOOR:
        return "claude", f"Jev is unsure (confidence {j['conf']:.2f})"
    if j["kind"] == "question":
        return "claude", "an answer has to be written"
    if j["conf"] < ACT_ALONE:
        return "claude", f"not confident enough to act alone ({j['conf']:.2f})"
    return "code", f"confident ({j['conf']:.2f}), a fixed rule can handle it"


# ACT, code side: exact tasks with known rules.
PRICE = re.compile(r"USD\s*([0-9]+(?:\.[0-9]{1,2})?)", re.I)
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DATE = re.compile(r"\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{4})\b", re.I)


def dates_in(text):
    return [date(int(y), MONTHS[m.lower()[:3]], int(d)) for d, m, y in DATE.findall(text)]


def code_quote(email, state):
    prices = [float(x) for x in PRICE.findall(email["body"])]
    if len(prices) != 1:
        return None  # more than one price: code cannot be sure which one applies
    price = prices[0]
    state["price_usd"], state["step"] = price, "quote received"
    if price <= state["target_price_usd"]:
        return {"did": f"read unit price USD {price:.2f}, at or under the USD {state['target_price_usd']:.2f} target",
                "propose": f"Accept the quote: USD {price:.2f} x {state['qty']:,} pcs"}
    return {"did": f"read unit price USD {price:.2f}, above the USD {state['target_price_usd']:.2f} target",
            "propose": f"Counter-offer at USD {state['target_price_usd']:.2f}"}


def code_delay(email, state):
    found = dates_in(email["body"])
    if not found:
        return None
    new, promised = max(found), date.fromisoformat(state["promised_ship"])
    late = (new - promised).days
    state["ship_date"], state["step"] = new.isoformat(), "delayed"
    out = {"did": f"new ship date {new.isoformat()}: {late} days after the promised {promised.isoformat()}"}
    if late > 7:
        out["propose"] = f"Tell the customer: order {state['order']} now ships {new.isoformat()} ({late} days late)"
    return out


def code_other(email, state):
    return {"did": "archived: not about the order"}


CODE_TASKS = {"quote": code_quote, "delay": code_delay, "other": code_other}


# ACT, LLM side: open-ended work goes to Claude through the headless CLI.
def claude_task(email, state):
    facts = {k: state[k] for k in ("step", "price_usd", "ship_date", "target_price_usd", "promised_ship")}
    prompt = (
        f"You help a buyer manage factory order {state['order']} ({state['qty']:,} pcs {state['item']}). "
        f"Current order state: {json.dumps(facts)}.\n\n"
        f"Factory email:\nSubject: {email['subject']}\n{email['body']}\n\n"
        "Reply in plain English, in exactly this format:\n"
        "SUMMARY: one line on what the factory is asking or offering\n"
        "REPLY: a reply to the factory of at most 3 sentences. Do not accept or reject anything final; "
        "say the buyer will confirm, and ask for anything missing."
    )
    t0 = time.perf_counter()
    out = subprocess.run(
        ["claude", "-p", prompt, "--model", "sonnet", "--output-format", "json", "--tools", "",
         "--no-session-persistence", "--setting-sources", "project,local", "--strict-mcp-config"],
        capture_output=True, text=True, timeout=240, cwd=HERE)
    data = json.loads(out.stdout)
    text = data.get("result", "")
    summary = re.search(r"SUMMARY:\s*(.+)", text)
    reply = re.search(r"REPLY:\s*(.+)", text, re.S)
    state["step"] = "reply drafted, waiting for approval"
    return {"did": summary.group(1).strip() if summary else text[:200],
            "propose": "Send the factory this reply: \"" + (reply.group(1).strip() if reply else text.strip()) + "\"",
            "s": round(time.perf_counter() - t0, 1), "cost_usd": data.get("total_cost_usd")}


async def main():
    state = json.loads((HERE / "order.json").read_text())
    lines = []

    def say(s=""):
        print(s, flush=True)
        lines.append(s)

    say(f"ORDER {state['order']}: {state['qty']:,} pcs {state['item']} | target USD {state['target_price_usd']:.2f}"
        f" | promised ship {state['promised_ship']} | thresholds: act alone >= {ACT_ALONE}, unsure < {FLOOR}")
    cafile = Path("/etc/ssl/cert.pem")  # macOS sandboxes need an explicit CA file; elsewhere use the default
    ctx = ssl.create_default_context(cafile=str(cafile)) if cafile.exists() else ssl.create_default_context()
    async with httpx2.AsyncClient(verify=ctx, timeout=60) as hc:
        async with AsyncTypeSafeClient(http_client=hc) as client:
            for i, email in enumerate(load_emails(), 1):
                say()
                say(f"[{i}] \"{email['subject']}\"")
                try:
                    j = await jev_decide(client, email, state)
                    ranked = sorted(j["p"].items(), key=lambda kv: -kv[1])
                    say(f"    JEV     {j['kind']} (p {ranked[0][1]:.2f}; next: {ranked[1][0]} {ranked[1][1]:.2f})"
                        f" | confidence {j['conf']:.2f} | needs a reply: p {j['needs_reply']:.2f} | {j['ms']} ms")
                    who, why = policy(j)
                except Exception as e:  # a service failure must not stop the agent
                    j, who, why = None, "claude", f"Jev unavailable ({type(e).__name__})"
                    say(f"    JEV     failed: {e}")
                say(f"    ROUTE   {who.upper()}: {why}")
                result = None
                if who == "code":
                    result = CODE_TASKS[j["kind"]](email, state)
                    if result is None:
                        say("    CODE    could not read the values with certainty, handing to Claude")
                        who = "claude"
                    else:
                        say(f"    CODE    {result['did']}")
                if who == "claude":
                    result = claude_task(email, state)
                    cost = f", ${result['cost_usd']:.3f}" if result.get("cost_usd") else ""
                    say(f"    CLAUDE  {result['did']} ({result['s']} s{cost})")
                if result.get("propose"):
                    state["approvals"].append(result["propose"])
                    say(f"    WAIT    needs your approval: {result['propose']}")
                state["log"].append({"email": email["file"], "jev": j, "route": who, "why": why, "result": result})
                say(f"    STATE   step: {state['step']} | price: {state['price_usd']} | ship date: {state['ship_date']}")

    say()
    say("APPROVAL QUEUE (nothing was sent):")
    for n, a in enumerate(state["approvals"], 1):
        say(f"  {n}. {a}")
    RUN.mkdir(exist_ok=True)
    (RUN / "state.json").write_text(json.dumps(state, indent=2, default=str))
    (HERE / "last-run.txt").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
