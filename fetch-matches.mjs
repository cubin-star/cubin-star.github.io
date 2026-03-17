/**
 * Bot pro denní výběr 6 fotbalových zápasů s Over 2.5 góly a kurzem 2.2–3.0
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
const MIN_EXPECTED_GOALS = 2.7;  // Bezpečnostní polštář – nechceme hraniční 2.5, chceme jasné Over
const PICK_COUNT = 6;
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
// Povolené africké: Egypt, Morocco, Tunisia, South-Africa

const EUROPEAN_COUNTRIES = new Set([
    'England', 'Spain', 'Germany', 'Italy', 'France', 'Netherlands', 'Portugal',
    'Belgium', 'Turkey', 'Austria', 'Switzerland', 'Scotland', 'Czech-Republic',
    'Denmark', 'Norway', 'Sweden', 'Finland', 'Poland', 'Greece', 'Croatia',
    'Serbia', 'Romania', 'Bulgaria', 'Hungary', 'Slovakia', 'Slovenia',
    'Ukraine', 'Ireland', 'Northern-Ireland', 'Wales', 'Iceland', 'Bosnia',
    'Montenegro', 'Albania', 'North-Macedonia', 'Luxembourg', 'Cyprus',
    'Malta', 'Estonia', 'Latvia', 'Lithuania', 'Georgia', 'Armenia',
    'Azerbaijan', 'Kazakhstan', 'Moldova', 'Kosovo', 'Faroe-Islands',
    'Gibraltar', 'Andorra', 'San-Marino', 'Liechtenstein',
]);

/** Detekce ženských soutěží podle názvu ligy */
function isWomenLeague(name) {
    const n = name.toLowerCase();
    return /women|feminine|feminin|\bfrauen\b|\bdamer\b|\bkvinner\b|\bžen[yí]?\b|\bladies\b|\bfemenin/i.test(n);
}

/**
 * Odhad úrovně (tieru) ligy podle názvu.
 * 1 = nejvyšší liga, 2 = druhá liga, atd.
 * Cup/pohárové soutěže a mezinárodní = tier 1.
 * WHITELIST přístup: co nerozpoznáme → tier 99 (= vyřazeno).
 */
function estimateLeagueTier(leagueName, country) {
    const name = leagueName.toLowerCase();

    // Mezinárodní / pohárové soutěže → vždy OK
    if (/champions league|europa league|conference league|euro \d|world cup|super cup|supercup|\bcup\b|\bcopa\b|\bcoupe\b|\bcoppa\b|\bpokal\b|\btaça\b|\bpohár\b|\bkaupa|\btrophy\b|\bshield\b/i.test(leagueName)) return 1;

    // ── Anglie – specifické názvy ──
    if (country === 'England') {
        if (/premier league/i.test(name)) return 1;
        if (/championship/i.test(name)) return 2;
        if (/league one/i.test(name)) return 3;
        if (/league two/i.test(name)) return 4;
        if (/national league/i.test(name) && !/north|south/i.test(name)) return 5;
        if (/national league.*(north|south)/i.test(name)) return 6;
        return 99;
    }

    // ── Španělsko – pouze La Liga a La Liga 2 (Segunda División) ──
    // Blokuje Segunda División RFEF, Tercera, Primera Federación atd.
    if (country === 'Spain') {
        if (/la liga(?!\s*2)/i.test(name)) return 1;
        if (/la liga 2/i.test(name)) return 2;
        if (/segunda divisi[oó]n(?!.*rfef)/i.test(name)) return 2;
        if (/smartbank/i.test(name)) return 2;
        // Copa del Rey je zachycena výše generickým cup regexem
        return 99;
    }

    // ── Whitelist 1. ligy (explicitní názvy) ──
    const TIER1 = [
        /la liga(?!\s*2)/i,                           // Španělsko
        /\bbundesliga(?!\s*2)/i,                      // Německo / Rakousko
        /\bserie a/i,                                 // Itálie
        /\bligue 1/i,                                 // Francie
        /\beredivisie/i,                              // Nizozemsko
        /primeira liga|liga portugal(?!\s*2)/i,        // Portugalsko
        /jupiler|pro league(?!\s*[2b])/i,             // Belgie
        /s[uü]per lig(?!\s*[2b])/i,                   // Turecko
        /super league(?!\s*2)/i,                       // Řecko / Švýcarsko
        /premiership/i,                                // Skotsko
        /superliga(?!\s*[2b])/i,                       // Dánsko / Srbsko
        /eliteserien/i,                                // Norsko
        /allsvenskan/i,                                // Švédsko
        /veikkausliiga/i,                              // Finsko
        /ekstraklasa/i,                                // Polsko
        /1\.\s*hnl|prva hnl/i,                        // Chorvatsko
        /liga i(?:\b|$)|liga 1(?:\b|$)/i,             // Rumunsko
        /first professional league|parva liga/i,       // Bulharsko
        /nb i(?:\b|$)|otp bank/i,                     // Maďarsko
        /fortuna liga|niké liga/i,                     // Slovensko / ČR
        /prvaliga|prva liga(?!\s*[2b])/i,             // Slovinsko
        /premier league/i,                             // Ukrajina / Irsko (obecný)
        /premier division/i,                           // Irsko
        /úrvalsdeild/i,                                // Island
        /meistaraflokkur/i,                            // Faerské ostrovy
        /a-league/i,                                   // Austrálie
        /j1 league|j-league/i,                         // Japonsko
        /k league 1/i,                                 // Jižní Korea
        /mls/i,                                        // USA
        /liga mx/i,                                    // Mexiko
        /brasileir[aã]o.*s[eé]rie a/i,                // Brazílie
        /primera divisi[oó]n(?!\s*[2b])/i,            // Argentina aj.
        /botola pro(?!\s*2)/i,                         // Maroko
        /ligue 1.*pro/i,                               // Tunisko
        /egyptian premier/i,                           // Egypt
        /south african premier/i,                      // Jižní Afrika
        /1\.\s*liga(?!\s*(fa|2|b))/i,                  // ČR (1. liga)
        /chance liga|synot liga/i,                      // ČR (sponzorský název)
    ];

    // ── Whitelist 2. ligy (explicitní názvy) ──
    const TIER2 = [
        /championship/i,                               // Anglie 2
        /la liga 2|segunda divisi[oó]n|smartbank/i,    // Španělsko
        /2\.\s*bundesliga/i,                           // Německo
        /serie b/i,                                    // Itálie
        /ligue 2/i,                                    // Francie
        /eerste divisie/i,                             // Nizozemsko
        /liga portugal 2|segunda liga/i,               // Portugalsko
        /challenger pro league/i,                      // Belgie
        /1\.\s*lig(?:i)?(?!\s*a)/i,                   // Turecko (2. úroveň)
        /super league 2/i,                             // Řecko
        /challenge league/i,                           // Švýcarsko
        /superliga 2|srpska liga/i,                    // Srbsko / Dánsko 2
        /1\.\s*division(?:\b|$)/i,                     // Dánsko / Norsko / Švédsko
        /obos-ligaen/i,                                // Norsko 2
        /superettan/i,                                 // Švédsko 2
        /ykk[oö]nen/i,                                // Finsko 2
        /i liga/i,                                     // Polsko 2
        /2\.\s*hnl/i,                                  // Chorvatsko 2
        /liga ii|liga 2(?:\b|$)/i,                     // Rumunsko 2
        /nb ii|nb 2/i,                                 // Maďarsko 2
        /2\.\s*liga/i,                                 // ČR / Slovensko 2
        /division 2/i,                                 // obecný
        /fnl|pfl/i,                                    // Rusko 2 (blokováno jinde)
        /brasileir[aã]o.*s[eé]rie b/i,                // Brazílie 2
        /k league 2/i,                                 // Jižní Korea 2
        /usl championship/i,                           // USA 2
        /liga de expansi[oó]n/i,                       // Mexiko 2
        /scottish championship/i,                      // Skotsko 2
    ];

    for (const re of TIER1) { if (re.test(name)) return 1; }
    for (const re of TIER2) { if (re.test(name)) return 2; }

    // Nerozpoznaná liga → tier 99 (vyřazeno)
    return 99;
}

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

async function getTeamLastFixtures(teamId, count = 3) {
    const data = await apiFetch(FOOTBALL_API, '/fixtures?team=' + teamId + '&last=' + count + '&status=FT');
    return data.response || [];
}

function countRecentUnder25(fixtures) {
    let under = 0;
    for (const f of fixtures) {
        const total = (f.goals?.home || 0) + (f.goals?.away || 0);
        if (total < 3) under++;  // Under 2.5 = méně než 3 góly
    }
    return under;
}

async function footballPicks(now, maxTime) {
    console.log('⚽ FOTBAL\n');
    const today = fmtDate(now), tomorrow = fmtDate(maxTime);
    const dates = [today]; if (tomorrow !== today) dates.push(tomorrow);
    let fixtures = [];
    for (const d of dates) { console.log('📅 Fixtures ' + d + '...'); fixtures.push(...await getFootballFixtures(d)); await sleep(350); }
    console.log('   ' + fixtures.length + ' naplánovaných zápasů\n');
    const skippedLeagues = new Set();
    fixtures = fixtures.filter(f => {
        const t = new Date(f.fixture.date);
        const c = f.league.country;
        if (t < now || t > maxTime) return false;
        if (EXCLUDED_COUNTRIES.has(c) || BLOCKED_AFRICAN.has(c)) return false;
        if (isWomenLeague(f.league.name)) { skippedLeagues.add('♀ ' + f.league.name + ' (' + c + ')'); return false; }
        const maxTier = c === 'England' ? 6 : 2;
        const tier = estimateLeagueTier(f.league.name, c);
        if (tier > maxTier) { skippedLeagues.add('⛔ T' + tier + ' ' + f.league.name + ' (' + c + ')'); return false; }
        return true;
    });
    if (skippedLeagues.size > 0) { console.log('   Vyřazeno:'); for (const s of skippedLeagues) console.log('     ' + s); }
    console.log('   ' + fixtures.length + ' v 24h okně (whitelist 1.+2. liga, EN max 6., poháry OK)');
    const fixtureMap = new Map(), leagueMap = new Map();
    for (const f of fixtures) {
        fixtureMap.set(f.fixture.id, f);
        const key = f.league.id + '_' + f.league.season;
        if (!leagueMap.has(key)) leagueMap.set(key, { id: f.league.id, season: f.league.season, name: f.league.name, country: f.league.country, dates: new Set() });
        leagueMap.get(key).dates.add(fmtDate(new Date(f.fixture.date)));
    }
    console.log('   ' + leagueMap.size + ' lig\n');
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
    // Kandidáti = zápasy s Over 2.5 kurzem v rozmezí
    const candidates = [...matchMap.values()];
    console.log('\n📊 ' + candidates.length + ' kandidátů s Over 2.5 (kurz ' + MIN_ODDS + '–' + MAX_ODDS + ')');

    // Analýza: pro každého kandidáta stáhni predictions (tým. statistiky, formu, H2H)
    const MAX_ANALYZE = 50;
    const toAnalyze = candidates.slice(0, MAX_ANALYZE);
    console.log('🔍 Analyzuji ' + toAnalyze.length + ' zápasů (predictions)...\n');

    const picks = [];
    let skippedLow = 0;
    for (let i = 0; i < toAnalyze.length; i++) {
        const m = toAnalyze[i];
        const avg = m.allOdds.reduce((a, b) => a + b, 0) / m.allOdds.length;
        const pred = await getMatchPrediction(m.fixtureId);
        if (pred) {
            const sc = scoreByTeamStats(pred);
            // Bezpečnostní polštář: přeskočit zápasy kde model čeká málo gólů
            if (sc.expectedGoals < MIN_EXPECTED_GOALS) {
                skippedLow++;
                continue;
            }
            picks.push({ league: m.league, country: m.country, match: m.match, tip: m.tip, odds: avg.toFixed(2), score: sc.total, detail: sc.detail, kickoff: m.kickoff, homeId: m.homeId, awayId: m.awayId, capabilityGoals: sc.capabilityGoals });
        }
        if (i < toAnalyze.length - 1) await sleep(350);
    }
    picks.sort((a, b) => b.score - a.score);

    // ═══════════ 2. průchod: Regresní filtr – ověření posledních zápasů ═══════════
    // Pro top kandidáty s capabilityGoals >= 2.7 stáhni poslední 3 zápasy obou týmů
    // a ověř, kolik z nich bylo Under 2.5. Pokud 1–2 → tým je "due" (dlužník).
    const VERIFY_TOP = 20;
    const toVerify = picks.slice(0, VERIFY_TOP).filter(p => p.capabilityGoals >= 2.7);
    if (toVerify.length > 0) {
        console.log('\n🔬 Regresní filtr: ověřuji posledních 3 zápasů pro ' + toVerify.length + ' kandidátů...');
        let dueCount = 0;
        for (const pick of toVerify) {
            const homeFix = await getTeamLastFixtures(pick.homeId, 3);
            await sleep(350);
            const awayFix = await getTeamLastFixtures(pick.awayId, 3);
            await sleep(350);
            const homeUnder = countRecentUnder25(homeFix);
            const awayUnder = countRecentUnder25(awayFix);
            // Tým s parametry na Over 2.5, ale 1–2 z posledních 3 zápasů pod 2.5 → dlužník
            const dueHome = homeUnder >= 1 && homeUnder <= 2;
            const dueAway = awayUnder >= 1 && awayUnder <= 2;
            if (dueHome || dueAway) {
                const dueBonus = (dueHome && dueAway) ? 0.6 : 0.35;
                pick.score += dueBonus;
                const dueTag = (dueHome ? 'H' + homeUnder + '/3u' : '') + (dueHome && dueAway ? '+' : '') + (dueAway ? 'A' + awayUnder + '/3u' : '');
                pick.detail += ', DUE🔄(' + dueTag + ')';
                dueCount++;
            }
        }
        console.log('   ' + dueCount + '/' + toVerify.length + ' kandidátů označeno jako DUE🔄 (dlužník)');
        picks.sort((a, b) => b.score - a.score);
    }

    console.log('📊 Analyzováno: ' + (picks.length + skippedLow) + ' zápasů (' + skippedLow + ' vyřazeno – exp. gólů < ' + MIN_EXPECTED_GOALS + ')');
    if (picks.length > 0) console.log('   Top 10 podle týmových statistik:');
    picks.slice(0, 10).forEach((p, i) => console.log('   ' + (i + 1) + '. [' + p.league + '] ' + p.match + ' | ' + p.detail + ' | score ' + p.score.toFixed(2)));
    return picks;
}

// ═══════════════════ PREDICTIONS ═══════════════════

async function getMatchPrediction(fixtureId) {
    const data = await apiFetch(FOOTBALL_API, '/predictions?fixture=' + fixtureId);
    return (data.response && data.response[0]) || null;
}

/**
 * Scoring čistě podle týmových dat – NE podle kurzů bookmakerů.
 *
 * Faktory:
 *  1. Útočná síla: průměr gólů obou týmů za posledních 5 zápasů
 *  2. Děravá obrana: průměr inkasovaných gólů obou týmů za posledních 5
 *  3. Celosezonní průměr gólů (for + against) obou týmů
 *  4. Domácí/venkovní split – přesnější odhad než celkové průměry
 *  5. H2H: průměr gólů ve vzájemných zápasech
 *  6. API prediction: bonus pokud API samo tipuje Over 2.5
 *  7. BTTS signál: oba týmy pravidelně skórují → větší šance na 3+ gólů
 *  8. Penalizace suchých týmů: tým co často neskóruje = riziko
 *  9. Regresní bonus: tým má sezónní parametry na Over 2.5, ale nedávno neprodukoval
 *     → regrese ke středu = pravděpodobný návrat k vyšším skóre
 */
function scoreByTeamStats(pred) {
    const home = pred.teams?.home;
    const away = pred.teams?.away;
    if (!home || !away) return { total: 0, detail: 'no data', expectedGoals: 0, capabilityGoals: 0 };

    // Last 5 matches
    const hFor5 = parseFloat(home.last_5?.goals?.for?.average) || 0;
    const hAgn5 = parseFloat(home.last_5?.goals?.against?.average) || 0;
    const aFor5 = parseFloat(away.last_5?.goals?.for?.average) || 0;
    const aAgn5 = parseFloat(away.last_5?.goals?.against?.average) || 0;

    // Season averages (celkové)
    const hForS = parseFloat(home.league?.goals?.for?.average?.total) || hFor5;
    const hAgnS = parseFloat(home.league?.goals?.against?.average?.total) || hAgn5;
    const aForS = parseFloat(away.league?.goals?.for?.average?.total) || aFor5;
    const aAgnS = parseFloat(away.league?.goals?.against?.average?.total) || aAgn5;

    // Domácí/venkovní split – přesnější: jak domácí skórují DOMA, jak hosté skórují VENKU
    const hForHome = parseFloat(home.league?.goals?.for?.average?.home) || hForS;
    const hAgnHome = parseFloat(home.league?.goals?.against?.average?.home) || hAgnS;
    const aForAway = parseFloat(away.league?.goals?.for?.average?.away) || aForS;
    const aAgnAway = parseFloat(away.league?.goals?.against?.average?.away) || aAgnS;

    // 1. Útočná síla (posledních 5): kolik gólů oba týmy střílejí
    const recentAttack = hFor5 + aFor5;

    // 2. Děravost obrany (posledních 5): kolik gólů oba týmy inkasují
    const recentDefWeak = hAgn5 + aAgn5;

    // 3. Sezonní průměr: stabilnější ukazatel
    const seasonAttack = hForS + aForS;
    const seasonDefWeak = hAgnS + aAgnS;

    // 4. Domácí/venkovní odhad: nejpřesnější prediktor
    const homeAwayExpected = (hForHome + aForAway + hAgnHome + aAgnAway) / 2;

    // Kombinovaný odhad gólů (posledních 5: 40%, sezóna: 30%, domácí/venkovní: 30%)
    const expectedRecent = (recentAttack + recentDefWeak) / 2;
    const expectedSeason = (seasonAttack + seasonDefWeak) / 2;
    const expectedGoals = expectedRecent * 0.4 + expectedSeason * 0.3 + homeAwayExpected * 0.3;

    // 5. H2H bonus
    let h2hAvg = 0;
    const h2h = pred.h2h || [];
    if (h2h.length > 0) {
        const totalG = h2h.reduce((a, g) => a + (g.goals?.home || 0) + (g.goals?.away || 0), 0);
        h2hAvg = totalG / h2h.length;
    }
    const h2hBonus = h2hAvg > 3.0 ? 0.4 : (h2hAvg > 2.5 ? 0.25 : (h2hAvg > 2.0 ? 0.1 : 0));

    // 6. API prediction bonus
    const apiTip = pred.predictions?.under_over;
    const apiBonus = (apiTip === '+3.5') ? 0.5 : (apiTip === '+2.5') ? 0.35 : 0;

    // 7. BTTS signál – oba týmy pravidelně skórují → víc gólů
    const hFailHome = parseInt(home.league?.failed_to_score?.home) || 0;
    const hPlayedHome = parseInt(home.league?.fixtures?.played?.home) || 1;
    const aFailAway = parseInt(away.league?.failed_to_score?.away) || 0;
    const aPlayedAway = parseInt(away.league?.fixtures?.played?.away) || 1;
    const hScoreRate = 1 - (hFailHome / hPlayedHome);
    const aScoreRate = 1 - (aFailAway / aPlayedAway);
    const bttsBonus = (hScoreRate >= 0.75 && aScoreRate >= 0.75) ? 0.4
                    : (hScoreRate >= 0.65 && aScoreRate >= 0.65) ? 0.2 : 0;

    // 8. Penalizace suchých týmů – tým co střílí < 0.8 za zápas je riziko
    const lowScorerPenalty = (hFor5 < 0.8 || aFor5 < 0.8) ? -0.5
                           : (hFor5 < 1.0 || aFor5 < 1.0) ? -0.2 : 0;

    // 9. Regresní bonus – tým má sezónní parametry na Over 2.5, ale nedávno neprodukoval
    //    Porovnání: sezónní domácí/venkovní capability vs průměr posledních 5 zápasů
    //    Pokud sezóna říká Over 2.5, ale last5 je nižší → tým je "dlužník" (regrese ke středu)
    const capabilityGoals = (hForHome + aAgnAway + aForAway + hAgnHome) / 2;
    const recentAvgGoals = (hFor5 + hAgn5 + aFor5 + aAgn5) / 2;
    const paramGap = capabilityGoals - recentAvgGoals;
    let regressionBonus = 0;
    if (capabilityGoals >= 2.7 && paramGap >= 0.3) {
        // Velký rozdíl (> 0.5) = silný signál, menší (0.3–0.5) = mírný
        regressionBonus = paramGap >= 0.6 ? 0.5 : (paramGap >= 0.4 ? 0.35 : 0.2);
    }

    const total = expectedGoals + h2hBonus + apiBonus + bttsBonus + lowScorerPenalty + regressionBonus;
    const flags = [apiTip === '+2.5' || apiTip === '+3.5' ? 'API✓' : '',
                   bttsBonus > 0 ? 'BTTS✓' : '',
                   regressionBonus > 0 ? 'REG🔄' : '',
                   lowScorerPenalty < 0 ? 'DRY⚠' : ''].filter(Boolean).join(' ');
    const detail = 'exp ' + expectedGoals.toFixed(1) + 'g, cap ' + capabilityGoals.toFixed(1) + ', L5 ' + recentAvgGoals.toFixed(1) + ', L5atk ' + recentAttack.toFixed(1) + ', H2H ' + h2hAvg.toFixed(1) + (flags ? ', ' + flags : '');
    return { total, detail, expectedGoals, capabilityGoals };
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
    let allPicks = await footballPicks(now, maxTime);
    console.log('\n📊 Celkem ' + allPicks.length + ' zápasů (' + reqCount + ' API req)');
    // Už seřazeno podle score – vybrat 6 z různých lig, priorita evropské soutěže
    const selected = [], usedLeagues = new Set();
    // 1. kolo: evropské ligy
    for (const pick of allPicks) { if (selected.length >= PICK_COUNT) break; if (EUROPEAN_COUNTRIES.has(pick.country) && !usedLeagues.has(pick.league)) { selected.push(pick); usedLeagues.add(pick.league); } }
    // 2. kolo: doplnit neevropskými, pokud není dost evropských
    for (const pick of allPicks) { if (selected.length >= PICK_COUNT) break; if (!usedLeagues.has(pick.league)) { selected.push(pick); usedLeagues.add(pick.league); } }

    // ═══════════════════ BACKFILL: doplnění z dalších 12h oken ═══════════════════
    const BACKFILL_WINDOW_MS = 12 * 60 * 60 * 1000;
    const MAX_BACKFILL_ROUNDS = 3;  // max 3 × 12h = +36h nad rámec 24h
    let backfillStart = maxTime;
    let backfillRound = 0;
    while (selected.length < PICK_COUNT && backfillRound < MAX_BACKFILL_ROUNDS) {
        backfillRound++;
        const backfillEnd = new Date(backfillStart.getTime() + BACKFILL_WINDOW_MS);
        const missing = PICK_COUNT - selected.length;
        console.log('\n🔄 Chybí ' + missing + ' zápas(ů) – prohledávám +12h okno #' + backfillRound + ' (' + backfillStart.toUTCString() + ' → ' + backfillEnd.toUTCString() + ')');
        const extraPicks = await footballPicks(backfillStart, backfillEnd);
        if (extraPicks.length === 0) {
            console.warn('   ⚠ Žádné vhodné zápasy v tomto okně.');
            backfillStart = backfillEnd;
            continue;
        }
        // Seřadit podle nejbližšího výkopu (při shodě upřednostnit vyšší score)
        extraPicks.sort((a, b) => {
            const td = new Date(a.kickoff) - new Date(b.kickoff);
            return td !== 0 ? td : b.score - a.score;
        });
        // 1. kolo backfillu: evropské ligy (dosud nepoužité)
        for (const pick of extraPicks) {
            if (selected.length >= PICK_COUNT) break;
            if (EUROPEAN_COUNTRIES.has(pick.country) && !usedLeagues.has(pick.league)) { selected.push(pick); usedLeagues.add(pick.league); }
        }
        // 2. kolo backfillu: ostatní ligy
        for (const pick of extraPicks) {
            if (selected.length >= PICK_COUNT) break;
            if (!usedLeagues.has(pick.league)) { selected.push(pick); usedLeagues.add(pick.league); }
        }
        backfillStart = backfillEnd;
    }
    if (selected.length < PICK_COUNT) {
        console.warn('\n⚠ Po backfillu stále chybí ' + (PICK_COUNT - selected.length) + ' zápas(ů). Pokračuji s ' + selected.length + '.');
    }

    if (selected.length === 0) { console.warn('\n⚠ Žádné vhodné zápasy nalezeny.'); process.exit(0); }
    const grouped = balanceGroups(selected);
    const output = grouped.map(m => ({ league: m.league, match: m.match, tip: m.tip, odds: m.odds, group: m.group, kickoff: m.kickoff }));
    writeFileSync('hot.json', JSON.stringify(output, null, 2), 'utf-8');
    console.log('\n✅ ' + output.length + ' zápasů → hot.json (' + reqCount + ' API req)\n');
    const gc = Math.ceil(output.length / 2);
    for (let g = 1; g <= gc; g++) { const gm = output.filter(m => m.group === g); const go = gm.reduce((a, m) => a * parseFloat(m.odds), 1); console.log('  📦 Sk.' + g + ' (' + go.toFixed(2) + '):'); gm.forEach(m => console.log('     ⚽ [' + m.league + '] ' + m.match + ' | ' + m.tip + ' @ ' + m.odds)); }
}

main().catch(err => { console.error('Chyba:', err); process.exit(1); });
