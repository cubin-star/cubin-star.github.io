"""
Denní generátor hokejových tipů Over 5.5 pro MAUI appku.

Stáhne kurzy z The Odds API pro všechny dostupné hokejové ligy,
najde Over 5.5 (nebo nejbližší Over ≥5.5) a vybere 2 zápasy,
jejichž součin kurzů je co nejblíže 3.0.

Výstup: hokey.json (formát kompatibilní s TodaysTipsPage.xaml.cs)
"""

import os
import sys
import json
import itertools
import requests
from datetime import datetime, timezone, timedelta

API_KEY = os.environ.get("ODDS_API_KEY", "")
TARGET_PRODUCT = 3.0
MIN_ODDS = 1.30
MAX_ODDS = 2.50
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


def extract_over_candidates(matches: list[dict], sport_key: str) -> list[dict]:
    """Z matchů vytáhne Over 5.5 (nebo nejbližší ≥5.5) kandidáty – pouze zápasy do 24h."""
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
            continue  # Neplatný čas → přeskočit

        best_over = None

        for bookmaker in match.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "totals":
                    continue
                for outcome in market.get("outcomes", []):
                    if outcome.get("name") != "Over":
                        continue
                    point = outcome.get("point", 0)
                    price = outcome.get("price", 0)

                    # Preferujeme přesně 5.5, ale bereme i blízké hodnoty (5.0, 6.0)
                    if point < 4.5 or point > 6.5:
                        continue

                    if MIN_ODDS <= price <= MAX_ODDS:
                        # Priorita: čím blíže 5.5, tím lepší
                        distance = abs(point - 5.5)
                        if best_over is None or distance < best_over["_dist"]:
                            best_over = {
                                "league": LEAGUE_NAMES.get(sport_key, sport_key),
                                "match": f"{home} vs {away}",
                                "tip": f"Over {point}",
                                "odds": str(round(price, 2)),
                                "_price": price,
                                "_dist": distance,
                                "_commence": commence,
                            }

        if best_over is not None:
            candidates.append(best_over)

    return candidates


def select_best_pair(candidates: list[dict]) -> list[dict] | None:
    """Vybere 2 zápasy, jejichž součin kurzů je nejblíže TARGET_PRODUCT."""
    if len(candidates) < 2:
        return None

    best_pair = None
    best_diff = float("inf")

    for a, b in itertools.combinations(candidates, 2):
        product = a["_price"] * b["_price"]
        diff = abs(product - TARGET_PRODUCT)
        if diff < best_diff:
            best_diff = diff
            best_pair = [a, b]

    return best_pair


def clean_tip(tip: dict) -> dict:
    """Odstraní interní pole začínající na '_'."""
    return {k: v for k, v in tip.items() if not k.startswith("_")}


def main():
    if not API_KEY:
        print("❌ ODDS_API_KEY není nastaven!")
        sys.exit(1)

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=24)
    print(f"🏒 Generuji hokejové tipy – {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"   Pouze zápasy od teď do {cutoff.strftime('%Y-%m-%d %H:%M UTC')} (24h okno)")
    print(f"   Cílový součin kurzů: {TARGET_PRODUCT}")
    print()

    all_candidates = []

    for sport in HOCKEY_SPORTS:
        print(f"📡 Stahuji: {LEAGUE_NAMES.get(sport, sport)}...")
        matches = fetch_odds(sport)
        if not matches:
            print(f"   (žádné zápasy)")
            continue

        candidates = extract_over_candidates(matches, sport)
        print(f"   Nalezeno {len(candidates)} kandidátů (Over ~5.5, kurz {MIN_ODDS}–{MAX_ODDS})")
        all_candidates.extend(candidates)

    print()
    print(f"📊 Celkem kandidátů: {len(all_candidates)}")

    if len(all_candidates) < 2:
        print("⚠ Nedostatek zápasů. Zapisuji prázdný JSON.")
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2, ensure_ascii=False)
        return

    pair = select_best_pair(all_candidates)
    if pair is None:
        print("⚠ Nepodařilo se najít vhodný pár.")
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2, ensure_ascii=False)
        return

    product = float(pair[0]["odds"]) * float(pair[1]["odds"])
    print(f"✅ Vybraný pár (součin kurzů = {product:.2f}):")
    for t in pair:
        print(f"   {t['league']}: {t['match']} → {t['tip']} @ {t['odds']}")

    result = [clean_tip(t) for t in pair]

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n💾 Zapsáno do {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
