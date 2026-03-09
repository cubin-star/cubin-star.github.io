/**
 * Bot pro denní výběr 6 zápasů (fotbal + hokej) s Over góly a kurzem >= 2.0
 *   Fotbal: Over 2.5 | Hokej: Over 5.5
 *
 * Používá API-Football a API-Hockey (api-sports.io) - 7500 req/den.
 * Výstup: hot.json
 *
 * Env: API_FOOTBALL_KEY1
 * Použití: node fetch-matches.mjs
 */

import { writeFileSync } from 'fs';

const API_KEY = process.env.API_FOOTBALL_KEY1;
if (!API_KEY) { console.error('Chybí API_FOOTBALL_KEY1 env proměnná.'); process.exit(1); }

const FOOTBALL_API = 'https://v3.football.api-sports.io';
const HOCKEY_API = 'https://v1.hockey.api-sports.io';
const MIN_ODDS = 2.0;
const PICK_COUNT = 6;
const EXCLUDED_COUNTRIES = ['Russia', 'Belarus'];
const TZ = 'Europe/Prague';
let reqCount = 0;

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function fmtDate(d) { return d.toISOString().split('T')[0]; }

async function apiFetch(baseUrl, path) {
    const url = baseUrl + path;
    try {
        const res = await fetch(url, { headers: { 'x-apisports-key': API_KEY } });
        reqCount++;
        if (!res.ok) { console.warn('  ⚠ HTTP ' + res.status + ': ' + path.split('?')[0]); return { response: [], paging: { total: 0 } }; }
        const data = await res.json();
        if (data.errors && Object.keys(data.errors).length > 0) { console.warn('  ⚠', JSON.stringify(data.errors)); return { response: [], paging: { total: 0 } }; }
        return data;
    } catch (e) { console.warn('  ⚠ Fetch error:', e.message); return { response: [], paging: { total: 0 } }; }
}

async function getFootballFixtures(date) {
    const data = await apiFetch(FOOTBALL_API, '/fixtures?date=' + date + '&timezone=' + TZ + '&status=NS');
    return data.response || [];
}

async function getFootballLeagueOdds(leagueId, season, date) {
    let all = [], page = 1, totalPages = 1;
    do {
        const data = await apiFetch(FOOTBALL_API, '/odds?league=' + leagueId + '&season=' + season + '&date=' + date + '&bet=5&page=' + page);
        all.push(...(data.response || []));
        totalPages = data.paging?.total || 0;
        page++;
        if (page <= totalPages) await sleep(350);
    } while (page <= totalPages);
    return all;
}

async function footballPicks(now, maxTime) {
    console.log('⚽ FOTBAL\n');
    const today = fmtDate(now), tomorrow = fmtDate(maxTime);
    const dates = [today]; if (tomorrow !== today) dates.push(tomorrow);
    let fixtures = [];
    for (const d of dates) { console.log('📅 Fixtures ' + d + '...'); fixtures.push(...await getFootballFixtures(d)); await sleep(350); }
    console.log('   ' + fixtures.length + ' naplánovaných zápasů\n');
    fixtures = fixtures.filter(f => { const t = new Date(f.fixture.date); return t >= now && t <= maxTime && !EXCLUDED_COUNTRIES.includes(f.league.country); });
    console.log('   ' + fixtures.length + ' v 24h okně (bez RU/BY)');
    const fixtureMap = new Map(), leagueMap = new Map();
    for (const f of fixtures) {
        fixtureMap.set(f.fixture.id, f);
        const key = f.league.id + '_' + f.league.season;
        if (!leagueMap.has(key)) leagueMap.set(key, { id: f.league.id, season: f.league.season, name: f.league.name, country: f.league.country, dates: new Set() });
        leagueMap.get(key).dates.add(fmtDate(new Date(f.fixture.date)));
    }
    console.log('   ' + leagueMap.size + ' lig\n');
    const picks = [];
    for (const [, lg] of leagueMap) {
        let lgPicks = 0;
        for (const d of lg.dates) {
            const oddsData = await getFootballLeagueOdds(lg.id, lg.season, d);
            for (const entry of oddsData) {
                const fix = fixtureMap.get(entry.fixture?.id); if (!fix) continue;
                for (const bm of entry.bookmakers || []) { for (const bet of bm.bets || []) { for (const v of bet.values || []) {
                    if (v.value !== 'Over 2.5') continue;
                    const odd = parseFloat(v.odd); if (isNaN(odd) || odd < MIN_ODDS) continue;
                    picks.push({ league: lg.name, match: fix.teams.home.name + ' - ' + fix.teams.away.name, tip: 'Over 2.5', odds: odd.toFixed(2), bookmaker: bm.name });
                    lgPicks++;
                } } }
            }
            await sleep(350);
        }
        if (lgPicks > 0) console.log('   📡 ' + lg.name + ' (' + lg.country + ') → ' + lgPicks + ' tipů');
    }
    console.log('\n📊 Fotbal: ' + picks.length + ' tipů Over 2.5 >= ' + MIN_ODDS);
    return picks;
}

async function hockeyPicks(now, maxTime) {
    console.log('\n🏒 HOKEJ\n');
    const today = fmtDate(now), tomorrow = fmtDate(maxTime);
    const dates = [today]; if (tomorrow !== today) dates.push(tomorrow);
    let games = [];
    for (const d of dates) { console.log('📅 Games ' + d + '...'); const data = await apiFetch(HOCKEY_API, '/games?date=' + d + '&timezone=' + TZ); games.push(...(data.response || [])); await sleep(350); }
    games = games.filter(g => { if (g.status?.short !== 'NS') return false; const t = new Date(g.date); if (t < now || t > maxTime) return false; return !EXCLUDED_COUNTRIES.includes(g.country?.name || ''); });
    console.log('   ' + games.length + ' zápasů v 24h okně\n');
    const picks = [];
    for (let i = 0; i < games.length; i++) {
        const g = games[i];
        const data = await apiFetch(HOCKEY_API, '/odds?game=' + g.id);
        for (const entry of data.response || []) { for (const bm of entry.bookmakers || []) { for (const bet of bm.bets || []) { for (const v of bet.values || []) {
            if (v.value !== 'Over 5.5') continue;
            const odd = parseFloat(v.odd); if (isNaN(odd) || odd < MIN_ODDS) continue;
            picks.push({ league: g.league?.name || 'Hockey', match: (g.teams?.home?.name || '?') + ' - ' + (g.teams?.away?.name || '?'), tip: 'Over 5.5', odds: odd.toFixed(2), bookmaker: bm.name });
        } } } }
        if (i < games.length - 1) await sleep(350);
    }
    console.log('📊 Hokej: ' + picks.length + ' tipů Over 5.5 >= ' + MIN_ODDS);
    return picks;
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
    console.log('🤖 Kombík Bot – API-Sports\n');
    const now = new Date(), maxTime = new Date(now.getTime() + 24 * 60 * 60 * 1000);
    console.log('⏰ ' + now.toUTCString() + ' → ' + maxTime.toUTCString() + '\n');
    const fP = await footballPicks(now, maxTime);
    const hP = await hockeyPicks(now, maxTime);
    let allPicks = [...fP, ...hP];
    console.log('\n📊 Celkem ' + allPicks.length + ' tipů (' + reqCount + ' API req)');
    const best = new Map();
    for (const p of allPicks) { if (!best.has(p.match) || parseFloat(p.odds) > parseFloat(best.get(p.match).odds)) best.set(p.match, p); }
    let unique = [...best.values()]; unique.sort((a, b) => parseFloat(b.odds) - parseFloat(a.odds));
    console.log('📊 Unikátní: ' + unique.length);
    const selected = [], usedLeagues = new Set();
    for (const pick of unique) { if (selected.length >= PICK_COUNT) break; if (!usedLeagues.has(pick.league)) { selected.push(pick); usedLeagues.add(pick.league); } }
    if (selected.length === 0) { console.warn('\n⚠ Žádné vhodné zápasy nalezeny.'); process.exit(0); }
    const grouped = balanceGroups(selected);
    const output = grouped.map(m => ({ league: m.league, match: m.match, tip: m.tip, odds: m.odds, group: m.group }));
    writeFileSync('hot.json', JSON.stringify(output, null, 2), 'utf-8');
    console.log('\n✅ ' + output.length + ' zápasů → hot.json (' + reqCount + ' API req)\n');
    const gc = Math.ceil(output.length / 2);
    for (let g = 1; g <= gc; g++) { const gm = output.filter(m => m.group === g); const go = gm.reduce((a, m) => a * parseFloat(m.odds), 1); console.log('  📦 Sk.' + g + ' (' + go.toFixed(2) + '):'); gm.forEach(m => console.log('     ' + (m.tip.includes('5.5') ? '🏒' : '⚽') + ' [' + m.league + '] ' + m.match + ' | ' + m.tip + ' @ ' + m.odds)); }
}

main().catch(err => { console.error('Chyba:', err); process.exit(1); });
