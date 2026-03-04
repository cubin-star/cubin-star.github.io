"""
Bot pro automaticke vyhledavani tenisovych tipu (Over games).
Pouziva The Odds API (https://the-odds-api.com/).
Spousti se pres GitHub Actions kazdy den v 8:00 CET.
"""

import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError

API_KEY = os.environ.get("ODDS_API_KEY3", "")
BASE_URL = "https://api.the-odds-api.com/v4"

MIN_ODDS = 1.75
MAX_TIPS = 2
MAX_HOURS_AHEAD = 24  # Zapasy musi zacinat do 24h od spusteni bota
OUTPUT_FILE = "tenis.json"

# Cesky cas (CET=UTC+1, CEST=UTC+2)
CET = timezone(timedelta(hours=1))
CEST = timezone(timedelta(hours=2))


def get_available_tennis_sports():
    """Ziska seznam aktualne dostupnych tenisovych sportu/turnaju."""
    url = f"{BASE_URL}/sports/?apiKey={API_KEY}"
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req) as resp:
            sports = json.loads(resp.read().decode())
            return [
                s for s in sports
                if "tennis" in s.get("key", "").lower() and s.get("active", False)
            ]
    except URLError as e:
        print(f"Chyba pri nacitani sportu: {e}")
        return []


def get_odds_for_sport(sport_key):
    """Ziska kurzy pro dany tenisovy sport/turnaj (totals = over/under)."""
    url = (
        f"{BASE_URL}/sports/{sport_key}/odds/"
        f"?apiKey={API_KEY}"
        f"&regions=eu"
        f"&markets=totals"
        f"&oddsFormat=decimal"
    )
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except URLError as e:
        print(f"  Chyba pri nacitani kurzu pro {sport_key}: {e}")
        return []


def find_over_tips(events, sport_title):
    """Najde Over tipy s kurzem >= MIN_ODDS, pouze zapasy do 24h."""
    tips = []
    now = datetime.now(timezone.utc)
    deadline = now + timedelta(hours=MAX_HOURS_AHEAD)

    for event in events:
        home = event.get("home_team", "N/A")
        away = event.get("away_team", "N/A")
        commence = event.get("commence_time", "")

        # Kontrola casoveho okna: zapas musi byt v budoucnu A do 24h
        if commence:
            try:
                match_time = datetime.fromisoformat(commence.replace("Z", "+00:00"))
                if match_time < now:
                    continue  # Zapas uz zacal
                if match_time > deadline:
                    continue  # Zapas je dal nez 24h
            except ValueError:
                continue  # Neplatny cas, preskocit
        else:
            continue  # Bez casu nezarazujeme

        bookmakers = event.get("bookmakers", [])
        if not bookmakers:
            continue

        # Projit vsechny sazkove kancelare a najit Over trhy
        best_over = None
        best_price = 0

        for bookmaker in bookmakers:
            for market in bookmaker.get("markets", []):
                if market.get("key") != "totals":
                    continue

                for outcome in market.get("outcomes", []):
                    if outcome.get("name", "").lower() != "over":
                        continue

                    price = outcome.get("price", 0)
                    point = outcome.get("point", 0)

                    if price >= MIN_ODDS and price > best_price:
                        best_price = price
                        best_over = {
                            "league": sport_title,
                            "match": f"{home} vs {away}",
                            "tip": f"Over{point}",
                            "odds": str(round(price, 2)),
                            "commence_time": commence,
                            "bookmaker": bookmaker.get("title", "N/A"),
                        }

        if best_over:
            tips.append(best_over)

    return tips


def select_best_tips(all_tips, count):
    """
    Vybere tipy nahodne s preferencí ruznych turnaju:
    1. Seskupi tipy podle turnaje (league)
    2. Nahodne vybere turnaje
    3. Z kazdeho turnaje nahodne vybere 1 zapas
    -> Vysledek: kazdy tip je z jineho turnaje (pokud je to mozne)
    """
    if not all_tips:
        return []

    # Seskupit tipy podle turnaje
    by_league = {}
    for tip in all_tips:
        league = tip["league"]
        if league not in by_league:
            by_league[league] = []
        by_league[league].append(tip)

    print(f"  Turnaje s tipy: {list(by_league.keys())}")
    for league, tips in by_league.items():
        print(f"    {league}: {len(tips)} tipu")

    selected = []
    leagues = list(by_league.keys())
    random.shuffle(leagues)

    # 1. Z kazdeho turnaje vybrat nahodne 1 tip (ruzne turnaje)
    for league in leagues:
        if len(selected) >= count:
            break
        tip = random.choice(by_league[league])
        selected.append(tip)
        by_league[league].remove(tip)  # Odebrat aby se neopakoval

    # 2. Pokud nemame dost tipu, doplnit z zbyvajicich (i stejny turnaj)
    if len(selected) < count:
        remaining = [t for tips in by_league.values() for t in tips]
        random.shuffle(remaining)
        for tip in remaining:
            if tip["match"] not in {s["match"] for s in selected}:
                selected.append(tip)
            if len(selected) >= count:
                break

    return selected


def get_czech_now():
    """Vrati aktualni cesky cas (CET nebo CEST podle mesice)."""
    now_utc = datetime.now(timezone.utc)
    month = now_utc.month
    # Zjednodusene: CEST platí zhruba brezen-rijen
    if 3 <= month <= 10:
        return now_utc.astimezone(CEST)
    return now_utc.astimezone(CET)


def format_match_time(commence_time_str):
    """Prevede UTC cas zapasu na cesky cas (HH:MM)."""
    try:
        match_utc = datetime.fromisoformat(commence_time_str.replace("Z", "+00:00"))
        month = match_utc.month
        if 3 <= month <= 10:
            match_local = match_utc.astimezone(CEST)
        else:
            match_local = match_utc.astimezone(CET)
        return match_local.strftime("%H:%M")
    except (ValueError, AttributeError):
        return ""


def format_tips_for_json(tips):
    """Formatuje tipy pro vystupni JSON (kompatibilni s MAUI aplikaci)."""
    czech_now = get_czech_now()
    today = czech_now.strftime("%d.%m.%Y")
    return [
        {
            "league": tip["league"],
            "match": tip["match"],
            "tip": tip["tip"],
            "odds": tip["odds"],
            "date": today,
            "time": format_match_time(tip.get("commence_time", "")),
        }
        for tip in tips
    ]


def main():
    if not API_KEY:
        print("ERROR: ODDS_API_KEY3 neni nastaveny!")
        sys.exit(1)

    print(f"=== Tennis Over Tips Bot ({datetime.now().strftime('%d.%m.%Y %H:%M')}) ===")
    print(f"Minimalni kurz: {MIN_ODDS}")
    print(f"Pocet tipu: {MAX_TIPS}")
    print(f"Casove okno: zapasy do {MAX_HOURS_AHEAD}h od ted")
    print()

    # 1. Ziskat dostupne tenisove turnaje
    print("Hledam dostupne tenisove turnaje...")
    available_sports = get_available_tennis_sports()

    if not available_sports:
        print("Zadne tenisove turnaje nejsou aktualne dostupne.")
        print("Zapisuji prazdny JSON...")
        czech_now = get_czech_now()
        empty_json = {
            "updated_at": czech_now.strftime("%d.%m.%Y %H:%M"),
            "tips": []
        }
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(empty_json, f, ensure_ascii=False, indent=2)
        return

    print(f"Nalezeno {len(available_sports)} aktivnich turnaju:")
    for sport in available_sports:
        print(f"  - {sport['title']} ({sport['key']})")
    print()

    # 2. Ziskat kurzy pro kazdy turnaj
    all_tips = []

    for sport in available_sports:
        print(f"Nacitam kurzy pro: {sport['title']}...")
        events = get_odds_for_sport(sport["key"])
        print(f"  Nalezeno {len(events)} zapasu")

        tips = find_over_tips(events, sport["title"])
        print(f"  Nalezeno {len(tips)} Over tipu s kurzem >= {MIN_ODDS}")
        all_tips.extend(tips)

    print()
    print(f"Celkem nalezeno {len(all_tips)} tipu.")

    # 3. Vybrat nejlepsi tipy
    best_tips = select_best_tips(all_tips, MAX_TIPS)

    if not best_tips:
        print("Zadne vhodne tipy nenalezeny pro dnesni den.")
        best_tips = []

    # 4. Zapsat do JSON
    output = format_tips_for_json(best_tips)

    print()
    print("Vybrane tipy:")
    for tip in output:
        print(f"  {tip['league']}: {tip['match']}")
        print(f"    {tip['tip']} @ {tip['odds']}")
    print()

    # Obalit do objektu s updated_at casovym razitkem
    czech_now = get_czech_now()
    final_json = {
        "updated_at": czech_now.strftime("%d.%m.%Y %H:%M"),
        "tips": output
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(final_json, f, ensure_ascii=False, indent=2)

    print(f"Zapsano do {OUTPUT_FILE} ({len(output)} tipu)")
    print(f"Cas aktualizace: {czech_now.strftime('%d.%m.%Y %H:%M')} CET/CEST")


if __name__ == "__main__":
    main()
