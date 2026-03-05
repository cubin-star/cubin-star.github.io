"""
Ultimate Football Overs — Daily Tip Generator
Fetches upcoming matches from The Odds API, selects 3 Over 2.5 tips
with odds >= 1.75, each from a different league.

Usage:
  python generate_tips.py

Environment variable required:
  ODDS_API_KEY5 — API key from https://the-odds-api.com

Output:
  fotbal.json — JSON array consumed by the mobile app
"""

import os
import json
import random
import urllib.request
from datetime import datetime, timezone, timedelta

API_KEY = os.environ.get("ODDS_API_KEY5", "")
MIN_ODDS = 1.75
NUM_TIPS = 3
OUTPUT_FILE = "fotbal.json"

# Soccer leagues supported by The Odds API
# https://the-odds-api.com/sports-odds-data/sports-apis.html
SOCCER_SPORTS = [
    "soccer_epl",                # English Premier League
    "soccer_spain_la_liga",      # La Liga
    "soccer_germany_bundesliga", # Bundesliga
    "soccer_italy_serie_a",      # Serie A
    "soccer_france_ligue_one",   # Ligue 1
    "soccer_uefa_champs_league", # Champions League
    "soccer_uefa_europa_league", # Europa League
    "soccer_netherlands_eredivisie",  # Eredivisie
    "soccer_portugal_primeira_liga",  # Primeira Liga
    "soccer_turkey_super_league",     # Turkish Süper Lig
    "soccer_brazil_campeonato",       # Brasileirão
    "soccer_epl_cup",                 # FA Cup
    "soccer_belgium_first_div",       # Belgian Pro League
    "soccer_scotland_premiership",    # Scottish Premiership
    "soccer_austria_bundesliga",      # Austrian Bundesliga
    "soccer_switzerland_superleague", # Swiss Super League
    "soccer_denmark_superliga",       # Danish Superliga
    "soccer_sweden_allsvenskan",      # Swedish Allsvenskan
    "soccer_norway_eliteserien",      # Norwegian Eliteserien
    "soccer_poland_ekstraklasa",      # Polish Ekstraklasa
    "soccer_greece_super_league",     # Greek Super League
    "soccer_czech_czech_football_league", # Czech First League
]

LEAGUE_NAMES = {
    "soccer_epl": "Premier League",
    "soccer_spain_la_liga": "La Liga",
    "soccer_germany_bundesliga": "Bundesliga",
    "soccer_italy_serie_a": "Serie A",
    "soccer_france_ligue_one": "Ligue 1",
    "soccer_uefa_champs_league": "Champions League",
    "soccer_uefa_europa_league": "Europa League",
    "soccer_netherlands_eredivisie": "Eredivisie",
    "soccer_portugal_primeira_liga": "Primeira Liga",
    "soccer_turkey_super_league": "Turkish Süper Lig",
    "soccer_brazil_campeonato": "Brasileirão",
    "soccer_epl_cup": "FA Cup",
    "soccer_belgium_first_div": "Belgian Pro League",
    "soccer_scotland_premiership": "Scottish Premiership",
    "soccer_austria_bundesliga": "Austrian Bundesliga",
    "soccer_switzerland_superleague": "Swiss Super League",
    "soccer_denmark_superliga": "Danish Superliga",
    "soccer_sweden_allsvenskan": "Swedish Allsvenskan",
    "soccer_norway_eliteserien": "Norwegian Eliteserien",
    "soccer_poland_ekstraklasa": "Polish Ekstraklasa",
    "soccer_greece_super_league": "Greek Super League",
    "soccer_czech_czech_football_league": "Czech First League",
}


def fetch_odds_for_sport(sport_key: str) -> list:
    """Fetch Over/Under odds for a given sport from The Odds API."""
    url = (
        f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
        f"?apiKey={API_KEY}"
        f"&regions=eu"
        f"&markets=totals"
        f"&oddsFormat=decimal"
        f"&dateFormat=iso"
    )
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data
    except Exception as e:
        print(f"  ⚠ Failed to fetch {sport_key}: {e}")
        return []


def extract_over25_candidates(events: list, sport_key: str) -> list:
    """
    From a list of events, extract those with Over 2.5 goals
    and best odds >= MIN_ODDS, starting within the next 24 hours.
    """
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=24)
    candidates = []

    for event in events:
        # Parse commence time
        commence = event.get("commence_time", "")
        try:
            dt = datetime.fromisoformat(commence.replace("Z", "+00:00"))
        except ValueError:
            continue

        # Must start within 24 hours
        if dt < now or dt > cutoff:
            continue

        home = event.get("home_team", "?")
        away = event.get("away_team", "?")

        # Look through bookmakers for Over 2.5
        best_over25_odds = 0.0
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
                        if odds > best_over25_odds:
                            best_over25_odds = odds

        if best_over25_odds >= MIN_ODDS:
            league_name = LEAGUE_NAMES.get(sport_key, sport_key)
            candidates.append({
                "League": league_name,
                "Match": f"{home} vs {away}",
                "Tip": "Over 2.5",
                "Odds": f"{best_over25_odds:.2f}",
                "sport_key": sport_key,
                "commence": dt,
                "odds_value": best_over25_odds,
            })

    return candidates


def select_tips(all_candidates: list, num: int = NUM_TIPS) -> list:
    """
    Select `num` tips from different leagues, preferring higher odds.
    """
    # Sort by odds descending (best value first)
    all_candidates.sort(key=lambda x: x["odds_value"], reverse=True)

    selected = []
    used_leagues = set()

    for c in all_candidates:
        if c["sport_key"] in used_leagues:
            continue
        selected.append(c)
        used_leagues.add(c["sport_key"])
        if len(selected) >= num:
            break

    # If not enough from different leagues, fill with remaining
    if len(selected) < num:
        for c in all_candidates:
            if c not in selected:
                selected.append(c)
                if len(selected) >= num:
                    break

    # Shuffle so the order isn't always by odds
    random.shuffle(selected)
    return selected[:num]


def main():
    if not API_KEY:
        print("❌ ODDS_API_KEY5 environment variable is not set!")
        print("   Get your free key at https://the-odds-api.com")
        return

    print(f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"🔍 Searching Over 2.5 tips with odds >= {MIN_ODDS}")
    print(f"📋 Scanning {len(SOCCER_SPORTS)} leagues...\n")

    all_candidates = []

    for sport in SOCCER_SPORTS:
        events = fetch_odds_for_sport(sport)
        if events:
            candidates = extract_over25_candidates(events, sport)
            if candidates:
                print(f"  ✅ {LEAGUE_NAMES.get(sport, sport)}: {len(candidates)} candidates")
                all_candidates.extend(candidates)
            else:
                print(f"  ⏭ {LEAGUE_NAMES.get(sport, sport)}: no qualifying matches")
        else:
            print(f"  ⏭ {LEAGUE_NAMES.get(sport, sport)}: no data")

    print(f"\n📊 Total candidates: {len(all_candidates)}")

    if len(all_candidates) == 0:
        print("❌ No qualifying matches found. Keeping previous tips.")
        return

    tips = select_tips(all_candidates)

    # Format for the app (remove internal fields)
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
