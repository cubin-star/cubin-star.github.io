#!/usr/bin/env python3
"""
SureBets Hockey Bot v2 – generates hokejs.json
Runs daily at 7:00 UTC via GitHub Actions.

v2 improvements over v1:
  1. Dynamic lines – finds selection (odds ≈ 1.80) & safe (odds ≈ 1.30)
     instead of fixed 5.0/3.5 → opens European leagues
  2. Recent form – rolling avg of last N games + over hit rate
  3. Variant C – both teams offensive (no contrast required)
  4. Head-to-head history
  5. Consistency (std dev filter)
  6. Rest / back-to-back filter
  7. Period-by-period analysis (2nd+3rd period scoring)
  8. Lower MIN_BASELINE (2.20 → opens SHL, Liiga, Extraliga, DEL…)

SETUP:
  1. Copy this file to the root of cubin-star/cubin-star.github.io
  2. Copy tools/github-actions-surebets-hokej.yml to .github/workflows/
  3. In repo Settings → Secrets → Actions, add:
     API_HOCKEY_KEY = your API key from api-sports.io
"""

import json
import math
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

# ===== CONFIG =====
API_KEY = os.environ.get("API_HOCKEY_KEY", "")
BASE_URL = "https://v1.hockey.api-sports.io"
DELAY = 0.3
OUTPUT = "hokejs.json"
OUTPUT_LIVE = "liveh.json"

MIN_GAMES = 5
ODDS_TOLERANCE = 0.30    # max deviation from target odds

EXCLUDED_COUNTRIES = {"russia", "belarus"}

# Dynamic line selection (like basketball bot)
SELECTION_ODDS = 1.80    # find Over line where odds ≈ 1.80 (aggressive)
OUTPUT_ODDS = 1.22       # find safer Over line where odds ≈ 1.22 (safe – bigger cushion)
MIN_SEL_ODDS = 1.40      # min acceptable odds for selection line
MAX_SEL_ODDS = 3.00      # max acceptable odds for selection line
MIN_LINE_GAP = 1.0       # output line must be at least 1.0 below selection line

# Hockey criteria – league-relative (ratios of game baseline)
# Baseline = průměr 4 per-team hodnot (h_for, a_for, h_agn, a_agn)
# → automaticky se přizpůsobí úrovni ligy (AHL ~3.2, SHL ~2.6, Extraliga ~2.7)
BOTH_FLOOR_R = 0.85      # oba alespoň 85% baseline (široký záchyt)
STRONG_MIN_R = 1.10      # "výrazný" tým 110%+ baseline (jasně nad normou ligy)
CONTRAST_MAX_R = 0.95    # protějšek pod 95% baseline (kontrast ≥ 15%)
MIN_BASELINE = 2.10      # sníženo z 2.40 → otevírá SHL, Liiga, DEL, Extraligu
EXPECTED_VS_OUTPUT_R = 1.22  # matchup expected musí překročit OUTPUT line o 22%
MIN_ATTACK = 2.00            # oba týmy musí střílet ≥ 2.0 g/z (žádný "mrtvý" útok)

# Variant C – both teams offensive (no contrast needed, but stricter floor)
BOTH_OFFENSE_R = 1.05    # oba skórují >= 105% baseline (přísnější než 100%)
BOTH_CONCEDE_R = 1.00    # oba inkasují >= 100% baseline (otevřený zápas)

# P2+P3 period filter (like football's 2nd-half filter)
MIN_P23_BASELINE = 0.80  # minimum P2+P3 baseline (avg per-team P2+P3 stat)

# Enhanced criteria – recent form, H2H, consistency, rest
RECENT_N = 10              # rolling window: last N finished games
RECENT_FLOOR_R = 0.97     # rolling avg total of last N games >= 97% of selection_line
MIN_OVER_HIT_RATE = 0.60  # >= 60% of last N games had total >= safe_line (each team)
MAX_TOTAL_SD = 2.3        # max std dev of game totals (tighter = more consistent)
H2H_MIN_GAMES = 2         # min H2H finished games to apply H2H filter
H2H_OVER_R = 0.90         # H2H avg total >= 90% of selection_line
MIN_REST_HOURS = 36        # min hours since last game (filters back-to-back)

# Period-by-period: 2nd+3rd period scoring must be substantial
LATE_PERIOD_MIN_R = 0.55  # goals in P2+P3 must be >= 55% of total (both teams)

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
            "season": g.get("league", {}).get("season", 2025),
            "country": g.get("country", {}).get("name", "?"),
            "timestamp": g.get("timestamp", 0),
        }
    print(f" {len(games)} upcoming")
    return games


def fetch_odds(game_id):
    """Fetch odds for a single game."""
    time.sleep(DELAY)
    data = api_get("odds", {"game": str(game_id)})
    return data.get("response", [])


def fetch_team_stats(league_id, season, team_id):
    """Fetch team statistics (goals scored/conceded averages)."""
    time.sleep(DELAY)
    data = api_get("teams/statistics", {
        "league": str(league_id),
        "season": str(season),
        "team": str(team_id),
    })
    return data.get("response")


def _parse_period(period_val):
    """Parse period score – handles both string '2 - 1' and dict {'home': 2, 'away': 1}."""
    if period_val is None:
        return None, None
    if isinstance(period_val, dict):
        try:
            return int(period_val.get("home", 0) or 0), int(period_val.get("away", 0) or 0)
        except (ValueError, TypeError):
            return None, None
    if isinstance(period_val, str) and "-" in period_val:
        parts = period_val.split("-")
        try:
            return int(parts[0].strip()), int(parts[1].strip())
        except (ValueError, IndexError):
            return None, None
    return None, None


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
        if status not in ("FT", "AOT", "AP"):
            continue
        scores = g.get("scores", {})
        h_total = scores.get("home")
        a_total = scores.get("away")
        if h_total is None or a_total is None:
            continue
        # Period scores for period-by-period analysis
        periods = g.get("periods", {}) or {}
        try:
            entry = {
                "timestamp": g.get("timestamp", 0),
                "home_id": g.get("teams", {}).get("home", {}).get("id", 0),
                "away_id": g.get("teams", {}).get("away", {}).get("id", 0),
                "home_total": int(h_total),
                "away_total": int(a_total),
                "total": int(h_total) + int(a_total),
            }
            # Period data – hockey API returns strings like "2 - 1"
            p1_str = periods.get("first")
            p2_str = periods.get("second")
            p3_str = periods.get("third")
            if p1_str and p2_str and p3_str:
                p1h, p1a = _parse_period(p1_str)
                p2h, p2a = _parse_period(p2_str)
                p3h, p3a = _parse_period(p3_str)
                if all(v is not None for v in (p1h, p1a, p2h, p2a, p3h, p3a)):
                    entry["p1_total"] = p1h + p1a
                    entry["p23_total"] = p2h + p2a + p3h + p3a
                    entry["p23_home"] = p2h + p3h
                    entry["p23_away"] = p2a + p3a
                    # Regulation total (3 periods only, no OT) – for fair P2+P3 ratio
                    entry["reg_total"] = p1h + p1a + p2h + p2a + p3h + p3a
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
        if status not in ("FT", "AOT", "AP"):
            continue
        scores = g.get("scores", {})
        h_total = scores.get("home")
        a_total = scores.get("away")
        if h_total is None or a_total is None:
            continue
        try:
            results.append({
                "timestamp": g.get("timestamp", 0),
                "total": int(h_total) + int(a_total),
            })
        except (ValueError, TypeError):
            pass
    results.sort(key=lambda x: x["timestamp"], reverse=True)
    return results


# ===== ODDS PARSING =====

def find_over_lines(odds_data):
    """Dynamic line discovery: find selection line (odds ≈ 1.80) and output line (odds ≈ 1.30).
    Works for any league – no fixed line numbers.
    Returns (selection, output) dicts with 'line', 'odd', 'label', 'odd_str'
    or (None, None) if not found."""
    for resp in odds_data:
        for bk in resp.get("bookmakers", []):
            for bet in bk.get("bets", []):
                # Bet 4 = Over/Under (full game), Bet 52 = Over/Under (Reg Time)
                if bet.get("id") not in (4, 52) and "over/under" not in bet.get("name", "").lower():
                    continue
                # Skip period-specific bets
                if "period" in bet.get("name", "").lower():
                    continue

                overs = []
                for val in bet.get("values", []):
                    v = str(val.get("value", ""))
                    if not v.lower().startswith("over"):
                        continue
                    try:
                        line = float(v.split()[-1])
                        # Only accept .5 lines (3.5, 4.5, 5.5, etc.)
                        if line % 1 != 0.5:
                            continue
                        odd = float(val.get("odd", "0"))
                        overs.append({
                            "line": line,
                            "odd": odd,
                            "label": v,
                            "odd_str": str(val.get("odd")),
                        })
                    except (ValueError, IndexError):
                        pass

                if len(overs) < 2:
                    continue

                # Find selection line (odds closest to SELECTION_ODDS)
                sel = min(overs, key=lambda x: abs(x["odd"] - SELECTION_ODDS))
                # Find output/safe line (odds closest to OUTPUT_ODDS)
                out = min(overs, key=lambda x: abs(x["odd"] - OUTPUT_ODDS))

                if (abs(sel["odd"] - SELECTION_ODDS) <= ODDS_TOLERANCE
                        and abs(out["odd"] - OUTPUT_ODDS) <= ODDS_TOLERANCE
                        and MIN_SEL_ODDS <= sel["odd"] <= MAX_SEL_ODDS
                        and out["line"] < sel["line"]
                        and sel["line"] - out["line"] >= MIN_LINE_GAP):
                    return sel, out

    return None, None


# ===== CRITERIA =====

def _sf(val, default=0.0):
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def meets_criteria(home_stats, away_stats, selection_line, output_line):
    """
    League-relative hockey criteria (home/away split) + expected total vs line.
    Baseline = avg of h_for, a_for, h_agn, a_agn → adapts to any league.
    A) oba conceded >= FLOOR_R * base + ofenzivní kontrast
    B) oba scored  >= FLOOR_R * base + defenzivní kontrast
    C) oba skórují i inkasují >= 100% baseline (otevřený zápas, žádný kontrast)
    D) expected >= 95% selection line (balanced high-scoring, no pattern needed)
    + expected total must exceed output_line * EXPECTED_VS_OUTPUT_R
    """
    if not home_stats or not away_stats:
        return False, "", 0.0

    h_played = int(_sf(home_stats.get("games", {}).get("played", {}).get("all", 0)))
    a_played = int(_sf(away_stats.get("games", {}).get("played", {}).get("all", 0)))
    if h_played < MIN_GAMES or a_played < MIN_GAMES:
        return False, f"few games: {h_played}/{a_played}", 0.0

    # Home team → home split, Away team → away split
    h_for = _sf(home_stats.get("goals", {}).get("for", {}).get("average", {}).get("home"))
    a_for = _sf(away_stats.get("goals", {}).get("for", {}).get("average", {}).get("away"))
    h_agn = _sf(home_stats.get("goals", {}).get("against", {}).get("average", {}).get("home"))
    a_agn = _sf(away_stats.get("goals", {}).get("against", {}).get("average", {}).get("away"))

    if h_for == 0 and a_for == 0:
        return False, "no stats", 0.0

    # Oba týmy musí mít minimální útočný výkon – žádný "mrtvý" útok
    if h_for < MIN_ATTACK or a_for < MIN_ATTACK:
        return False, f"weak attack: {h_for:.2f}/{a_for:.2f} (min {MIN_ATTACK})", 0.0

    # Game baseline = průměrná per-team úroveň scoringu v tomto matchupu
    baseline = (h_for + a_for + h_agn + a_agn) / 4
    if baseline == 0:
        return False, "zero baseline", 0.0
    if baseline < MIN_BASELINE:
        return False, f"baseline too low: {baseline:.2f} < {MIN_BASELINE}", 0.0

    # Expected total from venue matchup
    expected = (h_for + a_agn + a_for + h_agn) / 2

    # Check expected vs OUTPUT line (what we actually bet on, not the aggressive selection line)
    min_expected = output_line * EXPECTED_VS_OUTPUT_R
    if expected < min_expected:
        return False, (f"expected low: {expected:.1f} < {min_expected:.1f} "
                       f"(out={output_line:.1f}×{EXPECTED_VS_OUTPUT_R})"), 0.0

    both_floor = baseline * BOTH_FLOOR_R
    strong_min = baseline * STRONG_MIN_R
    contrast_max = baseline * CONTRAST_MAX_R

    # A) oba inkasují >= floor + ofenzivní kontrast (jeden >= strong, druhý < contrast)
    variant_a = (
        h_agn >= both_floor and a_agn >= both_floor
        and ((h_for >= strong_min and a_for < contrast_max)
             or (a_for >= strong_min and h_for < contrast_max))
    )

    # B) oba střílí >= floor + defenzivní kontrast (jeden >= strong, druhý < contrast)
    variant_b = (
        h_for >= both_floor and a_for >= both_floor
        and ((h_agn >= strong_min and a_agn < contrast_max)
             or (a_agn >= strong_min and h_agn < contrast_max))
    )

    # C) oba skórují i inkasují nadprůměrně → otevřený zápas (žádný kontrast nutný)
    offense_floor = baseline * BOTH_OFFENSE_R
    concede_floor = baseline * BOTH_CONCEDE_R
    variant_c = (
        h_for >= offense_floor and a_for >= offense_floor
        and h_agn >= concede_floor and a_agn >= concede_floor
    )

    if variant_a or variant_b or variant_c:
        if variant_a:
            tag = "A"
        elif variant_b:
            tag = "B"
        else:
            tag = "C"
        score = expected / selection_line if selection_line > 0 else 0.0
        detail = (f"[{tag}] scored {h_for:.2f}/{a_for:.2f}, conceded {h_agn:.2f}/{a_agn:.2f} "
                  f"(base={baseline:.2f}, exp={expected:.1f}, line={selection_line:.1f}, "
                  f"ratio={score:.3f})")
        return True, detail, score

    return False, (f"no variant | scored {h_for:.2f}/{a_for:.2f}, "
                   f"conceded {h_agn:.2f}/{a_agn:.2f} (base={baseline:.2f}, "
                   f"exp={expected:.1f})"), 0.0


def analyze_recent_form(games, team_id, n=RECENT_N):
    """Analyze last N finished games for a team.
    Returns dict with avg_total, totals, std_dev, last_game_ts, n_games,
    and period-by-period stats.
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

    # Period-by-period: calculate ratio of P2+P3 goals to regulation total (no OT)
    p23_games = [g for g in last_n if "p23_total" in g and g.get("reg_total", g["total"]) > 0]
    if p23_games:
        p23_ratios = [g["p23_total"] / g.get("reg_total", g["total"]) for g in p23_games]
        avg_p23_ratio = sum(p23_ratios) / len(p23_ratios)
        # Team-specific late scoring (P2+P3)
        team_p23 = []
        for g in p23_games:
            if g["home_id"] == team_id:
                team_p23.append(g.get("p23_home", 0))
            else:
                team_p23.append(g.get("p23_away", 0))
        avg_team_p23 = sum(team_p23) / len(team_p23) if team_p23 else 0
        # P2+P3 conceded (opponent's P2+P3 goals) – for contrast filter
        team_p23_conceded = []
        for g in p23_games:
            if g["home_id"] == team_id:
                team_p23_conceded.append(g.get("p23_away", 0))
            else:
                team_p23_conceded.append(g.get("p23_home", 0))
        avg_team_p23_conceded = sum(team_p23_conceded) / len(team_p23_conceded) if team_p23_conceded else 0
    else:
        avg_p23_ratio = None
        avg_team_p23 = None
        avg_team_p23_conceded = None

    return {
        "avg_total": avg_total,
        "avg_team_pts": avg_team_pts,
        "totals": totals,
        "std_dev": std_dev,
        "last_game_ts": last_ts,
        "n_games": len(last_n),
        "avg_p23_ratio": avg_p23_ratio,
        "avg_team_p23": avg_team_p23,
        "avg_team_p23_conceded": avg_team_p23_conceded,
    }


def meets_enhanced_criteria(home_form, away_form, h2h_games,
                            selection_line, safe_line, game_ts):
    """Enhanced criteria – recent form, H2H, consistency, rest, periods.

    1. Recent form: avg total of last N games >= RECENT_FLOOR_R * selection_line
    2. Over hit rate: >= MIN_OVER_HIT_RATE of last N games had total >= safe_line
    3. Consistency: std_dev <= MAX_TOTAL_SD (both teams)
    4. H2H: if >= H2H_MIN_GAMES, avg total >= H2H_OVER_R * selection_line
    5. Rest: both teams rested >= MIN_REST_HOURS
    6. Period analysis: P2+P3 goals >= LATE_PERIOD_MIN_R of total

    Returns (ok, detail_string).
    """
    parts = []

    # --- Checks 1-3, 5-6: Recent form (both teams must have data) ---
    if home_form and away_form:
        # Check 1: Rolling average total
        min_avg = selection_line * RECENT_FLOOR_R
        h_avg = home_form["avg_total"]
        a_avg = away_form["avg_total"]
        if h_avg < min_avg or a_avg < min_avg:
            return False, (f"recent avg low: {h_avg:.1f}/{a_avg:.1f} "
                           f"(min {min_avg:.1f}={RECENT_FLOOR_R}×{selection_line:.1f})")

        # Check 2: Over hit rate (against safe line)
        h_over = sum(1 for t in home_form["totals"] if t >= safe_line) / home_form["n_games"]
        a_over = sum(1 for t in away_form["totals"] if t >= safe_line) / away_form["n_games"]
        if h_over < MIN_OVER_HIT_RATE or a_over < MIN_OVER_HIT_RATE:
            return False, (f"over rate low: {h_over:.0%}/{a_over:.0%} "
                           f"(min {MIN_OVER_HIT_RATE:.0%} @ safe={safe_line:.1f})")

        # Check 3: Consistency (standard deviation)
        h_sd = home_form["std_dev"]
        a_sd = away_form["std_dev"]
        if h_sd > MAX_TOTAL_SD or a_sd > MAX_TOTAL_SD:
            return False, (f"inconsistent: SD {h_sd:.2f}/{a_sd:.2f} "
                           f"(max {MAX_TOTAL_SD:.1f})")

        parts.append(f"form={h_avg:.1f}/{a_avg:.1f} over={h_over:.0%}/{a_over:.0%} "
                     f"SD={h_sd:.2f}/{a_sd:.2f}")

        # Check 5: Rest (back-to-back filter)
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

        # Check 6: Period-by-period (P2+P3 scoring ratio)
        h_p23 = home_form.get("avg_p23_ratio")
        a_p23 = away_form.get("avg_p23_ratio")
        if h_p23 is not None and a_p23 is not None:
            if h_p23 < LATE_PERIOD_MIN_R or a_p23 < LATE_PERIOD_MIN_R:
                return False, (f"weak late periods: P2+P3 ratio {h_p23:.0%}/{a_p23:.0%} "
                               f"(min {LATE_PERIOD_MIN_R:.0%})")
            parts.append(f"P2+P3={h_p23:.0%}/{a_p23:.0%}")

        # Check 7: P2+P3 contrast filter (like football's 2nd-half A/B filter)
        h_p23_scr = home_form.get("avg_team_p23")
        a_p23_scr = away_form.get("avg_team_p23")
        h_p23_con = home_form.get("avg_team_p23_conceded")
        a_p23_con = away_form.get("avg_team_p23_conceded")
        if all(v is not None for v in (h_p23_scr, a_p23_scr, h_p23_con, a_p23_con)):
            p23_base = (h_p23_scr + a_p23_scr + h_p23_con + a_p23_con) / 4
            if p23_base < MIN_P23_BASELINE:
                return False, (f"P2+P3 base low: {p23_base:.2f} < {MIN_P23_BASELINE}")
            p23_floor = p23_base * BOTH_FLOOR_R
            p23_strong = p23_base * STRONG_MIN_R
            p23_contrast = p23_base * CONTRAST_MAX_R
            p23_off = p23_base * BOTH_OFFENSE_R
            p23_cfloor = p23_base * BOTH_CONCEDE_R

            # P2+P3 Varianta A: oba inkasují v P2+P3 >= floor + ofenzivní kontrast
            p23_var_a = (
                h_p23_con >= p23_floor and a_p23_con >= p23_floor
                and ((h_p23_scr >= p23_strong and a_p23_scr < p23_contrast)
                     or (a_p23_scr >= p23_strong and h_p23_scr < p23_contrast))
            )
            # P2+P3 Varianta B: oba střílí v P2+P3 >= floor + defenzivní kontrast
            p23_var_b = (
                h_p23_scr >= p23_floor and a_p23_scr >= p23_floor
                and ((h_p23_con >= p23_strong and a_p23_con < p23_contrast)
                     or (a_p23_con >= p23_strong and h_p23_con < p23_contrast))
            )
            # P2+P3 Varianta C: oba skórují i inkasují nadprůměrně v P2+P3
            p23_var_c = (
                h_p23_scr >= p23_off and a_p23_scr >= p23_off
                and h_p23_con >= p23_cfloor and a_p23_con >= p23_cfloor
            )
            if not (p23_var_a or p23_var_b or p23_var_c):
                return False, (f"P2+P3 contrast fail: scr {h_p23_scr:.2f}/{a_p23_scr:.2f}, "
                               f"con {h_p23_con:.2f}/{a_p23_con:.2f} (P23base={p23_base:.2f})")
            p23_tag = "P-A" if p23_var_a else ("P-B" if p23_var_b else "P-C")
            parts.append(f"{p23_tag}: scr={h_p23_scr:.2f}/{a_p23_scr:.2f} con={h_p23_con:.2f}/{a_p23_con:.2f}")
        else:
            parts.append("P2+P3=N/A")
    else:
        parts.append("form=N/A")

    # --- Check 4: Head-to-Head ---
    if h2h_games and len(h2h_games) >= H2H_MIN_GAMES:
        h2h_avg = sum(g["total"] for g in h2h_games) / len(h2h_games)
        h2h_min = selection_line * H2H_OVER_R
        if h2h_avg < h2h_min:
            return False, (f"H2H avg low: {h2h_avg:.1f} "
                           f"(min {h2h_min:.1f}={H2H_OVER_R}×{selection_line:.1f}, "
                           f"n={len(h2h_games)})")
        parts.append(f"H2H={h2h_avg:.1f}(n={len(h2h_games)})")
    else:
        n_h2h = len(h2h_games) if h2h_games else 0
        parts.append(f"H2H=N/A(n={n_h2h})")

    return True, " | ".join(parts)


# ===== MAIN =====

def main():
    if not API_KEY:
        print("API_HOCKEY_KEY not set!")
        return

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    cutoff = now + timedelta(hours=24)

    print("== SureBets Hockey Bot v2 ==")
    print(f"Time: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Dynamic lines: sel@~{SELECTION_ODDS} → safe@~{OUTPUT_ODDS} (tol={ODDS_TOLERANCE})")
    print(f"MIN_BASELINE: {MIN_BASELINE} | EXPECTED_VS_OUTPUT_R: {EXPECTED_VS_OUTPUT_R}")
    print(f"Variants: A(contr offense) B(contr defense) C(both open) + P2+P3 contrast")
    print(f"  A/B: FLOOR={BOTH_FLOOR_R}, STRONG={STRONG_MIN_R}, CONTRAST<{CONTRAST_MAX_R}")
    print(f"  C: offense>={BOTH_OFFENSE_R}×base, concede>={BOTH_CONCEDE_R}×base")
    print(f"Enhanced: form>={RECENT_FLOOR_R}×line, over>={MIN_OVER_HIT_RATE:.0%}@safe, "
          f"SD<={MAX_TOTAL_SD}, H2H>={H2H_OVER_R}×line, rest>={MIN_REST_HOURS}h, "
          f"P2+P3>={LATE_PERIOD_MIN_R:.0%}, P2+P3 contrast(A/B/C)\n")

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

    # 3. Fetch odds – dynamic line discovery
    candidates = []
    print(f"  Fetching odds for {len(filtered)} games...")
    for i, (gid, g) in enumerate(filtered.items()):
        label = f"{g['home']} vs {g['away']}"
        print(f"  [{i+1}/{len(filtered)}] {label[:45]:.<47s}", end="")
        odds_data = fetch_odds(gid)
        sel, out = find_over_lines(odds_data)
        if sel and out:
            print(f" sel={sel['label']}@{sel['odd_str']} → out={out['label']}@{out['odd_str']} ✓")
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

    # 4. Two-phase analysis: basic venue stats → enhanced form+H2H+periods
    results = []
    print(f"  Analyzing {len(candidates)} candidates...")
    for i, c in enumerate(candidates):
        print(f"  [{i+1}/{len(candidates)}] {c['match'][:45]:.<47s}", end="")
        try:
            # Phase 1: Basic venue criteria
            home_stats = fetch_team_stats(c["league_id"], c["season"], c["home_id"])
            away_stats = fetch_team_stats(c["league_id"], c["season"], c["away_id"])
            ok, detail, score = meets_criteria(home_stats, away_stats, c["sel_line"], c["out_line"])
            if not ok:
                print(f" fail ({detail})")
                continue

            # Phase 2: Enhanced criteria (recent form, H2H, consistency, rest, periods)
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

            print(f" ★ {detail}")
            print(f"       enhanced: {detail2}")
            print(f"       → {c['sel_label']}@{c['sel_odds']} → OUTPUT: {c['out_label']}@{c['out_odds']}")
            kickoff = datetime.fromtimestamp(c["timestamp"], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
            results.append({
                "league": c["league"],
                "match": c["match"],
                "tip": c["out_label"],
                "odds": c["out_odds"],
                "date": kickoff,
                "_score": score,
                "_league_id": c["league_id"],
                "_sel_label": c["sel_label"],
                "_sel_odds": c["sel_odds"],
            })
        except Exception as exc:
            print(f" ERROR: {exc}")

    # 5a. Write liveh.json – ALL qualifying matches with PRE-MATCH SELECTION line
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
    print(f"  Live: {len(live_out)} match(es) → {OUTPUT_LIVE}")

    # 5b. Best per league – keep only the top match from each league
    # Use league_id (not name) as key – e.g. Czech & Slovak "Extraliga" are different
    before = len(results)
    best_per_league = {}
    for r in results:
        lg_id = r["_league_id"]
        if lg_id not in best_per_league or r["_score"] > best_per_league[lg_id]["_score"]:
            best_per_league[lg_id] = r
    results = list(best_per_league.values())
    for r in results:
        r.pop("_score", None)
        r.pop("_league_id", None)
        r.pop("_sel_label", None)
        r.pop("_sel_odds", None)
    if before > len(results):
        print(f"\n  Dedup: {before} → {len(results)} (best per league)")

    # 6. Sort by kickoff time and write output
    results.sort(key=lambda r: r["date"])
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*50}")
    print(f"  Results: {len(results)} match(es) → {OUTPUT} + {OUTPUT_LIVE}")
    print(f"  API requests: {request_count} / 7500 ({request_count * 100 // 7500}%)")


if __name__ == "__main__":
    main()
