/**
 * Bot pro denní výběr 6 fotbalových zápasů s Over 2.5 góly a kurzem 2.2–3.0
 *
 * Logika výběru:
 *  1. Blacklist (ženské, mládež, amatér, esports) + detekce nižších lig (3.+ tier)
 *     – Anglie povolena až do 6. úrovně, ostatní země max 2. liga
 *  2. Kurzy Over 2.5 v rozmezí 2.2–3.0
 *  3. Scoring: vážený odhad gólů (L5 40% + sezóna 30% + domácí/venkovní 30%)
 *     + H2H, BTTS, API prediction, kvalita ligy, sweet-spot kurzu
 *  4. Výběr top 6 z různých soutěží (1 zápas = 1 liga)
 *
 * Používá API-Football (api-sports.io) - 7500 req/den.
 * Výstup: hot.json
 *
 * Env: API_FOOTBALL_KEY1
 * Použití: node fetch-matches.mjs
 */

import { writeFileSync } from 'fs';

const API_KEY = process.env.API_FOOTBALL_KEY1;
if (!API_KEY) { console.error('Chybí API_FOOTBALL_KEY1 env proměnná.'); process.exit(1); }

const FOOTBALL_API = 'https://v3.football.api-sports.io';
const MIN_ODDS = 2.2;
const MAX_ODDS = 3.0;
const PICK_COUNT = 6;
const MAX_ANALYZE = 80;
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

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function fmtDate(d) { return d.toISOString().split('T')[0]; }
function fmtKickoff(iso) {
    const d = new Date(iso);
    return d.toLocaleDateString('cs-CZ', { timeZone: TZ, day: '2-digit', month: '2-digit' }) + ' ' +
           d.toLocaleTimeString('cs-CZ', { timeZone: TZ, hour: '2-digit', minute: '2-digit' });
}

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

async function getMatchPrediction(fixtureId) {
    const data = await apiFetch(FOOTBALL_API, '/predictions?fixture=' + fixtureId);
    return (data.response && data.response[0]) || null;
}

/** Blacklist: zjevně nevhodné soutěže */
function isBlockedLeague(name) {
    return /\b(u1[0-9]|u2[0-3]|youth|juniors?|reserves?|amateur|friendl|simulation|esports?|cyber|women|feminine|feminin|frauen|damer|kvinner|žen[yí]?|ladies|femenin)\b/i.test(name);
}

/**
 * Odhad tieru ligy – jednoduchý, s rozumným defaultem.
 * 1 = nejvyšší liga, 2 = druhá, 3+ = nižší.
 * Neznámé ligy → tier 2 (propustí se, na rozdíl od starého whitelist přístupu).
 * Anglie: max tier 6. Ostatní: max tier 2.
 */
function estimateLeagueTier(leagueName, country) {
    const name = leagueName.toLowerCase();

    // Poháry / mezinárodní → vždy OK
    if (/champions league|europa league|conference league|euro \d|world cup|super cup|supercup|\bcup\b|\bcopa\b|\bcoupe\b|\bcoppa\b|\bpokal\b|\btaça\b|\bpohár\b|\bkaupa|\btrophy\b|\bshield\b/i.test(leagueName)) return 1;

    // ── Explicitní 3.+ tier (detekce nižších lig) ──
    if (/\b(tercera|3\. liga|3\. hnl|liga 3|serie c|ligue 3|national league.*(north|south)|regional|provincial|landesliga|oberliga|verbandsliga|kreisliga|bezirksliga|divisione|division 3|4\. liga|5\. liga|isthmian|southern league|northern league|step [3-6])\b/i.test(name)) return 3;

    // ── Anglie – specifické tiery ──
    if (country === 'England') {
        if (/premier league/i.test(name)) return 1;
        if (/championship/i.test(name)) return 2;
        if (/league one/i.test(name)) return 3;
        if (/league two/i.test(name)) return 4;
        if (/national league/i.test(name) && !/north|south/i.test(name)) return 5;
        if (/national league.*(north|south)/i.test(name)) return 6;
        return 2; // Neznámé anglické → tier 2 (propustí se)
    }

    // ── Známé 1. ligy ──
    const TIER1 = /premier league|la liga(?!\s*2)|\bbundesliga(?!\s*2)|\bserie a|\bligue 1|\beredivisie|primeira liga|liga portugal(?!\s*2)|jupiler|pro league(?!\s*[2b])|s[uü]per lig(?!\s*[2b])|super league(?!\s*2)|premiership|superliga(?!\s*[2b])|eliteserien|allsvenskan|veikkausliiga|ekstraklasa|1\.\s*hnl|prva hnl|liga i(?:\b|$)|liga 1(?:\b|$)|first professional league|parva liga|nb i(?:\b|$)|fortuna liga|niké liga|chance liga|prvaliga|premier division|úrvalsdeild|a-league|j1 league|j-league|k league 1|mls|liga mx|brasileir.*s[eé]rie a|primera divisi[oó]n|botola pro(?!\s*2)|egyptian premier|south african premier/i;
    if (TIER1.test(name)) return 1;

    // ── Známé 2. ligy ──
    const TIER2 = /la liga 2|segunda divisi[oó]n|2\.\s*bundesliga|serie b|ligue 2|eerste divisie|segunda liga|liga portugal 2|challenger pro league|1\.\s*lig(?:i)?(?!\s*a)|super league 2|challenge league|obos-ligaen|superettan|ykk[oö]nen|i liga|2\.\s*hnl|liga ii|liga 2(?:\b|$)|nb ii|nb 2|2\.\s*liga|brasileir.*s[eé]rie b|k league 2|usl championship|scottish championship|division 2|league one|league two/i;
    if (TIER2.test(name)) return 2;

    // Neznámé → tier 2 (dáme šanci – nezabíjíme jako starý whitelist)
    return 2;
}

function leagueQualityBonus(leagueName, country) {
    const name = leagueName.toLowerCase();
    if (/champions league|europa league|conference league|euro \d|world cup/i.test(name)) return 0.5;
    if (/\bcup\b|\bcopa\b|\bcoupe\b|\bcoppa\b|\bpokal\b|\btrophy\b|\bshield\b/i.test(name)) return 0.25;
    if (/premier league/i.test(name) && country === 'England') return 0.5;
    if (/la liga/i.test(name) && country === 'Spain') return 0.5;
    if (/bundesliga/i.test(name) && country === 'Germany') return 0.5;
    if (/serie a/i.test(name) && country === 'Italy') return 0.5;
    if (/ligue 1/i.test(name) && country === 'France') return 0.5;
    if (/eredivisie|primeira liga|liga portugal|jupiler|pro league|super lig|premiership|superliga|eliteserien|allsvenskan|ekstraklasa|fortuna liga|chance liga|a-league|j1 league|j-league|k league 1|mls|liga mx|brasileir/i.test(name)) return 0.35;
    if (/championship|2\.\s*bundesliga|serie b|ligue 2|segunda|eerste divisie|challenger|superettan|2\.\s*liga|league one|league two|obos/i.test(name)) return 0.2;
    const STRONG_REGIONS = new Set(['England','Spain','Germany','Italy','France','Netherlands','Portugal','Belgium','Turkey','Austria','Switzerland','Scotland','Czech-Republic','Denmark','Norway','Sweden','Finland','Poland','Greece','Croatia','Serbia','Romania','Hungary','Slovakia','Slovenia','Ukraine','Ireland','Iceland','Bosnia','Japan','South-Korea','Australia','USA','Mexico','Brazil','Argentina','Morocco','Tunisia','Egypt','South-Africa']);
    if (STRONG_REGIONS.has(country)) return 0.1;
    return 0;
}

function oddsValueScore(avgOdds) {
    if (avgOdds >= 2.2 && avgOdds <= 2.6) return 0.4;
    if (avgOdds > 2.6 && avgOdds <= 2.8) return 0.3;
    if (avgOdds > 2.8 && avgOdds <= 3.0) return 0.15;
    return 0.05;
}

function scoreMatch(pred, avgOdds, leagueName, country) {
    const home = pred.teams?.home;
    const away = pred.teams?.away;
    if (!home || !away) return { total: 0, detail: 'no data', expectedGoals: 0 };
    const hFor5 = parseFloat(home.last_5?.goals?.for?.average) || 0;
    const hAgn5 = parseFloat(home.last_5?.goals?.against?.average) || 0;
    const aFor5 = parseFloat(away.last_5?.goals?.for?.average) || 0;
    const aAgn5 = parseFloat(away.last_5?.goals?.against?.average) || 0;
    const hForS = parseFloat(home.league?.goals?.for?.average?.total) || hFor5;
    const hAgnS = parseFloat(home.league?.goals?.against?.average?.total) || hAgn5;
    const aForS = parseFloat(away.league?.goals?.for?.average?.total) || aFor5;
    const aAgnS = parseFloat(away.league?.goals?.against?.average?.total) || aAgn5;
    const hForHome = parseFloat(home.league?.goals?.for?.average?.home) || hForS;
    const hAgnHome = parseFloat(home.league?.goals?.against?.average?.home) || hAgnS;
    const aForAway = parseFloat(away.league?.goals?.for?.average?.away) || aForS;
    const aAgnAway = parseFloat(away.league?.goals?.against?.average?.away) || aAgnS;
    const recentAvg = (hFor5 + hAgn5 + aFor5 + aAgn5) / 2;
    const seasonAvg = (hForS + hAgnS + aForS + aAgnS) / 2;
    const splitAvg = (hForHome + aForAway + hAgnHome + aAgnAway) / 2;
    const expectedGoals = recentAvg * 0.4 + seasonAvg * 0.3 + splitAvg * 0.3;
    let h2hAvg = 0;
    const h2h = pred.h2h || [];
    if (h2h.length > 0) { h2hAvg = h2h.reduce((a, g) => a + (g.goals?.home || 0) + (g.goals?.away || 0), 0) / h2h.length; }
    const h2hBonus = h2hAvg > 3.5 ? 0.4 : h2hAvg > 3.0 ? 0.3 : h2hAvg > 2.5 ? 0.2 : h2hAvg > 2.0 ? 0.1 : 0;
    const apiTip = pred.predictions?.under_over;
    const apiBonus = apiTip === '+3.5' ? 0.5 : apiTip === '+2.5' ? 0.3 : 0;
    const hFailHome = parseInt(home.league?.failed_to_score?.home) || 0;
    const hPlayedHome = parseInt(home.league?.fixtures?.played?.home) || 1;
    const aFailAway = parseInt(away.league?.failed_to_score?.away) || 0;
    const aPlayedAway = parseInt(away.league?.fixtures?.played?.away) || 1;
    const hScoreRate = 1 - (hFailHome / hPlayedHome);
    const aScoreRate = 1 - (aFailAway / aPlayedAway);
    const bttsBonus = (hScoreRate >= 0.75 && aScoreRate >= 0.75) ? 0.35 : (hScoreRate >= 0.60 && aScoreRate >= 0.60) ? 0.15 : 0;
    const dryPenalty = (hFor5 < 0.6 || aFor5 < 0.6) ? -0.4 : (hFor5 < 0.8 || aFor5 < 0.8) ? -0.2 : 0;
    const leagueBonus = leagueQualityBonus(leagueName, country);
    const oddsBonus = oddsValueScore(avgOdds);
    const total = expectedGoals + h2hBonus + apiBonus + bttsBonus + dryPenalty + leagueBonus + oddsBonus;
    const flags = [apiTip === '+2.5' || apiTip === '+3.5' ? 'API✓' : '', bttsBonus > 0 ? 'BTTS✓' : '', dryPenalty < 0 ? 'DRY⚠' : '', leagueBonus >= 0.35 ? '⭐' : ''].filter(Boolean).join(' ');
    const detail = 'exp ' + expectedGoals.toFixed(1) + 'g, L5 ' + recentAvg.toFixed(1) + ', H2H ' + h2hAvg.toFixed(1) + ', odds ' + avgOdds.toFixed(2) + (flags ? ', ' + flags : '');
    return { total, detail, expectedGoals };
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
    console.log('🤖 Kombík Bot – API-Sports (v3)\n');
    const now = new Date();
    const maxTime = new Date(now.getTime() + 24 * 60 * 60 * 1000);
    console.log('⏰ ' + now.toUTCString() + ' → ' + maxTime.toUTCString() + ' (24h okno)\n');
    const today = fmtDate(now), tomorrow = fmtDate(maxTime);
    const dates = [today]; if (tomorrow !== today) dates.push(tomorrow);
    let fixtures = [];
    for (const d of dates) { console.log('📅 Fixtures ' + d + '...'); fixtures.push(...await getFootballFixtures(d)); await sleep(350); }
    console.log('   ' + fixtures.length + ' naplánovaných zápasů\n');
    const skipped = new Set();
    fixtures = fixtures.filter(f => {
        const t = new Date(f.fixture.date);
        const c = f.league.country;
        if (t < now || t > maxTime) return false;
        if (EXCLUDED_COUNTRIES.has(c) || BLOCKED_AFRICAN.has(c)) return false;
        if (isBlockedLeague(f.league.name)) { skipped.add('⛔ ' + f.league.name + ' (' + c + ')'); return false; }
        const maxTier = c === 'England' ? 6 : 2;
        const tier = estimateLeagueTier(f.league.name, c);
        if (tier > maxTier) { skipped.add('⛔ T' + tier + ' ' + f.league.name + ' (' + c + ')'); return false; }
        return true;
    });
    if (skipped.size > 0) { console.log('   Vyřazeno (' + skipped.size + '):'); for (const s of [...skipped].slice(0, 20)) console.log('     ' + s); if (skipped.size > 20) console.log('     ... a dalších ' + (skipped.size - 20)); }
    console.log('   ' + fixtures.length + ' zápasů po filtru (1.+2. liga, EN max 6., poháry OK)\n');
    const fixtureMap = new Map(), leagueMap = new Map();
    for (const f of fixtures) {
        fixtureMap.set(f.fixture.id, f);
        const key = f.league.id + '_' + f.league.season;
        if (!leagueMap.has(key)) leagueMap.set(key, { id: f.league.id, season: f.league.season, name: f.league.name, country: f.league.country, dates: new Set() });
        leagueMap.get(key).dates.add(fmtDate(new Date(f.fixture.date)));
    }
    console.log('   ' + leagueMap.size + ' lig → stahuji kurzy...\n');
    const matchMap = new Map();
    for (const [, lg] of leagueMap) {
        for (const d of lg.dates) {
            const oddsData = await getFootballLeagueOdds(lg.id, lg.season, d);
            for (const entry of oddsData) {
                const fix = fixtureMap.get(entry.fixture?.id); if (!fix) continue;
                const matchKey = fix.fixture.id;
                for (const bm of entry.bookmakers || []) { for (const bet of bm.bets || []) { for (const v of bet.values || []) {
                    if (v.value !== 'Over 2.5') continue;
                    const odd = parseFloat(v.odd); if (isNaN(odd) || odd < MIN_ODDS || odd > MAX_ODDS) continue;
                    if (!matchMap.has(matchKey)) matchMap.set(matchKey, { fixtureId: matchKey, homeId: fix.teams.home.id, awayId: fix.teams.away.id, league: lg.name, country: lg.country, match: fix.teams.home.name + ' - ' + fix.teams.away.name, tip: 'Over 2.5', kickoff: fix.fixture.date, allOdds: [] });
                    matchMap.get(matchKey).allOdds.push(odd);
                } } }
            }
            await sleep(350);
        }
    }
    const candidates = [...matchMap.values()];
    console.log('📊 ' + candidates.length + ' kandidátů s Over 2.5 (kurz ' + MIN_ODDS + '–' + MAX_ODDS + ')\n');
    if (candidates.length === 0) { console.warn('⚠ Žádné zápasy s vhodnými kurzy.'); process.exit(0); }
    candidates.sort((a, b) => {
        const aAvg = a.allOdds.reduce((x, y) => x + y, 0) / a.allOdds.length;
        const bAvg = b.allOdds.reduce((x, y) => x + y, 0) / b.allOdds.length;
        return Math.abs(aAvg - 2.2) - Math.abs(bAvg - 2.2);
    });
    const toAnalyze = candidates.slice(0, MAX_ANALYZE);
    console.log('🔍 Analyzuji ' + toAnalyze.length + '/' + candidates.length + ' zápasů (predictions)...\n');
    const scored = [];
    for (let i = 0; i < toAnalyze.length; i++) {
        const m = toAnalyze[i];
        const avg = m.allOdds.reduce((a, b) => a + b, 0) / m.allOdds.length;
        const pred = await getMatchPrediction(m.fixtureId);
        if (pred) {
            const sc = scoreMatch(pred, avg, m.league, m.country);
            scored.push({ league: m.league, country: m.country, match: m.match, tip: m.tip, odds: avg.toFixed(2), score: sc.total, detail: sc.detail, kickoff: m.kickoff });
        }
        if (i < toAnalyze.length - 1) await sleep(350);
    }
    console.log('📊 ' + scored.length + ' zápasů ohodnoceno');
    if (scored.length > 0) {
        scored.sort((a, b) => b.score - a.score);
        console.log('   Top 10:');
        scored.slice(0, 10).forEach((p, i) => console.log('   ' + (i + 1) + '. [' + p.league + '] ' + p.match + ' | ' + p.detail + ' | score ' + p.score.toFixed(2)));
    }
    // Výběr: top skóre, každý zápas z jiné soutěže
    const selected = [], usedLeagues = new Set();
    for (const pick of scored) {
        if (selected.length >= PICK_COUNT) break;
        if (!usedLeagues.has(pick.league)) { selected.push(pick); usedLeagues.add(pick.league); }
    }
    if (selected.length === 0) { console.warn('\n⚠ Žádné vhodné zápasy nalezeny.'); process.exit(0); }
    console.log('\n✅ Vybráno ' + selected.length + '/' + PICK_COUNT + ' zápasů z ' + usedLeagues.size + ' lig (' + reqCount + ' API req)\n');
    if (selected.length < PICK_COUNT) { console.warn('⚠ Pouze ' + selected.length + ' unikátních lig s vhodnými kurzy – méně než ' + PICK_COUNT + '.'); }
    const grouped = balanceGroups(selected);
    const output = grouped.map(m => ({ league: m.league, match: m.match, tip: m.tip, odds: m.odds, group: m.group, kickoff: m.kickoff, time: fmtKickoff(m.kickoff) }));
    writeFileSync('hot.json', JSON.stringify(output, null, 2), 'utf-8');
    console.log('📁 ' + output.length + ' zápasů → hot.json\n');
    const gc = Math.ceil(output.length / 2);
    for (let g = 1; g <= gc; g++) { const gm = output.filter(m => m.group === g); const go = gm.reduce((a, m) => a * parseFloat(m.odds), 1); console.log('  📦 Sk.' + g + ' (' + go.toFixed(2) + '):'); gm.forEach(m => console.log('     ⚽ [' + m.league + '] ' + m.match + ' | ' + m.tip + ' @ ' + m.odds + ' | 🕐 ' + m.time)); }
}

main().catch(err => { console.error('Chyba:', err); process.exit(1); });

