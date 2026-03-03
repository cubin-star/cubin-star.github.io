"""Denní generátor hokejových tipů Over 5.5 pro MAUI appku.

Stáhne kurzy z The Odds API pro všechny dostupné hokejové ligy,
najde PŘESNĚ Over 5.5 a vybere 2 zápasy, jejichž součin kurzů
je MINIMÁLNĚ 3.0 (a co nejblíže k 3.0).

Příklad: zápas A kurz 2.05 × zápas B kurz 1.60 = 3.28 ✅
Příklad: zápas A kurz 1.78 × zápas B kurz 1.80 = 3.20 ✅
Příklad: zápas A kurz 1.50 × zápas B kurz 1.90 = 2.85 ❌ (pod 3.0)

Výstup: hokey.json (formát kompatibilní s TodaysTipsPage.xaml.cs)
"""

import os
import sys
import json
import itertools
import requests
from datetime import datetime, timezone, timedelta

API_KEY = os.environ.get("ODDS_API_KEY", "")
MIN_PRODUCT = 3.0          # Součin kurzů MUSÍ být >= 3.0
REQUIRED_POINT = 5.5       # Vždy Over 5.5 gólů
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
    """Z matchů vytáhne PŘESNĚ Over 5.5 kandidáty – pouze zápasy do 24h."""
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

        # Sbíráme Over 5.5 kurzy od všech bookmakerů, bereme nejvyšší kurz
        best_price = None

        for bookmaker in match.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "totals":
                    continue
                for outcome in market.get("outcomes", []):
                    if outcome.get("name") != "Over":
                        continue
                    point = outcome.get("point", 0)
                    price = outcome.get("price", 0)

                    # POUZE přesně Over 5.5
                    if point != REQUIRED_POINT:
                        continue

                    if price > 1.0 and (best_price is None or price > best_price):
                        best_price = price

        if best_price is not None:
            candidates.append({
                "league": LEAGUE_NAMES.get(sport_key, sport_key),
                "match": f"{home} vs {away}",
                "tip": f"Over {REQUIRED_POINT}",
                "odds": str(round(best_price, 2)),
                "_price": best_price,
                "_commence": commence,
            })

    return candidates


def select_best_pair(candidates: list[dict]) -> list[dict] | None:
    """Vybere 2 zápasy: součin kurzů MUSÍ být >= 3.0, a co nejblíže k 3.0."""
    if len(candidates) < 2:
        return None

    best_pair = None
    best_product = float("inf")

    for a, b in itertools.combinations(candidates, 2):
        product = a["_price"] * b["_price"]
        # Součin MUSÍ být >= 3.0
        if product >= MIN_PRODUCT and product < best_product:
            best_product = product
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
    print(f"   Pravidla: VŽDY Over {REQUIRED_POINT}, součin kurzů >= {MIN_PRODUCT}")
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
            print(f"   (žádné zápasy s Over {REQUIRED_POINT})")
        all_candidates.extend(candidates)

    print()
    print(f"📊 Celkem kandidátů s Over {REQUIRED_POINT}: {len(all_candidates)}")

    if len(all_candidates) < 2:
        print("⚠ Nedostatek zápasů s Over 5.5. Zapisuji prázdný JSON.")
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2, ensure_ascii=False)
        return

    pair = select_best_pair(all_candidates)
    if pair is None:
        print(f"⚠ Žádný pár nemá součin kurzů >= {MIN_PRODUCT}. Zapisuji prázdný JSON.")
        # Ukáž nejlepší dostupný součin pro diagnostiku
        best = 0
        for a, b in itertools.combinations(all_candidates, 2):
            p = a["_price"] * b["_price"]
            if p > best:
                best = p
        print(f"   (Nejlepší dostupný součin: {best:.2f})")
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2, ensure_ascii=False)
        return

    product = pair[0]["_price"] * pair[1]["_price"]
    print(f"✅ Vybraný pár (součin kurzů = {product:.2f}, >= {MIN_PRODUCT} ✓):")
    for t in pair:
        print(f"   {t['league']}: {t['match']} → {t['tip']} @ {t['odds']}")
    print(f"   {pair[0]['odds']} × {pair[1]['odds']} = {product:.2f}")

    result = [clean_tip(t) for t in pair]

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n💾 Zapsáno do {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
