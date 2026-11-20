#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2.1", "matplotlib>=3.9"]
# ///
"""What it is worth for sparkjoy's classifier to tell apart the turn ends where a compaction loses
detail the next work needs from those where it doesn't.

    uv run --script model/predict.py                        # the last 14 days of ~/.claude/projects
    uv run --script model/predict.py --since 2026-11-07T12:00Z --until 2026-11-16T00:00Z

fit.py charges every compaction the same lost detail D, so its threshold treats every turn end
alike. Here a share of turn ends are mid-task, where a compaction loses D_hi, and the rest
finished, where it loses D_lo, each turn end's kind drawn independently. A classifier calls each
turn end finished or mid-task: rightly with probability a, independently of the others. With the
classifier, the policy compacts at a turn end it calls finished once the context reaches a floor, and
at one it calls mid-task once it reaches a ceiling. Without it, the policy is fit.py's threshold,
each compaction charged the average D. A replay of your sessions' requests and turn ends picks the
floor and ceiling for each a, and the threshold. Context size doesn't otherwise matter (β = 0), so
every arm is equally effective and cost per unit of effectiveness compares as cost. Neither arm
compacts idle sessions. Running the classifier costs a read of the context at each turn end it is
asked at, which is reported beside the saving rather than counted in it.
"""

import argparse
import multiprocessing
import os
from dataclasses import dataclass

import numpy as np

import fit
from report import CHECK_OUTPUT

MODEL = "claude-opus-5-5"


@dataclass(frozen=True)
class Mix:
    """How the lost detail splits between the two kinds of turn end, in dollars."""
    share: float  # of turn ends, mid-task
    lo: float  # D at a finished turn end
    hi: float  # D at a mid-task one

    @property
    def mean(self):
        return self.share * self.hi + (1 - self.share) * self.lo

    def given(self, fin, mid):
        """For a classifier right with probability `fin` at a finished turn end and `mid` at a mid-task one:
        the share of turn ends it calls finished, and the expected D where it calls one finished and
        where it calls one mid-task."""
        called = (1 - self.share) * fin + self.share * (1 - mid)
        on_fin = ((1 - self.share) * fin * self.lo + self.share * (1 - mid) * self.hi) / called
        on_mid = ((1 - self.share) * (1 - fin) * self.lo + self.share * mid * self.hi) / (1 - called)
        return called, on_fin, on_mid

    def __str__(self):
        return f"${self.mean:.2f} on average: {self.share:.0%} mid-task at ${self.hi:.2f}, the rest at ${self.lo:.2f}"


# Three averages of D, each with 30% of turn ends mid-task: the D measured after the compactions the
# user and Claude Code made, $0.35, the top of its 90% interval, $1.12 (docs/cost-model.md,
# "What does the model assume a compaction loses?"), and $3.72, enough on its own to move the best point to 400k. How the
# average splits between the share and the loss moves the saving much less than the average does.
# The share stays below 0.43: if the evals' accuracy held live, the plugin's compacting at 125 of at
# most 149 verdicts would put it there or lower. D at a finished turn end is 0 rather than below: a
# compaction could only help the next work if a long context hurt it, of which the doc finds no
# sign, so the lower trouble after sparkjoy's compactions is taken as where they fall, where a piece
# of work ends, not as what they cause.
SHARE = 0.3
MIXES = tuple(Mix(SHARE, 0.0, mean / SHARE) for mean in (0.35, 1.12, 3.72))
ACCURACIES = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
# The current question on the evals (evals/README.md), as runs it called rightly of runs: it
# compacted in all 12 at the 6 finished cases and kept in 6 of the 32 at the 4 mid-task ones.
RUNS = ((12, 12), (6, 32))
EVALS = tuple(k / n for k, n in RUNS)
CHECKS = (*((a, a) for a in ACCURACIES), EVALS)  # (right at a finished turn end, at a mid-task one)
FLOORS = np.arange(150_000, 350_001, 25_000)
CEILINGS = (*np.arange(150_000, 500_001, 25_000), np.inf)  # each paired with the floors at or below it
DRAWS = 8  # draws of the classifier's calls, the same for every arm


def init(state):
    global STATE
    STATE = state


def evaluate(task):
    """One arm over every session for one draw of the classifier's calls: its cost and the cost of
    running the classifier,
    in cached reads. Which kind a turn end is changes only what a compaction there costs, not what
    the policy does, which sees only the call, so each compaction is charged the expected D given
    the call and only the calls are drawn."""
    S, p, c, ttl, U = STATE
    mix, fin, mid, floor, ceiling, d = task
    called, on_fin, on_mid = mix.given(fin, mid)
    cost = checks = 0.0
    for s, u in zip(S, U[d]):
        finished = u < called
        # Claude Code's compaction near the window comes inside a turn, where work is in progress.
        lost = np.where(s.end, np.where(finished, on_fin, on_mid), mix.hi) / c.read
        total, trace = fit.replay(s, p, c, np.where(finished, floor, ceiling), ttl, lost=lost)
        held = trace + s.out  # the context at each turn end, the reply included
        asked = s.end & (held >= floor) & (held < ceiling)
        cost += total
        checks += (held[asked] + c.output * CHECK_OUTPUT).sum()
    return cost, checks


def solve(S, p, c, ttl):
    """For each mix: the best threshold without the classifier and its cost, the lost detail in that
    cost, and for each classifier in CHECKS, every (floor, ceiling)'s cost and the cost of running the
    classifier in each draw."""
    rng = np.random.default_rng(0)
    U = [[rng.random(len(s.size)) for s in S] for _ in range(DRAWS)]
    pairs = [(f, k) for k in CEILINGS for f in FLOORS if f <= k]
    tasks = [(mix, fin, mid, f, k, d) for mix in MIXES for fin, mid in CHECKS for f, k in pairs for d in range(DRAWS)]
    # The threshold arm charges the mean D, as a classifier right half the time does at either call; and
    # with no D at all, to price the lost detail it carries.
    tasks = list(dict.fromkeys(tasks + [(m, 0.5, 0.5, t, t, 0) for x in MIXES for m in (x, Mix(x.share, 0, 0))
                                        for t in FLOORS]))
    with multiprocessing.get_context("fork").Pool(min(8, os.cpu_count()), init, ((S, p, c, ttl, U),)) as pool:
        done = dict(zip(tasks, pool.map(evaluate, tasks, chunksize=4)))
    out = []
    for mix in MIXES:
        base = {t: done[mix, 0.5, 0.5, t, t, 0][0] for t in FLOORS}
        t = min(base, key=base.get)
        lost = base[t] - done[Mix(mix.share, 0, 0), 0.5, 0.5, t, t, 0][0]
        grid = {fm: {fk: np.array([done[mix, *fm, *fk, d] for d in range(DRAWS)]) for fk in pairs} for fm in CHECKS}
        out.append({"mix": mix, "threshold": t, "base": base[t], "lost": lost, "grid": grid})
    return out


def best(row, fin, mid):
    """The (floor, ceiling) with the least mean cost for the classifier (fin, mid), with its saving
    against the threshold, the saving's standard error over draws, and the cost of running it, as
    shares of the threshold's cost."""
    grid = row["grid"][fin, mid]
    pair = min(grid, key=lambda fk: grid[fk][:, 0].mean())
    saved = 1 - grid[pair][:, 0] / row["base"]
    return pair, saved.mean(), saved.std(ddof=1) / np.sqrt(DRAWS), grid[pair][:, 1].mean() / row["base"]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    fit.window_args(ap)
    a = ap.parse_args()
    S, since, until = fit.read(a)
    ttl = fit.usual(s.ttl for s in S)
    c = fit.prices(MODEL, ttl)
    p = fit.measure(S, ttl)
    print(f"{len(S)} sessions, {sum(s.end.sum() for s in S):,} turn ends, {since:%Y-%m-%d %H:%M}Z to "
          f"{until:%Y-%m-%d %H:%M}Z; {MODEL} prices, {ttl // 60}-minute cache; β = 0; {DRAWS} draws of the calls")
    rows = solve(S, p, c, ttl)
    show(rows, c)


def show(rows, c):
    k = lambda x: "never" if x == np.inf else f"{x / 1e3:.0f}k"
    for row in rows:
        print(f"\n{row['mix']}")
        print(f"  without the classifier: threshold {k(row['threshold'])}, its lost detail {row['lost'] / row['base']:.1%} "
              f"of its cost (${row['base'] * c.read:,.0f})")
        print("  classifier (finished, mid-task)   floor   ceiling   saved (s.e.)   running it costs   net")
        for fin, mid in (*((x, x) for x in ACCURACIES), EVALS):
            (f, ceiling), saved, se, asked = best(row, fin, mid)
            print(f"  {fin:4.2f}, {mid:5.3f}                     {k(f):>5}   {k(ceiling):>7}   {saved:6.2%} ({se:.2%})"
                  f"   {asked:6.2%}           {saved - asked:+6.2%}")
        sparkjoy = row["grid"][EVALS][250_000, np.inf]
        print(f"  sparkjoy now, asking from 250k with no ceiling, at the evals' accuracy: "
              f"{1 - sparkjoy[:, 0].mean() / row['base']:+.2%}, running it {sparkjoy[:, 1].mean() / row['base']:.2%}")


if __name__ == "__main__":
    main()
