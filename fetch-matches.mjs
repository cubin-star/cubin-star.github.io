/**
 * fetch-matches.mjs
 *
 * Stahne fixtures z API-Football, vybere 6 nahodnych zapasu Over 2.5
 * v kurzovem rozsahu 1.9-2.5 s preferencí hlavních evropských lig.
 *
 * Env: API_FOOTBALL_KEY1
 * Pouziti: node fetch-matches.mjs
 */

import { writeFileSync, readFileSync, existsSync } from 'fs';

const API_KEY = process.env.API_FOOTBALL_KEY1;
if (!API_KEY) { console.error('Chybi API_FOOTBALL_KEY1 env promenna.'); process.exit(1); }

const FOOTBALL_API = 'https://v3.football.api-sports.io';
const MIN_ODDS = 1.9;
const MAX_ODDS = 2.5;
const PICK_COUNT = 6;
const EXCLUDED_COUNTRIES = new Set(['Russia', 'Belarus']);
const TZ = 'Europe/Prague';
const USER_AGENT = 'kombik-bot/1.0 (+github-actions)';

let reqCount = 0;
let fixtureFetchErrors = 0;
let fixtureFetchAttempts = 0;

// --- Prioritní ligy ---
// Tier 1: Hlavní evropské první ligy
const TIER1_LEAGUES = new Set([
    'Premier League',
    'La Liga',
    'Serie A',
    'Bundesliga',
    'Ligue 1',
    'Eredivisie',
    'Primeira Liga',
    'Pro League',
    'Scottish Premiership',
    'Süper Lig',
    'Liga Portugal',
    'Ekstraklasa',
    'Czech Liga',
    'Fortuna Liga',
    'Super League 1',
    'Super League',
    'Austrian Football Bundesliga',
    'Austrian Bundesliga',
    'Eliteserien',
    'Allsvenskan',
    'Superliga',
    'Veikkausliiga',
]);

// Tier 2: Evropské druhé ligy
const TIER2_LEAGUES = new Set([
    'Championship',
    '2. Bundesliga',
    'Serie B',
    'LaLiga2',
    'Ligue 2',
    'Eerste Divisie',
    'Scottish Championship',
    'TFF First League',
    '1. Liga',
    'Fortuna 1. Liga',
    'I Liga',
    'National League',
    'League One',
    'League Two',
    'Challenger Pro League',
    'Liga de Honra',
]);

// Hlavní evropské země pro tier 3 prioritizaci
const EUROPE_COUNTRIES = new Set([
    'England','Spain','Germany','Italy','France','Netherlands','Portugal','Belgium',
    'Greece','Turkey','Poland','Czech-Republic','Slovakia','Scotland','Switzerland',
    'Austria','Sweden','Norway','Denmark','Finland','Serbia','Croatia','Ukraine',
    'Romania','Hungary','Bulgaria','Slovenia','Bosnia And Herzegovina',
]);

function leagueTier(leagueName, country) {
    if (TIER1_LEAGUES.has(leagueName)) return 1;
    if (TIER2_LEAGUES.has(leagueName)) return 2;
    if (EUROPE_COUNTRIES.has(country)) return 3;
    return 4;
}

function maskKey(k) {
    if (!k) return '(none)';
    if (k.length <= 8) return '***';
    return k.slice(0, 4) + '...' + k.slice(-4) + ' (len=' + k.length + ')';
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function shuffle(arr) { for (let i = arr.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [arr[i], arr[j]] = [arr[j], arr[i]]; } return arr; }
function fmtDate(d) { return d.toISOString().split('T')[0]; }

async function apiFetch(path) {
    const url = FOOTBALL_API + path;
    for (let attempt = 1; attempt <= 3; attempt++) {
        try {
            const res = await fetch(url, {
                headers: {
                    'x-apisports-key': API_KEY,
                    'Accept': 'application/json',
                    'User-Agent': USER_AGENT
                }
            });
            reqCount++;
            if (res.status === 429) {
                console.warn('  [429] Rate limit hit, waiting ' + (5 * attempt) + 's... (attempt ' + attempt + '/3)');
                await sleep(5000 * attempt);
                continue;
            }
            if (res.status === 403) {
                let body = '';
                try { body = (await res.text()).slice(0, 400); } catch { }
                console.warn('  [403] ' + path.split('?')[0] + ' key=' + maskKey(API_KEY) + ' body=' + body);
                if (attempt < 3) { await sleep(3000 * attempt); continue; }
                return { response: [], paging: { total: 0 }, __error: 403 };
            }
            if (!res.ok) {
                let body = '';
                try { body = (await res.text()).slice(0, 200); } catch { }
                console.warn('  HTTP ' + res.status + ': ' + path.split('?')[0] + ' body=' + body);
                return { response: [], paging: { total: 0 }, __error: res.status };
            }
            const data = await res.json();
            if (data.errors && Object.keys(data.errors).length > 0) {
                const errStr = JSON.stringify(data.errors);
                if (errStr.includes('rateLimit') || errStr.includes('Too many')) {
                    console.warn('  [RATE] ' + errStr + ' waiting ' + (5 * attempt) + 's... (attempt ' + attempt + '/3)');
                    await sleep(5000 * attempt);
                    continue;
                }
                console.warn('  ', errStr);
                return { response: [], paging: { total: 0 }, __error: 'api-errors' };
            }
            return data;
        } catch (e) { console.warn('  Fetch error:', e.message); return { response: [], paging: { total: 0 }, __error: 'exception' }; }
    }
    console.warn('  [FAIL] Max retries for: ' + path.split('?')[0]);
    return { response: [], paging: { total: 0 }, __error: 'max-retries' };
}

async function getFixtures(date) {
    fixtureFetchAttempts++;
    const data = await apiFetch('/fixtures?date=' + date + '&timezone=' + TZ + '&status=NS');
    if (data.__error) fixtureFetchErrors++;
    return data.response || [];
}

async function getLeagueOdds(leagueId, season, date) {
    let all = [], page = 1, totalPages = 1;
    do {
        const data = await apiFetch('/odds?league=' + leagueId + '&season=' + season + '&date=' + date + '&bet=5&page=' + page);
        all.push(...(data.response || []));
        totalPages = data.paging?.total || 0;
        page++;
        if (page <= totalPages) await sleep(450);
    } while (page <= totalPages);
    return all;
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

    if (!bestP) return picks.map((p, i) => ({ ...p, group: Math.floor(i / 2) + 1 }));
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
    const max24h = new Date(now.getTime() + 24 * 60 * 60 * 1000);
    console.log('Window: ' + now.toUTCString() + ' -> ' + max24h.toUTCString() + ' (24h)\n');

    const dates = new Set();
    for (let d = new Date(now); d <= max24h; d = new Date(d.getTime() + 24 * 60 * 60 * 1000)) {
        dates.add(fmtDate(d));
    }

    let fixtures = [];
    for (const d of dates) {
        console.log('Fixtures ' + d + '...');
        fixtures.push(...await getFixtures(d));
        await sleep(450);
    }
    console.log('   ' + fixtures.length + ' scheduled matches\n');

    if (fixtureFetchAttempts > 0 && fixtureFetchErrors === fixtureFetchAttempts) {
        console.error('FATAL: All /fixtures requests failed (' + fixtureFetchErrors + '/' + fixtureFetchAttempts + '). API key='
            + maskKey(API_KEY) + '. Aborting so workflow fails visibly.');
        process.exit(2);
    }

    fixtures = fixtures.filter(f => {
        const t = new Date(f.fixture.date);
        return t >= now && t <= max24h && !EXCLUDED_COUNTRIES.has(f.league.country);
    });
    console.log('   ' + fixtures.length + ' in 24h window (excl. RU/BY)');

    const fixtureMap = new Map(), leagueMap = new Map();
    for (const f of fixtures) {
        fixtureMap.set(f.fixture.id, f);
        const key = f.league.id + '_' + f.league.season;
        if (!leagueMap.has(key)) leagueMap.set(key, { id: f.league.id, season: f.league.season, name: f.league.name, country: f.league.country, dates: new Set() });
        leagueMap.get(key).dates.add(fmtDate(new Date(f.fixture.date)));
    }
    console.log('   ' + leagueMap.size + ' leagues\n');

    // Stažení Over 2.5 kurzů
    const candidateMap = new Map();
    for (const [, lg] of leagueMap) {
        for (const d of lg.dates) {
            const oddsData = await getLeagueOdds(lg.id, lg.season, d);
            for (const entry of oddsData) {
                const fix = fixtureMap.get(entry.fixture?.id); if (!fix) continue;
                const mKey = fix.fixture.id;
                for (const bm of entry.bookmakers || []) {
                    for (const bet of bm.bets || []) {
                        for (const v of bet.values || []) {
                            if (v.value !== 'Over 2.5') continue;
                            const odd = parseFloat(v.odd);
                            if (isNaN(odd) || odd < MIN_ODDS || odd > MAX_ODDS) continue;
                            if (!candidateMap.has(mKey)) {
                                candidateMap.set(mKey, {
                                    fixtureId: mKey,
                                    league: lg.name,
                                    country: lg.country,
                                    match: fix.teams.home.name + ' - ' + fix.teams.away.name,
                                    kickoff: fix.fixture.date,
                                    tip: 'Over 2.5',
                                    tier: leagueTier(lg.name, lg.country),
                                    allOdds: []
                                });
                            }
                            candidateMap.get(mKey).allOdds.push(odd);
                        }
                    }
                }
            }
            await sleep(450);
        }
    }

    const pool = [...candidateMap.values()].map(m => ({
        ...m,
        odds: (m.allOdds.reduce((a, b) => a + b, 0) / m.allOdds.length).toFixed(2),
    }));
    console.log('Candidates: ' + pool.length + ' (Over 2.5, odds ' + MIN_ODDS + '-' + MAX_ODDS + ')');

    const tier1 = shuffle(pool.filter(m => m.tier === 1));
    const tier2 = shuffle(pool.filter(m => m.tier === 2));
    const tier3 = shuffle(pool.filter(m => m.tier === 3));
    const tier4 = shuffle(pool.filter(m => m.tier === 4));

    console.log('Tier 1 (top EU ligy): ' + tier1.length);
    console.log('Tier 2 (2. EU ligy): ' + tier2.length);
    console.log('Tier 3 (ostatni Evropa): ' + tier3.length);
    console.log('Tier 4 (zbytek sveta): ' + tier4.length + '\n');

    const selected = [];
    for (const m of [...tier1, ...tier2, ...tier3, ...tier4]) {
        if (selected.length >= PICK_COUNT) break;
        selected.push(m);
        console.log('   [T' + m.tier + '] ' + m.match + ' | ' + m.league + ' (' + m.country + ') | Over 2.5 @ ' + m.odds);
    }

    console.log('\nVybrano: ' + selected.length + '/' + PICK_COUNT);
    if (selected.length < PICK_COUNT) {
        console.log('WARNING: Mene nez ' + PICK_COUNT + ' zapasu v danem kurzovem rozsahu.');
    }

    const live1 = [...tier1, ...tier2].map(m => ({ league: m.league, match: m.match, kickoff: m.kickoff, tip: m.tip, odds: m.odds }));
    writeFileSync('live1.json', JSON.stringify(live1, null, 2), 'utf-8');
    console.log('live1.json: ' + live1.length + ' matches (tier 1+2)');

    if (selected.length === 0) {
        writeFileSync('hot.json', JSON.stringify([], null, 2), 'utf-8');
        writeFileSync('best.json', JSON.stringify([], null, 2), 'utf-8');
        console.log('Zadne zapasy. Prazdny hot.json (' + reqCount + ' API req)');
        process.exit(0);
    }

    const best = [...selected]
        .sort((a, b) => parseFloat(b.odds) - parseFloat(a.odds))
        .slice(0, 3)
        .map(m => ({ league: m.league, match: m.match, kickoff: m.kickoff, tip: m.tip, odds: m.odds }));
    writeFileSync('best.json', JSON.stringify(best, null, 2), 'utf-8');
    console.log('best.json: ' + best.length + ' matches (top odds)');

    const grouped = balanceGroups(selected);
    const output = grouped.map(m => ({ league: m.league, match: m.match, kickoff: m.kickoff, tip: m.tip, odds: m.odds, group: m.group }));
    writeFileSync('hot.json', JSON.stringify(output, null, 2), 'utf-8');

    console.log('\n' + output.length + ' matches -> hot.json (' + reqCount + ' API req)\n');
    const gc = Math.ceil(output.length / 2);
    for (let g = 1; g <= gc; g++) {
        const gm = output.filter(m => m.group === g);
        const go = gm.reduce((a, m) => a * parseFloat(m.odds), 1);
        console.log('  Gr.' + g + ' (' + go.toFixed(2) + '):');
        gm.forEach(m => console.log('     [' + m.league + '] ' + m.match + ' | ' + m.tip + ' @ ' + m.odds + ' | ' + m.kickoff));
    }
}

main().catch(err => { console.error('Chyba:', err); process.exit(1); });

