"""Denní generátor hokejových tipů Over 5.5 pro MAUI appku.

Stáhne kurzy z The Odds API pro všechny dostupné hokejové ligy,
najde Over 5.5 gólů s kurzem >= 1.75 a náhodně vybere 2 zápasy.

Výstup: hokey.json (formát kompatibilní s TodaysTipsPage.xaml.cs)
"""

import os
import sys
import json
import random
import requests
from datetime import datetime, timezone, timedelta

API_KEY = os.environ.get("ODDS_API_KEY", "")
REQUIRED_POINT = 5.5       # Vždy Over 5.5 gólů
MIN_ODDS = 1.75            # Minimální kurz
OUTPUT_FILE = "hokey.json"

# Hokejové ligy podporované The Odds API
# (API vrátí prázdný seznam, pokud liga zrovna nemá zápasy – žádná chyba)
HOCKEY_SPORTS = [
    "icehockey_nhl",
    "icehockey_czech_extraliga",
    "icehockey_sweden_shl",
    "icehockey_finland_liiga",
    "icehockey_germany_del",
    "icehockey_switzerland_nla",
    "icehockey_world_championship",
]

# Mapování sport-key → čitelný název ligy
LEAGUE_NAMES = {
    "icehockey_nhl": "NHL",
    "icehockey_czech_extraliga": "Česká Extraliga",
    "icehockey_sweden_shl": "SHL (Švédsko)",
    "icehockey_finland_liiga": "Liiga (Finsko)",
    "icehockey_germany_del": "DEL (Německo)",
    "icehockey_switzerland_nla": "NLA (Švýcarsko)",
    "icehockey_world_championship": "MS v hokeji (IIHF)",
}


def fetch_odds(sport_key: str) -> list[dict]:
    """Stáhne Over/Under kurzy pro danou ligu (pouze zápasy do 24h)."""
    now = datetime.now(timezone.utc)
    commence_to = (now + timedelta(hours=24)).isoformat()

    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"
    params = {
        "apiKey": API_KEY,
        "regions": "eu",
        "markets": "totals",
        "oddsFormat": "decimal",
        "commenceTimeTo": commence_to,
    }
    try:
        resp = requests.get(url, params=params, timeout=20)
        if resp.status_code == 404:
            # Liga momentálně nemá zápasy
            return []
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"  ⚠ Chyba při stahování {sport_key}: {e}")
        return []


def extract_over55_candidates(matches: list[dict], sport_key: str) -> list[dict]:
    """Z matchů vytáhne Over 5.5 s kurzem >= 1.75 – pouze zápasy do 24h."""
    candidates = []
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=24)

    for match in matches:
        home = match.get("home_team", "?")
        away = match.get("away_team", "?")
        commence = match.get("commence_time", "")

        # Filtr: pouze zápasy začínající od teď do 24h
        try:
            match_time = datetime.fromisoformat(commence.replace("Z", "+00:00"))
            if match_time < now or match_time > cutoff:
                continue
        except (ValueError, AttributeError):
            continue

        # Sbíráme Over 5.5 kurzy od všech bookmakerů, bereme nejvyšší kurz
        # Pokud Over 5.5 neexistuje, bereme nejbližší Over >= 5.0 (5.0, 5.5, 6.0)
        best_price = None
        best_point = None

        for bookmaker in match.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "totals":
                    continue
                for outcome in market.get("outcomes", []):
                    if outcome.get("name") != "Over":
                        continue
                    point = outcome.get("point", 0)
                    price = outcome.get("price", 0)

                    # Přesně 5.5 je ideální, ale bereme i 5.0 nebo 6.0
                    if point < 5.0 or point > 6.5:
                        continue
                    if price < MIN_ODDS:
                        continue

                    # Priorita: 5.5 > 5.0 > 6.0 (čím blíže 5.5)
                    dist = abs(point - REQUIRED_POINT)
                    if best_point is None:
                        best_point = point
                        best_price = price
                    elif dist < abs(best_point - REQUIRED_POINT):
                        best_point = point
                        best_price = price
                    elif dist == abs(best_point - REQUIRED_POINT) and price > best_price:
                        best_price = price

        if best_price is not None:
            candidates.append({
                "league": LEAGUE_NAMES.get(sport_key, sport_key),
                "match": f"{home} vs {away}",
                "tip": f"Over {best_point}",
                "odds": str(round(best_price, 2)),
            })

    return candidates


def main():
    if not API_KEY:
        print("❌ ODDS_API_KEY není nastaven!")
        sys.exit(1)

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=24)
    print(f"🏒 Generuji hokejové tipy – {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"   Zápasy od teď do {cutoff.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"   Over {REQUIRED_POINT}, kurz >= {MIN_ODDS}")
    print()

    all_candidates = []

    for sport in HOCKEY_SPORTS:
        print(f"📡 Stahuji: {LEAGUE_NAMES.get(sport, sport)}...")
        matches = fetch_odds(sport)
        if not matches:
            print(f"   (žádné zápasy)")
            continue

        candidates = extract_over55_candidates(matches, sport)
        if candidates:
            for c in candidates:
                print(f"   ✓ {c['match']} → Over {REQUIRED_POINT} @ {c['odds']}")
        else:
            print(f"   (žádné Over {REQUIRED_POINT} s kurzem >= {MIN_ODDS})")
        all_candidates.extend(candidates)

    print()
    print(f"📊 Celkem kandidátů: {len(all_candidates)}")

    if len(all_candidates) == 0:
        print("⚠ Žádné zápasy. Zapisuji prázdný JSON.")
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2, ensure_ascii=False)
        return

    # Náhodně vyber 2 (nebo 1 pokud je jen 1)
    count = min(2, len(all_candidates))
    picked = random.sample(all_candidates, count)

    print(f"✅ Vybrané tipy ({count}):")
    for t in picked:
        print(f"   {t['league']}: {t['match']} → {t['tip']} @ {t['odds']}")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(picked, f, indent=2, ensure_ascii=False)

    print(f"\n💾 Zapsáno do {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
