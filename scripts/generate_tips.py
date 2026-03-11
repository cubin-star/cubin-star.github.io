"""Denní generátor hokejových tipů Over 5.5 pro MAUI appku.

Stáhne kurzy z API-Sports Hockey API pro všechny dostupné hokejové ligy,
najde Over 5.5 gólů s kurzem >= 1.75 a vybere 2 zápasy z různých lig
s NEJVYŠŠÍ pravděpodobností překročení Over 5.5 (na základě statistik týmů).

Metoda výběru (Goal Scoring Index):
  1. Pro každý kandidátský zápas stáhne posledních 5 zápasů obou týmů
  2. Spočítá průměr celkových gólů na zápas pro každý tým
  3. Odhadne očekávaný počet gólů v zápase
  4. Vybere zápasy s nejvyšším GSI (= největší šance na Over 5.5)

API: https://v1.hockey.api-sports.io
Free plan: 100 requestů/den

Výstup: hokey.json (formát kompatibilní s TodaysTipsPage.xaml.cs)
"""

import os
import sys
import json
import time
import requests
from datetime import datetime, timezone

API_KEY = os.environ.get("API_HOCKEY_KEY", "")
BASE_URL = "https://v1.hockey.api-sports.io"
MIN_ODDS = 1.75
OUTPUT_FILE = "hokey.json"
LAST_N_GAMES = 10  # Kolik posledních zápasů analyzovat pro každý tým

# Země, ze kterých se v ČR nedá sázet
BLOCKED_COUNTRIES = {"russia", "belarus"}

# Klíčová slova v názvu ligy → přeskočit
BLOCKED_LEAGUE_KEYWORDS = {"university", "universiade", "college", "ncaa", "u18", "u20", "ullh"}

# Bookmaři s nespolehlivými kurzy (exchange)
SKIP_BOOKMAKERS = {"betfair", "betfair exchange", "smarkets", "matchbook"}


def api_get(endpoint: str, params: dict | None = None) -> list:
    """API-Sports Hockey GET request (max 10 req/min)."""
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

    # Pauza 7s mezi requesty (free plan = max 10 req/min)
    time.sleep(7)
    return results


def fetch_team_stats(team_id: int, team_name: str) -> dict | None:
    """Komplexní analýza posledních N zápasů týmu.

    Vrátí:
      avg_scored    – průměr vstřelených gólů
      avg_conceded  – průměr obdržených gólů
      avg_total     – průměr celkových gólů na zápas
      over55_rate   – kolik % zápasů překročilo 5.5 (= 6+ gólů)
      trend         – rozdíl: průměr posledních 3 vs celkový průměr
      games         – počet analyzovaných zápasů
    """
    print(f"      📊 {team_name} (id={team_id})...")
    games = api_get("games", {"team": team_id, "last": LAST_N_GAMES})

    if not games:
        return None

    scored_list = []
    conceded_list = []
    total_list = []

    for g in games:
        home_id = g.get("teams", {}).get("home", {}).get("id")
        scores = g.get("scores", {})
        h = scores.get("home")
        a = scores.get("away")

        if h is None or a is None:
            continue

        h, a = int(h), int(a)
        total_list.append(h + a)

        if team_id == home_id:
            scored_list.append(h)
            conceded_list.append(a)
        else:
            scored_list.append(a)
            conceded_list.append(h)

    n = len(total_list)
    if n == 0:
        return None

    avg_scored = sum(scored_list) / n
    avg_conceded = sum(conceded_list) / n
    avg_total = sum(total_list) / n
    over55_count = sum(1 for t in total_list if t >= 6)
    over55_rate = over55_count / n

    # Trend: posledních 3 vs celkový průměr (kladný = góly rostou)
    if n >= 4:
        recent_avg = sum(total_list[:3]) / 3  # API vrací nejnovější první
        trend = recent_avg - avg_total
    else:
        trend = 0.0

    print(f"         → {n} zápasů | vstřeleno={avg_scored:.1f} obdrženo={avg_conceded:.1f}"
          f" celkem={avg_total:.1f} | Over5.5={over55_count}/{n} ({over55_rate:.0%})"
          f" | trend={trend:+.1f}")

    return {
        "avg_scored": avg_scored,
        "avg_conceded": avg_conceded,
        "avg_total": avg_total,
        "over55_rate": over55_rate,
        "trend": trend,
        "games": n,
    }


def calculate_match_gsi(home: dict, away: dict) -> tuple[float, str]:
    """Výpočet Goal Scoring Indexu pro zápas.

    Kombinuje 4 faktory:
      1. Matchup (40 %) – útok domácích vs obrana hostů a naopak
      2. Historický průměr (25 %) – celkové góly ze zápasů obou týmů
      3. Over 5.5 spolehlivost (25 %) – jak často týmy překračují 5.5
      4. Trend (10 %) – stoupající forma = bonus

    Vrátí (gsi_score, breakdown_text).
    """
    # Faktor 1: MATCHUP – útok jednoho týmu vs obrana druhého
    # "Kolik gólů očekáváme když domácí útočí na hosty a naopak"
    matchup = (home["avg_scored"] + away["avg_conceded"]
               + away["avg_scored"] + home["avg_conceded"]) / 2

    # Faktor 2: HISTORICKÝ PRŮMĚR – jak brankově náročné jsou zápasy obou týmů
    historical = (home["avg_total"] + away["avg_total"]) / 2

    # Faktor 3: OVER 5.5 SPOLEHLIVOST – jak často oba týmy překračují 5.5
    # Přepočteno na škálu gólů: 100% rate → +2.0, 50% → +1.0, 0% → 0
    avg_rate = (home["over55_rate"] + away["over55_rate"]) / 2
    reliability = avg_rate * 2.0  # max +2.0

    # Faktor 4: TREND – stoupající forma = bonus, klesající = malus
    trend = (home["trend"] + away["trend"]) / 2
    trend_bonus = max(-0.5, min(trend * 0.3, 0.5))  # omezeno na -0.5 až +0.5

    # Kompozitní GSI
    gsi = (matchup * 0.40
           + historical * 0.25
           + reliability * 0.25
           + trend_bonus)

    breakdown = (f"matchup={matchup:.1f} hist={historical:.1f}"
                 f" over55={avg_rate:.0%} trend={trend:+.1f}")

    return round(gsi, 2), breakdown


def clean_candidate(c: dict) -> dict:
    """Odstraní interní pole začínající na '_'."""
    return {k: v for k, v in c.items() if not k.startswith("_")}


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

    # Vyřadit Rusko a Bělorusko
    before = len(ns_games)
    ns_games = [
        g for g in ns_games
        if g.get("country", {}).get("name", "").lower() not in BLOCKED_COUNTRIES
        and g.get("league", {}).get("country", "").lower() not in BLOCKED_COUNTRIES
    ]
    blocked = before - len(ns_games)
    if blocked:
        print(f"   🚫 Vyřazeno {blocked} zápasů (Rusko/Bělorusko)")

    # Vyřadit univerzitní ligy
    before = len(ns_games)
    ns_games = [
        g for g in ns_games
        if not any(kw in g.get("league", {}).get("name", "").lower() for kw in BLOCKED_LEAGUE_KEYWORDS)
    ]
    blocked = before - len(ns_games)
    if blocked:
        print(f"   🚫 Vyřazeno {blocked} zápasů (univerzitní ligy)")

    print(f"   Zápasy k analýze: {len(ns_games)}")
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
        home_id = game.get("teams", {}).get("home", {}).get("id")
        away_id = game.get("teams", {}).get("away", {}).get("id")
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

                if bk_name.lower() in SKIP_BOOKMAKERS:
                    continue

                for bet in bookmaker.get("bets", []):
                    for value in bet.get("values", []):
                        val = str(value.get("value", ""))
                        odd_str = str(value.get("odd", "0"))

                        try:
                            price = float(odd_str)
                        except (ValueError, TypeError):
                            continue

                        if val == "Over 5.5" and 1.50 <= price <= 4.00:
                            if best_price is None or price < best_price:
                                best_price = price
                                best_bookmaker = bk_name

        if best_price is not None and best_price >= MIN_ODDS:
            print(f"   ✅ Over 5.5 @ {best_price} ({best_bookmaker})")
            all_candidates.append({
                "league": league_name,
                "match": match_label,
                "tip": "Over 5.5",
                "odds": str(round(best_price, 2)),
                "_home_id": home_id,
                "_away_id": away_id,
                "_home_name": home,
                "_away_name": away,
            })
        elif best_price is not None:
            print(f"   ⚠ Over 5.5 nalezen, ale kurz {best_price} < {MIN_ODDS}")
        else:
            print(f"   ❌ žádný Over 5.5")
        print()

    print(f"📊 Celkem kandidátů: {len(all_candidates)}")

    if not all_candidates:
        print("⚠ Žádné zápasy splňující kritéria. Zapisuji prázdný JSON.")
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2, ensure_ascii=False)
        return

    # ─── Krok 3: Statistické hodnocení (Goal Scoring Index) ───
    # Pro každý kandidátský zápas analyzuje posledních 10 zápasů obou týmů:
    #   - útočná síla vs obranná slabost soupeře
    #   - kolik % zápasů překročilo Over 5.5
    #   - trend (stoupají/klesají góly v posledních zápasech)
    print()
    print("📈 Hloubková analýza kandidátů (posl. 10 zápasů / tým)...")
    print()

    team_cache: dict[int, dict | None] = {}

    for c in all_candidates:
        home_id = c["_home_id"]
        away_id = c["_away_id"]

        print(f"   ⚔ {c['match']} ({c['league']})")

        if home_id not in team_cache:
            team_cache[home_id] = fetch_team_stats(home_id, c["_home_name"])
        if away_id not in team_cache:
            team_cache[away_id] = fetch_team_stats(away_id, c["_away_name"])

        home_stats = team_cache[home_id]
        away_stats = team_cache[away_id]

        if home_stats and away_stats:
            gsi, breakdown = calculate_match_gsi(home_stats, away_stats)
            c["_gsi"] = gsi
            print(f"      → GSI = {gsi:.1f} ({breakdown})")
        else:
            c["_gsi"] = 0.0
            print(f"      → GSI = 0.0 (nedostatek dat)")
        print()

    # Seřadit podle GSI sestupně
    all_candidates.sort(key=lambda x: x["_gsi"], reverse=True)

    print("🏆 Pořadí podle GSI (Goal Scoring Index):")
    for i, c in enumerate(all_candidates, 1):
        marker = "⭐" if c["_gsi"] >= 4.0 else "  "
        print(f"   {marker} {i}. GSI={c['_gsi']:.1f} | {c['league']}: {c['match']} @ {c['odds']}")

    # ─── Krok 4: Vyber TOP 2 z různých soutěží ───
    # Bereme nejlepší GSI, ale každý z jiné ligy
    picked = []
    used_leagues = set()

    for c in all_candidates:
        if c["league"] in used_leagues:
            continue
        picked.append(c)
        used_leagues.add(c["league"])
        if len(picked) >= 2:
            break

    # Pokud jen 1 liga, vezmeme alespoň 1 zápas
    if not picked and all_candidates:
        picked.append(all_candidates[0])

    print()
    print(f"✅ Vybrané tipy ({len(picked)}):")
    for t in picked:
        print(f"   {t['league']}: {t['match']} → {t['tip']} @ {t['odds']} (GSI={t['_gsi']:.1f})")

    # Vyčistit interní pole před zápisem
    result = [clean_candidate(t) for t in picked]

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n💾 Zapsáno do {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
