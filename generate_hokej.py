#!/usr/bin/env python3
"""
SureBets Hockey Bot – generates hokejs.json
Runs daily at 7:00 UTC via GitHub Actions.

Selection: Over 5.5 odds (1.75–3.00) + Variant A/B criteria
(thresholds scaled ×2.2 from football for hockey goal rates)
Output: Over 4.5 with odds from API

SETUP:
  1. Copy this file to the root of cubin-star/cubin-star.github.io
  2. Copy tools/github-actions-surebets-hokej.yml to .github/workflows/
  3. In repo Settings → Secrets → Actions, add:
     API_HOCKEY_KEY = your API key from api-sports.io
"""

import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

# ===== CONFIG =====
API_KEY = os.environ.get("API_HOCKEY_KEY", "")
BASE_URL = "https://v1.hockey.api-sports.io"
DELAY = 0.3
OUTPUT = "hokejs.json"

MIN_ODDS = 1.75
MAX_ODDS = 3.00

EXCLUDED_COUNTRIES = {"russia", "belarus"}

# Hockey criteria (football thresholds × 2.2 for Over 5.5)
#   Football: scored <1 / >=1.3, conceded >=1.5 / >=1.6
#   Hockey:   scored <2.2 / >=2.9, conceded >=3.3 / >=3.5
SCORED_LOW = 2.2
SCORED_DECENT = 2.9
CONCEDED_HIGH = 3.3
CONCEDED_VERY_HIGH = 3.5

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


# ===== CRITERIA =====

def _sf(val, default=0.0):
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def meets_criteria(home_stats, away_stats):
    """
    Variant A: scored(one < 2.2, other >= 2.9)  + conceded(one >= 3.3, other >= 3.5)
    Variant B: scored(one >= 3.3, other >= 3.5)  + conceded(one < 2.2, other >= 2.9)
    """
    if not home_stats or not away_stats:
        return False, ""

    h_for = _sf(home_stats.get("goals", {}).get("for", {}).get("average", {}).get("all"))
    a_for = _sf(away_stats.get("goals", {}).get("for", {}).get("average", {}).get("all"))
    h_agn = _sf(home_stats.get("goals", {}).get("against", {}).get("average", {}).get("all"))
    a_agn = _sf(away_stats.get("goals", {}).get("against", {}).get("average", {}).get("all"))

    min_for = min(h_for, a_for)
    max_for = max(h_for, a_for)
    min_agn = min(h_agn, a_agn)
    max_agn = max(h_agn, a_agn)

    variant_a = (min_for < SCORED_LOW and max_for >= SCORED_DECENT) and \
                (min_agn >= CONCEDED_HIGH and max_agn >= CONCEDED_VERY_HIGH)
    variant_b = (min_for >= CONCEDED_HIGH and max_for >= CONCEDED_VERY_HIGH) and \
                (min_agn < SCORED_LOW and max_agn >= SCORED_DECENT)

    if variant_a or variant_b:
        tag = "A" if variant_a else "B"
        detail = f"[{tag}] scored {h_for:.1f}/{a_for:.1f}, conceded {h_agn:.1f}/{a_agn:.1f}"
        return True, detail

    return False, ""


def find_odds(odds_data):
    """Find Over 5.5 odds (for selection) and Over 4.5 odds (for output).
    Returns (over55_odd, over45_odd) or (None, None)."""
    for resp in odds_data:
        for bk in resp.get("bookmakers", []):
            over55 = None
            over45 = None
            for bet in bk.get("bets", []):
                # Bet 4 = Over/Under (full game), Bet 52 = Over/Under (Reg Time)
                if bet.get("id") not in (4, 52) and "over/under" not in bet.get("name", "").lower():
                    continue
                # Skip period-specific bets
                if "period" in bet.get("name", "").lower():
                    continue
                for val in bet.get("values", []):
                    v = str(val.get("value", "")).lower()
                    try:
                        odd_val = float(val.get("odd", "0"))
                    except ValueError:
                        continue
                    if v == "over 5.5" and MIN_ODDS <= odd_val <= MAX_ODDS:
                        over55 = str(val.get("odd"))
                    if v == "over 4.5":
                        over45 = str(val.get("odd"))
            if over55 and over45:
                return over55, over45
    return None, None


# ===== MAIN =====

def main():
    if not API_KEY:
        print("API_HOCKEY_KEY not set!")
        return

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    cutoff = now + timedelta(hours=24)

    print("== SureBets Hockey Bot ==")
    print(f"Time: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Select: Over 5.5 odds {MIN_ODDS}–{MAX_ODDS} + Variant A/B → Output: Over 4.5")
    print(f"Thresholds: scored <{SCORED_LOW}/{SCORED_DECENT}+, conceded {CONCEDED_HIGH}+/{CONCEDED_VERY_HIGH}+\n")

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

    # 3. Fetch odds per game, find Over 5.5 candidates
    candidates = []
    print(f"  Fetching odds for {len(filtered)} games...")
    for i, (gid, g) in enumerate(filtered.items()):
        label = f"{g['home']} vs {g['away']}"
        print(f"  [{i+1}/{len(filtered)}] {label[:45]:.<47s}", end="")
        odds_data = fetch_odds(gid)
        over55, over45 = find_odds(odds_data)
        if over55 and over45:
            print(f" O5.5={over55} → O4.5={over45} ✓")
            candidates.append({
                "game_id": gid,
                "league": g["league"],
                "league_id": g["league_id"],
                "season": g["season"],
                "match": f"{g['home']} vs {g['away']}",
                "home_id": g["home_id"],
                "away_id": g["away_id"],
                "odds_55": over55,
                "odds_45": over45,
            })
        else:
            print(" no O5.5+O4.5 in range")

    print(f"\n  {len(candidates)} candidates (Over 5.5 @ {MIN_ODDS}–{MAX_ODDS})\n")

    if not candidates:
        print("No qualifying matches.")
        with open(OUTPUT, "w", encoding="utf-8") as f:
            json.dump([], f)
        return

    # 4. Analyze team stats for candidates
    results = []
    print(f"  Analyzing {len(candidates)} candidates...")
    for i, c in enumerate(candidates):
        print(f"  [{i+1}/{len(candidates)}] {c['match'][:45]:.<47s}", end="")
        home_stats = fetch_team_stats(c["league_id"], c["season"], c["home_id"])
        away_stats = fetch_team_stats(c["league_id"], c["season"], c["away_id"])
        ok, detail = meets_criteria(home_stats, away_stats)
        if ok:
            print(f" ★ {detail} | O5.5={c['odds_55']} → O4.5={c['odds_45']}")
            results.append({
                "league": c["league"],
                "match": c["match"],
                "tip": "Over 4.5",
                "odds": c["odds_45"],
            })
        else:
            print(" fail")

    # 5. Write output
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*50}")
    print(f"  Results: {len(results)} match(es) → {OUTPUT}")
    print(f"  API requests: {request_count} / 7500 ({request_count * 100 // 7500}%)")


if __name__ == "__main__":
    main()
