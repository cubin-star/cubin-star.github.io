/**
 * fetch-matches.mjs
 *
 * Stahne fixtures z API-Football, odfiltruje blokovane zeme/ligy
 * a zapise vysledek do hot.json.
 *
 * Env: API_FOOTBALL_KEY1
 * Pouziti: node fetch-matches.mjs
 */

import { writeFileSync } from 'fs';

const API_KEY = process.env.API_FOOTBALL_KEY1;
if (!API_KEY) { console.error('Chybi API_FOOTBALL_KEY1 env promenna.'); process.exit(1); }

const FOOTBALL_API = 'https://v3.football.api-sports.io';
const MIN_ODDS = 2.05;
const MAX_ODDS = 3.0;
const PICK_COUNT = 6;
const MAX_SCORED = 1.0;
const MIN_SCORED = 1.3;
const MIN_CONCEDED_STRICT = 1.5;
const MIN_CONCEDED_RELAXED = 1.3;
const MIN_PLAYED = 5;
const EXCLUDED_COUNTRIES = new Set(['Russia', 'Belarus']);
const BLOCKED_AFRICAN = new Set([
    'Algeria', 'Angola', 'Benin', 'Botswana', 'Burkina-Faso', 'Burundi', 'Cameroon',
    'Cape-Verde', 'Chad', 'Congo', 'Congo-DR', 'Djibouti', 'Equatorial-Guinea',
    'Eritrea', 'Eswatini', 'Ethiopia', 'Gabon', 'Gambia', 'Ghana', 'Guinea',
    'Guinea-Bissau', 'Ivory-Coast', 'Kenya', 'Lesotho', 'Liberia', 'Libya',
    'Madagascar', 'Malawi', 'Mali', 'Mauritania', 'Mauritius', 'Mozambique',
    'Namibia', 'Niger', 'Nigeria', 'Rwanda', 'Senegal', 'Seychelles',
    'Sierra-Leone', 'Somalia', 'South-Sudan', 'Sudan', 'Tanzania', 'Togo',
    'Uganda', 'Zambia', 'Zimbabwe',
]);
const TZ = 'Europe/Prague';
let reqCount = 0;

const EUROPEAN_COUNTRIES = new Set([
    'England', 'Spain', 'Germany', 'Italy', 'France', 'Netherlands', 'Portugal',
    'Belgium', 'Turkey', 'Austria', 'Switzerland', 'Scotland', 'Czech-Republic',
    'Poland', 'Denmark', 'Norway', 'Sweden', 'Greece', 'Croatia', 'Serbia',
    'Romania', 'Hungary', 'Ukraine', 'Slovakia', 'Bulgaria', 'Finland',
    'Ireland', 'Northern-Ireland', 'Wales', 'Iceland', 'Slovenia', 'Cyprus',
]);

function isSecondTier(name) {
    return /\b(2|II|segunda|championship|league two|league one|serie b|ligue 2|2\. liga|2\. bundesliga|eerste divisie|second|third|cup|pokal|coupe|copa|taca)\b/i.test(name);
}

function isBlockedLeague(name) {
    return /\b(u1[0-9]|u2[0-3]|youth|juniors?|reserves?|amateur|friendl|simulation|esports?|cyber|women|feminine|feminin|frauen|damer|kvinner|ladies|femenin|naiset|kobiety|feminino|girls)\b/i.test(name);
}

// Blokuje ligy 4. urovne a nize (vyjma Anglie, kde povolujeme az 6. uroven).
// Kazda zeme ma jiny system pojmenovani lig, proto resime per-country.
function isLowTierLeague(leagueName, country) {
    const name = leagueName;

    // === Anglie: povolujeme az do 6. urovne ===
    // 1: Premier League, 2: Championship, 3: League One, 4: League Two
    // 5: National League, 6: National League North/South
    // Block: 7+ (Southern League, Northern Premier, Isthmian, atd.)
    if (country === 'England') {
        return /\b(southern.*league|northern.*premier|isthmian|combined counties|eastern.*league|western.*league|midland.*league|hellenic|spartan|essex|kent.*league|sussex|lancashire)\b/i.test(name);
    }

    // === Skotsko: az do 3. urovne ===
    // 1: Premiership, 2: Championship, 3: League One
    // Block: League Two (4), Highland/Lowland League (5)
    if (country === 'Scotland') {
        return /\b(league two|highland|lowland)\b/i.test(name);
    }

    // === Nemecko ===
    // 1: Bundesliga, 2: 2. Bundesliga, 3: 3. Liga
    // Block: Regionalliga (4), Oberliga (5)+
    if (country === 'Germany') {
        return /\b(regionalliga|oberliga|landesliga|verbandsliga|bezirksliga)\b/i.test(name);
    }

    // === Rakousko ===
    // 1: Bundesliga, 2: 2. Liga, 3: Regionalliga
    // Pozor: Rakouska Regionalliga je 3. uroven (povolena!)
    // Block: Landesliga (4)+
    if (country === 'Austria') {
        return /\b(landesliga|gebietsliga)\b/i.test(name);
    }

    // === Italie ===
    // 1: Serie A, 2: Serie B, 3: Serie C
    // Block: Serie D (4), Eccellenza (5)+
    if (/\bserie\s*d\b/i.test(name) || /\b(eccellenza|promozione)\b/i.test(name)) return true;

    // === Francie ===
    // 1: Ligue 1, 2: Ligue 2, 3: National / National 1
    // Block: National 2 (4), National 3 (5)+
    if (/\bnational\s*[2-9]\b/i.test(name)) return true;

    // === Spanelsko ===
    // 1: La Liga, 2: Segunda División, 3: Primera Federación / Primera RFEF
    // Block: Segunda Federación/RFEF (4), Tercera (5)+
    if (/\b(segunda\s*(federaci[oó]n|rfef)|tercera)\b/i.test(name)) return true;

    // === Nizozemsko ===
    // 1: Eredivisie, 2: Eerste Divisie, 3: Tweede Divisie
    // Block: Derde Divisie (4)+
    if (/\b(derde|vierde)\b/i.test(name)) return true;

    // === Polsko ===
    // 1: Ekstraklasa, 2: I Liga (= 2. uroven), 3: II Liga (= 3. uroven)
    // Block: III Liga (= 4. uroven)+
    if (country === 'Poland' && /\b(iii\s*liga|3\.\s*liga)\b/i.test(name)) return true;

    // === Turecko ===
    // 1: Süper Lig, 2: 1. Lig, 3: 2. Lig
    // Block: 3. Lig (= 4. uroven)
    if (country === 'Turkey' && /\b3\.\s*lig\b/i.test(name)) return true;

    // === Cesko ===
    // 1: First League / Chance Liga, 2: FNL, 3: CFL/MSFL
    // Block: Divize (4)+
    if (country === 'Czech-Republic' && /\b(divize)\b/i.test(name)) return true;

    // === Slovensko ===
    // 1: Super Liga / Niké Liga, 2: 2. Liga, 3: 3. Liga
    // Block: 4. Liga+
    if (country === 'Slovakia' && /\b(4\.\s*liga|regionalna)\b/i.test(name)) return true;

    // === Portugalsko ===
    // 1: Primeira Liga, 2: Segunda Liga, 3: Liga 3
    // Block: Campeonato de Portugal (4)+
    if (country === 'Portugal' && /\b(campeonato de portugal)\b/i.test(name)) return true;

    // === Dansko ===
    // 1: Superliga, 2: 1st Division, 3: 2nd Division
    // Block: Denmark Series / 3rd Division (4)+
    if (country === 'Denmark' && /\b(3rd division|denmark series)\b/i.test(name)) return true;

    // === Norsko ===
    // 1: Eliteserien, 2: OBOS-ligaen / 1st Division, 3: 2nd Division
    // Block: 3rd Division / 3. divisjon (4)+
    if (country === 'Norway' && /\b(3rd division|3\.\s*divisjon)\b/i.test(name)) return true;

    // === Svedsko ===
    // 1: Allsvenskan, 2: Superettan, 3: Ettan
    // Block: Division 2 (= 4. uroven ve Svedsku!)+
    if (country === 'Sweden' && /\b(division\s*[2-9])\b/i.test(name)) return true;

    // === Finsko ===
    // 1: Veikkausliiga, 2: Ykkönen, 3: Kakkonen
    // Block: Kolmonen (4)+
    if (country === 'Finland' && /\b(kolmonen|nelonen)\b/i.test(name)) return true;

    // === Recko ===
    // 1: Super League 1, 2: Super League 2, 3: Gamma Ethniki
    // Block: Delta Ethniki (4)+
    if (country === 'Greece' && /\b(delta ethniki)\b/i.test(name)) return true;

    // === Rumunsko ===
    // 1: Liga I, 2: Liga II, 3: Liga III
    // Block: Liga IV (4)+
    if (country === 'Romania' && /\b(liga\s*(iv|4))\b/i.test(name)) return true;

    // === Madarsko ===
    // 1: NB I, 2: NB II, 3: NB III
    // Block: Megyei (county leagues, 4)+
    if (country === 'Hungary' && /\b(megyei|county)\b/i.test(name)) return true;

    // === Srbsko ===
    // 1: Super Liga, 2: Prva Liga, 3: Srpska Liga
    // Block: Zona (4)+
    if (country === 'Serbia' && /\b(zona)\b/i.test(name)) return true;

    // === Chorvatsko ===
    // 1: HNL / Prva HNL, 2: Druga HNL, 3: Prva NL
    // Block: Druga NL / zupanijska liga (4)+
    if (country === 'Croatia' && /\b(druga nl|zupanijska|county)\b/i.test(name)) return true;

    // === Belgie ===
    // 1: Pro League / Jupiler, 2: Challenger Pro League, 3: National 1 / 1st Amateur
    // Block: 2nd Amateur / National 2 (4)+
    if (country === 'Belgium' && /\b(2nd amateur|national\s*[2-9]|3rd amateur)\b/i.test(name)) return true;

    // === Svycarsko ===
    // 1: Super League, 2: Challenge League, 3: Promotion League
    // Block: 1. Liga (= 4. uroven ve Svycarsku!)+
    if (country === 'Switzerland' && /\b(1\.\s*liga)\b/i.test(name)) return true;

    // === Genericke vzory pro 4+ uroven (funguje pro ostatni zeme) ===
    if (/\bdivision\s*[4-9]\b/i.test(name)) return true;
    if (/\b[4-9]\.\s*(division|divisjon|divisie|liga)\b/i.test(name)) return true;
    if (/\b(4th|5th|6th|7th|8th|9th|10th)\b/i.test(name)) return true;
    if (/\b(fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s*(division|league|tier)?\b/i.test(name)) return true;
    if (/\b(district|parish|provincial|cantonal)\b/i.test(name)) return true;
    // Regionalliga je u vetsiny zemi 4+ (Rakousko a dalsi vyjimky reseny vyse)
    if (/\b(regionalliga)\b/i.test(name)) return true;

    return false;
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function shuffle(arr) { for (let i = arr.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [arr[i], arr[j]] = [arr[j], arr[i]]; } return arr; }
function fmtDate(d) { return d.toISOString().split('T')[0]; }

async function apiFetch(path) {
    const url = FOOTBALL_API + path;
    try {
        const res = await fetch(url, { headers: { 'x-apisports-key': API_KEY } });
        reqCount++;
        if (!res.ok) { console.warn('  HTTP ' + res.status + ': ' + path.split('?')[0]); return { response: [], paging: { total: 0 } }; }
        const data = await res.json();
        if (data.errors && Object.keys(data.errors).length > 0) { console.warn('  ', JSON.stringify(data.errors)); return { response: [], paging: { total: 0 } }; }
        return data;
    } catch (e) { console.warn('  Fetch error:', e.message); return { response: [], paging: { total: 0 } }; }
}

async function getFixtures(date) {
    const data = await apiFetch('/fixtures?date=' + date + '&timezone=' + TZ + '&status=NS');
    return data.response || [];
}

async function getLeagueOdds(leagueId, season, date) {
    let all = [], page = 1, totalPages = 1;
    do {
        const data = await apiFetch('/odds?league=' + leagueId + '&season=' + season + '&date=' + date + '&bet=5&page=' + page);
        all.push(...(data.response || []));
        totalPages = data.paging?.total || 0;
        page++;
        if (page <= totalPages) await sleep(350);
    } while (page <= totalPages);
    return all;
}

async function getPrediction(fixtureId) {
    const data = await apiFetch('/predictions?fixture=' + fixtureId);
    return (data.response && data.response[0]) || null;
}



// Vazeny nahodny vyber bez opakovani; vaha = ocekavane goly zapasu
// Kazdy zapas z jine ligy
function weightedPick(items, count) {
    const result = [];
    const usedLeagues = new Set();
    const remaining = [...items];
    for (let i = 0; i < count && remaining.length > 0; i++) {
        const available = remaining.filter(m => !usedLeagues.has(m.league));
        if (available.length === 0) break;
        const weights = available.map(m => m.expectedGoals || 1);
        const totalW = weights.reduce((a, b) => a + b, 0);
        let r = Math.random() * totalW;
        let idx = 0;
        for (; idx < weights.length - 1; idx++) {
            r -= weights[idx];
            if (r <= 0) break;
        }
        const pick = available[idx];
        result.push(pick);
        usedLeagues.add(pick.league);
        remaining.splice(remaining.indexOf(pick), 1);
    }
    return result;
}

function balanceGroups(picks) {
    const n = picks.length;
    if (n <= 2) return picks.map(p => ({ ...p, group: 1 }));
    const indices = picks.map((_, i) => i);
    const allP = generatePairings(indices);
    let bestP = null, bestD = Infinity;
    for (const pairing of allP) {
        const gO = pairing.map(pair => pair.reduce((a, idx) => a * parseFloat(picks[idx].odds), 1));
        const diff = Math.max(...gO) - Math.min(...gO);
        if (diff < bestD) { bestD = diff; bestP = pairing; }
    }
    const result = [];
    for (let g = 0; g < bestP.length; g++) { for (const idx of bestP[g]) result.push({ ...picks[idx], group: g + 1 }); }
    return result;
}

function generatePairings(indices) {
    const results = [];
    function recurse(rem, cur) {
        if (rem.length === 0) { results.push([...cur]); return; }
        if (rem.length === 1) { results.push([...cur, [rem[0]]]); return; }
        const first = rem[0], rest = rem.slice(1);
        for (let i = 0; i < rest.length; i++) { cur.push([first, rest[i]]); recurse(rest.filter((_, j) => j !== i), cur); cur.pop(); }
    }
    recurse(indices, []);
    return results;
}

async function main() {
    console.log('Kombik Bot - fetch-matches\n');
    const now = new Date();
    const maxTime = new Date(now.getTime() + 24 * 60 * 60 * 1000);
    console.log('Okno: ' + now.toUTCString() + ' -> ' + maxTime.toUTCString() + ' (24h)\n');

    const today = fmtDate(now), tomorrow = fmtDate(maxTime);
    const dates = [today]; if (tomorrow !== today) dates.push(tomorrow);
    let fixtures = [];
    for (const d of dates) { console.log('Fixtures ' + d + '...'); fixtures.push(...await getFixtures(d)); await sleep(350); }
    console.log('   ' + fixtures.length + ' naplanovanych zapasu\n');

    fixtures = fixtures.filter(f => {
        const t = new Date(f.fixture.date);
        const c = f.league.country;
        return t >= now && t <= maxTime
            && !EXCLUDED_COUNTRIES.has(c)
            && !BLOCKED_AFRICAN.has(c)
            && !isBlockedLeague(f.league.name)
            && !isLowTierLeague(f.league.name, c);
    });
    console.log('   ' + fixtures.length + ' v 24h okne (bez RU/BY/Afrika/zen/mladez/esport/nizkych soutezi)');

    // Mapovani fixtures a lig
    const fixtureMap = new Map(), leagueMap = new Map();
    for (const f of fixtures) {
        fixtureMap.set(f.fixture.id, f);
        const key = f.league.id + '_' + f.league.season;
        if (!leagueMap.has(key)) leagueMap.set(key, { id: f.league.id, season: f.league.season, name: f.league.name, country: f.league.country, dates: new Set() });
        leagueMap.get(key).dates.add(fmtDate(new Date(f.fixture.date)));
    }
    console.log('   ' + leagueMap.size + ' lig\n');

    // Stazeni kurzu Over 2.5 pro vsechny ligy
    const candidates = new Map();
    for (const [, lg] of leagueMap) {
        for (const d of lg.dates) {
            const oddsData = await getLeagueOdds(lg.id, lg.season, d);
            for (const entry of oddsData) {
                const fix = fixtureMap.get(entry.fixture?.id); if (!fix) continue;
                const matchKey = fix.fixture.id;
                for (const bm of entry.bookmakers || []) { for (const bet of bm.bets || []) { for (const v of bet.values || []) {
                    if (v.value !== 'Over 2.5') continue;
                    const odd = parseFloat(v.odd); if (isNaN(odd) || odd < MIN_ODDS || odd > MAX_ODDS) continue;
                    if (!candidates.has(matchKey)) candidates.set(matchKey, { fixtureId: matchKey, league: lg.name, country: lg.country, match: fix.teams.home.name + ' - ' + fix.teams.away.name, kickoff: fix.fixture.date, tip: 'Over 2.5', allOdds: [] });
                    candidates.get(matchKey).allOdds.push(odd);
                } } }
            }
            await sleep(350);
        }
    }
    const pool = [...candidates.values()].map(m => ({
        ...m,
        odds: (m.allOdds.reduce((a, b) => a + b, 0) / m.allOdds.length).toFixed(2),
    }));
    console.log('Kandidatu: ' + pool.length + ' s Over 2.5 (kurz ' + MIN_ODDS + '-' + MAX_ODDS + ')');

    // Filtrace pres predictions: scored < 1.0 + obdrzene > 1.5 pro OBA tymy
    shuffle(pool);
    console.log('Analyza tymu (predictions)...\n');
    const qualified15 = [];
    const qualified13 = [];
    for (const m of pool) {
        const pred = await getPrediction(m.fixtureId);
        if (pred) {
            const home = pred.teams?.home;
            const away = pred.teams?.away;
            if (home && away) {
                const hPlayed = parseInt(home.league?.fixtures?.played?.total) || 0;
                const aPlayed = parseInt(away.league?.fixtures?.played?.total) || 0;
                if (hPlayed < MIN_PLAYED || aPlayed < MIN_PLAYED) {
                    console.log('   [SKIP] ' + m.match + ' | malo zapasu: ' + hPlayed + '/' + aPlayed);
                    continue;
                }
                const hFor = parseFloat(home.league?.goals?.for?.average?.total) || parseFloat(home.last_5?.goals?.for?.average) || 0;
                const aFor = parseFloat(away.league?.goals?.for?.average?.total) || parseFloat(away.last_5?.goals?.for?.average) || 0;
                const hAgn = parseFloat(home.league?.goals?.against?.average?.total) || parseFloat(home.last_5?.goals?.against?.average) || 0;
                const aAgn = parseFloat(away.league?.goals?.against?.average?.total) || parseFloat(away.last_5?.goals?.against?.average) || 0;
                const expG = (hFor + aFor + hAgn + aAgn) / 2;
                const entry = { ...m, expectedGoals: expG };

                // Pomocne podminky
                const oneLowOneHighScored = (hFor < MAX_SCORED && aFor >= MIN_SCORED) || (aFor < MAX_SCORED && hFor >= MIN_SCORED);
                const oneLowOneHighConceded = (hAgn < MAX_SCORED && aAgn >= MIN_SCORED) || (aAgn < MAX_SCORED && hAgn >= MIN_SCORED);
                const bothStrictScored = hFor >= MIN_CONCEDED_STRICT && aFor >= MIN_CONCEDED_STRICT && (hFor > MIN_CONCEDED_STRICT || aFor > MIN_CONCEDED_STRICT);
                const bothStrictConceded = hAgn >= MIN_CONCEDED_STRICT && aAgn >= MIN_CONCEDED_STRICT && (hAgn > MIN_CONCEDED_STRICT || aAgn > MIN_CONCEDED_STRICT);
                const bothRelaxedScored = hFor >= MIN_CONCEDED_RELAXED && aFor >= MIN_CONCEDED_RELAXED;
                const bothRelaxedConceded = hAgn >= MIN_CONCEDED_RELAXED && aAgn >= MIN_CONCEDED_RELAXED;

                // 1. kolo:
                //   A) scored: jeden < 1.0 + druhy >= 1.3, conceded: oba >= 1.5 (min jeden >= 1.6)
                //   B) scored: oba >= 1.5 (min jeden >= 1.6), conceded: jeden >= 1.3 + druhy < 1.0
                if ((oneLowOneHighScored && bothStrictConceded)
                    || (bothStrictScored && oneLowOneHighConceded)) {
                    console.log('   [Q15] ' + m.match + ' | scored ' + hFor.toFixed(1) + '/' + aFor.toFixed(1) + ', conceded ' + hAgn.toFixed(1) + '/' + aAgn.toFixed(1) + ' => ' + expG.toFixed(2) + 'g');
                    qualified15.push(entry);
                // 2. kolo:
                //   A) scored: jeden < 1.0 + druhy >= 1.3, conceded: oba >= 1.3
                //   B) scored: oba >= 1.3, conceded: jeden < 1.0 + druhy >= 1.3
                } else if ((oneLowOneHighScored && bothRelaxedConceded)
                    || (bothRelaxedScored && oneLowOneHighConceded)) {
                    console.log('   [Q13] ' + m.match + ' | scored ' + hFor.toFixed(1) + '/' + aFor.toFixed(1) + ', conceded ' + hAgn.toFixed(1) + '/' + aAgn.toFixed(1) + ' => ' + expG.toFixed(2) + 'g');
                    qualified13.push(entry);
                }
            }
        }
        await sleep(350);
    }
    console.log('\n1. kolo (scored<1+>=1.3 & conceded>=1.5+1.6 NEBO scored>=1.5+1.6 & conceded<1+>=1.3): ' + qualified15.length + '/' + pool.length);
    console.log('2. kolo (scored<1+>=1.3 & conceded>=1.3+1.3 NEBO scored>=1.3+1.3 & conceded<1+>=1.3): ' + qualified13.length + '/' + pool.length);

    // 1. kolo vyberu: z qualified15 (obdrzene >= 1.5)
    const selected = [];
    console.log('\n--- 1. kolo vyberu (obdrzene >= 1.5) ---');
    const picked15 = weightedPick(qualified15, PICK_COUNT);
    for (const m of picked15) { m._qualified15 = true; selected.push(m); }
    console.log('Vybrano z 1. kola: ' + selected.length + ' zapasu');

    // 2. kolo vyberu: pokud < 6, doplnit z qualified13 (obdrzene >= 1.3)
    if (selected.length < PICK_COUNT && qualified13.length > 0) {
        console.log('\n--- 2. kolo vyberu (obdrzene >= 1.3) ---');
        const usedLeagues2 = new Set(selected.map(s => s.league));
        const available13 = qualified13.filter(m => !usedLeagues2.has(m.league));
        const picked13 = weightedPick(available13, PICK_COUNT - selected.length);
        for (const m of picked13) { m._qualified13 = true; selected.push(m); }
        console.log('Doplneno z 2. kola: ' + picked13.length + ', celkem: ' + selected.length);
    }

    // Fallback: pokud neni 6, doplnit z evropskych prvnich lig, pak z poolu
    if (selected.length < PICK_COUNT) {
        const usedIds = new Set(selected.map(s => s.fixtureId));
        const usedLeagues = new Set(selected.map(s => s.league));
        const remaining = pool.filter(m => !usedIds.has(m.fixtureId) && !usedLeagues.has(m.league));

        // 1) preferuj evropske prvni ligy
        const euroTop = remaining.filter(m => EUROPEAN_COUNTRIES.has(m.country) && !isSecondTier(m.league));
        shuffle(euroTop);
        for (const m of euroTop) {
            if (selected.length >= PICK_COUNT) break;
            if (usedLeagues.has(m.league)) continue;
            selected.push(m);
            usedLeagues.add(m.league);
            usedIds.add(m.fixtureId);
        }

        // 2) pokud stale < 6, jakykoli zbyvajici z poolu (unikatni liga)
        if (selected.length < PICK_COUNT) {
            const rest = remaining.filter(m => !usedIds.has(m.fixtureId) && !usedLeagues.has(m.league));
            shuffle(rest);
            for (const m of rest) {
                if (selected.length >= PICK_COUNT) break;
                if (usedLeagues.has(m.league)) continue;
                selected.push(m);
                usedLeagues.add(m.league);
            }
        }

        console.log('Fallback: doplneno na ' + selected.length + ' (evropske 1. ligy, pak pool, unikatni ligy)');
    }

    console.log('\nVybrano: ' + selected.length + ' zapasu\n');

    if (selected.length === 0) { console.warn('Zadne zapasy nenalezeny.'); process.exit(0); }

    // Rozdeleni do 3 kurzove vyrovnanych skupin po 2
    const grouped = balanceGroups(selected);
    const output = grouped.map(m => ({ league: m.league, match: m.match, kickoff: m.kickoff, tip: m.tip, odds: m.odds, group: m.group, qualified15: !!m._qualified15, qualified13: !!m._qualified13 }));
    writeFileSync('hot.json', JSON.stringify(output, null, 2), 'utf-8');

    console.log(output.length + ' zapasu -> hot.json (' + reqCount + ' API req)\n');
    const gc = Math.ceil(output.length / 2);
    for (let g = 1; g <= gc; g++) {
        const gm = output.filter(m => m.group === g);
        const go = gm.reduce((a, m) => a * parseFloat(m.odds), 1);
        console.log('  Sk.' + g + ' (' + go.toFixed(2) + '):');
        gm.forEach(m => console.log('     [' + m.league + '] ' + m.match + ' | ' + m.tip + ' @ ' + m.odds));
    }
}

main().catch(err => { console.error('Chyba:', err); process.exit(1); });
