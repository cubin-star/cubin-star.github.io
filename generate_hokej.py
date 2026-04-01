#!/usr/bin/env python3
"""
SureBets Hockey Bot – generates hokejs.json
Runs daily at 7:00 UTC via GitHub Actions.

Selection: Over 5.5 odds (1.40–3.00) + Variant A/B criteria + MIN_BASELINE
Output: Over 3.5 with odds from API (2-goal safety cushion)

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

MIN_ODDS = 1.40
MAX_ODDS = 3.00
MIN_GAMES = 5
ODDS_TOLERANCE = 0.35    # max deviation from target odds

EXCLUDED_COUNTRIES = {"russia", "belarus"}

# Selection line: Over 5.5 (find odds near ~1.80) → Output line: Over 3.5 (safer bet)
SELECTION_LINE = 5.5
OUTPUT_LINE = 3.5
SELECTION_ODDS_TARGET = 1.80   # target odds for selection line
OUTPUT_ODDS_TARGET = 1.40      # target odds for output line

# Hockey criteria – league-relative (ratios of game baseline)
# Baseline = průměr 4 per-team hodnot (h_for, a_for, h_agn, a_agn)
# → automaticky se přizpůsobí úrovni ligy (AHL ~3.2, SHL ~2.6, Extraliga ~2.7)
BOTH_FLOOR_R = 0.85      # oba alespoň 85% baseline (široký záchyt)
STRONG_MIN_R = 1.10      # "výrazný" tým 110%+ baseline (jasně nad normou ligy)
CONTRAST_MAX_R = 0.95    # protějšek pod 95% baseline (kontrast ≥ 15%)
MIN_BASELINE = 2.75      # minimum avg per-team stat → expected ~5.5+ gólů celkem

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
    League-relative hockey criteria (home/away split).
    Baseline = avg of h_for, a_for, h_agn, a_agn → adapts to any league.
    A) oba conceded >= FLOOR_R * base  AND  (jeden scored >= STRONG_R * base + druhy < CONTRAST_R * base)
    B) oba scored  >= FLOOR_R * base  AND  (jeden conceded >= STRONG_R * base + druhy < CONTRAST_R * base)
    """
    if not home_stats or not away_stats:
        return False, "", 0.0

    h_played = int(_sf(home_stats.get("games", {}).get("played", {}).get("all", 0)))
    a_played = int(_sf(away_stats.get("games", {}).get("played", {}).get("all", 0)))
    if h_played < MIN_GAMES or a_played < MIN_GAMES:
        return False, "", 0.0

    # Home team → home split, Away team → away split
    h_for = _sf(home_stats.get("goals", {}).get("for", {}).get("average", {}).get("home"))
    a_for = _sf(away_stats.get("goals", {}).get("for", {}).get("average", {}).get("away"))
    h_agn = _sf(home_stats.get("goals", {}).get("against", {}).get("average", {}).get("home"))
    a_agn = _sf(away_stats.get("goals", {}).get("against", {}).get("average", {}).get("away"))

    if h_for == 0 and a_for == 0:
        return False, "", 0.0

    # Game baseline = průměrná per-team úroveň scoringu v tomto matchupu
    baseline = (h_for + a_for + h_agn + a_agn) / 4
    if baseline == 0:
        return False, "", 0.0
    if baseline < MIN_BASELINE:
        return False, f"baseline too low: {baseline:.2f} < {MIN_BASELINE}", 0.0

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

    if variant_a or variant_b:
        tag = "A" if variant_a else "B"
        # Contrast score: ratio of strong stat to weak stat (higher = more asymmetry)
        if variant_a:
            s = sorted([h_for, a_for])
        else:
            s = sorted([h_agn, a_agn])
        score = s[1] / s[0] if s[0] > 0 else 99.0
        detail = (f"[{tag}] scored {h_for:.1f}/{a_for:.1f}, conceded {h_agn:.1f}/{a_agn:.1f} "
                  f"(base={baseline:.2f}, score={score:.2f})")
        return True, detail, score

    return False, "", 0.0


def find_odds(odds_data):
    """Find selection line (Over 5.5) and output line (Over 3.5) odds.
    Returns (sel_odds_str, out_odds_str) or (None, None)."""
    for resp in odds_data:
        for bk in resp.get("bookmakers", []):
            for bet in bk.get("bets", []):
                # Bet 4 = Over/Under (full game), Bet 52 = Over/Under (Reg Time)
                if bet.get("id") not in (4, 52) and "over/under" not in bet.get("name", "").lower():
                    continue
                # Skip period-specific bets
                if "period" in bet.get("name", "").lower():
                    continue

                sel_best = None
                out_best = None
                for val in bet.get("values", []):
                    v = str(val.get("value", "")).lower()
                    if not v.startswith("over"):
                        continue
                    try:
                        line = float(v.split()[-1])
                        odd = float(val.get("odd", "0"))
                    except (ValueError, IndexError):
                        continue

                    if line == SELECTION_LINE and MIN_ODDS <= odd <= MAX_ODDS:
                        if sel_best is None or abs(odd - SELECTION_ODDS_TARGET) < abs(float(sel_best) - SELECTION_ODDS_TARGET):
                            sel_best = str(val.get("odd"))

                    if line == OUTPUT_LINE:
                        if out_best is None or abs(odd - OUTPUT_ODDS_TARGET) < abs(float(out_best) - OUTPUT_ODDS_TARGET):
                            out_best = str(val.get("odd"))

                if sel_best and out_best:
                    return sel_best, out_best
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
    print(f"Select: Over {SELECTION_LINE} odds {MIN_ODDS}–{MAX_ODDS} + Variant A/B → Output: Over {OUTPUT_LINE}")
    print(f"MIN_BASELINE: {MIN_BASELINE} (expected ~{MIN_BASELINE*2:.1f}+ goals)")
    print(f"Ratios (× game baseline): FLOOR={BOTH_FLOOR_R}, STRONG={STRONG_MIN_R}, CONTRAST<{CONTRAST_MAX_R}")
    print(f"  A) both conc >= base*{BOTH_FLOOR_R} + one scored >= base*{STRONG_MIN_R}, other < base*{CONTRAST_MAX_R}")
    print(f"  B) both scored >= base*{BOTH_FLOOR_R} + one conc >= base*{STRONG_MIN_R}, other < base*{CONTRAST_MAX_R}\n")

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

    # 3. Fetch odds per game, find Over 5.5 (selection) + Over 3.5 (output)
    candidates = []
    print(f"  Fetching odds for {len(filtered)} games...")
    for i, (gid, g) in enumerate(filtered.items()):
        label = f"{g['home']} vs {g['away']}"
        print(f"  [{i+1}/{len(filtered)}] {label[:45]:.<47s}", end="")
        odds_data = fetch_odds(gid)
        sel_odds, out_odds = find_odds(odds_data)
        if sel_odds and out_odds:
            print(f" O{SELECTION_LINE}={sel_odds} → O{OUTPUT_LINE}={out_odds} ✓")
            candidates.append({
                "game_id": gid,
                "league": g["league"],
                "league_id": g["league_id"],
                "season": g["season"],
                "match": f"{g['home']} vs {g['away']}",
                "home_id": g["home_id"],
                "away_id": g["away_id"],
                "sel_odds": sel_odds,
                "out_odds": out_odds,
                "timestamp": g["timestamp"],
            })
        else:
            print(f" no O{SELECTION_LINE}+O{OUTPUT_LINE} pair")

    print(f"\n  {len(candidates)} candidates (Over {SELECTION_LINE} @ {MIN_ODDS}–{MAX_ODDS})\n")

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
        try:
            home_stats = fetch_team_stats(c["league_id"], c["season"], c["home_id"])
            away_stats = fetch_team_stats(c["league_id"], c["season"], c["away_id"])
            ok, detail, score = meets_criteria(home_stats, away_stats)
            if ok:
                print(f" ★ {detail} | O{SELECTION_LINE}={c['sel_odds']} → O{OUTPUT_LINE}={c['out_odds']}")
                kickoff = datetime.fromtimestamp(c["timestamp"], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
                results.append({
                    "league": c["league"],
                    "match": c["match"],
                    "tip": f"Over {OUTPUT_LINE}",
                    "odds": c["out_odds"],
                    "date": kickoff,
                    "_score": score,
                })
            else:
                # Print stats for debugging even on fail
                if home_stats and away_stats:
                    h_for = _sf(home_stats.get("goals", {}).get("for", {}).get("average", {}).get("home"))
                    a_for = _sf(away_stats.get("goals", {}).get("for", {}).get("average", {}).get("away"))
                    h_agn = _sf(home_stats.get("goals", {}).get("against", {}).get("average", {}).get("home"))
                    a_agn = _sf(away_stats.get("goals", {}).get("against", {}).get("average", {}).get("away"))
                    base = (h_for + a_for + h_agn + a_agn) / 4 if (h_for + a_for + h_agn + a_agn) > 0 else 0
                    print(f" fail | h_sc={h_for:.1f} h_cn={h_agn:.1f} a_sc={a_for:.1f} a_cn={a_agn:.1f} base={base:.2f}")
                elif not home_stats:
                    print(" fail (no home stats)")
                else:
                    print(" fail (no away stats)")
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

    # 6. Write output
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*50}")
    print(f"  Results: {len(results)} match(es) → {OUTPUT}")
    print(f"  API requests: {request_count} / 7500 ({request_count * 100 // 7500}%)")


if __name__ == "__main__":
    main()
