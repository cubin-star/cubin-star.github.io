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
HOCKEY_SPORTS = [
    "icehockey_nhl",
    "icehockey_czech_extraliga",
    "icehockey_sweden_shl",
    "icehockey_finland_liiga",
    "icehockey_germany_del",
    "icehockey_switzerland_nla",
    "icehockey_world_championship",
]

LEAGUE_NAMES = {
    "icehockey_nhl": "NHL",
    "icehockey_czech_extraliga": "Česká Extraliga",
    "icehockey_sweden_shl": "SHL (Švédsko)",
    "icehockey_finland_liiga": "Liiga (Finsko)",
    "icehockey_germany_del": "DEL (Německo)",
    "icehockey_switzerland_nla": "NLA (Švýcarsko)",
    "icehockey_world_championship": "MS v hokeji (IIHF)",
}


def discover_hockey_sports() -> list[str]:
    """Zjistí, které hokejové sporty jsou právě aktivní na API."""
    url = "https://api.the-odds-api.com/v4/sports"
    params = {"apiKey": API_KEY}
    try:
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        sports = resp.json()
        active = [s["key"] for s in sports if "hockey" in s.get("group", "").lower()
                  or "ice_hockey" in s.get("key", "")
                  or "icehockey" in s.get("key", "")]
        return active
    except Exception as e:
        print(f"  ⚠ Chyba při zjišťování sportů: {e}")
        return []


def fetch_odds(sport_key: str) -> list[dict]:
    """Stáhne Over/Under kurzy pro danou ligu."""
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"
    params = {
        "apiKey": API_KEY,
        "regions": "us,eu,uk",
        "markets": "totals",
        "oddsFormat": "decimal",
    }
    try:
        resp = requests.get(url, params=params, timeout=20)
        remaining = resp.headers.get("x-requests-remaining", "?")
        used = resp.headers.get("x-requests-used", "?")
        print(f"   (HTTP {resp.status_code}, použito: {used}, zbývá: {remaining})")

        if resp.status_code in (404, 422):
            print(f"   ⚠ API vrátilo {resp.status_code}: {resp.text[:200]}")
            return []
        resp.raise_for_status()
        data = resp.json()

        # Diagnostika: pokud API vrátí dict s chybou
        if isinstance(data, dict):
            print(f"   ⚠ API odpověď (dict): {json.dumps(data, indent=2)[:300]}")
            return []

        # Diagnostika: pokud prázdný seznam
        if isinstance(data, list) and len(data) == 0:
            print(f"   (API vrátilo prázdný seznam)")

        # Diagnostika: první zápas raw výpis (jen pro první ligu s daty)
        if isinstance(data, list) and len(data) > 0:
            print(f"   📦 API vrátilo {len(data)} zápasů")
            # Ukáž raw první zápas pro debug
            first = data[0]
            print(f"   🔍 Ukázkový zápas: {first.get('home_team')} vs {first.get('away_team')}")
            print(f"      commence_time: {first.get('commence_time')}")
            bks = first.get("bookmakers", [])
            print(f"      bookmakers: {len(bks)}")
            if bks:
                first_bk = bks[0]
                print(f"      první bookmaker: {first_bk.get('key')}")
                for mkt in first_bk.get("markets", []):
                    print(f"      market: {mkt.get('key')}, outcomes: {mkt.get('outcomes', [])[:3]}")

        return data if isinstance(data, list) else []
    except requests.RequestException as e:
        print(f"  ⚠ Chyba: {e}")
        return []


def fetch_odds_h2h_test(sport_key: str) -> list[dict]:
    """Diagnostický test: stáhne h2h kurzy (základní market) pro ověření API."""
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"
    params = {
        "apiKey": API_KEY,
        "regions": "us",
        "markets": "h2h",
        "oddsFormat": "decimal",
    }
    try:
        resp = requests.get(url, params=params, timeout=20)
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []


def extract_candidates(matches: list[dict], sport_key: str) -> list[dict]:
    """Z matchů vytáhne Over kandidáty – pouze zápasy do 24h."""
    candidates = []
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=24)

    for match in matches:
        home = match.get("home_team", "?")
        away = match.get("away_team", "?")
        commence = match.get("commence_time", "")
        match_label = f"{home} vs {away}"

        # Filtr: pouze zápasy začínající od teď do 24h
        try:
            match_time = datetime.fromisoformat(commence.replace("Z", "+00:00"))
            if match_time < now or match_time > cutoff:
                continue
        except (ValueError, AttributeError):
            continue

        # Diagnostika: vypsat VŠECHNY Over hodnoty pro tento zápas
        all_overs = []
        for bookmaker in match.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "totals":
                    continue
                for outcome in market.get("outcomes", []):
                    if outcome.get("name") != "Over":
                        continue
                    point = outcome.get("point", 0)
                    price = outcome.get("price", 0)
                    all_overs.append((point, price, bookmaker.get("key", "?")))

        if all_overs:
            # Zobrazit všechny nalezené Over hodnoty
            points_summary = sorted(set(p for p, _, _ in all_overs))
            print(f"   📋 {match_label}: Over body = {points_summary}")
            for pt, pr, bk in sorted(all_overs):
                print(f"      Over {pt} @ {pr} ({bk})")

        # Vybrat nejlepší Over: preferujeme 5.5, pak nejbližší (5.0, 6.0, 4.5...)
        best_price = None
        best_point = None
        for pt, pr, _ in all_overs:
            if pt < 4.5 or pt > 7.5:
                continue
            if pr < MIN_ODDS:
                continue
            dist = abs(pt - REQUIRED_POINT)
            if best_point is None:
                best_point = pt
                best_price = pr
            elif dist < abs(best_point - REQUIRED_POINT):
                best_point = pt
                best_price = pr
            elif dist == abs(best_point - REQUIRED_POINT) and pr > best_price:
                best_price = pr

        if best_price is not None:
            candidates.append({
                "league": LEAGUE_NAMES.get(sport_key, sport_key),
                "match": match_label,
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

    # Krok 1: Zjistit aktivní hokejové sporty
    print("🔍 Zjišťuji aktivní hokejové ligy na API...")
    active_sports = discover_hockey_sports()
    print(f"   Aktivní na API: {active_sports if active_sports else '(žádné nalezeny)'}")

    # Sloučit s naším seznamem
    all_sports = list(set(HOCKEY_SPORTS + active_sports))
    all_sports.sort()
    print(f"   Budu hledat v: {all_sports}")
    print()

    # Krok 2: Diagnostika – funguje API vůbec? Zkusíme h2h pro NHL
    print("🧪 Diagnostika: testuji API s h2h marketem pro NHL...")
    h2h_test = fetch_odds_h2h_test("icehockey_nhl")
    if h2h_test:
        print(f"   ✅ h2h test OK – {len(h2h_test)} zápasů nalezeno")
        print(f"   Ukázka: {h2h_test[0].get('home_team')} vs {h2h_test[0].get('away_team')}")
    else:
        print("   ⚠ h2h test vrátil 0 zápasů – API klíč nebo NHL sezóna?")
    print()

    all_candidates = []

    for sport in all_sports:
        league_name = LEAGUE_NAMES.get(sport, sport)
        print(f"📡 Stahuji: {league_name}...")
        matches = fetch_odds(sport)
        if not matches:
            print(f"   (žádné zápasy v příštích 24h)")
            continue

        print(f"   Nalezeno {len(matches)} zápasů, hledám Over...")
        candidates = extract_candidates(matches, sport)
        if candidates:
            print(f"   ✅ {len(candidates)} kandidátů s kurzem >= {MIN_ODDS}")
        else:
            print(f"   ❌ žádný Over >= {MIN_ODDS}")
        all_candidates.extend(candidates)
        print()

    print(f"📊 Celkem kandidátů: {len(all_candidates)}")

    if len(all_candidates) == 0:
        print("⚠ Žádné zápasy splňující kritéria. Zapisuji prázdný JSON.")
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
