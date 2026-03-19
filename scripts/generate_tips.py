"""Denní generátor hokejových tipů Over 5.5 pro MAUI appku.

Stáhne kurzy z API-Sports Hockey API pro všechny dostupné hokejové ligy,
najde Over 5.5 gólů s kurzem >= 1.75 a náhodně vybere 2 zápasy z různých lig.

Metoda výběru (Goal Scoring Index – normalizovaný 0–10, 6 faktorů):
  1. Pro každý kandidátský zápas stáhne posledních 20 zápasů obou týmů
  2. Stáhne vzájemné zápasy (H2H) obou týmů
  3. Spočítá GSI kombinující:
       Over 5.5 historie (30 %) – jak často týmy překračují 5.5
       Matchup (20 %)           – útok vs obrana
       H2H (20 %)               – přímá vzájemná historie
       Historický průměr (15 %) – průměr celkových gólů
       Home/Away kontext (10 %) – výkon doma vs venku
       Trend (5 %)              – stoupající/klesající forma
  4. Penalizuje zápasy s malým vzorkem dat
  5. Seřadí kandidáty a náhodně vybere 2 z různých lig

API: https://v1.hockey.api-sports.io
Premium plan: 7 500 requestů/den, 300 req/min

Výstup: hokey.json (formát kompatibilní s TodaysTipsPage.xaml.cs)
"""

import os
import sys
import json
import time
import random
import requests
from datetime import datetime, timezone

API_KEY = os.environ.get("API_HOCKEY_KEY", "")
BASE_URL = "https://v1.hockey.api-sports.io"
MIN_ODDS = 1.75
OUTPUT_FILE = "hokey.json"
LAST_N_GAMES = 20   # Premium: větší vzorek pro spolehlivější statistiku
H2H_LAST = 10       # Kolik vzájemných zápasů analyzovat

# Země, ze kterých se v ČR nedá sázet
BLOCKED_COUNTRIES = {"russia", "belarus"}

# Klíčová slova v názvu ligy → přeskočit
BLOCKED_LEAGUE_KEYWORDS = {"university", "universiade", "college", "ncaa", "u18", "u20", "ullh"}

# Bookmaři s nespolehlivými kurzy (exchange)
SKIP_BOOKMAKERS = {"betfair", "betfair exchange", "smarkets", "matchbook"}

# Počítadlo requestů
_request_count = 0


def api_get(endpoint: str, params: dict | None = None) -> list:
    """API-Sports Hockey GET request (Premium: 300 req/min)."""
    global _request_count
    url = f"{BASE_URL}/{endpoint}"
    headers = {"x-apisports-key": API_KEY}
    resp = requests.get(url, headers=headers, params=params or {}, timeout=20)
    _request_count += 1

    remaining = resp.headers.get("x-ratelimit-requests-remaining", "?")
    print(f"   (req #{_request_count}, API zbývá: {remaining})")
    resp.raise_for_status()
    data = resp.json()

    errors = data.get("errors")
    if errors and (isinstance(errors, dict) and errors or isinstance(errors, list) and errors):
        print(f"   ⚠ API chyba: {errors}")
        return []

    results = data.get("response", [])
    print(f"   → {len(results)} výsledků")

    # Premium plan: 300 req/min → pauza 0.3s (bezpečná rezerva)
    time.sleep(0.3)
    return results


def fetch_team_stats(team_id: int, team_name: str) -> dict | None:
    """Komplexní analýza posledních N zápasů týmu včetně home/away splitů.

    Vrátí:
      avg_scored       – průměr vstřelených gólů
      avg_conceded     – průměr obdržených gólů
      avg_total        – průměr celkových gólů na zápas
      over55_rate      – kolik % zápasů překročilo 5.5 (= 6+ gólů)
      home_avg_total   – průměr celkových gólů v domácích zápasech
      home_over55_rate – Over 5.5 rate v domácích zápasech
      away_avg_total   – průměr celkových gólů ve venkovních zápasech
      away_over55_rate – Over 5.5 rate ve venkovních zápasech
      trend            – rozdíl: průměr posledních 5 vs celkový průměr
      games            – počet analyzovaných zápasů
    """
    print(f"      📊 {team_name} (id={team_id})...")
    games = api_get("games", {"team": team_id, "last": LAST_N_GAMES})

    if not games:
        return None

    scored_list = []
    conceded_list = []
    total_list = []
    home_totals = []
    away_totals = []

    for g in games:
        home_id = g.get("teams", {}).get("home", {}).get("id")
        scores = g.get("scores", {})
        h = scores.get("home")
        a = scores.get("away")

        if h is None or a is None:
            continue

        h, a = int(h), int(a)
        total = h + a
        total_list.append(total)

        if team_id == home_id:
            scored_list.append(h)
            conceded_list.append(a)
            home_totals.append(total)
        else:
            scored_list.append(a)
            conceded_list.append(h)
            away_totals.append(total)

    n = len(total_list)
    if n == 0:
        return None

    avg_scored = sum(scored_list) / n
    avg_conceded = sum(conceded_list) / n
    avg_total = sum(total_list) / n
    over55_count = sum(1 for t in total_list if t >= 6)
    over55_rate = over55_count / n

    # Home/Away splity
    if home_totals:
        home_avg_total = sum(home_totals) / len(home_totals)
        home_over55_rate = sum(1 for t in home_totals if t >= 6) / len(home_totals)
    else:
        home_avg_total = avg_total
        home_over55_rate = over55_rate

    if away_totals:
        away_avg_total = sum(away_totals) / len(away_totals)
        away_over55_rate = sum(1 for t in away_totals if t >= 6) / len(away_totals)
    else:
        away_avg_total = avg_total
        away_over55_rate = over55_rate

    # Trend: posledních 5 vs celkový průměr (kladný = góly rostou)
    if n >= 6:
        recent_avg = sum(total_list[:5]) / 5  # API vrací nejnovější první
        trend = recent_avg - avg_total
    else:
        trend = 0.0

    print(f"         → {n} zápasů | vstřeleno={avg_scored:.1f} obdrženo={avg_conceded:.1f}"
          f" celkem={avg_total:.1f} | Over5.5={over55_count}/{n} ({over55_rate:.0%})"
          f" | 🏠{home_avg_total:.1f}({home_over55_rate:.0%})"
          f" ✈{away_avg_total:.1f}({away_over55_rate:.0%})"
          f" | trend={trend:+.1f}")

    return {
        "avg_scored": avg_scored,
        "avg_conceded": avg_conceded,
        "avg_total": avg_total,
        "over55_rate": over55_rate,
        "home_avg_total": home_avg_total,
        "home_over55_rate": home_over55_rate,
        "away_avg_total": away_avg_total,
        "away_over55_rate": away_over55_rate,
        "trend": trend,
        "games": n,
    }


def fetch_h2h_stats(home_id: int, away_id: int,
                    home_name: str, away_name: str) -> dict | None:
    """Analýza vzájemných zápasů (Head-to-Head).

    Vrátí:
      avg_total    – průměr celkových gólů ve vzájemných zápasech
      over55_rate  – kolik % vzájemných zápasů překročilo 5.5
      games        – počet analyzovaných vzájemných zápasů
    """
    print(f"      🤝 H2H: {home_name} vs {away_name}...")
    games = api_get("games/h2h", {"h2h": f"{home_id}-{away_id}", "last": H2H_LAST})

    if not games:
        print(f"         → žádná H2H data")
        return None

    total_list = []
    for g in games:
        scores = g.get("scores", {})
        h = scores.get("home")
        a = scores.get("away")
        if h is None or a is None:
            continue
        total_list.append(int(h) + int(a))

    n = len(total_list)
    if n == 0:
        print(f"         → žádná H2H data s výsledky")
        return None

    avg_total = sum(total_list) / n
    over55_count = sum(1 for t in total_list if t >= 6)
    over55_rate = over55_count / n

    print(f"         → {n} vzájemných | celkem={avg_total:.1f}"
          f" | Over5.5={over55_count}/{n} ({over55_rate:.0%})")

    return {
        "avg_total": avg_total,
        "over55_rate": over55_rate,
        "games": n,
    }


def calculate_match_gsi(home: dict, away: dict,
                        h2h: dict | None = None) -> tuple[float, str]:
    """Výpočet Goal Scoring Indexu pro zápas (Premium – 6 faktorů).

    Všechny faktory normalizovány na škálu 0–10.

    Kombinuje 6 faktorů:
      1. Over 5.5 historie (30 %) – jak často týmy překračují 5.5
      2. Matchup (20 %) – útok domácích vs obrana hostů a naopak
      3. H2H (20 %) – přímá vzájemná historie Over 5.5
      4. Historický průměr (15 %) – celkové góly ze zápasů obou týmů
      5. Home/Away kontext (10 %) – výkon domácích doma + hostů venku
      6. Trend (5 %) – stoupající forma = bonus

    Pokud H2H data nejsou k dispozici, váha se přerozdělí.
    Penalizace za malý vzorek dat (< 10 zápasů).

    Vrátí (gsi_score, breakdown_text).
    """
    # ─── Faktor 1: OVER 5.5 HISTORIE (přímý prediktor) ───
    avg_rate = (home["over55_rate"] + away["over55_rate"]) / 2
    over55_score = avg_rate * 10.0

    # ─── Faktor 2: MATCHUP – útok vs obrana ───
    matchup_raw = (home["avg_scored"] + away["avg_conceded"]
                   + away["avg_scored"] + home["avg_conceded"]) / 2
    matchup_score = max(0.0, min((matchup_raw - 3.0) / 6.0 * 10.0, 10.0))

    # ─── Faktor 3: H2H – vzájemná historie ───
    if h2h and h2h["games"] >= 2:
        h2h_score = h2h["over55_rate"] * 10.0
        # Bonus za vysoký průměr gólů ve vzájemných zápasech
        h2h_avg_bonus = max(0.0, min((h2h["avg_total"] - 4.0) / 5.0 * 3.0, 3.0))
        h2h_score = min(h2h_score + h2h_avg_bonus, 10.0)
        has_h2h = True
    else:
        h2h_score = 0.0
        has_h2h = False

    # ─── Faktor 4: HISTORICKÝ PRŮMĚR – celkové góly ───
    historical_raw = (home["avg_total"] + away["avg_total"]) / 2
    historical_score = max(0.0, min((historical_raw - 3.0) / 6.0 * 10.0, 10.0))

    # ─── Faktor 5: HOME/AWAY KONTEXT ───
    # Domácí: jak brankové jsou jejich domácí zápasy
    # Hosté: jak brankové jsou jejich venkovní zápasy
    context_rate = (home["home_over55_rate"] + away["away_over55_rate"]) / 2
    context_avg = (home["home_avg_total"] + away["away_avg_total"]) / 2
    context_score = (context_rate * 10.0 * 0.6
                     + max(0.0, min((context_avg - 3.0) / 6.0 * 10.0, 10.0)) * 0.4)

    # ─── Faktor 6: TREND – stoupající forma ───
    trend = (home["trend"] + away["trend"]) / 2
    trend_score = max(0.0, min(5.0 + trend * 1.5, 10.0))

    # ─── Váhy (přerozdělení pokud chybí H2H) ───
    if has_h2h:
        w_over55, w_matchup, w_h2h, w_hist, w_context, w_trend = (
            0.30, 0.20, 0.20, 0.15, 0.10, 0.05)
    else:
        # Bez H2H: váhu 20 % přerozdělíme → over55 +10 %, matchup +5 %, context +5 %
        w_over55, w_matchup, w_h2h, w_hist, w_context, w_trend = (
            0.40, 0.25, 0.00, 0.15, 0.15, 0.05)

    # ─── Penalizace za malý vzorek (< 10 zápasů) ───
    min_games = min(home["games"], away["games"])
    sample_factor = min(min_games / 10.0, 1.0)

    # ─── Kompozitní GSI (0–10) ───
    gsi_raw = (over55_score * w_over55
               + matchup_score * w_matchup
               + h2h_score * w_h2h
               + historical_score * w_hist
               + context_score * w_context
               + trend_score * w_trend)
    gsi = round(gsi_raw * sample_factor, 2)

    h2h_tag = f"h2h={h2h['over55_rate']:.0%}({h2h_score:.1f})" if has_h2h else "h2h=N/A"
    breakdown = (f"over55={avg_rate:.0%}({over55_score:.1f})"
                 f" matchup={matchup_raw:.1f}({matchup_score:.1f})"
                 f" {h2h_tag}"
                 f" hist={historical_raw:.1f}({historical_score:.1f})"
                 f" 🏠✈={context_score:.1f}"
                 f" trend={trend:+.1f}({trend_score:.1f})"
                 f" n={min_games}")

    return gsi, breakdown


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

    # ─── Krok 3: Statistické hodnocení (Goal Scoring Index – 6 faktorů) ───
    print()
    print(f"📈 Hloubková analýza kandidátů (posl. {LAST_N_GAMES} zápasů + H2H)...")
    print()

    team_cache: dict[int, dict | None] = {}
    h2h_cache: dict[str, dict | None] = {}

    for c in all_candidates:
        home_id = c["_home_id"]
        away_id = c["_away_id"]

        print(f"   ⚔ {c['match']} ({c['league']})")

        # Team stats (s cache)
        if home_id not in team_cache:
            team_cache[home_id] = fetch_team_stats(home_id, c["_home_name"])
        if away_id not in team_cache:
            team_cache[away_id] = fetch_team_stats(away_id, c["_away_name"])

        # H2H stats (s cache)
        h2h_key = f"{min(home_id, away_id)}-{max(home_id, away_id)}"
        if h2h_key not in h2h_cache:
            h2h_cache[h2h_key] = fetch_h2h_stats(
                home_id, away_id, c["_home_name"], c["_away_name"])

        home_stats = team_cache[home_id]
        away_stats = team_cache[away_id]
        h2h_stats = h2h_cache[h2h_key]

        if home_stats and away_stats:
            gsi, breakdown = calculate_match_gsi(home_stats, away_stats, h2h_stats)
            c["_gsi"] = gsi
            print(f"      → GSI = {gsi:.1f} ({breakdown})")
        else:
            c["_gsi"] = 0.0
            print(f"      → GSI = 0.0 (nedostatek dat, fallback)")
        print()

    # Seřadit podle GSI sestupně
    all_candidates.sort(key=lambda x: x["_gsi"], reverse=True)

    print(f"🏆 Pořadí podle GSI – {len(all_candidates)} kandidátů:")
    for i, c in enumerate(all_candidates, 1):
        marker = "⭐" if c["_gsi"] >= 5.0 else "  "
        print(f"   {marker} {i}. GSI={c['_gsi']:.1f} | {c['league']}: {c['match']} @ {c['odds']}")

    # ─── Krok 4: Vyber náhodně 2 z různých soutěží ───
    # Ze všech kandidátů vybereme náhodně 2 z různých lig
    picked = []

    # Seskupíme kandidáty podle ligy
    by_league: dict[str, list[dict]] = {}
    for c in all_candidates:
        by_league.setdefault(c["league"], []).append(c)

    if len(by_league) >= 2:
        # Náhodně vybereme 2 různé ligy a z každé náhodného kandidáta
        chosen_leagues = random.sample(list(by_league.keys()), 2)
        for lg in chosen_leagues:
            picked.append(random.choice(by_league[lg]))
    elif len(by_league) == 1:
        # Jen 1 liga – vybereme náhodně až 2 kandidáty z ní
        only_league = list(by_league.values())[0]
        picked = random.sample(only_league, min(2, len(only_league)))
    elif all_candidates:
        picked.append(random.choice(all_candidates))

    print()
    print(f"✅ Vybrané tipy ({len(picked)}):")
    for t in picked:
        print(f"   {t['league']}: {t['match']} → {t['tip']} @ {t['odds']} (GSI={t['_gsi']:.1f})")

    # Vyčistit interní pole před zápisem
    result = [clean_candidate(t) for t in picked]

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n💾 Zapsáno do {OUTPUT_FILE}")
    print(f"📊 Celkem spotřebováno {_request_count} requestů (z 7500)")


if __name__ == "__main__":
    main()
