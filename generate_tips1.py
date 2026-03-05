"""
Ultimate Football Overs — Daily Tip Generator v2
Fetches upcoming matches from The Odds API, selects 3 Over 2.5 tips
with odds >= 1.75, each from a different league.

Improvements:
  - Rate-limit aware (delays + retry on 429)
  - Prioritized leagues (top leagues first)
  - Stops early when enough candidates found
  - Smarter selection with randomized variety

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
MIN_ODDS = 1.75
NUM_TIPS = 3
MIN_CANDIDATES = 8          # collect at least this many before stopping
DELAY_BETWEEN_REQUESTS = 2  # seconds between API calls
MAX_RETRIES = 2             # retry count on 429
OUTPUT_FILE = "fotbal.json"

# Leagues ordered by priority (top leagues first — most likely to have matches)
LEAGUES = [
    ("soccer_epl",                    "Premier League"),
    ("soccer_spain_la_liga",          "La Liga"),
    ("soccer_germany_bundesliga",     "Bundesliga"),
    ("soccer_italy_serie_a",         "Serie A"),
    ("soccer_france_ligue_one",      "Ligue 1"),
    ("soccer_uefa_champs_league",    "Champions League"),
    ("soccer_uefa_europa_league",    "Europa League"),
    ("soccer_netherlands_eredivisie", "Eredivisie"),
    ("soccer_portugal_primeira_liga", "Primeira Liga"),
    ("soccer_turkey_super_league",    "Turkish Süper Lig"),
    ("soccer_brazil_campeonato",      "Brasileirão"),
    ("soccer_belgium_first_div",      "Belgian Pro League"),
    ("soccer_scotland_premiership",   "Scottish Premiership"),
    ("soccer_epl_cup",                "FA Cup"),
    ("soccer_austria_bundesliga",     "Austrian Bundesliga"),
    ("soccer_switzerland_superleague","Swiss Super League"),
    ("soccer_denmark_superliga",      "Danish Superliga"),
    ("soccer_sweden_allsvenskan",     "Swedish Allsvenskan"),
    ("soccer_norway_eliteserien",     "Norwegian Eliteserien"),
    ("soccer_poland_ekstraklasa",     "Polish Ekstraklasa"),
    ("soccer_greece_super_league",    "Greek Super League"),
    ("soccer_czech_czech_football_league", "Czech First League"),
]


def fetch_odds_for_sport(sport_key: str, league_name: str) -> list:
    """Fetch Over/Under odds for a given sport with retry on 429."""
    url = (
        f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
        f"?apiKey={API_KEY}"
        f"&regions=eu"
        f"&markets=totals"
        f"&oddsFormat=decimal"
        f"&dateFormat=iso"
    )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                # Log remaining API quota
                remaining = resp.headers.get("x-requests-remaining", "?")
                used = resp.headers.get("x-requests-used", "?")
                print(f"  📡 API quota: {remaining} remaining, {used} used")
                data = json.loads(resp.read().decode("utf-8"))
                return data
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = DELAY_BETWEEN_REQUESTS * attempt * 2
                print(f"  ⏳ Rate limited on {league_name}, waiting {wait}s (attempt {attempt}/{MAX_RETRIES})...")
                time.sleep(wait)
            elif e.code == 404:
                print(f"  ⏭ {league_name}: not in season")
                return []
            elif e.code == 401:
                print(f"  ❌ Invalid API key!")
                return []
            else:
                print(f"  ⚠ HTTP {e.code} for {league_name}: {e.reason}")
                return []
        except Exception as e:
            print(f"  ⚠ Failed to fetch {league_name}: {e}")
            return []

    print(f"  ❌ {league_name}: all retries failed")
    return []


def extract_over25_candidates(events: list, sport_key: str, league_name: str) -> list:
    """
    Extract matches with Over 2.5 goals odds >= MIN_ODDS,
    starting within the next 24 hours.
    """
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=24)
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

        # Collect Over 2.5 odds from all bookmakers
        over25_odds_list = []
        for bookmaker in event.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "totals":
                    continue
                for outcome in market.get("outcomes", []):
                    if (
                        outcome.get("name") == "Over"
                        and outcome.get("point") == 2.5
                    ):
                        odds = float(outcome.get("price", 0))
                        if odds > 0:
                            over25_odds_list.append(odds)

        if not over25_odds_list:
            continue

        # Use the AVERAGE odds across bookmakers (more stable than max)
        avg_odds = sum(over25_odds_list) / len(over25_odds_list)
        best_odds = max(over25_odds_list)

        if best_odds >= MIN_ODDS:
            match_time = dt.strftime("%H:%M")
            candidates.append({
                "League": league_name,
                "Match": f"{home} vs {away}",
                "Tip": "Over 2.5",
                "Odds": f"{best_odds:.2f}",
                "sport_key": sport_key,
                "commence": dt,
                "odds_value": best_odds,
                "avg_odds": avg_odds,
                "num_bookmakers": len(over25_odds_list),
                "match_time": match_time,
            })

    return candidates


def select_tips(all_candidates: list, num: int = NUM_TIPS) -> list:
    """
    Select tips from different leagues.
    Prefer matches where multiple bookmakers agree on high odds (more reliable).
    """
    # Score = average odds × number of bookmakers agreeing
    # This favors matches where the market consensus is Over 2.5
    for c in all_candidates:
        c["score"] = c["avg_odds"] * min(c["num_bookmakers"], 5)

    # Sort by score descending
    all_candidates.sort(key=lambda x: x["score"], reverse=True)

    selected = []
    used_leagues = set()

    # First pass: one per league, best scored
    for c in all_candidates:
        if c["sport_key"] in used_leagues:
            continue
        selected.append(c)
        used_leagues.add(c["sport_key"])
        if len(selected) >= num:
            break

    # Second pass: fill remaining from any league if needed
    if len(selected) < num:
        for c in all_candidates:
            if c not in selected:
                selected.append(c)
                if len(selected) >= num:
                    break

    # Shuffle the final selection for variety
    random.shuffle(selected)
    return selected[:num]


def main():
    if not API_KEY:
        print("❌ ODDS_API_KEY5 environment variable is not set!")
        print("   Get your free key at https://the-odds-api.com")
        return

    print(f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"🔍 Searching Over 2.5 tips with odds >= {MIN_ODDS}")
    print(f"📋 Scanning up to {len(LEAGUES)} leagues...\n")

    all_candidates = []
    leagues_with_candidates = 0

    for i, (sport_key, league_name) in enumerate(LEAGUES):
        # If we have enough candidates from enough different leagues, stop
        if len(all_candidates) >= MIN_CANDIDATES and leagues_with_candidates >= NUM_TIPS:
            print(f"\n  ✅ Enough candidates ({len(all_candidates)}) from {leagues_with_candidates} leagues — skipping remaining")
            break

        # Rate limit delay (skip before first request)
        if i > 0:
            time.sleep(DELAY_BETWEEN_REQUESTS)

        print(f"  [{i+1}/{len(LEAGUES)}] {league_name}...")
        events = fetch_odds_for_sport(sport_key, league_name)

        if events:
            candidates = extract_over25_candidates(events, sport_key, league_name)
            if candidates:
                print(f"       ✅ {len(candidates)} candidates found")
                all_candidates.extend(candidates)
                leagues_with_candidates += 1
            else:
                print(f"       ⏭ no qualifying matches in next 24h")
        else:
            print(f"       ⏭ no data")

    print(f"\n📊 Total candidates: {len(all_candidates)} from {leagues_with_candidates} leagues")

    if len(all_candidates) == 0:
        print("❌ No qualifying matches found. Keeping previous tips.")
        return

    tips = select_tips(all_candidates)

    # Format output for the app
    output = []
    for t in tips:
        output.append({
            "League": t["League"],
            "Match": t["Match"],
            "Tip": t["Tip"],
            "Odds": t["Odds"],
        })

    print(f"\n🎯 Selected {len(output)} tips:")
    for i, tip in enumerate(output, 1):
        label = "🔓" if i <= 2 else "🔒 (ad)"
        print(f"  {label} {tip['League']}: {tip['Match']} — {tip['Tip']} @ {tip['Odds']}")

    # Write JSON
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
