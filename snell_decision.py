"""
Pulling Blake Snell in 2020 World Series Game 6: what was the decision worth?

1. The reliever's state going into Game 6, using only games BEFORE the decision:
   four-seam velocity and spin vs. his regular season, workload, results.
2. Expected wOBA: third-time-through Snell (from snell_vs_peers.py) vs. the
   reliever under three scenarios, plus the break-even point.
3. What was at stake: Rays win probability and leverage index (LI) at the
   moment Snell was pulled, and the actual swing in the rest of the 6th inning.
4. The decision, converted into win probability for each scenario.
5. Chart: Rays win probability through Game 6, with the pull marked.

Run snell_vs_peers.py first: this script reads output/snell_like_starts.csv.
Setup:  pip install pybaseball pandas matplotlib
Run:    python snell_decision.py

Most pulls repeat the ones the earlier scripts made, so they should come from
pybaseball's cache. If they don't, the league part takes about 10 minutes.
"""
from pathlib import Path

import numpy as np
import pandas as pd
from pybaseball import cache, statcast, statcast_pitcher

cache.enable()

GAME_DATE = "2020-10-27"                           # 2020 World Series Game 6
SNELL_NAME = "Snell, Blake"
POST_START, POST_END = "2020-09-29", "2020-10-27"  # same pull as snell_tto.py
RELIEVER_REG = ("2019-03-20", "2020-09-27")        # reliever's 2019-2020 regular seasons

# Same seasons and monthly pulls as snell_vs_peers.py, so they come from the cache.
LEAGUE_SEASONS = {
    2018: ("2018-03-29", "2018-10-01"),
    2019: ("2019-03-20", "2019-09-29"),
    2020: ("2020-07-23", "2020-09-27"),
}

# ---- Assumptions you can change -------------------------------------------
N_BATTERS = 3        # batters affected by the decision (rest of the 6th inning)
K_REG = 300          # regression strength in PA: a pitcher's own numbers count fully
                     # only once he has many more than K_REG plate appearances
WOBA_SCALE = 1.2     # wOBA points -> runs per PA (about 1.2 in 2018-2020)
RUNS_PER_WIN = 10    # at average leverage (LI = 1), 10 runs is about 1 win
SNELL_FALLBACK = 0.314   # used if output/snell_like_starts.csv isn't found
LI_OVERRIDE = None   # set a number to skip the league LI calculation

OUT = Path("output")

# Chart colors (light surface)
C = dict(surface="#fcfcfb", text="#0b0b0b", text2="#52514e", muted="#898781",
         grid="#e1e0d9", axis="#c3c2b7", series="#2a78d6")

WHIFFS = {"swinging_strike", "swinging_strike_blocked", "missed_bunt"}
SWINGS = WHIFFS | {"foul", "foul_tip", "foul_bunt", "hit_into_play",
                   "hit_into_play_no_out", "hit_into_play_score"}
STATE = ["inning", "half", "outs", "bases", "diff"]
LEAGUE_COLS = ["game_pk", "game_type", "pitcher", "at_bat_number", "pitch_number",
               "inning", "inning_topbot", "outs_when_up", "on_1b", "on_2b", "on_3b",
               "bat_score", "fld_score", "events", "woba_value", "woba_denom",
               "estimated_woba_using_speedangle", "delta_home_win_exp"]


# ---------------------------------------------------------------- helpers

def month_chunks(start: str, end: str):
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    while s <= e:
        m_end = min(s + pd.offsets.MonthEnd(0), e)
        yield s.strftime("%Y-%m-%d"), m_end.strftime("%Y-%m-%d")
        s = m_end + pd.Timedelta(days=1)


def pa_rows(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["woba_denom"] == 1]


def xwoba_values(pa: pd.DataFrame) -> pd.Series:
    """Expected wOBA per PA: Statcast's estimate on batted balls, actual value on K/BB/HBP."""
    est = pa["estimated_woba_using_speedangle"]
    return est.where(est.notna(), pa["woba_value"])


def regress(rate: float, n: int, prior: float, k: int = K_REG) -> float:
    """Shrink a small-sample rate toward a prior: the fewer PA, the closer to the prior."""
    return (rate * n + prior * k) / (n + k)


def appearance_stats(g: pd.DataFrame) -> dict:
    pa = pa_rows(g)
    ff = g[g["pitch_type"] == "FF"]
    swings = g["description"].isin(SWINGS).sum()
    spin = ff["release_spin_rate"].dropna()
    return {
        "pitches": len(g),
        "PA": len(pa),
        "4-seam_velo": round(ff["release_speed"].mean(), 1) if len(ff) else np.nan,
        "4-seam_spin": round(spin.mean(), 0) if len(spin) else np.nan,
        "whiff%": round(g["description"].isin(WHIFFS).sum() / swings, 3) if swings else np.nan,
        "wOBA": round(pa["woba_value"].mean(), 3) if len(pa) else np.nan,
        "xwOBA": round(xwoba_values(pa).mean(), 3) if len(pa) else np.nan,
    }


def state_key(pa: pd.DataFrame) -> pd.DataFrame:
    """Game state at the start of a PA: inning, half, outs, runners, batting team's score margin."""
    bases = (pa["on_1b"].notna().astype(int).astype(str)
             + pa["on_2b"].notna().astype(int).astype(str)
             + pa["on_3b"].notna().astype(int).astype(str))
    return pd.DataFrame({
        "inning": pa["inning"].clip(upper=9).astype(int).values,
        "half": pa["inning_topbot"].values,
        "outs": pa["outs_when_up"].astype(int).values,
        "bases": bases.values,
        "diff": (pa["bat_score"] - pa["fld_score"]).clip(-4, 4).astype(int).values,
    })


def pa_table(df: pd.DataFrame) -> pd.DataFrame:
    """One row per PA: game state from its first pitch, win-probability change over the
    whole PA. Statcast records the change per pitch, so it is summed (this catches wild
    pitches and other events in the middle of a PA)."""
    df = df.sort_values(["game_pk", "at_bat_number", "pitch_number"])
    start = df.drop_duplicates(["game_pk", "at_bat_number"], keep="first")
    change = (df.groupby(["game_pk", "at_bat_number"])["delta_home_win_exp"]
                .sum(min_count=1).reset_index())
    return (start.drop(columns="delta_home_win_exp")
                 .merge(change, on=["game_pk", "at_bat_number"])
                 .reset_index(drop=True))


# ---------------------------------------------------------------- Game 6

def load_game6():
    game = statcast(GAME_DATE, GAME_DATE)
    game = (game[game["game_type"] == "W"]
            .sort_values(["at_bat_number", "pitch_number"]).reset_index(drop=True))
    snell = game[game["player_name"] == SNELL_NAME]
    if snell.empty:
        raise SystemExit("Couldn't find Snell in the Game 6 data.")
    half = snell["inning_topbot"].iloc[0]
    after = game[(game["inning_topbot"] == half)
                 & (game["at_bat_number"] > snell["at_bat_number"].max())]
    decision = after.iloc[0]          # first pitch after Snell left
    rays = decision["home_team"] if half == "Top" else decision["away_team"]
    return game, int(decision["pitcher"]), decision["player_name"], decision, rays, half


# ---------------------------------------------------------------- 1. reliever

def reliever_state(rel_id: int, rel_name: str) -> dict:
    print(f"\n=== 1. {rel_name}: state going into Game 6 ===")
    reg = statcast_pitcher(*RELIEVER_REG, rel_id)
    reg = reg[reg["game_type"] == "R"].copy()
    reg["year"] = pd.to_datetime(reg["game_date"]).dt.year

    post = statcast(POST_START, POST_END)
    post = post[post["pitcher"] == rel_id].copy()
    post["date"] = pd.to_datetime(post["game_date"])
    pre = post[post["date"] < pd.Timestamp(GAME_DATE)]
    g6 = post[post["date"] == pd.Timestamp(GAME_DATE)]

    rows = []
    for year, g in reg.groupby("year"):
        rows.append({"when": f"{year} regular season",
                     "games": g["game_pk"].nunique(), **appearance_stats(g)})
    prev = None
    for (date, _), g in pre.groupby(["date", "game_pk"]):
        rest = "" if prev is None else f"{(date - prev).days - 1}"
        rows.append({"when": f"{date.date()} ({g['game_type'].iloc[0]})", "games": 1,
                     "days_rest": rest, **appearance_stats(g)})
        prev = date
    rows.append({"when": "Postseason before Game 6", "games": pre["game_pk"].nunique(),
                 **appearance_stats(pre)})
    if not g6.empty:
        rows.append({"when": "Game 6 (after the decision, not used)", "games": 1,
                     **appearance_stats(g6)})
    table = pd.DataFrame(rows)
    print(table.fillna("").to_string(index=False))
    table.to_csv(OUT / "reliever_state.csv", index=False)

    reg20 = reg[reg["year"] == 2020]
    v_reg = reg20.loc[reg20["pitch_type"] == "FF", "release_speed"].mean()
    v_pre = pre.loc[pre["pitch_type"] == "FF", "release_speed"].mean()
    days = pre["date"].dt.normalize().drop_duplicates().sort_values()
    b2b = int((days.diff().dt.days == 1).sum())
    print(f"\n4-seam velocity: {v_reg:.1f} mph in the 2020 regular season, "
          f"{v_pre:.1f} mph in the postseason before Game 6 ({v_pre - v_reg:+.1f}).")
    print(f"Workload before Game 6: {len(days)} appearances in "
          f"{(pd.Timestamp(GAME_DATE) - pd.Timestamp(POST_START)).days} days, "
          f"{b2b} on back-to-back days, {len(pre)} pitches.")

    reg_pa, pre_pa = pa_rows(reg), pa_rows(pre)
    return {"reg_x": xwoba_values(reg_pa).mean(), "reg_n": len(reg_pa),
            "pre_x": xwoba_values(pre_pa).mean(), "pre_n": len(pre_pa)}


# ---------------------------------------------------------------- league pieces

def league_baselines() -> tuple:
    """League reliever xwOBA, and an LI table built from 2018-2020 regular-season PAs."""
    print("\nBuilding league baselines (reliever xwOBA, leverage by game state) ...")
    rel_n, rel_x = 0, 0.0
    li_parts = []
    for s, e in [c for v in LEAGUE_SEASONS.values() for c in month_chunks(*v)]:
        df = statcast(s, e)
        if df is None or df.empty:
            continue
        df = df[df["game_type"] == "R"]
        df = df[[c for c in LEAGUE_COLS if c in df.columns]]
        df = df.sort_values(["game_pk", "at_bat_number", "pitch_number"])

        starters = (df.drop_duplicates(["game_pk", "inning_topbot"])
                      [["game_pk", "inning_topbot", "pitcher"]]
                      .rename(columns={"pitcher": "starter"}))
        d2 = df.merge(starters, on=["game_pk", "inning_topbot"])
        rp = pa_rows(d2[d2["pitcher"] != d2["starter"]])
        rel_n += len(rp)
        rel_x += xwoba_values(rp).sum()

        if "delta_home_win_exp" in df.columns and df["delta_home_win_exp"].notna().any():
            pa = pa_table(df)
            pa = pa[(pa["inning"] <= 9) & pa["delta_home_win_exp"].notna()]
            key = state_key(pa)
            key["abs_dwp"] = pa["delta_home_win_exp"].abs().values
            li_parts.append(key.groupby(STATE)["abs_dwp"].agg(["size", "sum"]).reset_index())

    li = None
    if li_parts:
        li = pd.concat(li_parts).groupby(STATE)[["size", "sum"]].sum().reset_index()
        li["LI"] = (li["sum"] / li["size"]) / (li["sum"].sum() / li["size"].sum())
        li.to_csv(OUT / "leverage_by_state.csv", index=False)
    return rel_x / rel_n, li


# ---------------------------------------------------------------- 3. stakes

def win_probability(game: pd.DataFrame, rays: str):
    """Rays win probability before each PA, or None if the data has no win expectancy."""
    if "home_win_exp" not in game.columns or game["home_win_exp"].isna().all():
        return None
    pa = pa_table(game)
    wp = pa["home_win_exp"].astype(float).values
    d = pa["delta_home_win_exp"].astype(float).fillna(0).values
    # Is home_win_exp recorded before or after each PA? Pick the one that chains up.
    err_before = np.nanmean(np.abs(wp[:-1] + d[:-1] - wp[1:]))
    err_after = np.nanmean(np.abs(wp[:-1] + d[1:] - wp[1:]))
    home_before = wp if err_before <= err_after else wp - d
    rays_home = pa["home_team"].iloc[0] == rays
    pa["rays_wp_before"] = home_before if rays_home else 1 - home_before
    pa["rays_dwp"] = d if rays_home else -d
    return pa


def stakes(game, decision, rays, half, li_table):
    print("\n=== 3. What was at stake when Snell was pulled ===")
    out = {"wp": np.nan, "li": np.nan, "swing": np.nan}
    pa = win_probability(game, rays)
    if pa is not None:
        row = pa[pa["at_bat_number"] == decision["at_bat_number"]].iloc[0]
        inn = int(decision["inning"])
        rest = pa[(pa["inning"] == inn) & (pa["inning_topbot"] == half)
                  & (pa["at_bat_number"] >= decision["at_bat_number"])]
        out["wp"], out["swing"] = row["rays_wp_before"], rest["rays_dwp"].sum()
        print(f"{rays} win probability when Snell left: {out['wp']:.1%}")
        print(f"Change over the rest of that half-inning (inning {inn}): {out['swing']:+.1%}")
    else:
        print("This data has no win-expectancy columns; skipping the win-probability part.")

    if LI_OVERRIDE is not None:
        out["li"] = LI_OVERRIDE
        print(f"Leverage index (set by hand): {out['li']:.2f}")
    elif li_table is not None:
        st = state_key(pd.DataFrame([decision]))
        hit = st.merge(li_table, on=STATE)
        if not hit.empty:
            out["li"] = float(hit["LI"].iloc[0])
            s = st.iloc[0]
            print(f"Game state: inning {s['inning']} {s['half'].lower()}, {s['outs']} out, "
                  f"bases {s['bases']} (1st-2nd-3rd), batting team margin {s['diff']:+d}")
            print(f"Leverage index: {out['li']:.2f} (1.0 = average PA, 2.0+ = high leverage; "
                  f"based on {int(hit['size'].iloc[0])} regular-season PAs in this state)")
    if np.isnan(out["li"]):
        print("Couldn't compute LI. Set LI_OVERRIDE at the top of the file "
              "(FanGraphs' play log for this game lists it).")
    print("Note: win probability here is for this game only. Being one loss from elimination")
    print("raises the stakes of the whole game, but doesn't change which pitcher was better.")
    return out, pa


# ---------------------------------------------------------------- 4. decision

def snell_expectation() -> tuple:
    path = OUT / "snell_like_starts.csv"
    if path.exists():
        t = pd.read_csv(path, index_col=0)
        row = t[t.index.str.contains("pitches")]
        if not row.empty and pd.notna(row["3rd_wOBA"].iloc[0]):
            return float(row["3rd_wOBA"].iloc[0]), row.index[0]
    return SNELL_FALLBACK, "default value (snell_vs_peers.py output not found)"


def decision_value(rel_name, rel, league_x, li):
    print("\n=== 2 & 4. Expected wOBA and what the decision was worth ===")
    snell_x, source = snell_expectation()
    print(f"Snell, third time through: {snell_x:.3f}  (from: {source})")
    print(f"League relievers 2018-2020 xwOBA: {league_x:.3f}")

    full = regress(rel["reg_x"], rel["reg_n"], league_x)
    recent = regress(rel["pre_x"], rel["pre_n"], full)
    scenarios = {
        f"Full-strength {rel_name}": (full,
            f"2019-20 regular season ({rel['reg_n']} PA, xwOBA {rel['reg_x']:.3f}), regressed to league"),
        "Recent form": (recent,
            f"postseason before Game 6 ({rel['pre_n']} PA, xwOBA {rel['pre_x']:.3f}), regressed to full-strength"),
        "Pessimistic": (rel["pre_x"], "postseason xwOBA taken at face value"),
    }

    rows = []
    for name, (x, basis) in scenarios.items():
        runs_per_pa = (snell_x - x) / WOBA_SCALE
        dwp = N_BATTERS * runs_per_pa * li / RUNS_PER_WIN if not np.isnan(li) else np.nan
        rows.append({"scenario": name, "reliever_xwOBA": round(x, 3),
                     "gap_vs_Snell": round(snell_x - x, 3),
                     "runs_saved_per_PA": round(runs_per_pa, 3),
                     f"win_prob_change_{N_BATTERS}_PA": f"{dwp:+.1%}" if not np.isnan(dwp) else "n/a",
                     "basis": basis})
    table = pd.DataFrame(rows)
    print(table.drop(columns="basis").to_string(index=False))
    for r in rows:
        print(f"  {r['scenario']}: {r['basis']}")
    table.to_csv(OUT / "decision_value.csv", index=False)

    print(f"\nBreak-even: pulling Snell only helps if the reliever's expected wOBA is below {snell_x:.3f}.")
    print("Positive win_prob_change = pulling Snell raised the Rays' chance to win, by that many points.")
    print(f"Assumes the choice affects {N_BATTERS} batters. Both pitchers face the same hitters,")
    print("so batter quality mostly cancels out (platoon matchups are not modeled).")


# ---------------------------------------------------------------- 5. chart

def chart(pa: pd.DataFrame, decision_ab: int, rays: str):
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("\n(matplotlib not installed, skipping chart)")
        return
    y = np.append(pa["rays_wp_before"].values,
                  pa["rays_wp_before"].iloc[-1] + pa["rays_dwp"].iloc[-1]) * 100
    x = np.arange(len(y))
    i = int(np.flatnonzero(pa["at_bat_number"].values == decision_ab)[0])

    fig, ax = plt.subplots(figsize=(9, 4.5), facecolor=C["surface"])
    ax.set_facecolor(C["surface"])
    ax.step(x, y, where="post", color=C["series"], linewidth=2)
    ax.axvline(i, color=C["muted"], linewidth=1)
    ax.plot(i, y[i], "o", color=C["series"], markersize=8,
            markeredgecolor=C["surface"], markeredgewidth=2, zorder=3)
    # Put the label in the emptier half of the plot so it never sits on the line.
    above = y[i] < 50
    ax.annotate(f"Snell pulled\n{rays} {y[i]:.0f}% to win", (i, y[i]),
                xytext=(-12, 50 if above else -50), textcoords="offset points",
                ha="right", va="bottom" if above else "top", color=C["text"], fontsize=9,
                bbox=dict(boxstyle="round,pad=0.3", facecolor=C["surface"], edgecolor="none"),
                arrowprops=dict(arrowstyle="-", color=C["muted"], linewidth=1))

    firsts = pa.drop_duplicates("inning")
    ax.set_xticks(firsts.index)
    ax.set_xticklabels(firsts["inning"].astype(int))
    ax.set_xlim(0, len(y) - 1)
    ax.set_ylim(0, 100)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_yticklabels(["0%", "25%", "50%", "75%", "100%"])
    ax.grid(axis="y", color=C["grid"], linewidth=1)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(C["axis"])
    ax.tick_params(colors=C["muted"], length=0)
    ax.set_xlabel("Inning", color=C["text2"])
    ax.set_title(f"{rays} win probability, 2020 World Series Game 6",
                 loc="left", color=C["text"], fontsize=12)
    plt.tight_layout()
    plt.savefig(OUT / "game6_win_probability.png", dpi=150, facecolor=C["surface"])
    print(f"\nChart saved to {OUT / 'game6_win_probability.png'}")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    game, rel_id, rel_name, decision, rays, half = load_game6()
    rel = reliever_state(rel_id, rel_name)
    league_x, li_table = league_baselines()
    st, pa = stakes(game, decision, rays, half, li_table)
    decision_value(rel_name, rel, league_x, st["li"])
    if pa is not None:
        chart(pa, int(decision["at_bat_number"]), rays)
