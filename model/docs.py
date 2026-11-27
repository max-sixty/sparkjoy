#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2.1", "matplotlib>=3.9", "scipy>=1.14"]
# ///
"""Regenerates the figures and charts the README and docs/cost-model.md quote, from one read of my
sessions over the docs' window, SINCE to UNTIL.

    uv run --script model/docs.py

It reads the main thread of each interactive session in the window (`claude -p` runs are left
out), at low priority, and fits the cost model to them with fit.py, at fit.MODEL's list prices and
the cache lifetime most sessions used. It writes every figure to docs/figures.json, by section:

  window    the window, and the sessions and requests in it
  fit       the model's parameters, with 90% intervals from resampling sessions; at the base case
            fit.BASE for β, the steady state's most efficient threshold and how it moves with each
            parameter and with β; the replay's most efficient turn-end threshold and pairs of
            floors; what sparkjoy saved as the sessions ran; compacting mid-turn against at turn
            ends; work that differs; and each model's prices
  context   what fills the context after a compaction (context.py)
  charts    the figures the cost model quotes from its charts (charts.py)
  replay    the figures the README quotes from its charts (replay_charts.py)

calibrate.py writes its own section, `calibrate`, which measures D over a longer window. It draws each chart in a
light and a dark theme, and publishes them to sparkjoy-assets with sparkjoy_assets.py, which points the README's
and docs' links at them. Figures keep their units: tokens, dollars under keys ending in `_usd`,
and shares and savings as fractions; a threshold never reached is null.
"""

import argparse
import tempfile
from dataclasses import replace
from pathlib import Path

import charts
import context
import fit
import numpy as np
import replay_charts
import sparkjoy_assets
from fit import (
    ASKS,
    AUTO,
    BASE,
    CC_IDLE,
    CHART_BETAS,
    FLOOR,
    IDLE,
    IDLE_FLOOR,
    IDLE_FLOORS,
    MIX,
    MIX_DRAWS,
    RIGHT,
    STRETCH,
    THRESHOLDS,
)

SINCE, UNTIL = "2026-11-18T00:00Z", "2026-11-23T00:00Z"
# A prefix of the id of the session the README's session chart shows.
SESSION = "00e0c1c4"


def k(x):
    """A context as the figures name it: "250k", or "never" for no threshold."""
    return "never" if x == np.inf else f"{x / 1e3:.0f}k"


def interval(v, values, **kw):
    return {"value": v, "interval_90": np.percentile(values, [5, 95], **kw)}


def parameters(S, p, c, boot):
    """The parameters, the terms of the cost model they give, and the steady state's closed form."""
    h, c0, F0 = fit.terms(p, c)
    _, text, ratio, summaries, near = fit.summary_output(S)
    size, grew = (
        np.concatenate([getattr(s, f)[s.kind == fit.NORMAL] for s in S])
        for f in ("size", "grew")
    )
    return {
        "parameters": {
            name: interval(getattr(p, name), [getattr(b, name) for b in boot])
            for name in (
                "K",
                "first_request",
                "P",
                "E",
                "N",
                "rereads",
                "g",
                "out",
                "lapse",
                "turn",
                "turn_median",
                "turn_growth",
                "idle_turns",
            )
        },
        "compactions": {
            "count": p.compactions,
            "median_seconds": p.duration,
            # N is the summary's text times the median output over visible output of the requests
            # whose visible output is half to 1.5 times the summary's.
            "summary_text_median": text,
            "summaries": summaries,
            "output_over_visible": ratio,
            "requests_compared": near,
        },
        "growth_by_context": {
            f"{k(lo)}-{k(lo + 100_000)}": grew[
                (size >= lo) & (size < lo + 100_000)
            ].mean()
            for lo in range(100_000, 800_000, 100_000)
        },
        "terms": {"h": h, "c0": c0, "F0": F0, "F0_usd": F0 * c.read},
        "closed_form_threshold": interval(
            fit.closed_form(p, c), [fit.closed_form(b, c) for b in boot]
        ),
        "idle_break_even": fit.idle_break_even(p, c),
    }


def steady_state(p, c, boot, BX):
    """The steady state at the base case: its most efficient threshold, X*, how far it moves with each
    parameter, and the β and D that would put it elsewhere."""
    ib = list(CHART_BETAS).index(BASE)
    X0 = fit.steady(p, c, BASE)

    def J0(x, d=0.0):  # cost per unit of effectiveness against Claude Code's default
        return fit.per_unit(p, c, x, BASE, d) / fit.per_unit(p, c, AUTO, BASE, d)

    near = [x for x in range(150_000, AUTO, 1_000) if J0(x) <= J0(X0) + 0.01]
    x0, Xl, Jl = fit.log_steady(p, c, BASE)
    d = fit.lost_for(p, c, 700_000, BASE)
    reach = {}
    for level in (300_000, 400_000, 600_000, 700_000, AUTO):
        beta = next(
            (b for b in np.arange(0, 5.001, 0.01) if fit.steady(p, c, b) >= level), None
        )
        if beta is None:
            reach[k(level)] = None
            continue
        X = fit.steady(p, c, beta)
        reach[k(level)] = {
            "beta": beta,
            "threshold": X,
            "effectiveness_after_compaction": fit.eta(p.K, beta),
            "gain_against_claude_code": fit.per_unit(p, c, AUTO, beta)
            / fit.per_unit(p, c, X, beta)
            - 1,
        }
    return {
        # A request's effectiveness against one at the full window.
        "effectiveness": {
            "250k": fit.eta(250_000, BASE),
            "150k": fit.eta(150_000, BASE),
            "K": fit.eta(p.K, BASE),
        },
        "threshold": interval(X0, BX[:, ib]),
        "saved": 1 - J0(X0),
        "within_a_point": [min(near), max(near)],
        "threshold_without_lapses": fit.steady(replace(p, lapse=0), c, BASE),
        "threshold_with_twice_the_summary": fit.steady(replace(p, N=2 * p.N), c, BASE),
        # As for a user with few instructions.
        "threshold_with_K": {
            k(K): fit.steady(replace(p, K=K), c, BASE) for K in (60_000, 70_000)
        },
        "sensitivity": {
            ("beta" if field == "β" else field): {
                "halved": half,
                "doubled": twice,
                "across_interval_90": span,
            }
            for field, (half, twice, span) in fit.sensitivity(p, c, boot, BASE).items()
        },
        # With η = ln(x / x0), x0 set so a request at 150k is as effective against 1M as at the base case.
        "log_effectiveness": {"x0": x0, "threshold": Xl, "saved": 1 - Jl},
        "D_for_700k": None
        if d is None
        else {"usd": d * c.read, "gain_against_claude_code": 1 / J0(700_000, d) - 1},
        "beta_for_threshold": reach,
    }


def replayed(p, c, cost, effectiveness, cost_r, work_r, BT):
    """The replay at the base case: the most efficient threshold at turn ends and after any request,
    and what other thresholds give up."""
    ib = list(CHART_BETAS).index(BASE)
    T, saved = fit.best(cost, effectiveness)
    Tr, _ = fit.best(cost_r, work_r)
    at = {x: list(THRESHOLDS).index(x) for x in THRESHOLDS}
    J = cost.sum(0)[:, None] / effectiveness.sum(0)
    Jb = J[:, ib] / J[-1, ib]  # at the base case, against Claude Code's default
    X0 = fit.steady(p, c, BASE)
    near = THRESHOLDS[Jb[:-1] <= Jb[:-1].min() + 0.01]
    return {
        "threshold_after_any_request": Tr[0],
        # Its interval resamples sessions with K, P and N held at their fitted values.
        "threshold": interval(T[ib], BT[:, ib], method="nearest"),
        "saved": 1 - saved[ib],
        "saved_from": {k(x): 1 - Jb[at[x]] for x in (250_000, 400_000)},
        "within_a_point": [near.min(), near.max()],
        # How much more than the best compacting at each threshold costs.
        "extra_cost": {
            k(x): {
                "steady": fit.per_unit(p, c, x, BASE) / fit.per_unit(p, c, X0, BASE)
                - 1,
                "replay": Jb[at[x]] / Jb.min() - 1,
            }
            for x in (400_000, 600_000)
        },
        "threshold_by_beta": {f"{b:g}": t for b, t in zip(CHART_BETAS, T, strict=True)},
        # The saving each turn-end threshold gives up against the most efficient one, by β.
        "saving_given_up_by_beta": {
            k(x): {
                f"{b:g}": v
                for b, v in zip(CHART_BETAS, J[at[x]] / J[-1] - saved, strict=True)
            }
            for x in (250_000, 400_000)
        },
    }


def pairs_of_floors(S, p, c, ttl, cost, effectiveness):
    """Turn-end and idle floors chosen together at the base case, the turn-end floors running to
    compacting only idle sessions, against Claude Code's default; and the pair `floors` fits by cost
    alone, as the report does."""
    ib = list(CHART_BETAS).index(BASE)
    turns, idles = (*range(100_000, 600_001, 50_000), np.inf), (None, *IDLE_FLOORS)
    never = cost[:, -1].sum()
    Jn = never / effectiveness[:, -1, ib].sum()
    saved = {
        key: 1 - v[0] / v[1][0] / Jn
        for key, v in fit.pairs(S, p, c, ttl, turns, idles).items()
    }
    # Claude Code's own idle compaction, which asks nothing, in a session someone has open.
    cc_cost, cc_work = fit.arm(S, p, c, ttl, np.inf, (BASE,), idle=CC_IDLE)
    claude_open = 1 - cc_cost.sum() / cc_work.sum() / Jn
    top = max(saved, key=saved.get)
    near = [key for key in saved if (saved[top] - saved[key]) * 100 <= 1]
    cheapest = tuple(fit.floors(S, p, c, ttl)[0])
    saved |= {
        key: 1 - v[0] / v[1][0] / Jn
        for key, v in fit.pairs(S, p, c, ttl, *([x] for x in cheapest)).items()
    }

    def pair(t, i):
        """The pair's saving, and in a session someone has open, where Claude Code compacts idle
        sessions itself, the saving that is left where the idle floor is below Claude Code's."""
        return {
            "turn_floor": t,
            "idle_floor": i,
            "saved": saved[t, i],
            "saved_open": None
            if i is None or i > CC_IDLE
            else 1 - (1 - saved[t, i]) / (1 - claude_open),
        }

    policies = {}
    for t, i in (
        (FLOOR, None),
        (FLOOR, IDLE_FLOOR),
        (400_000, None),
        (400_000, 200_000),
        (200_000, None),
    ):
        policies[f"{k(t)}_and_{k(i) if i else 'none'}"] = pair(t, i)
    return {
        "claude_code_usd": never * c.read,
        "policies": policies,
        "best_pair": pair(*top),
        "saving_sparkjoy_gives_up": saved[top] - saved[FLOOR, IDLE_FLOOR],
        "within_a_point": {
            "turn_floors": [min(t for t, _ in near), max(t for t, _ in near)],
            "idle_floors": [
                min(i or np.inf for _, i in near),
                max(i or np.inf for _, i in near),
            ],
        },
        "cost_alone_pair": pair(*cheapest),
        # Saved by pair: rows the turn-end floor, columns the idle floor, "none" for none.
        "saved_by_pair": {
            k(t): {(k(i) if i else "none"): saved[t, i] for i in idles} for t in turns
        },
        "claude_code_idle_compaction_saved": claude_open,
    }


def as_ran(runs, draws):
    """The README's Cost saving: sparkjoy's compactions as they ran, against Claude Code alone, with
    each session as it ran and as if every session were in the background or open, with the saving's
    90% interval over the sessions resampled, and how much each of the costliest quarter saved."""
    out = {}
    for opened, name in ((None, "as_ran"), (False, "background"), (True, "open")):
        ran, without, checks = np.array([r[0] for r in runs[opened]]).T
        costly = without > np.percentile(without, 75)
        each = 1 - ran[costly] / without[costly]
        out[name] = {
            "saved": interval(
                1 - ran.sum() / without.sum(),
                [1 - ran[d].sum() / without[d].sum() for d in draws],
            ),
            "with_usd": ran.sum(),
            "without_usd": without.sum(),
            "saved_usd": without.sum() - ran.sum(),
            "questions_usd": checks.sum(),
            "questions_share": checks.sum() / ran.sum(),
            "costliest_quarter_saved": {
                "median": np.median(each),
                "p10": np.percentile(each, 10),
                "p90": np.percentile(each, 90),
            },
        }
    return out


PLACES = {
    "after_any_request": lambda t, y: t == y,
    "turn_ends_only": lambda t, y: y == np.inf,
    "turn_ends_with_backstop": lambda t, y: t < y < np.inf,
}


def mid_turn(S, p, c, ttl, cost, effectiveness):
    """Compacting after any request, only at turn ends, or at turn ends with a backstop after any
    request, each at its most efficient thresholds and compacting idle sessions from IDLE_FLOOR, with
    each mid-turn compaction charged D wasted requests beyond what one at a turn end loses, each priced
    as a request at K."""
    h, c0, _ = fit.terms(p, c)
    betas = (0.0, BASE)
    mo = fit.moments(S, p, c, ttl, betas)
    wasted = h * p.K + c0

    def unit(key, i, D):  # cost per unit of work
        return (mo[key][0] + D * wasted * mo[key][1]) / mo[key][2][i]

    nothing = [
        cost[:, -1].sum() / effectiveness[:, -1, list(CHART_BETAS).index(b)].sum()
        for b in betas
    ]

    def lowest(kind, i, D):
        return min(
            (key for key in mo if PLACES[kind](*key)), key=lambda key: unit(key, i, D)
        )

    def policy(key, i, D):
        return {
            "turn_threshold": key[0],
            "any_request_threshold": key[1],
            "saved": 1 - unit(key, i, D) / nothing[i],
            "mid_turn_compactions": mo[key][1],
        }

    out = {"wasted_request_usd": wasted * c.read}
    for i, b in enumerate(betas):
        # The D, in wasted requests, from which each costs as little as compacting after any request.
        even = {
            kind: next(
                (
                    D
                    for D in range(101)
                    if unit(lowest(kind, i, D), i, D)
                    <= unit(lowest("after_any_request", i, D), i, D)
                ),
                None,
            )
            for kind in ("turn_ends_only", "turn_ends_with_backstop")
        }
        out[f"beta_{b:g}"] = {
            "policies": {kind: policy(lowest(kind, i, 0), i, 0) for kind in PLACES},
            "break_even": {
                kind: None
                if D is None
                else {"wasted_requests": D, "usd": D * wasted * c.read}
                for kind, D in even.items()
            },
            "backstops_from_floor": {
                k(y): policy((t, y), i, 0)
                for t, y in mo
                if t == FLOOR and FLOOR < y < np.inf
            },
            "after_any_request_by_threshold": {
                k(t): policy((t, y), i, 0)
                for t, y in mo
                if t == y and t % 50_000 == 0 and t >= 200_000
            },
            "losing": {
                str(D): {
                    "turn_ends_with_backstop": policy(
                        lowest("turn_ends_with_backstop", i, D), i, D
                    ),
                    "after_any_request": policy(
                        lowest("after_any_request", i, D), i, D
                    ),
                }
                for D in (2, 6)
            },
        }
    return out


def mixed_work(S, p, c, ttl):
    """Work that differs: MIX's kinds of turns, the judgment naming each turn end's next kind. Every
    saving is against Claude Code's default, which compacts near the window, and in a session someone
    has open on the one-hour cache, also every idle context from CC_IDLE, the judgment's included. The
    judgment doesn't know which kind of session it is in, so it compacts from the same pair of
    thresholds in both. Every policy of sparkjoy's pays the questions it asks from FLOOR."""
    # The steady state on the same mix, each kind running whole cycles, as under "Work that differs
    # in β", where Claude Code's default compacts at AUTO, the last point of `curve`.
    X = fit.curve(p, c, 0)[0]
    Js = np.array([fit.curve(p, c, b)[1] for _, b in MIX])
    Jq = np.array(
        [fit.curve(p, c, b, asks=FLOOR)[1] for _, b in MIX]
    )  # with its questions
    w = np.array([share for share, _ in MIX])
    single = (w[:, None] * Js).sum(0)

    def judged(a):  # J with the judgment right a of the time in each kind
        keep = np.array([1 - a, a]) * w
        return (keep[:, None] * Jq).sum(0).min() + ((w - keep)[:, None] * Jq).sum(
            0
        ).min()

    at_floor = sum(share * fit.per_unit(p, c, FLOOR, b, asks=FLOOR) for share, b in MIX)
    replays = {}
    for name, stretch, right in (
        ("base", STRETCH, RIGHT),
        ("always_right", STRETCH, 1.0),
        ("long_stretches", 20, RIGHT),
    ):
        rng = np.random.default_rng(0)
        draws = [fit.kinds(S, rng, MIX, stretch, right) for _ in range(MIX_DRAWS)]
        pair, _ = fit.judging(S, p, c, ttl, draws, (400_000, 800_000))
        at_floors = fit.shared(
            *fit.mixed(S, p, c, ttl, draws, FLOOR, idle=IDLE_FLOOR, asks=ASKS)
        )
        entry = {"stretch": stretch, "right": right, "pair": list(pair)}
        for where, claude in (("background", None), ("open", CC_IDLE)):
            default = fit.shared(*fit.mixed(S, p, c, ttl, draws, np.inf, idle=claude))
            Jj = fit.shared(
                *fit.mixed(
                    S,
                    p,
                    c,
                    ttl,
                    draws,
                    pair,
                    judged=True,
                    claude_idle=claude,
                    asks=ASKS,
                )
            )
            entry[where] = {
                "judgment_saved": 1 - Jj / default,
                "floors_saved": 1 - at_floors / default,
            }
        replays[name] = entry
    return {
        "mix": [{"share": share, "beta": b} for share, b in MIX],
        "stretch": STRETCH,
        "right": RIGHT,
        "draws": MIX_DRAWS,
        "steady": {
            "saved_at_floor": 1 - at_floor / single[-1],
            "best_single_threshold": X[single.argmin()],
            "saved_best_single": 1 - single.min() / single[-1],
            "judgment_saved": 1 - judged(RIGHT) / single[-1],
            "judgment_saved_always_right": 1 - judged(1) / single[-1],
            # How often the judgment must be right to save more than the best single threshold.
            "judgment_beats_single_from": next(
                a for a in np.arange(0.5, 1.0001, 0.005) if judged(a) < single.min()
            ),
        },
        "replay": replays,
    }


def models(S):
    """The base case's thresholds and savings at each model's prices, on each cache lifetime."""
    gaps = np.concatenate(
        [np.append(s.gap[1:], 0)[s.end] for s in S]
    )  # from each turn end to the next request
    measured = {t: fit.measure(S, t) for t in (3600, 300)}
    out = {}
    for model in (
        "claude-opus-5-5",
        "claude-sonnet-5-5",
        "claude-fable-5-2",
        "claude-haiku-5-5",
    ):
        out[model] = {}
        for t in (3600, 300):
            q, cq = measured[t], fit.prices(model, t)
            gc, ge = fit.grid(S, q, cq, t, betas=(BASE,))
            (tt,), (sv,) = fit.best(gc, ge)
            never = gc[:, -1].sum() / ge[:, -1, 0].sum()
            from_floor = {
                i: v[0] / v[1][0] / never
                for (_, i), v in fit.pairs(
                    S, q, cq, t, (FLOOR,), (None, IDLE_FLOOR)
                ).items()
            }
            h, _, F0 = fit.terms(q, cq)
            out[model][f"{t // 60}m"] = {
                "lapse": q.lapse,
                "lapse_turn_end": q.lapse_turn_end,
                "h": h,
                "F0_usd": F0 * cq.read,
                "idle_break_even": fit.idle_break_even(q, cq),
                "idle_turns": q.idle_turns,
                # Of the pauses past the idle wait before sparkjoy asks, the share the cache doesn't outlive.
                "pauses_outliving_cache": np.mean(gaps[gaps > IDLE[t]] > t),
                "steady_threshold": fit.steady(q, cq, BASE),
                "replay_threshold": tt,
                "replay_saved": 1 - sv,
                "saved_from_floor": 1 - from_floor[None],
                "saved_from_floors": 1 - from_floor[IDLE_FLOOR],
            }
    return out


def main():
    S, since, until = fit.read(
        argparse.Namespace(since=SINCE, until=UNTIL, projects=fit.PROJECTS)
    )
    ttl = fit.usual(s.ttl for s in S)
    c = fit.prices(fit.MODEL, ttl)
    p = fit.measure(S, ttl)
    rng = np.random.default_rng(0)
    draws = [rng.integers(0, len(S), len(S)) for _ in range(fit.DRAWS)]
    boot = [fit.measure([S[i] for i in d], ttl) for d in draws]
    BX = np.array([[fit.steady(b, c, beta) for beta in CHART_BETAS] for b in boot])
    cost, effectiveness = fit.grid(S, p, c, ttl)
    cost_r, work_r = fit.grid(S, p, c, ttl, betas=(BASE,), every=True)
    BT = np.array([fit.best(cost, effectiveness, d)[0] for d in draws])
    runs = {o: [fit.as_run(s, p, o, fit.MODEL) for s in S] for o in (None, False, True)}

    parts = context.measure(S, p)
    fitted = {
        "prices": {
            "model": fit.MODEL,
            "cache_minutes": ttl // 60,
            # A cache write and an output token, in cached reads.
            "write_in_reads": c.write,
            "output_in_reads": c.output,
        },
        **parameters(S, p, c, boot),
        "steady": steady_state(p, c, boot, BX),
        "replay": replayed(p, c, cost, effectiveness, cost_r, work_r, BT),
        "floors": pairs_of_floors(S, p, c, ttl, cost, effectiveness),
        "as_ran": as_ran(runs, draws),
        "mid_turn": mid_turn(S, p, c, ttl, cost, effectiveness),
        "mixed_work": mixed_work(S, p, c, ttl),
        "models": models(S),
    }
    with tempfile.TemporaryDirectory() as scratch:
        drawn = Path(scratch)
        drawings = {
            "charts": charts.draw(
                drawn, p, c, boot, cost, effectiveness, BT, BX, parts
            ),
            "replay": replay_charts.draw(drawn, S, runs, SESSION),
        }
        for chart in sorted(drawn.glob("*.svg")):
            sparkjoy_assets.image(chart)
    fit.save_figures(
        {
            "window": {
                "since": f"{since:%Y-%m-%dT%H:%M}Z",
                "until": f"{until:%Y-%m-%dT%H:%M}Z",
                "sessions": len(S),
                # In the background (`claude --bg`, agent view), where Claude Code compacts no idle session.
                "background_sessions": sum(s.background for s in S),
                "requests": sum(len(s.size) for s in S),
            },
            "fit": fitted,
            "context": parts,
            **drawings,
        }
    )
    print(f"wrote {fit.DOC_FIGURES}")
    sparkjoy_assets.publish(sparkjoy_assets.IMAGES)


if __name__ == "__main__":
    main()
