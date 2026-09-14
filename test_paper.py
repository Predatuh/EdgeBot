#!/usr/bin/env python3
"""Offline checks on the paper ledger: pricing, marking, grading, calibration.

No network. Every board here is hand-built so the arithmetic is checkable by
hand, which is the only way to be sure the money is right.
"""
import json
import os
import tempfile

import paper

FAIL = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        FAIL.append(name)


def leg(ticker, ask, bid=None, p=None, event=None):
    return {"ticker": ticker, "ask": ask, "bid": bid if bid is not None else ask - 0.01,
            "p": p if p is not None else ask - 0.005, "league": "ncaaf",
            "event_id": event or ("E-" + ticker), "pick": ticker, "opp": "them",
            "date": "2026-09-20"}


print("entry pricing")
t = paper.make_ticket([leg("A", 0.50), leg("B", 0.50)], stake=10.0)
check("cost is the product of the asks", abs(t["entry"]["cost"] - 0.25) < 1e-9,
      str(t["entry"]["cost"]))
check("payout is one over the cost", abs(t["entry"]["multiple"] - 4.0) < 1e-6)
check("win probability is the product of the true prices",
      abs(t["entry"]["win_prob"] - 0.495 * 0.495) < 1e-6, str(t["entry"]["win_prob"]))
check("entry prices are written down, not referenced",
      t["legs"][0]["entry_ask"] == 0.50 and t["legs"][0]["entry_bid"] == 0.49)
check("a fresh ticket is open and unresolved",
      t["status"] == "open" and all(l["result"] is None for l in t["legs"]))

print("\nthe ledger refuses duplicates")
led = {"version": 1, "tickets": []}
check("first one lands", paper.add(led, paper.make_ticket([leg("A", 0.5)])) is not None)
check("the same ticket again does not",
      paper.add(led, paper.make_ticket([leg("A", 0.5)])) is None)
check("...and the ledger still holds one", len(led["tickets"]) == 1)
check("the same legs for a different owner is a different ticket",
      paper.add(led, paper.make_ticket([leg("A", 0.5)], owner="you")) is not None)
check("leg order does not make a new ticket",
      paper.add(led, paper.make_ticket([leg("B", 0.5), leg("A", 0.5)])) is not None
      and paper.add(led, paper.make_ticket([leg("A", 0.5), leg("B", 0.5)])) is None)

print("\nmarking to market")
led = {"version": 1, "tickets": []}
paper.add(led, paper.make_ticket([leg("A", 0.50, bid=0.49), leg("B", 0.50, bid=0.49)],
                                 stake=10.0))
paper.mark_to_market(led, {"A": {"bid": 0.49}, "B": {"bid": 0.49}})
m = led["tickets"][0]["mark"]
check("a ticket is under water the moment it is bought", m["pnl"] < 0, str(m["pnl"]))
check("...by exactly the spread it crossed",
      abs(m["value"] - 10.0 * (0.49 * 0.49) / 0.25) < 0.01, str(m["value"]))
paper.mark_to_market(led, {"A": {"bid": 0.80}, "B": {"bid": 0.80}})
m = led["tickets"][0]["mark"]
check("a ticket that moved your way is worth more",
      abs(m["value"] - 10.0 * 0.64 / 0.25) < 0.01, str(m["value"]))
check("...and shows the gain", m["pnl"] > 15, str(m["pnl"]))
paper.mark_to_market(led, {"A": {"bid": 0.90}})
check("a leg that has left the board holds its last mark",
      led["tickets"][0]["legs"][1]["mark_bid"] == 0.80)

print("\nselling")
ok = paper.cash_out(led["tickets"][0])
check("cashing out closes at the mark", ok and led["tickets"][0]["status"] == "cashed")
check("...and books that as the return",
      led["tickets"][0]["returned"] == led["tickets"][0]["exit"]["value"])
check("...and the p&l is return minus stake",
      abs(led["tickets"][0]["pnl"]
          - (led["tickets"][0]["returned"] - led["tickets"][0]["stake"])) < 0.011)
check("a closed ticket cannot be cashed twice", paper.cash_out(led["tickets"][0]) is False)
fresh = paper.make_ticket([leg("Z", 0.5)])
check("a ticket that was never marked cannot be sold", paper.cash_out(fresh) is False)

print("\ngrading")
led = {"version": 1, "tickets": []}
paper.add(led, paper.make_ticket([leg("A", 0.50), leg("B", 0.50)], stake=10.0))
paper.grade(led, {"A": "win"})
check("one winning leg does not settle the ticket", led["tickets"][0]["status"] == "open")
paper.grade(led, {"B": "win"})
t = led["tickets"][0]
check("every leg in wins the ticket", t["status"] == "won")
check("the payout comes off the entry price, not a later one",
      abs(t["returned"] - 40.0) < 0.01, str(t["returned"]))
check("profit is return minus stake", abs(t["pnl"] - 30.0) < 0.01)

led = {"version": 1, "tickets": []}
paper.add(led, paper.make_ticket([leg("A", 0.50), leg("B", 0.50)], stake=10.0))
paper.grade(led, {"A": "loss"})
check("one losing leg ends it immediately", led["tickets"][0]["status"] == "lost")
check("a lost ticket returns nothing", led["tickets"][0]["returned"] == 0.0)
check("...and loses exactly the stake", led["tickets"][0]["pnl"] == -10.0)

led = {"version": 1, "tickets": []}
paper.add(led, paper.make_ticket([leg("A", 0.50), leg("B", 0.25)], stake=10.0))
paper.grade(led, {"A": "win", "B": "void"})
t = led["tickets"][0]
check("a voided leg drops out rather than losing the ticket", t["status"] == "won")
check("...and the payout shrinks to the legs that stood",
      abs(t["returned"] - 20.0) < 0.01, str(t["returned"]))

led = {"version": 1, "tickets": []}
paper.add(led, paper.make_ticket([leg("A", 0.5)], stake=10.0))
paper.grade(led, {"A": "void"})
check("an all-void ticket gives the stake back",
      led["tickets"][0]["status"] == "void" and led["tickets"][0]["returned"] == 10.0)

print("\nsettlement comes from the market, not from a guess")
ev = {"event": "E1", "sides": [
    {"ticker": "H", "name": "Home", "won": True, "settled": True, "is_tie": False},
    {"ticker": "A", "name": "Away", "won": False, "settled": True, "is_tie": False}]}
r = paper.results_from_events([ev])
check("the winner wins and the loser loses", r == {"H": "win", "A": "loss"}, str(r))
void = {"event": "E2", "sides": [
    {"ticker": "X", "name": "X", "won": False, "settled": True, "is_tie": False},
    {"ticker": "Y", "name": "Y", "won": False, "settled": True, "is_tie": False}]}
check("a game where nobody won is void, not two losses",
      paper.results_from_events([void]) == {"X": "void", "Y": "void"})
half = {"event": "E3", "sides": [
    {"ticker": "P", "name": "P", "won": False, "settled": True, "is_tie": False},
    {"ticker": "Q", "name": "Q", "won": False, "settled": False, "is_tie": False}]}
check("a half-settled event is not graded at all",
      paper.results_from_events([half]) == {})

print("\nthe scoreboard")
led = {"version": 1, "tickets": []}
w = paper.make_ticket([leg("A", 0.5), leg("B", 0.5)], owner="bot", stake=10.0, key="swing")
l = paper.make_ticket([leg("C", 0.5), leg("D", 0.5)], owner="bot", stake=10.0, key="swing")
mine = paper.make_ticket([leg("E", 0.5)], owner="you", stake=10.0)
for x in (w, l, mine):
    led["tickets"].append(x)
paper.grade(led, {"A": "win", "B": "win", "C": "loss", "E": "win"})
s = paper.summary(led)
check("each owner is counted separately",
      s["by_owner"]["bot"]["settled"] == 2 and s["by_owner"]["you"]["settled"] == 1)
check("the bot's p&l is right",
      abs(s["by_owner"]["bot"]["pnl"] - 20.0) < 0.01, str(s["by_owner"]["bot"]["pnl"]))
check("roi is against what was staked",
      abs(s["by_owner"]["bot"]["roi"] - 100.0) < 0.1, str(s["by_owner"]["bot"]["roi"]))
check("expected wins are carried next to actual ones",
      abs(s["by_owner"]["bot"]["implied_wins"] - 2 * 0.495 ** 2) < 0.01)
check("rungs are only the bot's", set(s["by_rung"]) == {"swing"})
check("the recent list is newest first and capped",
      len(s["recent"]) == 3 and s["recent"][0]["status"] in ("won", "lost"))

print("\ncalibration")
led = {"version": 1, "tickets": []}
# 100 legs bought at 90c that won 80 times: that band is worse than its price
for i in range(100):
    t = paper.make_ticket([leg(f"L{i}", 0.90)], owner="bot")
    t["legs"][0]["result"] = "win" if i < 80 else "loss"
    led["tickets"].append(t)
cal = paper.calibration(led)
b = cal["bands"]["90c+"]
check("the hit rate is measured, not assumed", b["n"] == 100 and b["wins"] == 80)
check("drag is hit rate over what was paid", abs(b["drag"] - 0.80 / 0.90) < 1e-3, str(b["drag"]))
check("a band with enough legs is trusted", b["trusted"])
ld = paper.learned_discount(cal)
check("the learned discount replaces that band", "90c+" in ld, str(ld))
check("...floored so one bad run cannot shut a band off", ld["90c+"] >= 0.85)

led2 = {"version": 1, "tickets": []}
for i in range(100):
    t = paper.make_ticket([leg(f"W{i}", 0.50)], owner="bot")
    t["legs"][0]["result"] = "win" if i < 70 else "loss"       # 70% at 50c: beating it
    led2["tickets"].append(t)
cal2 = paper.calibration(led2)
check("a band that beat its price measures above 1", cal2["bands"]["40-60c"]["drag"] > 1.3)
check("...but is never allowed to inflate the builder's estimate",
      paper.learned_discount(cal2)["40-60c"] == 1.0)

thin = {"version": 1, "tickets": []}
for i in range(5):
    t = paper.make_ticket([leg(f"T{i}", 0.95)], owner="bot")
    t["legs"][0]["result"] = "loss"
    thin["tickets"].append(t)
check("five legs do not get to rewrite anything",
      paper.learned_discount(paper.calibration(thin)) == {})
check("...and the file says why", paper.calibration(thin)["bands"]["90c+"]["trusted"] is False)

print("\nopen legs never count as evidence")
led3 = {"version": 1, "tickets": []}
t = paper.make_ticket([leg("O", 0.9)], owner="bot")
led3["tickets"].append(t)
check("an ungraded leg is not in the calibration",
      paper.calibration(led3)["legs_settled"] == 0)

print("\nround trip through disk")
with tempfile.TemporaryDirectory() as d:
    p = os.path.join(d, "paper", "ledger.json")
    led = {"version": 1, "tickets": [paper.make_ticket([leg("A", 0.5)])]}
    paper.save_ledger(led, p)
    back = paper.load_ledger(p)
    check("a saved ledger reads back identical",
          back["tickets"][0]["id"] == led["tickets"][0]["id"])
    check("...with a timestamp", bool(back["updated_utc"]))
    check("a missing ledger is an empty one, not a crash",
          paper.load_ledger(os.path.join(d, "nope.json"))["tickets"] == [])
    with open(os.path.join(d, "junk.json"), "w") as f:
        f.write("{not json")
    check("a corrupt ledger does not take the run down",
          paper.load_ledger(os.path.join(d, "junk.json"))["tickets"] == [])
    sp = os.path.join(d, "settled.json")
    paper.settled_feed(led, {"A": "win"}, sp)
    paper.settled_feed(led, {"B": "loss"}, sp)
    with open(sp) as f:
        feed = json.load(f)
    check("the settled feed accumulates rather than replacing",
          feed["results"] == {"A": "win", "B": "loss"}, str(feed["results"]))
    # a ticket built on a phone and never sent here has legs this ledger has
    # never seen; if the feed only carried its own, that ticket never grades
    paper.settled_feed(led, {"STRANGER": "win"}, sp)
    with open(sp) as f:
        feed = json.load(f)
    check("...and carries results for legs no ticket here holds",
          feed["results"].get("STRANGER") == "win")
    big = {f"OLD{i}": "loss" for i in range(60)}
    paper.settled_feed(led, big, sp, keep=10)
    with open(sp) as f:
        feed = json.load(f)
    check("pruning keeps it bounded", len(feed["results"]) <= 11, str(len(feed["results"])))
    check("...and never drops a leg a ticket still refers to",
          feed["results"].get("A") == "win")

print("\nlogging a ticket of your own")
board = [leg("A", 0.60), leg("B", 0.70)]
t, missing = paper.slate_from_tickers(["A", "B", "GONE"], board, owner="you")
check("it prices off the live board", abs(t["entry"]["cost"] - 0.42) < 1e-6)
check("a ticker that is not trading is reported, not invented", missing == ["GONE"])
check("...and the rest of the ticket still stands", len(t["legs"]) == 2)
none, missing = paper.slate_from_tickers(["NOPE"], board)
check("a ticket with nothing tradeable in it is refused", none is None and missing == ["NOPE"])

print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: " + ", ".join(FAIL)))
raise SystemExit(1 if FAIL else 0)
