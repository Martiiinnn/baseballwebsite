"""
Snell (third time through) vs. the reliever, batter by batter.

Instead of head-to-head history (tiny samples, little predictive value), each
matchup is estimated from larger samples:
  - each Dodgers hitter's 2018-2020 numbers vs. left- and right-handed pitchers
  - each pitcher's 2018-2020 numbers vs. left- and right-handed batters
  - the league average for that handedness matchup
All of these are shrunk toward league norms when the sample is small.

Expected xwOBA for a matchup =
    league average for (batter side, pitcher hand)
  + how much better/worse this hitter is than average vs. that pitcher hand
  + how much better/worse this pitcher is than average vs. that batter side

The pitchers' overall levels come from the earlier scripts:
  Snell third time through   -> output/snell_like_starts.csv  (snell_vs_peers.py)
  reliever, three scenarios  -> output/decision_value.csv     (snell_decision.py)
  leverage of the moment     -> output/leverage_by_state.csv  (snell_decision.py)

Head-to-head history is printed for reference only and is not used.

Setup:  pip install pybaseball pandas
Run:    python snell_matchups.py   (after the other three scripts)
"""
from pathlib import Path

import numpy as np
import pandas as pd
from pybaseball import cache, playerid_reverse_lookup, statcast

cache.enable()

GAME_DATE = "2020-10-27"
SNELL_NAME = "Snell, Blake"
POST_START, POST_END = "2020-09-29", "2020-10-27"
LEAGUE_SEASONS = {                       # same monthly pulls as the earlier scripts (cached)
    2018: ("2018-03-29", "2018-10-01"),
    2019: ("2019-03-20", "2019-09-29"),
    2020: ("2020-07-23", "2020-09-27"),
}

# ---- Assumptions you can change -------------------------------------------
N_BATTERS = 3        # how many upcoming hitters the decision affects
K_OVERALL = 300      # PA needed before a hitter's overall xwOBA counts as much as the league prior
K_SPLIT = 600        # platoon splits are noisy: shrink them hard toward the league platoon gap
WOBA_SCALE = 1.2
RUNS_PER_WIN = 10
SNELL_FALLBACK = 0.314
LI_OVERRIDE = None

OUT = Path("output")

STRIKEOUTS = {"strikeout", "strikeout_double_play"}
WALKS = {"walk"}
WHIFFS = {"swinging_strike", "swinging_strike_blocked", "missed_bunt"}
SWINGS = WHIFFS | {"foul", "foul_tip", "foul_bunt", "hit_into_play",
                   "hit_into_play_no_out", "hit_into_play_score"}
COLS = ["game_pk", "game_type", "game_date", "pitcher", "batter", "stand", "p_throws",
        "events", "description", "type", "launch_speed", "woba_value", "woba_denom",
        "estimated_woba_using_speedangle"]
SUMS = ["PA", "xw", "w", "K", "BB", "whiff", "swing", "bbe", "hard", "ev"]
STATE = ["inning", "half", "outs", "bases", "diff"]


# ---------------------------------------------------------------- helpers

def month_chunks(start: str, end: str):
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    while s <= e:
        m_end = min(s + pd.offsets.MonthEnd(0), e)
        yield s.strftime("%Y-%m-%d"), m_end.strftime("%Y-%m-%d")
        s = m_end + pd.Timedelta(days=1)


def flags(df: pd.DataFrame) -> pd.DataFrame:
    """Per-pitch counters that add up into rate stats."""
    pa = df["woba_denom"] == 1
    est = df["estimated_woba_using_speedangle"]
    bip = (df["type"] == "X") & df["launch_speed"].notna()
    return pd.DataFrame({
        "PA": pa,
        "xw": est.where(est.notna(), df["woba_value"]).where(pa, 0).fillna(0),
        "w": df["woba_value"].where(pa, 0).fillna(0),
        "K": pa & df["events"].isin(STRIKEOUTS),
        "BB": pa & df["events"].isin(WALKS),
        "whiff": df["description"].isin(WHIFFS),
        "swing": df["description"].isin(SWINGS),
        "bbe": bip,
        "hard": bip & (df["launch_speed"] >= 95),
        "ev": df["launch_speed"].where(bip, 0).fillna(0),
    }, index=df.index)


def sums_by(df: pd.DataFrame, by: list) -> pd.DataFrame:
    f = flags(df)
    for c in by:
        f[c] = df[c].values
    return f.groupby(by)[SUMS].sum()


def rates(s) -> dict:
    """Turn summed counters into the six stats we report."""
    pa, sw, bbe = s["PA"], s["swing"], s["bbe"]
    return {
        "PA": int(pa),
        "xwOBA": round(s["xw"] / pa, 3) if pa else np.nan,
        "wOBA": round(s["w"] / pa, 3) if pa else np.nan,
        "K%": round(s["K"] / pa, 3) if pa else np.nan,
        "BB%": round(s["BB"] / pa, 3) if pa else np.nan,
        "whiff%": round(s["whiff"] / sw, 3) if sw else np.nan,
        "hard_hit%": round(s["hard"] / bbe, 3) if bbe else np.nan,
        "avg_EV": round(s["ev"] / bbe, 1) if bbe else np.nan,
    }


def regress(rate, n, prior, k):
    if n == 0 or pd.isna(rate):
        return prior
    return (rate * n + prior * k) / (n + k)


def state_key(pa: pd.DataFrame) -> pd.DataFrame:
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


# ---------------------------------------------------------------- inputs from earlier scripts

def earlier_results():
    snell_x = SNELL_FALLBACK
    p = OUT / "snell_like_starts.csv"
    if p.exists():
        t = pd.read_csv(p, index_col=0)
        row = t[t.index.str.contains("pitches")]
        if not row.empty and pd.notna(row["3rd_wOBA"].iloc[0]):
            snell_x = float(row["3rd_wOBA"].iloc[0])

    p = OUT / "decision_value.csv"
    if not p.exists():
        raise SystemExit("Run snell_decision.py first (output/decision_value.csv is missing).")
    scenarios = pd.read_csv(p)[["scenario", "reliever_xwOBA"]]
    return snell_x, scenarios


def leverage(decision) -> float:
    if LI_OVERRIDE is not None:
        return LI_OVERRIDE
    p = OUT / "leverage_by_state.csv"
    if p.exists():
        li = pd.read_csv(p, dtype={"bases": str})   # keep "001" from turning into 1
        hit = state_key(pd.DataFrame([decision])).merge(li, on=STATE)
        if not hit.empty:
            return float(hit["LI"].iloc[0])
    return np.nan


# ---------------------------------------------------------------- data

def load_game6():
    game = statcast(GAME_DATE, GAME_DATE)
    game = (game[game["game_type"] == "W"]
            .sort_values(["at_bat_number", "pitch_number"]).reset_index(drop=True))
    snell = game[game["player_name"] == SNELL_NAME]
    half = snell["inning_topbot"].iloc[0]
    after = game[(game["inning_topbot"] == half)
                 & (game["at_bat_number"] > snell["at_bat_number"].max())]
    decision = after.iloc[0]
    batters = after.drop_duplicates("at_bat_number")["batter"].drop_duplicates().head(N_BATTERS)
    stands = after.drop_duplicates("batter").set_index("batter")["stand"]
    pitchers = {
        "Snell": (int(snell["pitcher"].iloc[0]), snell["p_throws"].iloc[0]),
        decision["player_name"]: (int(decision["pitcher"]), decision["p_throws"]),
    }
    return decision, list(batters), stands, pitchers


def collect(batter_ids, pitcher_ids):
    """League sums by handedness matchup + all rows involving our players."""
    print("Pulling 2018-2020 regular seasons (from cache if the earlier scripts ran) ...")
    league, ours = [], []
    for s, e in [c for v in LEAGUE_SEASONS.values() for c in month_chunks(*v)]:
        df = statcast(s, e)
        if df is None or df.empty:
            continue
        df = df[df["game_type"] == "R"][COLS]
        league.append(sums_by(df, ["stand", "p_throws"]))
        ours.append(df[df["batter"].isin(batter_ids) | df["pitcher"].isin(pitcher_ids)])
    league = pd.concat(league).groupby(level=[0, 1]).sum()
    ours = pd.concat(ours, ignore_index=True)

    post = statcast(POST_START, POST_END)
    post = post[pd.to_datetime(post["game_date"]) < pd.Timestamp(GAME_DATE)]
    post = post[post["batter"].isin(batter_ids) & post["pitcher"].isin(pitcher_ids)][COLS]
    return league, ours, post


# ---------------------------------------------------------------- main

def main():
    OUT.mkdir(exist_ok=True)
    snell_x, scenarios = earlier_results()
    decision, batters, stands, pitchers = load_game6()
    rel_name = [k for k in pitchers if k != "Snell"][0]
    lk = playerid_reverse_lookup(batters, key_type="mlbam")
    names = {r.key_mlbam: f"{r.name_first.title()} {r.name_last.title()}" for r in lk.itertuples()}

    pitcher_ids = [pid for pid, _ in pitchers.values()]
    league, ours, post = collect(batters, pitcher_ids)

    def L(stand, hand):                      # league xwOBA for a handedness matchup
        return league.loc[(stand, hand), "xw"] / league.loc[(stand, hand), "PA"]

    def L_side(stand):                       # league xwOBA for all batters of one side
        s = league.xs(stand, level="stand").sum()
        return s["xw"] / s["PA"]

    def L_hand(hand):                        # league xwOBA for all pitchers of one hand
        s = league.xs(hand, level="p_throws").sum()
        return s["xw"] / s["PA"]

    # ---- 1. hitters vs. LHP / RHP
    print("\n=== 1. Upcoming hitters, 2018-2020, by pitcher hand ===")
    rows, bat_eff = [], {}
    for b in batters:
        stand = stands[b]
        mine = ours[ours["batter"] == b]
        tot = flags(mine)[SUMS].sum()
        overall = regress(tot["xw"] / tot["PA"] if tot["PA"] else np.nan, tot["PA"],
                          L_side(stand), K_OVERALL)
        for hand in ("L", "R"):
            split = mine[mine["p_throws"] == hand]
            s = flags(split)[SUMS].sum()
            raw_gap = (s["xw"] / s["PA"] - tot["xw"] / tot["PA"]) if s["PA"] and tot["PA"] else np.nan
            gap = regress(raw_gap, s["PA"], L(stand, hand) - L_side(stand), K_SPLIT)
            bat_eff[(b, hand)] = overall + gap - L(stand, hand)
            rows.append({"hitter": names.get(b, b), "bats": stand, "vs": f"{hand}HP",
                         **rates(s), "league_xwOBA": round(L(stand, hand), 3),
                         "estimated_edge": round(bat_eff[(b, hand)], 3)})
    t1 = pd.DataFrame(rows)
    print(t1.to_string(index=False))
    t1.to_csv(OUT / "hitters_by_pitcher_hand.csv", index=False)

    # ---- 2. pitchers vs. LHB / RHB
    print("\n=== 2. Pitchers, 2018-2020, by batter side ===")
    rows, pit_gap = [], {}
    for label, (pid, hand) in pitchers.items():
        mine = ours[ours["pitcher"] == pid]
        tot = flags(mine)[SUMS].sum()
        for side in ("L", "R"):
            split = mine[mine["stand"] == side]
            s = flags(split)[SUMS].sum()
            raw_gap = (s["xw"] / s["PA"] - tot["xw"] / tot["PA"]) if s["PA"] and tot["PA"] else np.nan
            pit_gap[(pid, side)] = regress(raw_gap, s["PA"], L(side, hand) - L_hand(hand), K_SPLIT)
            rows.append({"pitcher": label, "throws": hand, "vs": f"{side}HB", **rates(s),
                         "platoon_gap_used": round(pit_gap[(pid, side)], 3)})
    t2 = pd.DataFrame(rows)
    print(t2.to_string(index=False))
    print("platoon_gap_used: how much worse (+) or better (-) than his own average he is vs. that side,")
    print("after shrinking toward the league's typical platoon gap.")
    t2.to_csv(OUT / "pitchers_by_batter_side.csv", index=False)

    # ---- 3. head-to-head, reference only
    print("\n=== 3. Head-to-head history (reference only, NOT used: samples are tiny) ===")
    h2h = pd.concat([ours[ours["batter"].isin(batters) & ours["pitcher"].isin(pitcher_ids)], post])
    rows = []
    for label, (pid, _) in pitchers.items():
        for b in batters:
            g = h2h[(h2h["pitcher"] == pid) & (h2h["batter"] == b)]
            r = rates(flags(g)[SUMS].sum())
            rows.append({"pitcher": label, "hitter": names.get(b, b),
                         "PA": r["PA"], "xwOBA": r["xwOBA"], "K%": r["K%"]})
    t3 = pd.DataFrame(rows)
    print(t3.fillna("-").to_string(index=False))
    t3.to_csv(OUT / "head_to_head_reference.csv", index=False)

    # ---- 4. expected xwOBA per matchup
    print("\n=== 4. Expected xwOBA, batter by batter ===")
    levels = {"Snell (3rd time through)": ("Snell", snell_x)}
    for r in scenarios.itertuples():
        levels[f"{rel_name}: {r.scenario.replace(rel_name, '').strip() or 'scenario'}"] = (rel_name, r.reliever_xwOBA)

    rows = []
    for b in batters:
        stand = stands[b]
        row = {"hitter": names.get(b, b), "bats": stand}
        for col, (who, level) in levels.items():
            pid, hand = pitchers[who]
            pitcher_edge = (level - L_hand(hand)) + (pit_gap[(pid, stand)] - (L(stand, hand) - L_hand(hand)))
            row[col] = round(L(stand, hand) + bat_eff[(b, hand)] + pitcher_edge, 3)
        rows.append(row)
    t4 = pd.DataFrame(rows)
    print(t4.to_string(index=False))
    t4.to_csv(OUT / "matchup_expectations.csv", index=False)

    # ---- 5. decision value
    li = leverage(decision)
    print(f"\n=== 5. What pulling Snell was worth, using these {N_BATTERS} matchups ===")
    print(f"Leverage index: {li:.2f}" if not np.isnan(li) else
          "Leverage index unknown: run snell_decision.py first or set LI_OVERRIDE.")
    snell_col = "Snell (3rd time through)"
    rows = []
    for col in [c for c in levels if c != snell_col]:
        gap = (t4[snell_col] - t4[col]).sum()           # total xwOBA points saved over the batters
        runs = gap / WOBA_SCALE
        dwp = runs * li / RUNS_PER_WIN if not np.isnan(li) else np.nan
        rows.append({"reliever_scenario": col, "xwOBA_saved_total": round(gap, 3),
                     "runs_saved": round(runs, 3),
                     "win_prob_change": f"{dwp:+.1%}" if not np.isnan(dwp) else "n/a"})
    t5 = pd.DataFrame(rows)
    print(t5.to_string(index=False))
    t5.to_csv(OUT / "decision_value_matchups.csv", index=False)
    print("\nPositive = pulling Snell raised the Rays' chance to win. Compare with")
    print("output/decision_value.csv, which assumed average hitters and ignored handedness.")


if __name__ == "__main__":
    main()
