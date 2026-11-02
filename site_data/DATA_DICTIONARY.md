# Data dictionary: site_data/coaches.db

Built by build_database.py from Statcast (2018-2020 regular seasons, 2020 postseason).
Counting columns ending in _sum are totals; divide by pa (or the matching count) to get a rate, so any group of rows can be added up correctly.

## starter_games (11,622 rows)

One row per start. early_* = first two times through the order (what the manager knows when deciding); third_* = how the third time through went. Manager page: 'should I pull him?'

Columns: game_pk, game_date, season, game_type, pitcher, pitcher_name, throws, team, opponent, pitches, early_pa, early_k, early_woba_sum, early_xwoba_sum, early_pitches, early_whiffs, early_swings, third_pa, third_k, third_woba_sum, third_woba_sq, third_xwoba_sum, early_k_pct, early_woba, early_whiff_pct, third_woba

## pitcher_games (51,117 rows)

One row per pitcher per game: pitches, results, four-seam velocity and spin, whiff rate, days of rest. Bullpen board and pitching coach page.

Columns: game_pk, game_date, season, game_type, pitcher, pitcher_name, throws, team, opponent, is_starter, first_inning, batters_faced, pitches, runs_while_pitching, ff_velo_n, ff_velo_sum, ff_spin_n, ff_spin_sum, pa, k, bb, h, xwoba_sum, woba_sum, whiffs, swings, bbe, hard_hit, ev_sum, ff_velo, ff_spin, whiff_pct, xwoba, days_rest

## pitcher_splits (2,392 rows)

Each pitcher vs. left- and right-handed batters, 2018-2020 regular seasons.

Columns: pitcher, pitcher_name, throws, batter_side, pa, k, bb, h, xwoba_sum, woba_sum, whiffs, swings, bbe, hard_hit, ev_sum, xwoba, woba, k_pct, bb_pct, whiff_pct, hard_hit_pct, avg_ev

## batter_splits (2,424 rows)

Each hitter vs. left- and right-handed pitchers, 2018-2020 regular seasons.

Columns: batter, bats, pitcher_hand, pa, k, bb, h, xwoba_sum, woba_sum, whiffs, swings, bbe, hard_hit, ev_sum, xwoba, woba, k_pct, bb_pct, whiff_pct, hard_hit_pct, avg_ev

## league_platoon (4 rows)

League averages for each batter-side / pitcher-hand matchup.

Columns: batter_side, pitcher_hand, pa, k, bb, h, xwoba_sum, woba_sum, whiffs, swings, bbe, hard_hit, ev_sum, xwoba, woba, k_pct, bb_pct, whiff_pct, hard_hit_pct, avg_ev

## league_tto (12 rows)

League starters by season and time through the order.

Columns: season, tto, pa, k, bb, h, xwoba_sum, woba_sum, whiffs, swings, bbe, hard_hit, ev_sum, xwoba, woba, k_pct, bb_pct, whiff_pct, hard_hit_pct, avg_ev

## league_reliever (3 rows)

League relievers by season.

Columns: season, pa, k, bb, h, xwoba_sum, woba_sum, whiffs, swings, bbe, hard_hit, ev_sum, xwoba, woba, k_pct, bb_pct, whiff_pct, hard_hit_pct, avg_ev

## leverage_by_state (3,674 rows)

Leverage index (LI) by inning, half, outs, runners (1st-2nd-3rd) and the batting team's score margin (capped at +/-4). 1.0 = average, 2.0+ = high.

Columns: inning, half, outs, bases, diff, n_pa, li

## postseason_pa (4,019 rows)

Every 2020 postseason plate appearance. home_win_exp = home team's win probability before the PA; delta_home_win_exp = change over the whole PA. Game pages.

Columns: game_pk, game_date, game_type, home_team, away_team, inning, inning_topbot, at_bat_number, pitcher, batter, stand, p_throws, outs_when_up, on_1b, on_2b, on_3b, bat_score, fld_score, runs_scored, events, woba_value, xwoba, tto, is_starter, home_win_exp, delta_home_win_exp

## players (1,934 rows)

Player id -> name.

Columns: player_id, name
