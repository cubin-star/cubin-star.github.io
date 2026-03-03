"""
Daily football Over 2.5 tips fetcher.

Uses The Odds API (https://the-odds-api.com/) free tier to find
football matches where the Over 2.5 goals line has odds >= 1.75,
then picks the best two and writes them to tips.json.

Required environment variable:
    ODDS_API_KEY  –  free key from https://the-odds-api.com/
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

API_KEY = os.environ.get("ODDS_API_KEY1", "")
BASE_URL = "https://api.the-odds-api.com/v4/sports"

# Football (soccer) sport keys to scan – covers major leagues.
SPORT_KEYS = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_one",
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    "soccer_netherlands_eredivisie",
    "soccer_portugal_primeira_liga",
    "soccer_turkey_super_league",
    "soccer_brazil_campeonato",
    "soccer_efl_champ",
    "soccer_belgium_first_div",
    "soccer_czech_football_league",
    "soccer_poland_ekstraklasa",
]

# Display-friendly league names.
LEAGUE_NAMES: dict[str, str] = {
    "soccer_epl": "England – Premier League",
    "soccer_spain_la_liga": "Spain – La Liga",
    "soccer_germany_bundesliga": "Germany – Bundesliga",
    "soccer_italy_serie_a": "Italy – Serie A",
    "soccer_france_ligue_one": "France – Ligue 1",
    "soccer_uefa_champs_league": "UEFA Champions League",
    "soccer_uefa_europa_league": "UEFA Europa League",
    "soccer_netherlands_eredivisie": "Netherlands – Eredivisie",
    "soccer_portugal_primeira_liga": "Portugal – Primeira Liga",
    "soccer_turkey_super_league": "Turkey – Super League",
    "soccer_brazil_campeonato": "Brazil – Serie A",
    "soccer_efl_champ": "England – Championship",
    "soccer_belgium_first_div": "Belgium – First Division",
    "soccer_czech_football_league": "Czech – First League",
    "soccer_poland_ekstraklasa": "Poland – Ekstraklasa",
}

MIN_ODDS = 1.75
PICK_COUNT = 2

# Path to the output file (repo root / tips.json).
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "tips.json"


def fetch_over25_candidates() -> list[dict]:
    """Return a list of {league, match, odds} for Over 2.5 lines with odds >= MIN_ODDS.
    Only matches starting within the next 24 hours are included."""
    candidates: list[dict] = []
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=24)
    print(f"Time window: {now:%Y-%m-%d %H:%M} UTC  →  {cutoff:%Y-%m-%d %H:%M} UTC")

    for sport_key in SPORT_KEYS:
        url = f"{BASE_URL}/{sport_key}/odds"
        params = {
            "apiKey": API_KEY,
            "regions": "eu",
            "markets": "totals",
            "oddsFormat": "decimal",
        }

        try:
            resp = requests.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            print(f"[WARN] {sport_key}: request failed – {exc}")
            continue

        if resp.status_code == 422:
            # No upcoming events for this sport.
            continue
        if resp.status_code != 200:
            print(f"[WARN] {sport_key}: HTTP {resp.status_code}")
            continue

        events = resp.json()
        league = LEAGUE_NAMES.get(sport_key, sport_key)

        for event in events:
            # Filter: only matches starting within the next 24 hours.
            commence_str = event.get("commence_time", "")
            if not commence_str:
                continue
            try:
                commence = datetime.fromisoformat(commence_str.replace("Z", "+00:00"))
            except ValueError:
                continue
            if commence < now or commence > cutoff:
                continue

            home = event.get("home_team", "?")
            away = event.get("away_team", "?")
            match_name = f"{home} vs {away}"

            for bookmaker in event.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    if market.get("key") != "totals":
                        continue
                    for outcome in market.get("outcomes", []):
                        if (
                            outcome.get("name") == "Over"
                            and outcome.get("point") == 2.5
                            and outcome.get("price", 0) >= MIN_ODDS
                        ):
                            candidates.append(
                                {
                                    "league": league,
                                    "match": match_name,
                                    "odds": outcome["price"],
                                    "commence": commence,
                                }
                            )
                            break  # one Over 2.5 per bookmaker is enough
                    break  # only need the first totals market
                break  # only need the first bookmaker

    return candidates


def pick_best(candidates: list[dict], count: int = PICK_COUNT) -> list[dict]:
    """
    Remove duplicates (same match), keep the highest odds per match,
    then return *count* matches sorted by kick-off time (earliest first).
    """
    best_by_match: dict[str, dict] = {}
    for c in candidates:
        key = c["match"]
        if key not in best_by_match or c["odds"] > best_by_match[key]["odds"]:
            best_by_match[key] = c

    sorted_candidates = sorted(best_by_match.values(), key=lambda x: x["commence"])
    return sorted_candidates[:count]


def main() -> None:
    if not API_KEY:
        print("ERROR: ODDS_API_KEY1 environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    print("Fetching Over 2.5 odds from The Odds API …")
    candidates = fetch_over25_candidates()
    print(f"Found {len(candidates)} candidate lines across all leagues.")

    picks = pick_best(candidates)

    if not picks:
        print("No matches found matching criteria. Keeping existing tips.json.")
        sys.exit(0)

    tips_json = [
        {
            "league": p["league"],
            "match": p["match"],
            "tip": "Over 2.5",
            "odds": f"{p['odds']:.2f}",
        }
        for p in picks
    ]

    OUTPUT_PATH.write_text(json.dumps(tips_json, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Written {len(tips_json)} tips to {OUTPUT_PATH}")
    for t in tips_json:
        print(f"  • {t['league']} | {t['match']} | {t['tip']} @ {t['odds']}")


if __name__ == "__main__":
    main()
