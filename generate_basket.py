#!/usr/bin/env python3
"""
SureBets Basketball Bot – generates baskets.json
Runs daily at 7:00 UTC via GitHub Actions.

Basketball has no fixed Over line – each game has its own.
1. Find "selection line" (Over where odds ≈ 1.90) – aggressive, high line
2. Derive dynamic Variant A/B thresholds from that line
3. If qualified, output the "safe line" (Over where odds ≈ 1.30) – ~20pt cushion

Thresholds are proportional ratios of half_line (= selection_line / 2),
compressed for basketball's tighter scoring distribution.

SETUP:
  1. Copy this file to the root of cubin-star/cubin-star.github.io
  2. Copy tools/github-actions-surebets-basket.yml to .github/workflows/
  3. In repo Settings → Secrets → Actions, add:
     API_BASKETBALL_KEY = your API key from api-sports.io
"""

import json
import math
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

# ===== CONFIG =====
API_KEY = os.environ.get("API_BASKETBALL_KEY", "")
BASE_URL = "https://v1.basketball.api-sports.io"
DELAY = 0.3
OUTPUT = "baskets.json"
OUTPUT_LIVE = "liveb.json"

EXCLUDED_COUNTRIES = {"russia", "belarus"}
MIN_GAMES = 6

# Target odds for line selection
SELECTION_ODDS = 1.90   # find the Over line near this odds (aggressive – higher line)
OUTPUT_ODDS = 1.22       # find the safer Over line near this odds (safe – bigger cushion)
ODDS_TOLERANCE = 0.30    # max deviation from target

# Criteria – venue-specific matchup vs. bookmaker line
BOTH_FLOOR_R = 0.93       # uvolněno z 0.97 (evro ligy mají 1 tým mírně pod průměrem)
MIN_HALF_LINE = 70        # minimální half_line – sníženo z 80 (otevírá EuroCup, NCAA, atd.)
EXPECTED_MIN_R = 1.02     # uvolněno z 1.05 (NBA top zápasy padaly o 2 body)
OFFENSE_VS_LEAGUE_R = 0.90  # uvolněno z 0.95 (jeden tým může být pod průměrem)

# Enhanced criteria – recent form, H2H, consistency, rest
RECENT_N = 10               # rolling window: last N finished games
RECENT_FLOOR_R = 0.97       # rolling avg total of last N games >= 97% of selection_line
MIN_OVER_HIT_RATE = 0.60    # >= 60% of last N games had total >= safe_line (each team)
MAX_TOTAL_SD = 22.0         # max std dev of game totals – uvolněno z 20 (evro ligy mají vyšší)
H2H_MIN_GAMES = 2           # min H2H finished games to apply H2H filter
H2H_OVER_R = 0.92           # H2H avg total >= 92% of selection_line
MIN_REST_HOURS = 0           # 0 = vypnuto (back-to-back filtr deaktivován)

# Defense leakage – both teams must concede enough (porous defense)
BOTH_CONCEDE_FLOOR_R = 0.90  # uvolněno z 0.95 (lepší obrana neznamená zákaz)
MIN_LINE_GAP = 10.0          # output line must be at least 10 pts below selection line

# 2nd half filter (like football's 2nd-half filter)
MIN_2H_RATIO = 0.47           # 2H bodů musí být ≥ 47% celkových bodů
MIN_2H_BASELINE = 38.0        # minimum 2H baseline (avg per-team scored+conceded in Q3+Q4)

# Pace proxy z Q1 – pomalý start = riziko Under
MIN_Q1_RATIO = 0.22           # avg Q1 score musí být >= 22% selection_line / 4 baseline
                              # pomalé starty (< 22 %) = pravděpodobně low-pace zápas

# Blowout / garbage time filter – velký rozdíl skóre tlačí Q4 dolů
BLOWOUT_MARGIN = 25           # rozdíl > 25 bodů = blowout
MAX_BLOWOUT_RATE = 0.40       # max 40 % posledních H2H smí být blowouty

# Cross-market konfirmace: oba team totaly Over levné = bookmaker vidí oba aktivní
TT_BONUS_ODDS = 1.95          # pokud oba Team Total Over kurzy <= 1.95
TT_BONUS_MULT = 1.15          # multiplikátor skóre při konfirmaci

# Quality score system – pouští jen TOP zápasy splňující víc než jen základ
# Každé splněné kritérium = body. Zápas musí mít >= MIN_QUALITY_SCORE.
MIN_QUALITY_SCORE = 4    # min počet bodů z kvalitního skóre (0-10)
MAX_TIPS_PER_DAY = 3     # globální limit – nejlepších N podle skóre (kvalita>kvantita)

request_count = 0


# ===== API =====

def api_get(endpoint, params):
    global request_count
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{BASE_URL}/{endpoint}?{query}"
    req = urllib.request.Request(url)
    req.add_header("x-apisports-key", API_KEY)
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                request_count += 1
                remaining = resp.headers.get("x-ratelimit-requests-remaining", "?")
                print(f" [{remaining}]", end="")
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5 * attempt)
            else:
                print(f" HTTP{e.code}", end="")
                return {}
        except Exception:
            return {}
    return {}


def fetch_games(date_str):
    print(f"  Games {date_str}...", end="")
    data = api_get("games", {"date": date_str, "timezone": "UTC"})
    games = {}
    for g in data.get("response", []):
        gid = g.get("id")
        if not gid:
            continue
        status = g.get("status", {}).get("short", "")
        if status not in ("NS", "TBD", ""):
            continue
        games[gid] = {
            "home": g.get("teams", {}).get("home", {}).get("name", "?"),
            "away": g.get("teams", {}).get("away", {}).get("name", "?"),
            "home_id": g.get("teams", {}).get("home", {}).get("id", 0),
            "away_id": g.get("teams", {}).get("away", {}).get("id", 0),
            "league": g.get("league", {}).get("name", "?"),
            "league_id": g.get("league", {}).get("id", 0),
            "season": g.get("league", {}).get("season", ""),
            "country": g.get("country", {}).get("name", "?"),
            "timestamp": g.get("timestamp", 0),
        }
    print(f" {len(games)} upcoming")
    return games


def fetch_odds(game_id):
    time.sleep(DELAY)
    data = api_get("odds", {"game": str(game_id)})
    return data.get("response", [])


def fetch_team_stats(league_id, season, team_id):
    time.sleep(DELAY)
    data = api_get("statistics", {
        "league": str(league_id),
        "season": str(season),
        "team": str(team_id),
    })
    return data.get("response")


def fetch_team_games(team_id, league_id, season):
    """Fetch all finished games for a team in given league+season."""
    time.sleep(DELAY)
    data = api_get("games", {
        "team": str(team_id),
        "league": str(league_id),
        "season": str(season),
    })
    finished = []
    for g in data.get("response", []):
        status = g.get("status", {}).get("short", "")
        if status not in ("FT", "AOT"):
            continue
        scores = g.get("scores", {})
        h_total = scores.get("home", {}).get("total")
        a_total = scores.get("away", {}).get("total")
        if h_total is None or a_total is None:
            continue
        try:
            entry = {
                "timestamp": g.get("timestamp", 0),
                "home_id": g.get("teams", {}).get("home", {}).get("id", 0),
                "away_id": g.get("teams", {}).get("away", {}).get("id", 0),
                "home_total": int(h_total),
                "away_total": int(a_total),
                "total": int(h_total) + int(a_total),
                "margin": abs(int(h_total) - int(a_total)),
            }
            # Quarter data for 2nd half (Q3+Q4) analysis
            h_sc = scores.get("home", {})
            a_sc = scores.get("away", {})
            h_q3 = h_sc.get("quarter_3")
            h_q4 = h_sc.get("quarter_4")
            a_q3 = a_sc.get("quarter_3")
            a_q4 = a_sc.get("quarter_4")
            if all(v is not None for v in (h_q3, h_q4, a_q3, a_q4)):
                try:
                    entry["h_sh"] = int(h_q3) + int(h_q4)
                    entry["a_sh"] = int(a_q3) + int(a_q4)
                    entry["total_sh"] = entry["h_sh"] + entry["a_sh"]
                    # Regulation total (4Q, no OT) for fair 2H ratio
                    h_q1 = int(h_sc.get("quarter_1", 0) or 0)
                    h_q2 = int(h_sc.get("quarter_2", 0) or 0)
                    a_q1 = int(a_sc.get("quarter_1", 0) or 0)
                    a_q2 = int(a_sc.get("quarter_2", 0) or 0)
                    entry["reg_total"] = h_q1 + h_q2 + int(h_q3) + int(h_q4) + a_q1 + a_q2 + int(a_q3) + int(a_q4)
                    # Q1 total (pace proxy)
                    entry["q1_total"] = h_q1 + a_q1
                except (ValueError, TypeError):
                    pass
            finished.append(entry)
        except (ValueError, TypeError):
            pass
    finished.sort(key=lambda x: x["timestamp"], reverse=True)
    return finished


def fetch_h2h(home_id, away_id):
    """Fetch head-to-head history between two teams."""
    time.sleep(DELAY)
    data = api_get("games", {"h2h": f"{home_id}-{away_id}"})
    results = []
    for g in data.get("response", []):
        status = g.get("status", {}).get("short", "")
        if status not in ("FT", "AOT"):
            continue
        scores = g.get("scores", {})
        h_total = scores.get("home", {}).get("total")
        a_total = scores.get("away", {}).get("total")
        if h_total is None or a_total is None:
            continue
        try:
            results.append({
                "timestamp": g.get("timestamp", 0),
                "total": int(h_total) + int(a_total),
                "margin": abs(int(h_total) - int(a_total)),
            })
        except (ValueError, TypeError):
            pass
    results.sort(key=lambda x: x["timestamp"], reverse=True)
    return results


# ===== ODDS PARSING =====

def find_over_lines(odds_data):
    """Find selection line (odds ≈ 1.80) and output line (odds ≈ 1.45).
    Returns (selection, output, home_tt_over, away_tt_over) where the team-total
    Over odds (closest to selection_line/2) are returned for cross-market
    confirmation. Team total values are None if not found.
    Returns (None, None, None, None) if main lines not found."""
    sel_result = None
    out_result = None
    home_tt = None
    away_tt = None

    for resp in odds_data:
        for bk in resp.get("bookmakers", []):
            for bet in bk.get("bets", []):
                bet_id = bet.get("id")
                bet_name = bet.get("name", "").lower()

                # --- Cross-market: Home/Away Team Total ---
                # Hledáme bet, kde název obsahuje "team total" + "home"/"away"
                if "team total" in bet_name or ("total" in bet_name and ("home" in bet_name or "away" in bet_name)):
                    is_home = "home" in bet_name
                    is_away = "away" in bet_name
                    if is_home or is_away:
                        # Vezmeme nejnižší Over kurz (nejvíc preferovaný bookmakerem)
                        for val in bet.get("values", []):
                            v = str(val.get("value", ""))
                            if not v.lower().startswith("over"):
                                continue
                            try:
                                odd = float(val.get("odd", "0"))
                                if is_home and (home_tt is None or odd < home_tt):
                                    home_tt = odd
                                if is_away and (away_tt is None or odd < away_tt):
                                    away_tt = odd
                            except (ValueError, TypeError):
                                pass

                # --- Main: Over/Under full game (bet id 4) ---
                if (sel_result is None or out_result is None) and bet_id == 4:
                    overs = []
                    for val in bet.get("values", []):
                        v = str(val.get("value", ""))
                        if not v.lower().startswith("over"):
                            continue
                        try:
                            line = float(v.split()[-1])
                            odd = float(val.get("odd", "0"))
                            overs.append({
                                "line": line,
                                "odd": odd,
                                "label": v,
                                "odd_str": str(val.get("odd")),
                            })
                        except (ValueError, IndexError):
                            pass

                    if len(overs) >= 2:
                        sel = min(overs, key=lambda x: abs(x["odd"] - SELECTION_ODDS))
                        out = min(overs, key=lambda x: abs(x["odd"] - OUTPUT_ODDS))

                        if abs(sel["odd"] - SELECTION_ODDS) <= ODDS_TOLERANCE and \
                           abs(out["odd"] - OUTPUT_ODDS) <= ODDS_TOLERANCE and \
                           out["line"] < sel["line"] and \
                           sel["line"] - out["line"] >= MIN_LINE_GAP:
                            sel_result = sel
                            out_result = out

    return sel_result, out_result, home_tt, away_tt


# ===== CRITERIA =====

def _sf(val, default=0.0):
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def meets_criteria(home_stats, away_stats, selection_line):
    """Basketball criteria – venue-specific matchup vs. bookmaker line.

    Uses home/away splits to calculate matchup-specific expected total,
    then checks if it meaningfully exceeds the bookmaker's selection line.
    This finds matches where the bookmaker underpriced the Over.

    1. Overall stats → league baseline
    2. Home/away venue splits → matchup expected total
    3. Both teams must score >= BOTH_FLOOR_R * half_line at their venue
    4. Both teams' venue offense >= league avg per team * OFFENSE_VS_LEAGUE_R
    5. Matchup expected must exceed selection_line * EXPECTED_MIN_R
    6. Score = expected / selection_line (ranking)
    """
    if not home_stats or not away_stats:
        return False, "", 0.0

    h_played = int(_sf(home_stats.get("games", {}).get("played", {}).get("all", 0)))
    a_played = int(_sf(away_stats.get("games", {}).get("played", {}).get("all", 0)))
    if h_played < MIN_GAMES or a_played < MIN_GAMES:
        return False, f"few games: {h_played}/{a_played}", 0.0

    # Overall stats (league baseline)
    h_for = _sf(home_stats.get("points", {}).get("for", {}).get("average", {}).get("all"))
    a_for = _sf(away_stats.get("points", {}).get("for", {}).get("average", {}).get("all"))
    h_agn = _sf(home_stats.get("points", {}).get("against", {}).get("average", {}).get("all"))
    a_agn = _sf(away_stats.get("points", {}).get("against", {}).get("average", {}).get("all"))

    if h_for == 0 or a_for == 0:
        return False, "no stats", 0.0

    half = selection_line / 2
    if half < MIN_HALF_LINE:
        return False, f"half_line too low: {half:.0f} < {MIN_HALF_LINE}", 0.0

    # Venue-specific stats: home team AT HOME, away team AWAY
    # Fallback to overall if venue split unavailable (0 = no data)
    h_for_h = _sf(home_stats.get("points", {}).get("for", {}).get("average", {}).get("home")) or h_for
    a_for_a = _sf(away_stats.get("points", {}).get("for", {}).get("average", {}).get("away")) or a_for
    h_agn_h = _sf(home_stats.get("points", {}).get("against", {}).get("average", {}).get("home")) or h_agn
    a_agn_a = _sf(away_stats.get("points", {}).get("against", {}).get("average", {}).get("away")) or a_agn

    # League baseline from overall stats
    league_avg = (h_for + h_agn + a_for + a_agn) / 2
    league_avg_per_team = league_avg / 2

    # Matchup expected from venue splits
    # Home attack at home + away defense leakage away
    # + Away attack away + home defense leakage at home
    expected = (h_for_h + a_agn_a + a_for_a + h_agn_h) / 2

    # --- Check 1: Both teams must have active offense at their venue ---
    min_floor = half * BOTH_FLOOR_R
    if h_for_h < min_floor or a_for_a < min_floor:
        return False, (f"weak venue offense: {h_for_h:.0f}/{a_for_a:.0f} "
                       f"(min {min_floor:.0f})"), 0.0

    # --- Check 2: Both teams venue offense above league average ---
    offense_floor = league_avg_per_team * OFFENSE_VS_LEAGUE_R
    if h_for_h < offense_floor or a_for_a < offense_floor:
        return False, (f"venue offense below league avg: {h_for_h:.0f}/{a_for_a:.0f} "
                       f"(league avg/team={league_avg_per_team:.0f})"), 0.0

    # --- Check 2b: Both teams must have porous defense at their venue ---
    concede_floor = half * BOTH_CONCEDE_FLOOR_R
    if h_agn_h < concede_floor or a_agn_a < concede_floor:
        return False, (f"defense too tight: concede {h_agn_h:.0f}/{a_agn_a:.0f} "
                       f"(min {concede_floor:.0f})"), 0.0

    # --- Check 3: Matchup expected must exceed bookmaker line ---
    min_expected = selection_line * EXPECTED_MIN_R
    if expected < min_expected:
        return False, (f"venue {h_for_h:.0f}+{a_for_a:.0f} conc {h_agn_h:.0f}+{a_agn_a:.0f} "
                       f"(exp={expected:.0f} < line*{EXPECTED_MIN_R}={min_expected:.0f}, "
                       f"league={league_avg:.0f})"), 0.0

    score = expected / selection_line
    detail = (f"venue {h_for_h:.0f}+{a_for_a:.0f} conc {h_agn_h:.0f}+{a_agn_a:.0f} "
              f"(exp={expected:.0f} vs line={selection_line:.0f}, "
              f"league={league_avg:.0f}, ratio={score:.3f})")
    return True, detail, score


def analyze_recent_form(games, team_id, n=RECENT_N):
    """Analyze last N finished games for a team.
    Returns dict with avg_total, totals, std_dev, last_game_ts, n_games.
    Returns None if insufficient data."""
    if not games:
        return None
    last_n = games[:n]
    if len(last_n) < MIN_GAMES:
        return None

    totals = [g["total"] for g in last_n]
    team_points = []
    for g in last_n:
        if g["home_id"] == team_id:
            team_points.append(g["home_total"])
        else:
            team_points.append(g["away_total"])

    avg_total = sum(totals) / len(totals)
    avg_team_pts = sum(team_points) / len(team_points)
    variance = sum((t - avg_total) ** 2 for t in totals) / len(totals)
    std_dev = math.sqrt(variance)
    last_ts = games[0]["timestamp"] if games else 0

    # Q1 pace proxy – avg total bodů v Q1
    q1_games = [g for g in last_n if "q1_total" in g]
    avg_q1 = (sum(g["q1_total"] for g in q1_games) / len(q1_games)) if q1_games else None

    # Blowout rate – kolik % posledních zápasů končilo s rozdílem > BLOWOUT_MARGIN
    blowout_count = sum(1 for g in last_n if g.get("margin", 0) > BLOWOUT_MARGIN)
    blowout_rate = blowout_count / len(last_n) if last_n else 0.0

    # 2nd half (Q3+Q4) analysis – like football's 2nd-half filter
    sh_games = [g for g in last_n if "total_sh" in g and g.get("reg_total", g["total"]) > 0]
    if sh_games:
        sh_ratios = [g["total_sh"] / g.get("reg_total", g["total"]) for g in sh_games]
        avg_sh_ratio = sum(sh_ratios) / len(sh_ratios)
        team_sh = []
        team_sh_conceded = []
        for g in sh_games:
            if g["home_id"] == team_id:
                team_sh.append(g.get("h_sh", 0))
                team_sh_conceded.append(g.get("a_sh", 0))
            else:
                team_sh.append(g.get("a_sh", 0))
                team_sh_conceded.append(g.get("h_sh", 0))
        avg_team_sh = sum(team_sh) / len(team_sh) if team_sh else 0
        avg_team_sh_conceded = sum(team_sh_conceded) / len(team_sh_conceded) if team_sh_conceded else 0
    else:
        avg_sh_ratio = None
        avg_team_sh = None
        avg_team_sh_conceded = None

    return {
        "avg_total": avg_total,
        "avg_team_pts": avg_team_pts,
        "totals": totals,
        "std_dev": std_dev,
        "last_game_ts": last_ts,
        "n_games": len(last_n),
        "avg_q1": avg_q1,
        "blowout_rate": blowout_rate,
        "avg_sh_ratio": avg_sh_ratio,
        "avg_team_sh": avg_team_sh,
        "avg_team_sh_conceded": avg_team_sh_conceded,
    }


def meets_enhanced_criteria(home_form, away_form, h2h_games,
                            selection_line, safe_line, game_ts):
    """Enhanced criteria – recent form, H2H, consistency, rest.

    1. Recent form: avg total of last N games >= RECENT_FLOOR_R * selection_line
    2. Over hit rate: >= MIN_OVER_HIT_RATE of last N games had total >= safe_line
    3. Consistency: std_dev <= MAX_TOTAL_SD (both teams)
    4. H2H: if >= H2H_MIN_GAMES, avg total >= H2H_OVER_R * selection_line
    5. Rest: both teams rested >= MIN_REST_HOURS

    Returns (ok, detail_string).
    """
    parts = []

    # --- Checks 1-3 & 5: Recent form (both teams must have data) ---
    if home_form and away_form:
        # Check 1: Rolling average total
        min_avg = selection_line * RECENT_FLOOR_R
        h_avg = home_form["avg_total"]
        a_avg = away_form["avg_total"]
        if h_avg < min_avg or a_avg < min_avg:
            return False, (f"recent avg low: {h_avg:.0f}/{a_avg:.0f} "
                           f"(min {min_avg:.0f}={RECENT_FLOOR_R}*{selection_line:.0f})")

        # Check 2: Over hit rate (against safe line)
        h_over = sum(1 for t in home_form["totals"] if t >= safe_line) / home_form["n_games"]
        a_over = sum(1 for t in away_form["totals"] if t >= safe_line) / away_form["n_games"]
        if h_over < MIN_OVER_HIT_RATE or a_over < MIN_OVER_HIT_RATE:
            return False, (f"over rate low: {h_over:.0%}/{a_over:.0%} "
                           f"(min {MIN_OVER_HIT_RATE:.0%} @ safe={safe_line:.0f})")

        # Check 3: Consistency (standard deviation)
        h_sd = home_form["std_dev"]
        a_sd = away_form["std_dev"]
        if h_sd > MAX_TOTAL_SD or a_sd > MAX_TOTAL_SD:
            return False, (f"inconsistent: SD {h_sd:.1f}/{a_sd:.1f} "
                           f"(max {MAX_TOTAL_SD:.0f})")

        parts.append(f"form={h_avg:.0f}/{a_avg:.0f} over={h_over:.0%}/{a_over:.0%} "
                     f"SD={h_sd:.0f}/{a_sd:.0f}")

        # Check 3b: Q1 pace proxy – pomalý start = riziko Under
        # Očekávaná Q1 hodnota = selection_line / 4 (jeden quarter z reg času)
        h_q1 = home_form.get("avg_q1")
        a_q1 = away_form.get("avg_q1")
        if h_q1 is not None and a_q1 is not None:
            q1_floor = (selection_line / 4) * (1 + MIN_Q1_RATIO - 0.25)  # baseline Q1 ~ line/4
            # Jednodušší práh: oba týmy musí mít Q1 ratio >= MIN_Q1_RATIO ze selection_line
            h_q1_r = h_q1 / selection_line
            a_q1_r = a_q1 / selection_line
            if h_q1_r < MIN_Q1_RATIO or a_q1_r < MIN_Q1_RATIO:
                return False, (f"slow Q1 start: ratio {h_q1_r:.0%}/{a_q1_r:.0%} "
                               f"(min {MIN_Q1_RATIO:.0%} of {selection_line:.0f})")
            parts.append(f"Q1={h_q1:.0f}/{a_q1:.0f}({h_q1_r:.0%}/{a_q1_r:.0%})")

        # Check 3c: Blowout rate – garbage time tlačí Q4 dolů
        h_blow = home_form.get("blowout_rate", 0.0)
        a_blow = away_form.get("blowout_rate", 0.0)
        if h_blow > MAX_BLOWOUT_RATE or a_blow > MAX_BLOWOUT_RATE:
            return False, (f"blowout-heavy: rate {h_blow:.0%}/{a_blow:.0%} "
                           f"(max {MAX_BLOWOUT_RATE:.0%}, margin>{BLOWOUT_MARGIN})")
        parts.append(f"blow={h_blow:.0%}/{a_blow:.0%}")

        # Check 5: Rest (back-to-back filter) – přeskočeno pokud MIN_REST_HOURS == 0
        if MIN_REST_HOURS > 0:
            game_dt = datetime.fromtimestamp(game_ts, tz=timezone.utc)
            h_last_dt = datetime.fromtimestamp(home_form["last_game_ts"], tz=timezone.utc)
            a_last_dt = datetime.fromtimestamp(away_form["last_game_ts"], tz=timezone.utc)
            h_rest_h = (game_dt - h_last_dt).total_seconds() / 3600
            a_rest_h = (game_dt - a_last_dt).total_seconds() / 3600
            if h_rest_h < MIN_REST_HOURS or a_rest_h < MIN_REST_HOURS:
                return False, (f"B2B fatigue: rest {h_rest_h:.0f}h/{a_rest_h:.0f}h "
                               f"(min {MIN_REST_HOURS}h)")
            parts.append(f"rest={h_rest_h:.0f}h/{a_rest_h:.0f}h")

        # Check 6: 2nd half ratio (Q3+Q4 scoring distribution)
        h_sh_ratio = home_form.get("avg_sh_ratio")
        a_sh_ratio = away_form.get("avg_sh_ratio")
        if h_sh_ratio is not None and a_sh_ratio is not None:
            if h_sh_ratio < MIN_2H_RATIO or a_sh_ratio < MIN_2H_RATIO:
                return False, (f"weak 2nd half: ratio {h_sh_ratio:.0%}/{a_sh_ratio:.0%} "
                               f"(min {MIN_2H_RATIO:.0%})")
            parts.append(f"2H={h_sh_ratio:.0%}/{a_sh_ratio:.0%}")

            # Check 7: 2nd half scoring baseline
            h_sh_scr = home_form.get("avg_team_sh")
            a_sh_scr = away_form.get("avg_team_sh")
            h_sh_con = home_form.get("avg_team_sh_conceded")
            a_sh_con = away_form.get("avg_team_sh_conceded")
            if all(v is not None for v in (h_sh_scr, a_sh_scr, h_sh_con, a_sh_con)):
                base_2h = (h_sh_scr + a_sh_scr + h_sh_con + a_sh_con) / 4
                if base_2h < MIN_2H_BASELINE:
                    return False, (f"2H baseline low: {base_2h:.1f} < {MIN_2H_BASELINE} "
                                   f"(scr {h_sh_scr:.1f}/{a_sh_scr:.1f}, con {h_sh_con:.1f}/{a_sh_con:.1f})")
                parts.append(f"2Hbase={base_2h:.0f}")
            else:
                parts.append("2Hbase=N/A")
        else:
            parts.append("2H=N/A")
    else:
        parts.append("form=N/A")

    # --- Check 4: Head-to-Head ---
    if h2h_games and len(h2h_games) >= H2H_MIN_GAMES:
        h2h_avg = sum(g["total"] for g in h2h_games) / len(h2h_games)
        h2h_min = selection_line * H2H_OVER_R
        if h2h_avg < h2h_min:
            return False, (f"H2H avg low: {h2h_avg:.0f} "
                           f"(min {h2h_min:.0f}={H2H_OVER_R}*{selection_line:.0f}, "
                           f"n={len(h2h_games)})")
        # H2H blowout filtr – pokud byly H2H často blowouty, garbage time tlačí Q4 dolů
        h2h_blow = sum(1 for g in h2h_games if g.get("margin", 0) > BLOWOUT_MARGIN) / len(h2h_games)
        if h2h_blow > MAX_BLOWOUT_RATE:
            return False, (f"H2H blowouts: {h2h_blow:.0%} "
                           f"(max {MAX_BLOWOUT_RATE:.0%}, n={len(h2h_games)})")
        parts.append(f"H2H={h2h_avg:.0f}(n={len(h2h_games)},bl={h2h_blow:.0%})")
    else:
        n_h2h = len(h2h_games) if h2h_games else 0
        parts.append(f"H2H=N/A(n={n_h2h})")

    return True, " | ".join(parts)


def compute_quality_score(home_form, away_form, h2h_games,
                          selection_line, safe_line,
                          h_tt, a_tt):
    """Quality score 0-10 – body za splnění kvalitních (nepovinných) kritérií.
    Čím vyšší, tím lepší zápas. Min. MIN_QUALITY_SCORE pro propuštění do výsledků.

    Bodovník:
    + 2  recent form avg (oba) ≥ 1.10× safe_line — silný recent trend
    + 1  recent form avg (oba) > selection_line
    + 2  oba týmy mají Over hit-rate ≥ 80 % na safe_line
    + 1  oba mají Over hit-rate ≥ 75 %
    + 1  H2H avg ≥ selection_line (silný H2H signál)
    + 1  oba mají std_dev ≤ 60 % maximálního prahu (velmi konzistentní)
    + 1  oba mají 2H ratio ≥ 50 % (dominantní H2 scoring)
    + 1  oba TT Over kurzy ≤ 1.85 (silná konfirmace bookmakerem)
    + 1  oba mají blowout rate ≤ 20 % (málo garbage time)
    """
    pts = 0
    reasons = []

    if home_form and away_form:
        h_avg = home_form["avg_total"]
        a_avg = away_form["avg_total"]

        # 1. Recent form vs line
        if h_avg >= safe_line * 1.10 and a_avg >= safe_line * 1.10:
            pts += 2
            reasons.append("form≥1.10×safe(+2)")
        elif h_avg > selection_line and a_avg > selection_line:
            pts += 1
            reasons.append("form>sel(+1)")

        # 2. Over hit-rate
        h_over = sum(1 for t in home_form["totals"] if t >= safe_line) / home_form["n_games"]
        a_over = sum(1 for t in away_form["totals"] if t >= safe_line) / away_form["n_games"]
        if h_over >= 0.80 and a_over >= 0.80:
            pts += 2
            reasons.append("over≥80%(+2)")
        elif h_over >= 0.75 and a_over >= 0.75:
            pts += 1
            reasons.append("over≥75%(+1)")

        # 3. Konzistence (SD)
        if home_form["std_dev"] <= MAX_TOTAL_SD * 0.6 and away_form["std_dev"] <= MAX_TOTAL_SD * 0.6:
            pts += 1
            reasons.append("SD≤60%(+1)")

        # 4. 2H ratio dominantní pozdní scoring
        h_sh = home_form.get("avg_sh_ratio")
        a_sh = away_form.get("avg_sh_ratio")
        if h_sh is not None and a_sh is not None:
            if h_sh >= 0.50 and a_sh >= 0.50:
                pts += 1
                reasons.append("2H≥50%(+1)")

        # 5. Blowout rate – málo garbage time
        h_blow = home_form.get("blowout_rate", 0.0)
        a_blow = away_form.get("blowout_rate", 0.0)
        if h_blow <= 0.20 and a_blow <= 0.20:
            pts += 1
            reasons.append("blow≤20%(+1)")

    # 6. H2H signál
    if h2h_games and len(h2h_games) >= H2H_MIN_GAMES:
        h2h_avg = sum(g["total"] for g in h2h_games) / len(h2h_games)
        if h2h_avg >= selection_line:
            pts += 1
            reasons.append("H2H≥sel(+1)")

    # 7. TT silná konfirmace
    if h_tt is not None and a_tt is not None and h_tt <= 1.85 and a_tt <= 1.85:
        pts += 1
        reasons.append("TT≤1.85(+1)")

    return pts, ", ".join(reasons) if reasons else "no bonuses"


# ===== MAIN =====

def main():
    if not API_KEY:
        print("API_BASKETBALL_KEY not set!")
        return

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    cutoff = now + timedelta(hours=24)

    print("== SureBets Basketball Bot ==")
    print(f"Time: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Select: Over @ ~{SELECTION_ODDS} odds → Output: Over @ ~{OUTPUT_ODDS} odds")
    print(f"MIN_HALF_LINE: {MIN_HALF_LINE}, BOTH_FLOOR_R: {BOTH_FLOOR_R}, EXPECTED_MIN_R: {EXPECTED_MIN_R}")
    print(f"Criteria: venue_expected >= line*{EXPECTED_MIN_R}, "
          f"venue offense >= half*{BOTH_FLOOR_R} & >= league/team*{OFFENSE_VS_LEAGUE_R}, "
          f"defense concede >= half*{BOTH_CONCEDE_FLOOR_R}")
    print(f"Enhanced: form>={RECENT_FLOOR_R}*line, over>={MIN_OVER_HIT_RATE:.0%}@safe, "
          f"SD<={MAX_TOTAL_SD:.0f}, H2H>={H2H_OVER_R}*line, rest>={MIN_REST_HOURS}h (0=off), "
          f"2H>={MIN_2H_RATIO:.0%}, 2Hbase>={MIN_2H_BASELINE:.0f}, "
          f"Q1>={MIN_Q1_RATIO:.0%}, blow<={MAX_BLOWOUT_RATE:.0%}@>{BLOWOUT_MARGIN}, "
          f"TT bonus@<={TT_BONUS_ODDS} (×{TT_BONUS_MULT})\n")

    # 1. Fetch games
    games_today = fetch_games(today)
    time.sleep(DELAY)
    games_tomorrow = fetch_games(tomorrow)
    all_games = {**games_today, **games_tomorrow}
    print(f"  Total: {len(all_games)} games\n")

    if not all_games:
        print("No games found.")
        with open(OUTPUT, "w", encoding="utf-8") as f:
            json.dump([], f)
        return

    # 2. Filter by time window + country
    filtered = {}
    for gid, g in all_games.items():
        ts = datetime.fromtimestamp(g["timestamp"], tz=timezone.utc)
        country = g.get("country", "").lower()
        if ts >= now and ts <= cutoff and country not in EXCLUDED_COUNTRIES:
            filtered[gid] = g
    print(f"  After filter (24h, no RU/BY): {len(filtered)} games\n")

    # 3. Fetch odds, find selection + output lines
    candidates = []
    print(f"  Fetching odds for {len(filtered)} games...")
    for i, (gid, g) in enumerate(filtered.items()):
        label = f"{g['home']} vs {g['away']}"
        print(f"  [{i+1}/{len(filtered)}] {label[:45]:.<47s}", end="")
        odds_data = fetch_odds(gid)
        sel, out, h_tt, a_tt = find_over_lines(odds_data)
        if sel and out:
            tt_str = ""
            if h_tt is not None or a_tt is not None:
                tt_str = f" TT={h_tt or '-'}/{a_tt or '-'}"
            print(f" sel={sel['label']}@{sel['odd_str']} → out={out['label']}@{out['odd_str']}{tt_str} ✓")
            candidates.append({
                "game_id": gid,
                "league": g["league"],
                "league_id": g["league_id"],
                "season": g["season"],
                "match": f"{g['home']} vs {g['away']}",
                "home_id": g["home_id"],
                "away_id": g["away_id"],
                "sel_line": sel["line"],
                "sel_label": sel["label"],
                "sel_odds": sel["odd_str"],
                "out_line": out["line"],
                "out_label": out["label"],
                "out_odds": out["odd_str"],
                "home_tt_odd": h_tt,
                "away_tt_odd": a_tt,
                "timestamp": g["timestamp"],
            })
        else:
            print(" no lines found")

    print(f"\n  {len(candidates)} candidates\n")

    if not candidates:
        print("No qualifying matches.")
        with open(OUTPUT, "w", encoding="utf-8") as f:
            json.dump([], f)
        with open(OUTPUT_LIVE, "w", encoding="utf-8") as f:
            json.dump([], f)
        return

    # 4. Analyze team stats (two-phase: basic venue → enhanced form+H2H)
    results = []
    print(f"  Analyzing {len(candidates)} candidates...")
    for i, c in enumerate(candidates):
        print(f"  [{i+1}/{len(candidates)}] {c['match'][:45]:.<47s}", end="")
        try:
            # Phase 1: Basic venue criteria
            home_stats = fetch_team_stats(c["league_id"], c["season"], c["home_id"])
            away_stats = fetch_team_stats(c["league_id"], c["season"], c["away_id"])
            ok, detail, score = meets_criteria(home_stats, away_stats, c["sel_line"])
            if not ok:
                print(f" fail ({detail})")
                continue

            # Phase 2: Enhanced criteria (recent form, H2H, consistency, rest)
            print(f" basic✓", end="")
            home_games = fetch_team_games(c["home_id"], c["league_id"], c["season"])
            away_games = fetch_team_games(c["away_id"], c["league_id"], c["season"])
            h2h = fetch_h2h(c["home_id"], c["away_id"])
            home_form = analyze_recent_form(home_games, c["home_id"])
            away_form = analyze_recent_form(away_games, c["away_id"])
            ok2, detail2 = meets_enhanced_criteria(
                home_form, away_form, h2h,
                c["sel_line"], c["out_line"], c["timestamp"])
            if not ok2:
                print(f" enhanced fail ({detail2})")
                continue

            # Cross-market konfirmace: oba Team Total Over kurzy levné = bonus do skóre
            h_tt = c.get("home_tt_odd")
            a_tt = c.get("away_tt_odd")
            if (h_tt is not None and a_tt is not None
                    and h_tt <= TT_BONUS_ODDS and a_tt <= TT_BONUS_ODDS):
                score *= TT_BONUS_MULT
                detail2 += f" | TT={h_tt:.2f}/{a_tt:.2f}★"

            # Quality score – body za kvalitní (nepovinná) kritéria
            qpts, qdetail = compute_quality_score(
                home_form, away_form, h2h,
                c["sel_line"], c["out_line"], h_tt, a_tt)
            if qpts < MIN_QUALITY_SCORE:
                print(f" quality fail (Q={qpts}/{MIN_QUALITY_SCORE}: {qdetail})")
                continue

            print(f" ★ Q={qpts} {detail}")
            print(f"       enhanced: {detail2}")
            print(f"       quality: {qdetail}")
            print(f"       → {c['sel_label']}@{c['sel_odds']} → OUTPUT: {c['out_label']}@{c['out_odds']}")
            kickoff = datetime.fromtimestamp(c["timestamp"], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
            results.append({
                "league": c["league"],
                "match": c["match"],
                "tip": c["out_label"],
                "odds": c["out_odds"],
                "date": kickoff,
                "_score": score,
                "_quality": qpts,
                "_sel_label": c["sel_label"],
                "_sel_odds": c["sel_odds"],
            })
        except Exception as exc:
            print(f" ERROR: {exc}")

    # 5a. Write liveb.json – ALL qualifying matches with PRE-MATCH SELECTION line
    live_results = sorted(results, key=lambda r: r["date"])
    live_out = [{
        "league": r["league"],
        "match": r["match"],
        "tip": r["_sel_label"],
        "odds": r["_sel_odds"],
        "date": r["date"],
    } for r in live_results]
    with open(OUTPUT_LIVE, "w", encoding="utf-8") as f:
        json.dump(live_out, f, indent=2, ensure_ascii=False)
    print(f"  Live: {len(live_out)} match(es) \u2192 {OUTPUT_LIVE}")

    # 5b. Best per league – keep only the top match from each league (by Q+score)
    before = len(results)
    best_per_league = {}
    for r in results:
        lg = r["league"]
        rank = (r["_quality"], r["_score"])
        cur_rank = (best_per_league[lg]["_quality"], best_per_league[lg]["_score"]) if lg in best_per_league else (-1, -1)
        if rank > cur_rank:
            best_per_league[lg] = r
    results = list(best_per_league.values())
    if before > len(results):
        print(f"\n  Dedup: {before} → {len(results)} (best per league by Q+score)")

    # 5c. Globální TOP-N podle (quality, score) – kvalita > kvantita
    if len(results) > MAX_TIPS_PER_DAY:
        results.sort(key=lambda r: (r["_quality"], r["_score"]), reverse=True)
        before_n = len(results)
        results = results[:MAX_TIPS_PER_DAY]
        print(f"  Top-N filter: {before_n} → {len(results)} (max {MAX_TIPS_PER_DAY}/day)")

    # Cleanup interních polí
    for r in results:
        r.pop("_score", None)
        r.pop("_quality", None)
        r.pop("_sel_label", None)
        r.pop("_sel_odds", None)

    # 6. Sort by kickoff time and write output
    results.sort(key=lambda r: r["date"])
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*50}")
    print(f"  Results: {len(results)} match(es) → {OUTPUT} + {OUTPUT_LIVE}")
    print(f"  API requests: {request_count} / 7500 ({request_count * 100 // 7500}%)")


if __name__ == "__main__":
    main()
