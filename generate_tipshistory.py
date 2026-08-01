#!/usr/bin/env python3
"""
Over 2.5 – Tips History Evaluator
Spouští se denně v 6:00 UTC přes GitHub Actions.

Čte tips.json (dva fotbalové zápasy = jeden tiket na daný den),
přes API-Football (api-sports.io) dohledá koncová skóre včerejších zápasů,
vyhodnotí Over/Under podle pole "tip" a připojí tiket do tipshistory.json.

Formát tipshistory.json (co čte MAUI aplikace):
[
  {
    "date": "2025-01-15T05:30:00Z",
    "matches": [
      { "league":"…", "match":"A vs B", "tip":"Over 2.5",
        "odds":"1.95", "score":"2:1", "result":"✓" },
      { "league":"…", "match":"C vs D", "tip":"Over 2.5",
        "odds":"2.10", "score":"1:0", "result":"✗" }
    ]
  },
  …
]

Pokud aspoň jeden zápas není dohraný, tiket se neuloží do history,
ale zůstane v pending_tipshistory.json a bude přehodnocen příští běh.
"""

import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

# ===== CONFIG =====
FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY1", "")

FOOTBALL_URL = "https://v3.football.api-sports.io"
DELAY = 0.35

TIPS_FILE = "tips.json"
HISTORY_FILE = "tipshistory.json"
PENDING_FILE = "pending_tipshistory.json"

MAX_HISTORY = 400          # kolik nejnovějších ticketů si držet
MATCH_BUFFER = timedelta(hours=2)   # kolik čekat po výkopu, než hledat výsledek

request_count = 0


# ===== HELPERS =====

def api_get(endpoint, params):
    global request_count
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{FOOTBALL_URL}/{endpoint}?{query}"
    req = urllib.request.Request(url)
    req.add_header("x-apisports-key", FOOTBALL_KEY)
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                request_count += 1
                data = json.loads(resp.read().decode("utf-8"))
                # API-Football vrací chyby v "errors" a i popísaný stav
                errors = data.get("errors")
                if errors:
                    print(f"  ! API errors for {endpoint}: {errors}")
                results = data.get("results")
                if results is not None:
                    print(f"  API {endpoint} results={results}")
                return data
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="ignore")[:200]
            except Exception:
                pass
            if e.code == 429:
                print(f"  HTTP 429 (rate limit), sleeping {5*attempt}s")
                time.sleep(5 * attempt)
            else:
                print(f"  HTTP {e.code} for {endpoint}: {body}")
                return {}
        except Exception as ex:
            print(f"  ERR {ex}")
            return {}
    return {}


import re
import unicodedata

# Slova, která u fotbalových klubů typicky přebývají a matou porovnání.
_STOPWORDS = {
    "fc", "cf", "sc", "sk", "ac", "afc", "cfc", "bk", "if", "ff",
    "club", "clube", "cd", "cp", "cs", "de", "la", "el", "los",
    "united", "utd", "city", "town",
}


def _strip_diacritics(s):
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize(name):
    """Normalizace pro porovnání jmen týmů: lowercase, bez diakritiky,
    pomlčky/podtržítka/tečky -> mezera, jen alfanumerické znaky."""
    s = _strip_diacritics(name or "").lower()
    s = re.sub(r"[\-_./]+", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _tokens(name):
    return [t for t in normalize(name).split() if t and t not in _STOPWORDS]


def names_match(api_name, pred_name):
    a = normalize(api_name)
    b = normalize(pred_name)
    if not a or not b:
        return False
    # 1) přesná / substringová shoda po normalizaci
    if a == b or a in b or b in a:
        return True
    # 2) token-based: alespoň jeden významný token musí sedět
    ta = set(_tokens(api_name))
    tb = set(_tokens(pred_name))
    if not ta or not tb:
        return False
    common = ta & tb
    # Aspoň jedno slovo se musí shodovat a mít >= 3 znaky (aby "as" nedělalo bordel)
    return any(len(w) >= 3 for w in common)


def parse_over_line(tip):
    """'Over 2.5' -> 2.5"""
    parts = (tip or "").strip().split()
    if len(parts) == 2 and parts[0].lower() == "over":
        try:
            return float(parts[1])
        except ValueError:
            pass
    return None


def parse_match_teams(match_str):
    parts = (match_str or "").split(" vs ")
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return None, None


def _get_ci(obj, *names):
    """Vrátí první ne-prázdnou hodnotu z objektu podle názvů (case-insensitive)."""
    if not isinstance(obj, dict):
        return ""
    lower = {k.lower(): v for k, v in obj.items()}
    for n in names:
        v = lower.get(n.lower())
        if v not in (None, ""):
            return v
    return ""


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ===== FIXTURE LOOKUP =====

def fetch_finished_fixtures(date_str):
    """Vrátí seznam všech dohraných zápasů daného dne (UTC)."""
    if not FOOTBALL_KEY:
        print("  ! API_FOOTBALL_KEY1 not set")
        return []
    time.sleep(DELAY)
    data = api_get("fixtures", {"date": date_str, "timezone": "UTC"})
    all_response = data.get("response", []) or []
    finished = []
    status_counter = {}
    for f in all_response:
        status = f.get("fixture", {}).get("status", {}).get("short", "")
        status_counter[status] = status_counter.get(status, 0) + 1
        if status not in ("FT", "AET", "PEN"):
            continue
        home = f.get("teams", {}).get("home", {}).get("name", "")
        away = f.get("teams", {}).get("away", {}).get("name", "")
        gh = f.get("goals", {}).get("home", 0) or 0
        ga = f.get("goals", {}).get("away", 0) or 0
        finished.append({
            "home": home,
            "away": away,
            "total": gh + ga,
            "score": f"{gh}:{ga}",
        })
    print(f"    -> {date_str}: {len(all_response)} total, {len(finished)} finished, statuses={status_counter}")
    return finished


def find_score(match_str, fixtures):
    home, away = parse_match_teams(match_str)
    if not home or not away:
        return None
    for fx in fixtures:
        if names_match(fx["home"], home) and names_match(fx["away"], away):
            return fx
    # Zkus i obrácené pořadí (kdyby byly týmy prohozené v tips.json)
    for fx in fixtures:
        if names_match(fx["home"], away) and names_match(fx["away"], home):
            return {"home": fx["away"], "away": fx["home"],
                    "total": fx["total"],
                    "score": fx["score"].split(":")[1] + ":" + fx["score"].split(":")[0]}
    return None


def _debug_dump_similar(match_str, fixtures, limit=8):
    """Když zápas nenajdeme, vypíše kandidáty, kde alespoň jeden tým částečně sedí."""
    home, away = parse_match_teams(match_str)
    if not home or not away or not fixtures:
        return
    candidates = []
    for fx in fixtures:
        if (names_match(fx["home"], home) or names_match(fx["away"], away)
            or names_match(fx["home"], away) or names_match(fx["away"], home)):
            candidates.append(fx)
    if candidates:
        print(f"    ? Similar fixtures for '{match_str}':")
        for fx in candidates[:limit]:
            print(f"        - {fx['home']} vs {fx['away']}  ({fx['score']})")
    else:
        # Ukaž prvních pár, ať víme, že vůbec něco přišlo z API
        print(f"    ? No fixture matched '{match_str}'. First few from API this day:")
        for fx in fixtures[:limit]:
            print(f"        - {fx['home']} vs {fx['away']}  ({fx['score']})")


def evaluate_match(item, fixtures_by_date, target_date_str):
    """Vrátí (score, verdict) nebo None pokud zápas není dohraný / nenalezen."""
    match_str = item.get("match", "")

    # Zápas může být v tips.json bez data – vyhledáme ve včerejším i dnešním dni
    dates_to_try = [target_date_str]
    yday = (datetime.fromisoformat(target_date_str) - timedelta(days=1)).date().isoformat()
    tmrw = (datetime.fromisoformat(target_date_str) + timedelta(days=1)).date().isoformat()
    dates_to_try.extend([yday, tmrw])

    for d in dates_to_try:
        if d not in fixtures_by_date:
            print(f"    Fetching fixtures for {d} …")
            fixtures_by_date[d] = fetch_finished_fixtures(d)
        hit = find_score(match_str, fixtures_by_date[d])
        if hit:
            line = parse_over_line(item.get("tip", ""))
            if line is None:
                return None
            verdict = "\u2713" if hit["total"] > line else "\u2717"
            return hit["score"], verdict
    # Nic nenalezeno – vypiš kandidáty pro cílový den, ať víme, jak API pojmenovává týmy
    _debug_dump_similar(match_str, fixtures_by_date.get(target_date_str, []))
    return None


def item_key(m):
    return f"{m.get('match', '')}|{m.get('tip', '')}|{m.get('odds', '')}"


def ticket_key(ticket):
    parts = [item_key(m) for m in ticket.get("matches", [])]
    return "||".join(sorted(parts))


# ===== MAIN =====

def main():
    now = datetime.now(timezone.utc)
    print("== Over Tips History Evaluator ==")
    print(f"Time: {now.strftime('%Y-%m-%d %H:%M UTC')}\n")

    if not FOOTBALL_KEY:
        print("No API_FOOTBALL_KEY1 – nothing will be evaluated.")

    # 1) Načíst pending tikety z minula
    pending = load_json(PENDING_FILE, [])
    # Vyhodit rozbité tikety s prázdnými match stringy (z buggy předchozí verze)
    original_pending = len(pending)
    pending = [p for p in pending
               if any((m.get("match") or "").strip() for m in p.get("matches", []))]
    if len(pending) != original_pending:
        print(f"  cleaned {original_pending - len(pending)} broken pending ticket(s) with empty matches")
    print(f"  Pending from previous run: {len(pending)}")

    # 2) Přidat dnešní tiket z tips.json (pokud tam ještě není)
    tips = load_json(TIPS_FILE, [])
    if isinstance(tips, dict):
        tips = tips.get("tips", [])
    if tips:
        # Datum tiketu = včerejší den (zápasy se hrály včera, bot je vyhodnocuje dnes ráno).
        # Aplikace zobrazuje jen datum, čas není důležitý.
        yesterday = (now.date() - timedelta(days=1)).isoformat() + "T05:30:00Z"
        matches = []
        for t in tips:
            matches.append({
                "league": _get_ci(t, "league", "League", "division", "competition"),
                "match":  _get_ci(t, "match", "Match", "fixture", "game"),
                "tip":    _get_ci(t, "tip", "Tip", "pick", "prediction"),
                "odds":   str(_get_ci(t, "odds", "Odds", "price", "kurs", "kurt")),
            })
        # Odstranit prázdné položky (bez match string)
        matches = [m for m in matches if m["match"]]
        new_ticket = {"date": yesterday, "matches": matches}
        existing_keys = {ticket_key(p) for p in pending}
        if not matches:
            print(f"  ! tips.json parsed but no valid matches (check field names)")
        elif ticket_key(new_ticket) not in existing_keys:
            pending.append(new_ticket)
            print(f"  + added today's ticket ({len(matches)} matches)")
        else:
            print(f"  today's ticket already pending")
    else:
        print("  tips.json empty or missing")

    print(f"  Total pending tickets: {len(pending)}\n")

    # 3) Vyhodnotit každý pending tiket
    fixtures_by_date = {}
    evaluated = []
    still_pending = []

    for ticket in pending:
        date_str = ticket.get("date", "")
        try:
            base_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except Exception:
            base_date = now
        target = base_date.date().isoformat()

        # Nevyhodnocuj tikety, jejichž datum je teprve v budoucnu (jiný den).
        # Pro dnešní tiket (base_date = dnes 05:30 UTC) chceme vyhodnotit hned,
        # protože zápasy proběhly předchozí večer. Zápasy, které ještě nejsou
        # dohrané, vyfiltruje fetch_finished_fixtures (status != FT/AET/PEN)
        # a tiket správně zůstane v pending přes evaluate_match -> None.
        if base_date.date() > now.date():
            still_pending.append(ticket)
            continue

        all_done = True
        for m in ticket.get("matches", []):
            if m.get("result") in ("\u2713", "\u2717"):
                continue
            print(f"  ~ searching: '{m.get('match','')}' | tip='{m.get('tip','')}' | target date={target}")
            res = evaluate_match(m, fixtures_by_date, target)
            if res is None:
                all_done = False
                print(f"    NOT FOUND")
                continue
            score, verdict = res
            m["score"] = score
            m["result"] = verdict
            icon = "\u2705" if verdict == "\u2713" else "\u274C"
            print(f"  {icon} {m.get('match','')} → {score} ({verdict})")

        if all_done and ticket.get("matches"):
            evaluated.append(ticket)
        else:
            still_pending.append(ticket)
            print(f"  ⏳ ticket {date_str} still incomplete")

    # 4) Merge do history (dedup dle ticket_key)
    history = load_json(HISTORY_FILE, [])
    keys = {ticket_key(h) for h in history}
    added = 0
    for t in evaluated:
        if ticket_key(t) not in keys:
            history.append(t)
            keys.add(ticket_key(t))
            added += 1

    history.sort(key=lambda x: x.get("date", ""), reverse=True)
    history = history[:MAX_HISTORY]

    # 5) Uložit
    save_json(HISTORY_FILE, history)
    save_json(PENDING_FILE, still_pending)

    wins = sum(1 for h in history if all(m.get("result") == "\u2713" for m in h.get("matches", [])))
    total = len(history)
    pct = (wins * 100 // total) if total > 0 else 0

    print(f"\n{'='*50}")
    print(f"  Newly evaluated tickets: {len(evaluated)} ({added} added)")
    print(f"  Still pending:           {len(still_pending)}")
    print(f"  History total tickets:   {total} (✓ {wins} / ✗ {total - wins} = {pct}%)")
    print(f"  API requests:            {request_count}")


if __name__ == "__main__":
    main()
