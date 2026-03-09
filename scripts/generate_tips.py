"""Denní generátor hokejových tipů Over 5.5 pro MAUI appku.

Stáhne kurzy z API-Sports Hockey API pro všechny dostupné hokejové ligy,
najde Over 5.5 gólů s kurzem >= 1.75 a náhodně vybere 2 zápasy z různých lig.

API: https://v1.hockey.api-sports.io
Free plan: 100 requestů/den

Výstup: hokey.json (formát kompatibilní s TodaysTipsPage.xaml.cs)
"""

import os
import sys
import json
import random
import requests
from datetime import datetime, timezone

API_KEY = os.environ.get("API_HOCKEY_KEY", "")
BASE_URL = "https://v1.hockey.api-sports.io"
MIN_ODDS = 1.75
OUTPUT_FILE = "hokey.json"


def api_get(endpoint: str, params: dict | None = None) -> list:
    """API-Sports Hockey GET request."""
    url = f"{BASE_URL}/{endpoint}"
    headers = {"x-apisports-key": API_KEY}
    resp = requests.get(url, headers=headers, params=params or {}, timeout=20)
    remaining = resp.headers.get("x-ratelimit-requests-remaining", "?")
    print(f"   (zbývá requestů: {remaining})")
    resp.raise_for_status()
    data = resp.json()

    errors = data.get("errors")
    if errors and (isinstance(errors, dict) and errors or isinstance(errors, list) and errors):
        print(f"   ⚠ API chyba: {errors}")
        return []

    results = data.get("response", [])
    print(f"   → {len(results)} výsledků")
    return results


def main():
    if not API_KEY:
        print("❌ API_HOCKEY_KEY není nastaven!")
        sys.exit(1)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"🏒 Generuji hokejové tipy – {today}")
    print(f"   Over 5.5, kurz >= {MIN_ODDS}")
    print()

    # Krok 1: Stáhni dnešní zápasy (1 request)
    print("📡 Stahuji dnešní hokejové zápasy...")
    games = api_get("games", {"date": today})

    if not games:
        print("⚠ Žádné zápasy dnes. Zapisuji prázdný JSON.")
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2, ensure_ascii=False)
        return

    # Filtruj jen nezačaté zápasy
    ns_games = [g for g in games if g.get("status", {}).get("short") == "NS"]
    print(f"   Celkem zápasů: {len(games)}, nezačatých: {len(ns_games)}")
    print()

    if not ns_games:
        print("⚠ Žádné nezačaté zápasy. Zapisuji prázdný JSON.")
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2, ensure_ascii=False)
        return

    # Krok 2: Pro každý zápas stáhni odds (1 request na zápas)
    all_candidates = []

    for game in ns_games:
        game_id = game["id"]
        home = game.get("teams", {}).get("home", {}).get("name", "?")
        away = game.get("teams", {}).get("away", {}).get("name", "?")
        league_name = game.get("league", {}).get("name", "?")
        match_label = f"{home} vs {away}"

        print(f"📋 {league_name}: {match_label}")
        odds_data = api_get("odds", {"game": game_id})

        if not odds_data:
            print(f"   (žádné kurzy)")
            print()
            continue

        # Hledej Over 5.5
        best_price = None
        best_bookmaker = None

        for entry in odds_data:
            for bookmaker in entry.get("bookmakers", []):
                bk_name = bookmaker.get("name", "?")
                for bet in bookmaker.get("bets", []):
                    for value in bet.get("values", []):
                        val = str(value.get("value", ""))
                        odd = value.get("odd", "0")

                        try:
                            price = float(odd)
                        except (ValueError, TypeError):
                            continue

                        # Hledáme přesně "Over 5.5"
                        if val == "Over 5.5" and price >= MIN_ODDS:
                            if best_price is None or price > best_price:
                                best_price = price
                                best_bookmaker = bk_name

        if best_price is not None:
            print(f"   ✅ Over 5.5 @ {best_price} ({best_bookmaker})")
            all_candidates.append({
                "league": league_name,
                "match": match_label,
                "tip": "Over 5.5",
                "odds": str(round(best_price, 2)),
            })
        else:
            print(f"   ❌ žádný Over 5.5 >= {MIN_ODDS}")
        print()

    print(f"📊 Celkem kandidátů: {len(all_candidates)}")

    if not all_candidates:
        print("⚠ Žádné zápasy splňující kritéria. Zapisuji prázdný JSON.")
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2, ensure_ascii=False)
        return

    # Vyber 2 z různých soutěží
    by_league: dict[str, list[dict]] = {}
    for c in all_candidates:
        by_league.setdefault(c["league"], []).append(c)

    print(f"   Ligy s kandidáty: {list(by_league.keys())}")

    picked = []
    if len(by_league) >= 2:
        two_leagues = random.sample(list(by_league.keys()), 2)
        for lg in two_leagues:
            picked.append(random.choice(by_league[lg]))
    else:
        only_league = list(by_league.keys())[0]
        picked.append(random.choice(by_league[only_league]))
        print(f"   ⚠ Pouze 1 liga ({only_league}), nelze vybrat ze dvou různých")

    print(f"✅ Vybrané tipy ({len(picked)}):")
    for t in picked:
        print(f"   {t['league']}: {t['match']} → {t['tip']} @ {t['odds']}")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(picked, f, indent=2, ensure_ascii=False)

    print(f"\n💾 Zapsáno do {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
