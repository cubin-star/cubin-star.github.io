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
BOTH_FLOOR_R = 0.97       # oba týmy musí střílet alespoň 97% half-line na svém venue
MIN_HALF_LINE = 80        # minimální half_line – filtruje nízko-skórující ligy (80 = 160 bodů celkem)
EXPECTED_MIN_R = 1.05     # matchup expected (venue splits) musí překročit line o 5%
OFFENSE_VS_LEAGUE_R = 0.95  # offense obou týmů (venue) musí být >= 95% celkového průměru ligy

# Enhanced criteria – recent form, H2H, consistency, rest
RECENT_N = 10               # rolling window: last N finished games
RECENT_FLOOR_R = 0.97       # rolling avg total of last N games >= 97% of selection_line
MIN_OVER_HIT_RATE = 0.65    # >= 65% of last N games had total >= safe_line (each team)
MAX_TOTAL_SD = 20.0         # max std dev of game totals – prefer consistent high-scoring
H2H_MIN_GAMES = 2           # min H2H finished games to apply H2H filter
H2H_OVER_R = 0.95           # H2H avg total >= 95% of selection_line
MIN_REST_HOURS = 36          # min hours since last game (36h filters back-to-back)

# Defense leakage – both teams must concede enough (porous defense)
BOTH_CONCEDE_FLOOR_R = 0.95  # oba inkasují ≥ 95% half-line na svém venue
MIN_LINE_GAP = 10.0          # output line must be at least 10 pts below selection line

# 2nd half filter (like football's 2nd-half filter)
MIN_2H_RATIO = 0.47           # 2H bodů musí být ≥ 47% celkových bodů
MIN_2H_BASELINE = 38.0        # minimum 2H baseline (avg per-team scored+conceded in Q3+Q4)

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
            })
        except (ValueError, TypeError):
            pass
    results.sort(key=lambda x: x["timestamp"], reverse=True)
    return results


# ===== ODDS PARSING =====

def find_over_lines(odds_data):
    """Find selection line (odds ≈ 1.80) and output line (odds ≈ 1.45).
    Returns (selection, output) dicts with 'line', 'odd', 'label', 'odd_str'
    or (None, None) if not found."""
    for resp in odds_data:
        for bk in resp.get("bookmakers", []):
            for bet in bk.get("bets", []):
                # Only full-game Over/Under (bet id 4)
                if bet.get("id") != 4:
                    continue

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

                if len(overs) < 2:
                    continue

                sel = min(overs, key=lambda x: abs(x["odd"] - SELECTION_ODDS))
                out = min(overs, key=lambda x: abs(x["odd"] - OUTPUT_ODDS))

                if abs(sel["odd"] - SELECTION_ODDS) <= ODDS_TOLERANCE and \
                   abs(out["odd"] - OUTPUT_ODDS) <= ODDS_TOLERANCE and \
                   out["line"] < sel["line"] and \
                   sel["line"] - out["line"] >= MIN_LINE_GAP:
                    return sel, out

    return None, None


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
        parts.append(f"H2H={h2h_avg:.0f}(n={len(h2h_games)})")
    else:
        n_h2h = len(h2h_games) if h2h_games else 0
        parts.append(f"H2H=N/A(n={n_h2h})")

    return True, " | ".join(parts)


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
          f"SD<={MAX_TOTAL_SD:.0f}, H2H>={H2H_OVER_R}*line, rest>={MIN_REST_HOURS}h, "
          f"2H>={MIN_2H_RATIO:.0%}, 2Hbase>={MIN_2H_BASELINE:.0f}\n")

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

    # 5b. Best per league – keep only the top match from each league
    before = len(results)
    best_per_league = {}
    for r in results:
        lg = r["league"]
        if lg not in best_per_league or r["_score"] > best_per_league[lg]["_score"]:
            best_per_league[lg] = r
    results = list(best_per_league.values())
    for r in results:
        r.pop("_score", None)
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

