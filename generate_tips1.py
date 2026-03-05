"""
Ultimate Football Overs — Daily Tip Generator v4
Fetches ALL upcoming matches from The Odds API across 40+ leagues,
collects every Over 2.5 candidate, then picks the 3 best tips.

v4 Strategy:
  - ALWAYS scans ALL leagues (no early stopping)
  - Collects maximum candidates first, then selects best 3
  - Smart retry with exponential backoff on rate limits
  - Multiple bookmaker regions (eu + uk + au)
  - 30h time window
  - Flexible odds threshold with fallback

Usage:
  python generate_tips1.py

Environment variable required:
  ODDS_API_KEY5 — API key from https://the-odds-api.com

Output:
  fotbal.json — JSON array consumed by the mobile app
"""

import os
import json
import random
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

API_KEY = os.environ.get("ODDS_API_KEY5", "")
IDEAL_MIN_ODDS = 1.75
FALLBACK_MIN_ODDS = 1.55
NUM_TIPS = 3
HOURS_AHEAD = 30
DELAY_BETWEEN_REQUESTS = 1.8
MAX_RETRIES = 3
OUTPUT_FILE = "fotbal.json"

LEAGUES = [
    ("soccer_epl",                         "Premier League"),
    ("soccer_spain_la_liga",               "La Liga"),
    ("soccer_germany_bundesliga",          "Bundesliga"),
    ("soccer_italy_serie_a",              "Serie A"),
    ("soccer_france_ligue_one",           "Ligue 1"),
    ("soccer_uefa_champs_league",         "Champions League"),
    ("soccer_uefa_europa_league",         "Europa League"),
    ("soccer_uefa_europa_conference_league","Conference League"),
    ("soccer_netherlands_eredivisie",      "Eredivisie"),
    ("soccer_portugal_primeira_liga",      "Primeira Liga"),
    ("soccer_turkey_super_league",         "Turkish Süper Lig"),
    ("soccer_belgium_first_div",           "Belgian Pro League"),
    ("soccer_scotland_premiership",        "Scottish Premiership"),
    ("soccer_austria_bundesliga",          "Austrian Bundesliga"),
    ("soccer_switzerland_superleague",     "Swiss Super League"),
    ("soccer_denmark_superliga",           "Danish Superliga"),
    ("soccer_sweden_allsvenskan",          "Swedish Allsvenskan"),
    ("soccer_norway_eliteserien",          "Norwegian Eliteserien"),
    ("soccer_finland_veikkausliiga",       "Finnish Veikkausliiga"),
    ("soccer_poland_ekstraklasa",          "Polish Ekstraklasa"),
    ("soccer_greece_super_league",         "Greek Super League"),
    ("soccer_czech_czech_football_league", "Czech First League"),
    ("soccer_russia_premier_league",       "Russian Premier League"),
    ("soccer_romania_liga_1",              "Romanian Liga 1"),
    ("soccer_efl_champ",                   "EFL Championship"),
    ("soccer_germany_bundesliga2",         "Bundesliga 2"),
    ("soccer_spain_segunda_division",      "La Liga 2"),
    ("soccer_italy_serie_b",              "Serie B"),
    ("soccer_france_ligue_two",           "Ligue 2"),
    ("soccer_epl_cup",                     "FA Cup"),
    ("soccer_fa_cup",                      "FA Cup"),
    ("soccer_league_cup",                  "League Cup"),
    ("soccer_brazil_campeonato",           "Brasileirão"),
    ("soccer_brazil_serie_b",             "Brasileirão Série B"),
    ("soccer_argentina_primera_division",  "Argentine Liga"),
    ("soccer_conmebol_copa_libertadores",  "Copa Libertadores"),
    ("soccer_usa_mls",                     "MLS"),
    ("soccer_mexico_ligamx",              "Liga MX"),
    ("soccer_japan_j_league",              "J-League"),
    ("soccer_korea_kleague1",             "K-League"),
    ("soccer_australia_aleague",           "A-League"),
    ("soccer_china_superleague",           "Chinese Super League"),
]


def fetch_odds(sport_key: str, league_name: str) -> list:
    """Fetch odds with exponential backoff retry."""
    url = (
        f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
        f"?apiKey={API_KEY}"
        f"&regions=eu,uk,au"
        f"&markets=totals"
        f"&oddsFormat=decimal"
        f"&dateFormat=iso"
    )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                remaining = resp.headers.get("x-requests-remaining", "?")
                print(f" 📡{remaining}", end="")
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 3 * (2 ** attempt)  # 6s, 12s, 24s
                print(f" ⏳{wait}s", end="")
                time.sleep(wait)
            elif e.code in (404, 422):
                return []
            elif e.code == 401:
                print(" ❌KEY!")
                return []
            else:
                return []
        except Exception:
            return []
    return []


def extract_candidates(events: list, sport_key: str, league_name: str, min_odds: float) -> list:
    """Extract all Over 2.5 candidates within time window."""
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=HOURS_AHEAD)
    candidates = []

    for event in events:
        commence = event.get("commence_time", "")
        try:
            dt = datetime.fromisoformat(commence.replace("Z", "+00:00"))
        except ValueError:
            continue

        if dt < now or dt > cutoff:
            continue

        home = event.get("home_team", "?")
        away = event.get("away_team", "?")

        over25_odds = []
        for bm in event.get("bookmakers", []):
            for mkt in bm.get("markets", []):
                if mkt.get("key") != "totals":
                    continue
                for out in mkt.get("outcomes", []):
                    if out.get("name") == "Over" and out.get("point") == 2.5:
                        price = float(out.get("price", 0))
                        if price > 0:
                            over25_odds.append(price)

        if not over25_odds:
            continue

        best = max(over25_odds)
        avg = sum(over25_odds) / len(over25_odds)

        if best >= min_odds:
            candidates.append({
                "League": league_name,
                "Match": f"{home} vs {away}",
                "Tip": "Over 2.5",
                "Odds": f"{best:.2f}",
                "sport_key": sport_key,
                "best": best,
                "avg": avg,
                "bm_count": len(over25_odds),
            })

    return candidates


def select_best_tips(all_candidates: list, num: int = NUM_TIPS) -> list:
    """
    From ALL collected candidates, pick the best 3 from different leagues.
    Score favors: high average odds + many bookmakers agreeing.
    """
    for c in all_candidates:
        # Higher score = better tip
        # avg odds shows market consensus, bm_count shows confidence
        c["score"] = c["avg"] * min(c["bm_count"], 8)

    # Sort all by score
    all_candidates.sort(key=lambda x: x["score"], reverse=True)

    # First: pick best from each unique league
    selected = []
    used_leagues = set()
    for c in all_candidates:
        if c["sport_key"] in used_leagues:
            continue
        selected.append(c)
        used_leagues.add(c["sport_key"])
        if len(selected) >= num:
            break

    # Fill if needed (allow same league)
    if len(selected) < num:
        for c in all_candidates:
            if c not in selected:
                selected.append(c)
                if len(selected) >= num:
                    break

    random.shuffle(selected)
    return selected[:num]


def main():
    if not API_KEY:
        print("❌ ODDS_API_KEY5 not set!")
        return

    print(f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"🔍 Over 2.5 | odds >= {IDEAL_MIN_ODDS} | {HOURS_AHEAD}h window")
    print(f"📋 Scanning ALL {len(LEAGUES)} leagues — no early stop\n")

    # ---- PHASE 1: Collect ALL candidates from ALL leagues ----
    all_candidates = []
    leagues_hit = 0
    rate_limited = 0

    for i, (sport_key, league_name) in enumerate(LEAGUES):
        if i > 0:
            time.sleep(DELAY_BETWEEN_REQUESTS)

        print(f"  [{i+1:2d}/{len(LEAGUES)}] {league_name:.<30s}", end="")
        events = fetch_odds(sport_key, league_name)

        if events:
            cands = extract_candidates(events, sport_key, league_name, FALLBACK_MIN_ODDS)
            if cands:
                print(f" ✅ {len(cands)} matches")
                all_candidates.extend(cands)
                leagues_hit += 1
            else:
                print(f" — no matches")
        else:
            # Check if it was rate limited (no data could mean that)
            print(f" — skip")

    # ---- PHASE 2: Summary of ALL candidates ----
    ideal = [c for c in all_candidates if c["best"] >= IDEAL_MIN_ODDS]
    fallback = [c for c in all_candidates if c["best"] < IDEAL_MIN_ODDS]

    print(f"\n{'='*50}")
    print(f"📊 COLLECTED: {len(all_candidates)} total candidates from {leagues_hit} leagues")
    print(f"   🟢 {len(ideal)} with odds >= {IDEAL_MIN_ODDS}")
    print(f"   🟡 {len(fallback)} with odds {FALLBACK_MIN_ODDS}–{IDEAL_MIN_ODDS}")
    print(f"{'='*50}")

    if not all_candidates:
        print("❌ No matches found at all. Keeping previous tips.")
        return

    # ---- PHASE 3: Select best 3 from all candidates ----
    # Prefer ideal odds candidates, fall back to all if needed
    pool = ideal if len(ideal) >= NUM_TIPS else all_candidates
    tips = select_best_tips(pool)

    output = []
    for t in tips:
        output.append({
            "League": t["League"],
            "Match": t["Match"],
            "Tip": t["Tip"],
            "Odds": t["Odds"],
        })

    print(f"\n🎯 FINAL {len(output)} tips (from {len(all_candidates)} candidates):")
    for i, tip in enumerate(output, 1):
        label = "🔓" if i <= 2 else "🔒 (ad)"
        print(f"  {label} {tip['League']}: {tip['Match']} — {tip['Tip']} @ {tip['Odds']}")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
