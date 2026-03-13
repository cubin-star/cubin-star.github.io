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
ALLOWED_POINTS = {18.5, 19.5, 20.5, 21.5, 22.5}  # Povolene hranice pro Over
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
    """Najde Over tipy s kurzem >= MIN_ODDS, pouze zapasy do 24h.
    Sbira data od VSECH bookmakeru pro pozdejsi analyzu."""
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
                    continue
                if match_time > deadline:
                    continue
            except ValueError:
                continue
        else:
            continue

        bookmakers = event.get("bookmakers", [])
        if not bookmakers:
            continue

        # Sebrat VSECHNY Over nabidky od vsech bookmakeru
        all_over_lines = []   # [(point, price, bookmaker_name), ...]
        all_under_lines = []  # [(point, price, bookmaker_name), ...]

        for bookmaker in bookmakers:
            bk_name = bookmaker.get("title", "N/A")
            for market in bookmaker.get("markets", []):
                if market.get("key") != "totals":
                    continue
                for outcome in market.get("outcomes", []):
                    point = outcome.get("point", 0)
                    price = outcome.get("price", 0)
                    name = outcome.get("name", "").lower()

                    if name == "over":
                        all_over_lines.append((point, price, bk_name))
                    elif name == "under":
                        all_under_lines.append((point, price, bk_name))

        if not all_over_lines:
            continue

        # Najit nejlepsi povolenou nabidku (pro zobrazeni v JSON)
        valid_offers = [
            (pt, pr, bk) for pt, pr, bk in all_over_lines
            if pt in ALLOWED_POINTS and pr >= MIN_ODDS
        ]

        if not valid_offers:
            continue

        # Vybrat nabidku s nejnizsi hranici (nejsnazsi Over)
        valid_offers.sort(key=lambda x: x[0])
        best_point, best_price, best_bk = valid_offers[0]

        tips.append({
            "league": sport_title,
            "match": f"{home} vs {away}",
            "tip": f"Over{best_point}",
            "odds": str(round(best_price, 2)),
            "commence_time": commence,
            "bookmaker": best_bk,
            # Data pro analyzu
            "_all_over_lines": all_over_lines,
            "_all_under_lines": all_under_lines,
            "_bet_point": best_point,
        })

    return tips


def calculate_over_score(tip):
    """
    Vlastni metoda pro odhad pravdepodobnosti Over.
    NEPOUZIVA kurzy jako indikator pravdepodobnosti.

    Analyzuje TRZNI STRUKTURU - kam bookmakeri umistuji linku
    (= ocekavany pocet gamu), ne jake daji kurzy.

    Skore 0-100. Vyssi = vetsi sance ze Over vyjde.

    Faktory:
    1. Buffer (0-40b): Rozdil mezi ocekavanym totalem a nasi hranici.
       Cim vyse bookmakeri nastavuji linku NAD nasi hranici, tim spis Over projde.
    2. Shoda trhu (0-25b): Souhlasi bookmakeri kde linka je?
       Maly rozptyl = jisty odhad, velky rozptyl = nejistota.
    3. Hloubka trhu (0-15b): Kolik bookmakeru nabizi trh.
       Vic bookmakeru = lepe analyzovany zapas.
    4. Pozice hranice (0-20b): Nizsi hranice = prirozene snazsi Over.
       Over 18.5 je snazsi nez Over 22.5.
    """
    all_over = tip.get("_all_over_lines", [])
    bet_point = tip.get("_bet_point", 22.5)

    if not all_over:
        return 0

    # -- Vsechny linky (points) od bookmakeru --
    all_points = [pt for pt, _, _ in all_over]

    # Median = nejlepsi odhad "ocekavaneho totalu"
    sorted_points = sorted(all_points)
    n = len(sorted_points)
    if n % 2 == 1:
        market_median = sorted_points[n // 2]
    else:
        market_median = (sorted_points[n // 2 - 1] + sorted_points[n // 2]) / 2

    # --- FAKTOR 1: Buffer (0-40 bodu) ---
    # Rozdil mezi medianem trhu a nasi sazkou
    # Priklad: median=22.5, sazime Over 20.5 -> buffer=2.0 -> vysoke skore
    # Priklad: median=20.5, sazime Over 22.5 -> buffer=-2.0 -> nizke skore
    buffer = market_median - bet_point
    # Normalizace: buffer -2..+4 -> 0..40
    buffer_score = max(0, min(40, (buffer + 2) * (40 / 6)))

    # --- FAKTOR 2: Shoda trhu (0-25 bodu) ---
    # Rozptyl linek - maly rozptyl = bookmakeri se shoduji = jistejsi predikce
    if n >= 2:
        mean_pt = sum(all_points) / n
        variance = sum((p - mean_pt) ** 2 for p in all_points) / n
        std_dev = variance ** 0.5
        # std_dev 0 = perfektni shoda (25b), std_dev >= 2.5 = velka nejistota (0b)
        agreement_score = max(0, min(25, 25 * (1 - std_dev / 2.5)))
    else:
        agreement_score = 5  # Jen 1 bookmaker = malo dat

    # --- FAKTOR 3: Hloubka trhu (0-15 bodu) ---
    # Pocet unikatnich bookmakeru nabizejicich Over
    unique_bookmakers = len(set(bk for _, _, bk in all_over))
    # 1 bookmaker = 3b, 5+ = 15b
    depth_score = min(15, unique_bookmakers * 3)

    # --- FAKTOR 4: Pozice hranice (0-20 bodu) ---
    # Nizsi hranice = snazsi Over (v tenise je prumer ~22 gamu)
    # 18.5 -> 20b, 19.5 -> 16b, 20.5 -> 12b, 21.5 -> 8b, 22.5 -> 4b
    point_bonus = {18.5: 20, 19.5: 16, 20.5: 12, 21.5: 8, 22.5: 4}
    position_score = point_bonus.get(bet_point, 0)

    total_score = buffer_score + agreement_score + depth_score + position_score

    return round(total_score, 1)


def select_best_tips(all_tips, count):
    """
    Vybere tipy s nejvyssim over-skore, preferuje ruzne turnaje.
    1. Spocita over-skore pro kazdy tip
    2. Seradi podle skore (sestupne)
    3. Vybere nejlepsi tipy z ruznych turnaju
    """
    if not all_tips:
        return []

    # Spocitat skore pro kazdy tip
    for tip in all_tips:
        tip["_score"] = calculate_over_score(tip)

    # Seradit podle skore (nejvyssi prvni)
    all_tips.sort(key=lambda t: t["_score"], reverse=True)

    print()
    print("  === Analyza Over pravdepodobnosti ===")
    for tip in all_tips:
        over_lines = tip.get("_all_over_lines", [])
        points = [pt for pt, _, _ in over_lines]
        print(f"  {tip['match']}")
        print(f"    Tip: {tip['tip']} | Skore: {tip['_score']}/100")
        print(f"    Linky bookmakeru: {sorted(points)}")
        print(f"    Pocet bookmakeru: {len(set(bk for _, _, bk in over_lines))}")

    # Vybrat nejlepsi s preferencí ruznych turnaju
    selected = []
    used_leagues = set()

    # 1. pruchod: z kazdeho turnaje vzit nejlepsi tip
    for tip in all_tips:
        if len(selected) >= count:
            break
        league = tip["league"]
        if league not in used_leagues:
            selected.append(tip)
            used_leagues.add(league)

    # 2. pruchod: doplnit zbytkem (i stejny turnaj) podle skore
    if len(selected) < count:
        for tip in all_tips:
            if tip in selected:
                continue
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

