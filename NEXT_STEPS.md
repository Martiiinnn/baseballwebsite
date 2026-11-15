# Next steps (ideas, not started yet)

Recorded Nov 2, 2026.

## 1. Matchup layer for the manager tool
- **Handedness:** let the manager enter the next three hitters and use their splits vs. LHP/RHP
  (`batter_splits` is already in `site_data/coaches.db`).
- **Location (hot zones):** Statcast `zone` (strike zone 1-9, outside 11-14).
  Expected result = sum over zones of (pitcher's share of pitches in the zone × hitter's result in the zone),
  with small samples shrunk toward league average.
- **Pitch type:** hitter's run value per 100 pitches against each pitch type (`delta_run_exp`),
  weighted by the pitcher's pitch mix. Use this rather than OPS by pitch type: OPS is per plate
  appearance, and one plate appearance usually includes several pitch types.
- **Backtest:** build matchups from earlier seasons, predict the next season's plate appearances,
  and compare handedness only vs. + zones vs. + pitch types. Report whether the extra detail actually
  predicts better.

## 2. Second case study: 2026 postseason bullpens, Rays vs. Brewers
Both bullpens were effective but used very differently (MLB.com:
"Rays and Brewers have effective, yet completely different bullpens").
- Starter length: times through the order when pulled; bullpen share of innings
- Leverage index when each reliever entered; how often they had the platoon advantage
- Workload: consecutive days, pitches; velocity and whiff-rate trends through the series
- Story angle: "Six years after Snell" (the Rays and Kevin Cash back in the World Series)

## 3. Data update (needed for #2, useful for #1)
- Extend `build_database.py` to 2024-2026 and use those seasons as baselines.
- Why: the pitch clock and shift limits (2023) and the three-batter minimum for relievers (2020)
  changed run environment and bullpen usage. The three-batter minimum also explains why the tool
  evaluates a pitching change over three hitters.
