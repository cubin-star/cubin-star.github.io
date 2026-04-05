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
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

# ===== CONFIG =====
API_KEY = os.environ.get("API_BASKETBALL_KEY", "")
BASE_URL = "https://v1.basketball.api-sports.io"
DELAY = 0.3
OUTPUT = "baskets.json"

EXCLUDED_COUNTRIES = {"russia", "belarus"}
MIN_GAMES = 6

# Target odds for line selection
SELECTION_ODDS = 1.90   # find the Over line near this odds (aggressive – higher line)
OUTPUT_ODDS = 1.30       # find the safer Over line near this odds (safe – lower line)
ODDS_TOLERANCE = 0.35    # max deviation from target

# Criteria – venue-specific matchup vs. bookmaker line
BOTH_FLOOR_R = 0.95       # oba týmy musí střílet alespoň 95% half-line na svém venue
MIN_HALF_LINE = 80        # minimální half_line – filtruje nízko-skórující ligy (80 = 160 bodů celkem)
EXPECTED_MIN_R = 1.02     # matchup expected (venue splits) musí překročit line o 2%
OFFENSE_VS_LEAGUE_R = 0.93  # offense obou týmů (venue) musí být >= 93% celkového průměru ligy

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
                   out["line"] < sel["line"]:
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
          f"venue offense >= half*{BOTH_FLOOR_R} & >= league/team*{OFFENSE_VS_LEAGUE_R}\n")

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
        return

    # 4. Analyze team stats
    results = []
    print(f"  Analyzing {len(candidates)} candidates...")
    for i, c in enumerate(candidates):
        print(f"  [{i+1}/{len(candidates)}] {c['match'][:45]:.<47s}", end="")
        try:
            home_stats = fetch_team_stats(c["league_id"], c["season"], c["home_id"])
            away_stats = fetch_team_stats(c["league_id"], c["season"], c["away_id"])
            ok, detail, score = meets_criteria(home_stats, away_stats, c["sel_line"])
            if ok:
                print(f" ★ {detail}")
                print(f"       → {c['sel_label']}@{c['sel_odds']} → OUTPUT: {c['out_label']}@{c['out_odds']}")
                kickoff = datetime.fromtimestamp(c["timestamp"], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
                results.append({
                    "league": c["league"],
                    "match": c["match"],
                    "tip": c["out_label"],
                    "odds": c["out_odds"],
                    "date": kickoff,
                    "_score": score,
                })
            else:
                print(f" fail ({detail})")
        except Exception as exc:
            print(f" ERROR: {exc}")

    # 5. Best per league – keep only the top match from each league
    before = len(results)
    best_per_league = {}
    for r in results:
        lg = r["league"]
        if lg not in best_per_league or r["_score"] > best_per_league[lg]["_score"]:
            best_per_league[lg] = r
    results = list(best_per_league.values())
    for r in results:
        r.pop("_score", None)
    if before > len(results):
        print(f"\n  Dedup: {before} → {len(results)} (best per league)")

    # 6. Sort by kickoff time and write output
    results.sort(key=lambda r: r["date"])
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*50}")
    print(f"  Results: {len(results)} match(es) → {OUTPUT}")
    print(f"  API requests: {request_count} / 7500 ({request_count * 100 // 7500}%)")


if __name__ == "__main__":
    main()
