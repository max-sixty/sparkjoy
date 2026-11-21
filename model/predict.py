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
each compaction charged the average D. A replay of your sessions' requests and turn ends picks, for
each β in BETAS, the floor and ceiling for each a and the threshold with the least cost per unit of
effectiveness, J, and gives each saving as a share of the threshold's J. Neither arm compacts idle
sessions. Running the classifier costs a read of the context at each turn end it is asked at, which
is reported beside the saving rather than counted in it.
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
        # One that calls every turn end finished never calls one mid-task.
        on_mid = ((1 - self.share) * (1 - fin) * self.lo + self.share * mid * self.hi) / (1 - called) if called < 1 else 0
        return called, on_fin, on_mid

    def __str__(self):
        return f"${self.mean:.2f} on average: {self.share:.0%} mid-task at ${self.hi:.2f}, the rest at ${self.lo:.2f}"


# Three averages of D, each with 30% of turn ends mid-task: fit.LOST, the D measured after the
# compactions the user and Claude Code made, the top of its 90% interval, and enough on its own to move
# the best point to 400k. How the average splits between the share and the loss moves the saving much
# less than the average does.
# The share stays below 0.43: if the evals' accuracy held live, the plugin's compacting at 125 of at
# most 149 verdicts would put it there or lower. D at a finished turn end is 0 rather than below: a
# compaction could only help the next work if a long context hurt it, of which the doc finds no
# sign, so the lower trouble after sparkjoy's compactions is taken as where they fall, where a piece
# of work ends, not as what they cause.
SHARE = 0.3
MIXES = tuple(Mix(SHARE, 0.0, mean / SHARE) for mean in fit.LOST)
ACCURACIES = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
# The current question on the evals (evals/README.md, "Batches"): it compacted in every run at the
# finished cases, and kept the context at the mid-task ones in a share of runs that swings from batch
# to batch with the same question and transcripts. Each is (runs it kept in, runs) at the mid-task
# cases: all its batches pooled, and the batches that kept least and most.
RUNS = {"the evals pooled": (29, 64), "their lowest batch": (0, 16), "their highest batch": (23, 32)}
EVALS = {name: (1.0, kept / runs) for name, (kept, runs) in RUNS.items()}
CHECKS = (*((a, a) for a in ACCURACIES), *EVALS.values())  # (right at a finished turn end, at a mid-task one)
# Three of fit.BETAS. At β = 1 no compaction short of Claude Code's own pays, and the grids would have to
# span the window.
BETAS = (0, 0.3, 0.6)
# The threshold without the classifier, and the floors and ceilings with it, reach past the best at
# every β: at β = 0.6 with D = $3.72 the best threshold is 500k and the best ceiling 750k.
THRESHOLDS = np.arange(150_000, 900_001, 25_000)
FLOORS = np.arange(150_000, 450_001, 25_000)
CEILINGS = (*np.arange(150_000, 900_001, 25_000), np.inf)  # each paired with the floors at or below it
DRAWS = 8  # draws of the classifier's calls, the same for every arm


def init(state):
    global STATE
    STATE = state


def evaluate(task):
    """One arm over every session for one draw of the classifier's calls: its cost and the cost of
    running the classifier, in cached reads, and its effectiveness at each β in BETAS. Which kind a turn
    end is changes only what a compaction there costs, not what the policy does, which sees only the
    call, so each compaction is charged the expected D given the call and only the calls are drawn."""
    S, p, c, ttl, U = STATE
    mix, fin, mid, floor, ceiling, d = task
    called, on_fin, on_mid = mix.given(fin, mid)
    cost = checks = 0.0
    effectiveness = np.zeros(len(BETAS))
    for s, u in zip(S, U[d]):
        finished = u < called
        # Claude Code's compaction near the window comes inside a turn, where work is in progress.
        lost = np.where(s.end, np.where(finished, on_fin, on_mid), mix.hi) / c.read
        total, trace = fit.replay(s, p, c, np.where(finished, floor, ceiling), ttl, lost=lost)
        held = trace + s.out  # the context at each turn end, the reply included
        asked = s.end & (held >= floor) & (held < ceiling)
        cost += total
        checks += (held[asked] + c.output * CHECK_OUTPUT).sum()
        effectiveness += [fit.eta(trace, b).sum() for b in BETAS]
    return cost, checks, effectiveness


def solve(S, p, c, ttl):
    """For each mix and each β in BETAS: the best threshold without the classifier and its J, the lost
    detail's share of its cost, and for each classifier in CHECKS, every (floor, ceiling)'s J and the J of
    running the classifier, in each draw."""
    rng = np.random.default_rng(0)
    U = [[rng.random(len(s.size)) for s in S] for _ in range(DRAWS)]
    pairs = [(f, k) for k in CEILINGS for f in FLOORS if f <= k]
    tasks = [(mix, fin, mid, f, k, d) for mix in MIXES for fin, mid in CHECKS for f, k in pairs for d in range(DRAWS)]
    # The threshold arm charges the mean D, as a classifier right half the time does at either call; and
    # with no D at all, to price the lost detail it carries.
    tasks = list(dict.fromkeys(tasks + [(m, 0.5, 0.5, t, t, 0) for x in MIXES for m in (x, Mix(x.share, 0, 0))
                                        for t in THRESHOLDS]))
    with multiprocessing.get_context("fork").Pool(min(8, os.cpu_count()), init, ((S, p, c, ttl, U),)) as pool:
        done = dict(zip(tasks, pool.map(evaluate, tasks, chunksize=4)))
    out = []
    for mix in MIXES:
        for i, beta in enumerate(BETAS):
            # An arm's cost, or with n = 1 its checks', per unit of its effectiveness at this β.
            per_unit = lambda arm, n=0, i=i: arm[n] / arm[2][i]
            base = {t: per_unit(done[mix, 0.5, 0.5, t, t, 0]) for t in THRESHOLDS}
            t = min(base, key=base.get)
            lost = 1 - done[Mix(mix.share, 0, 0), 0.5, 0.5, t, t, 0][0] / done[mix, 0.5, 0.5, t, t, 0][0]
            grid = {fm: {fk: np.array([[per_unit(done[mix, *fm, *fk, d], n) for n in (0, 1)] for d in range(DRAWS)])
                         for fk in pairs} for fm in CHECKS}
            out.append({"mix": mix, "beta": beta, "threshold": t, "base": base[t], "lost": lost, "grid": grid})
    return out


def best(row, fin, mid):
    """The (floor, ceiling) with the least mean J for the classifier (fin, mid), with its saving against
    the threshold, the saving's standard error over draws, and the J of running it, as shares of the
    threshold's J."""
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
          f"{until:%Y-%m-%d %H:%M}Z; {MODEL} prices, {ttl // 60}-minute cache; {DRAWS} draws of the calls")
    rows = solve(S, p, c, ttl)
    show(rows)


def show(rows):
    k = lambda x: "never" if x == np.inf else f"{x / 1e3:.0f}k"
    for row in rows:
        if row["beta"] == BETAS[0]:
            print(f"\n{row['mix']}")
        print(f"  β = {row['beta']:g}: without the classifier, threshold {k(row['threshold'])}, its lost detail "
              f"{row['lost']:.1%} of its cost")
        print("    classifier (finished, mid-task)       floor   ceiling   saved (s.e.)   running it costs   net")
        names = {v: f"{name}, {RUNS[name][0]} of {RUNS[name][1]}" for name, v in EVALS.items()}
        for fin, mid in CHECKS:
            (f, ceiling), saved, se, asked = best(row, fin, mid)
            print(f"    {fin:4.2f}, {mid:5.3f} {names.get((fin, mid), ''):29s} {k(f):>5}   {k(ceiling):>7}   "
                  f"{saved:6.2%} ({se:.2%})   {asked:6.2%}           {saved - asked:+6.2%}")
        sparkjoy = row["grid"][EVALS["the evals pooled"]][250_000, np.inf]
        print(f"    sparkjoy now, asking from 250k with no ceiling, at the evals' pooled accuracy: "
              f"{1 - sparkjoy[:, 0].mean() / row['base']:+.2%}, running it {sparkjoy[:, 1].mean() / row['base']:.2%}")


if __name__ == "__main__":
    main()
