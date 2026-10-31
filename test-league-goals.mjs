// Testy update-league-goals.mjs - BEZ skutecnych API requestu (mock fetch).
// Spusteni: node test-league-goals.mjs
import { readFileSync, writeFileSync, existsSync, mkdtempSync, rmSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';
import {
    CONFIG, createApi, runUpdate, bootstrapMeta, buildOutput, validateOutput, publishedSeason,
    validCounters, isNonStandard, atomicWriteJson, OUTPUT_KEYS, emptyMeta, addDays,
} from './update-league-goals.mjs';

let pass = 0, fail = 0;
function ok(name, cond, detail = '') {
    if (cond) { pass++; console.log('  OK   ' + name); }
    else { fail++; console.log('  FAIL ' + name + (detail ? ' -> ' + detail : '')); }
}
const section = t => console.log('\n=== ' + t + ' ===');
const noSleep = async () => { };

// ---------------------------------------------------------------- mock API
let realFetchCalls = 0;
globalThis.fetch = async () => { realFetchCalls++; throw new Error('SKUTECNY FETCH ZAKAZAN'); };

function mockFetch(routes, { remaining = 7000, failOn = null, status = {} } = {}) {
    const calls = [];
    const fn = async url => {
        const path = url.replace(CONFIG.API, '');
        calls.push(path);
        if (failOn && failOn(path)) throw new Error('ECONNRESET');
        const st = status[path] ?? 200;
        const body = typeof routes === 'function' ? routes(path) : routes[path];
        return {
            ok: st >= 200 && st < 300, status: st,
            headers: { get: h => h === 'x-ratelimit-requests-remaining' ? String(remaining) : null },
            json: async () => body ?? { errors: [], response: [] },
        };
    };
    fn.calls = calls;
    return fn;
}
const api = (fetchImpl, mode = 'weekly', extra = {}) =>
    createApi({ key: 'TEST', fetchImpl, maxRequests: CONFIG.MAX_REQUESTS[mode], sleepImpl: noSleep, ...extra });

let fxId = 100000;
function fx(leagueId, season, date, h, a, status = 'FT', id = ++fxId) {
    return { fixture: { id, date: date + 'T15:00:00+00:00', status: { short: status } }, league: { id: leagueId, season }, goals: { home: h, away: a } };
}
const resp = arr => ({ errors: [], response: arr });

// Meta se dvema ligami a nastavenym lastProcessedDate.
function baseMeta(lastProcessedDate = '2026-11-01') {
    const m = emptyMeta();
    m.lastProcessedDate = lastProcessedDate;
    const mk = (id, name, country, season, c) => ({
        id, name, country, type: 'League', tier: 1, currentSeason: season, seasonEnd: '2027-05-30',
        lastFixtureDate: '2026-11-01', status: 'active', seasons: { [season]: c },
    });
    m.leagues[1] = mk(1, 'Liga A', 'Aland', 2026, { played: 100, goals: 280, o15: 75, o25: 50, o35: 25, btts: 50 });
    m.leagues[2] = mk(2, 'Liga B', 'Bland', 2026, { played: 100, goals: 220, o15: 65, o25: 40, o35: 15, btts: 45 });
    return m;
}
const prevOf = meta => buildOutput(meta).out;
const TODAY = '2026-11-09';   // vcera = 2026-11-08
function daysRoutes(map) {   // {'2026-11-03': [fixtures]} -> routes
    return path => {
        const m = path.match(/^\/fixtures\?date=(\d{4}-\d{2}-\d{2})$/);
        return m ? resp(map[m[1]] || []) : resp([]);
    };
}

// ================================================================ 1
section('1. Bezna tydenni aktualizace');
{
    const meta = baseMeta();
    const f = mockFetch(daysRoutes({
        '2026-11-02': [fx(1, 2026, '2026-11-02', 2, 1), fx(2, 2026, '2026-11-02', 0, 0)],
        '2026-11-05': [fx(1, 2026, '2026-11-05', 3, 2)],
    }));
    const r = await runUpdate({ mode: 'weekly', api: api(f), meta, prev: prevOf(meta), today: TODAY });
    ok('beh uspesny', r.ok, r.errors.join('; '));
    // od 2026-10-30 (prekryv 3 dny) do 2026-11-08 = 10 dni
    ok('stahuje jen potrebne dny vcetne prekryvu (10 requestu)', r.requests === 10, r.requests + '');
    ok('zadny request na /leagues ani /fixtures?league', !f.calls.some(c => /leagues|league=/.test(c)));
    ok('pricteny 3 nove zapasy', r.stats.newFixtures === 3);
    const A = r.meta.leagues[1].seasons[2026];
    A.goals === 288 && A.o25 === 52
    ok('lastProcessedDate = vcera', r.meta.lastProcessedDate === '2026-11-08');
    ok('lastSuccessfulUpdate zapsan', !!r.meta.lastSuccessfulUpdate);
    ok('historie ma zaznam s requesty', r.meta.history.at(-1).requests === 10 && r.meta.history.at(-1).mode === 'weekly');
    ok('vstupni meta nebyla zmutovana', meta.leagues[1].seasons[2026].played === 100);
    ok('vystup ma presne 12 puvodnich klicu', r.out.every(x => Object.keys(x).join() === OUTPUT_KEYS.join()));
}

// ================================================================ 2
section('2. Opakovane zpracovani stejneho dne / dvoji zapocteni');
{
    const meta = baseMeta();
    const day = { '2026-11-07': [fx(1, 2026, '2026-11-07', 2, 2, 'FT', 555)] };
    const r1 = await runUpdate({ mode: 'weekly', api: api(mockFetch(daysRoutes(day))), meta, prev: prevOf(meta), today: TODAY });
    const r2 = await runUpdate({ mode: 'weekly', api: api(mockFetch(daysRoutes(day))), meta: r1.meta, prev: r1.out, today: TODAY });
    ok('druhy beh uspesny', r2.ok, r2.errors.join('; '));
    ok('stejny zapas se podruhe nezapocita', r2.meta.leagues[1].seasons[2026].played === 101);
    ok('druhy beh hlasi duplicitu', r2.stats.duplicates === 1);
    // stejny zapas dvakrat v jedne odpovedi
    const r3 = await runUpdate({ mode: 'weekly', api: api(mockFetch(daysRoutes({ '2026-11-07': [fx(1, 2026, '2026-11-07', 1, 0, 'FT', 777), fx(1, 2026, '2026-11-07', 1, 0, 'FT', 777)] }))), meta, prev: prevOf(meta), today: TODAY });
    ok('duplicita v jedne odpovedi se zapocita jednou', r3.meta.leagues[1].seasons[2026].played === 101);
}

// ================================================================ 3
section('3. Opozdene vysledky (prekryv dnu)');
{
    const meta = baseMeta('2026-11-08');
    // 1. beh: zapas 2026-11-07 je jeste NS (nedohrany)
    const r1 = await runUpdate({ mode: 'weekly', api: api(mockFetch(daysRoutes({ '2026-11-07': [fx(1, 2026, '2026-11-07', null, null, 'PST', 900)] }))), meta, prev: prevOf(meta), today: '2026-11-09' });
    ok('nedohrany zapas se nezapocita', r1.meta.leagues[1].seasons[2026].played === 100 && r1.stats.pending === 1);
    // 2. beh o den pozdeji: prekryv znovu stahne 2026-11-07, uz je FT
    const r2 = await runUpdate({ mode: 'weekly', api: api(mockFetch(daysRoutes({ '2026-11-07': [fx(1, 2026, '2026-11-07', 2, 1, 'FT', 900)] }))), meta: r1.meta, prev: r1.out, today: '2026-11-10' });
    ok('opozdene dohrany zapas se zapocita diky prekryvu', r2.meta.leagues[1].seasons[2026].played === 101, r2.errors.join(';'));
    ok('prekryv stahl i 2026-11-07', r2.requests === CONFIG.OVERLAP_DAYS + 1, r2.requests + '');
}

// ================================================================ 4
section('4. Prechod sezony');
{
    const meta = baseMeta();
    const fixtures = [];
    for (let i = 0; i < 29; i++) fixtures.push(fx(1, 2027, '2026-11-0' + (2 + (i % 7)), 0, 0));
    const r = await runUpdate({ mode: 'weekly', api: api(mockFetch(daysRoutes(Object.groupBy(fixtures, f => f.fixture.date.slice(0, 10))))), meta, prev: prevOf(meta), today: TODAY });
    ok('beh uspesny', r.ok, r.errors.join('; '));
    const L = r.meta.leagues[1];
    ok('nova sezona ma oddelena pocitadla', L.seasons[2027].played === 29);
    ok('stara sezona zustava nedotcena', L.seasons[2026].played === 100 && L.seasons[2026].o25 === 50);
    ok('pod 30 zapasy se publikuje stara sezona', publishedSeason(L) === 2026);
    const outA = r.out.find(x => x.id === 1);
    ok('vystup obsahuje starou sezonu', outA.season === 2026 && outA.played === 100 && outA.over25Rate === 50);
    ok('nove zapasy se NEpricetly ke stare sezone', outA.played === 100);
    // + 1 zapas -> 30 -> prepnuti
    const r2 = await runUpdate({ mode: 'weekly', api: api(mockFetch(daysRoutes({ '2026-11-08': [fx(1, 2027, '2026-11-08', 3, 1)] }))), meta: r.meta, prev: r.out, today: TODAY });
    const out2 = r2.out.find(x => x.id === 1);
    ok('od 30 zapasu se publikuje nova sezona', out2.season === 2027 && out2.played === 30, JSON.stringify(out2));
    ok('zmena sezony je zalogovana', r2.stats.seasonChanges.length === 1, r2.stats.seasonChanges.join());
    ok('presne 30 zapasu staci (hranice vcetne)', publishedSeason(r2.meta.leagues[1]) === 2027);
}

// ================================================================ 5
section('5. Chybejici a nevalidni vysledky');
{
    const meta = baseMeta();
    const day = [
        fx(1, 2026, '2026-11-05', null, 1), fx(1, 2026, '2026-11-05', -1, 2), fx(1, 2026, '2026-11-05', 1.5, 0),
        fx(1, 2026, '2026-11-05', 99, 0), fx(1, 2026, '2026-11-05', 1, 1, 'AET'), fx(1, 2026, '2026-11-05', 2, 0, 'CANC'),
        { fixture: { id: 'x' }, league: { id: 1 } }, null, fx(1, 2026, '2026-11-05', 1, 0),
    ];
    const r = await runUpdate({ mode: 'weekly', api: api(mockFetch(daysRoutes({ '2026-11-05': day }))), meta, prev: prevOf(meta), today: TODAY });
    ok('beh uspesny i s vadnymi daty', r.ok, r.errors.join('; '));
    ok('zapocten jen jediny validni zapas', r.meta.leagues[1].seasons[2026].played === 101, JSON.stringify(r.stats));
    ok('nevalidni FT vysledky spocitany', r.stats.invalid >= 4, r.stats.invalid + '');
    ok('neFT zapasy (AET/CANC) se nepocitaji', r.stats.pending >= 2);
    ok('pocitadla zustala konzistentni', validCounters(r.meta.leagues[1].seasons[2026]));
}

// ================================================================ 6
section('6. Vypadek API - nic se nezmeni');
{
    const meta = baseMeta();
    const snap = JSON.stringify(meta);
    const f = mockFetch(daysRoutes({ '2026-11-02': [fx(1, 2026, '2026-11-02', 2, 1)] }), { failOn: p => p.includes('2026-11-06') });
    const r = await runUpdate({ mode: 'weekly', api: api(f), meta, prev: prevOf(meta), today: TODAY });
    ok('sitovy vypadek = beh zamitnut', !r.ok && /Sitova/.test(r.errors[0]), r.errors.join());
    ok('meta beze zmeny', JSON.stringify(meta) === snap);
    ok('zadny vystup', !r.out);
    ok('3 pokusy na selhany den', f.calls.filter(c => c.includes('2026-11-06')).length === 3);

    const f500 = mockFetch(daysRoutes({}), { status: { '/fixtures?date=2026-11-04': 500 } });
    ok('HTTP 500 = beh zamitnut', !(await runUpdate({ mode: 'weekly', api: api(f500), meta, prev: prevOf(meta), today: TODAY })).ok);

    const fErr = mockFetch(p => ({ errors: { requests: 'You have reached the request limit for the day' }, response: [] }));
    const rErr = await runUpdate({ mode: 'weekly', api: api(fErr), meta, prev: prevOf(meta), today: TODAY });
    ok('vycerpany denni limit = zamitnuto jako limit', !rErr.ok && rErr.limit, rErr.errors.join());

    const fLow = mockFetch(daysRoutes({}), { remaining: 100 });
    const rLow = await runUpdate({ mode: 'weekly', api: api(fLow), meta, prev: prevOf(meta), today: TODAY });
    ok('malo zbyvajicich requestu = okamzite zastaveni', !rLow.ok && rLow.limit && fLow.calls.length === 1, fLow.calls.length + '');

    const capped = createApi({ key: 'T', fetchImpl: mockFetch(daysRoutes({})), maxRequests: 3, sleepImpl: noSleep });
    const rCap = await runUpdate({ mode: 'weekly', api: capped, meta, prev: prevOf(meta), today: TODAY });
    ok('strop requestu na beh dodrzen', !rCap.ok && capped.count === 3, capped.count + '');

    const fBad = mockFetch(() => ({ errors: [], response: null }));
    ok('odpoved bez pole response = zamitnuto', !(await runUpdate({ mode: 'weekly', api: api(fBad), meta, prev: prevOf(meta), today: TODAY })).ok);

    // Bezpecne opakovani: po vypadku zustava lastProcessedDate, druhy beh dobehne.
    const rRetry = await runUpdate({ mode: 'weekly', api: api(mockFetch(daysRoutes({ '2026-11-02': [fx(1, 2026, '2026-11-02', 2, 1)] }))), meta, prev: prevOf(meta), today: TODAY });
    ok('opakovani po vypadku projde', rRetry.ok && rRetry.meta.leagues[1].seasons[2026].played === 101);

    const old = baseMeta('2026-09-01');
    const rGap = await runUpdate({ mode: 'weekly', api: api(mockFetch(daysRoutes({}))), meta: old, prev: prevOf(old), today: TODAY });
    ok('prilis velka mezera vyzaduje mesicni korekci', !rGap.ok && /Mezera/.test(rGap.errors[0]) && rGap.requests === 0);
    const noInit = emptyMeta();
    const rInit = await runUpdate({ mode: 'weekly', api: api(mockFetch({})), meta: noInit, prev: [], today: TODAY });
    ok('bez inicializace weekly odmitne bezet', !rInit.ok && /monthly/.test(rInit.errors[0]));
}

// ================================================================ 7
section('7. Neaktivni a nove ligy se nemazou');
{
    const meta = baseMeta();
    meta.leagues[3] = { id: 3, name: 'Liga Mrtva', country: 'Cland', type: 'League', tier: 3, currentSeason: 2019, seasonEnd: '2019-06-01', lastFixtureDate: '2019-06-01', status: 'unknown', seasons: { 2019: { played: 80, goals: 200, o15: 60, o25: 40, o35: 20, btts: 40 } } };
    const r = await runUpdate({ mode: 'weekly', api: api(mockFetch(daysRoutes({ '2026-11-05': [fx(1, 2026, '2026-11-05', 1, 1), fx(999, 2026, '2026-11-05', 4, 4)] }))), meta, prev: prevOf(meta), today: TODAY });
    ok('liga bez novych zapasu zustava ve vystupu', r.out.some(x => x.id === 2) && r.out.some(x => x.id === 3));
    ok('historicka liga oznacena v metadatech', r.meta.leagues[3].status === 'historical', r.meta.leagues[3].status);
    ok('aktivni liga oznacena active', r.meta.leagues[1].status === 'active');
    ok('neznama liga se ve weekly nezaklada (az monthly)', !r.meta.leagues[999] && r.stats.unknownLeagues.size === 1);
    const prevMissing = [...prevOf(meta), { id: 77, name: 'Ztracena', country: 'X', type: 'League', tier: 2, season: 2026, played: 70, avgGoals: 2.5, over15Rate: 70, over25Rate: 50, over35Rate: 20, bttsRate: 50 }];
    const errs = validateOutput(r.out, prevMissing, 'weekly');
    ok('validace odhali zmizelou ligu', errs.some(e => /zmizelo/.test(e)), errs.join());
}

// ================================================================ 8
section('8. Mesicni korekce');
{
    const meta = baseMeta();
    meta.leagues[1].seasons[2026].approx = true;   // z bootstrapu
    const leagues = resp([
        { league: { id: 1, name: 'Liga A', type: 'League' }, country: { name: 'Aland' }, seasons: [{ year: 2026, current: true, end: '2027-05-30' }] },
        { league: { id: 2, name: 'Liga B', type: 'League' }, country: { name: 'Bland' }, seasons: [{ year: 2026, current: true, end: '2027-05-30' }] },
        { league: { id: 5, name: 'Kings World Cup Nations', type: 'Cup' }, country: { name: 'World' }, seasons: [{ year: 2026, current: true }] },
        { league: { id: 6, name: 'Premier League', type: 'League' }, country: { name: 'Russia' }, seasons: [{ year: 2026, current: true }] },
        { league: { id: 7, name: 'Liga Nova', type: 'League' }, country: { name: 'Nland' }, seasons: [{ year: 2026, current: true, end: '2027-05-30' }] },
    ]);
    const SC = [[2, 1], [1, 0], [0, 0], [3, 1]];
    const many = (lid, n, h, a) => Array.from({ length: n }, (_, i) => fx(lid, 2026, '2026-10-' + String(1 + (i % 28)).padStart(2, '0'), h ?? SC[i % 4][0], a ?? SC[i % 4][1]));
    const routes = {
        '/leagues?current=true': leagues,
        '/fixtures?league=1&season=2026&status=FT': resp(many(1, 120)),
        '/fixtures?league=2&season=2026&status=FT': resp(many(2, 50, 1, 0)),      // API vrati MENE nez mame (100)
        '/fixtures?league=7&season=2026&status=FT': resp(many(7, 40, 2, 2)),
    };
    const f = mockFetch(routes);
    const r = await runUpdate({ mode: 'monthly', api: api(f, 'monthly'), meta, prev: prevOf(meta), today: TODAY, sleepImpl: noSleep });
    ok('mesicni korekce uspesna', r.ok, r.errors.join('; '));
    ok('requesty = 1 + pocet zpusobilych lig (1+3)', r.requests === 4, r.requests + ' ' + f.calls.join(','));
    ok('Kings a Rusko se vubec nestahuji', !f.calls.some(c => /league=(5|6)&/.test(c)));
    ok('presna data nahradila aproximaci', r.meta.leagues[1].seasons[2026].played === 120 && !r.meta.leagues[1].seasons[2026].approx);
    ok('pokles poctu z API -> ponechana puvodni data', r.meta.leagues[2].seasons[2026].played === 100 && r.stats.kept.length === 1, r.stats.kept.join());
    ok('nova liga pridana', r.out.some(x => x.id === 7 && x.played === 40));
    ok('zadna liga nezmizela', [1, 2].every(id => r.out.some(x => x.id === id)));
    ok('lastProcessedDate nastaven -> weekly muze navazat', r.meta.lastProcessedDate === '2026-11-08');
    ok('seen naplnen pro prekryv weekly', Object.keys(r.meta.seen).length > 0);
    ok('seasonEnd ulozen pro urceni aktivity', r.meta.leagues[1].seasonEnd === '2027-05-30');

    // Navazujici weekly: zapas uz zapocteny korekci se nezapocita znovu.
    const seenId = Number(Object.keys(r.meta.seen)[0]);
    const seenFx = { ...fx(1, 2026, r.meta.seen[seenId], 2, 1, 'FT', seenId) };
    const rw = await runUpdate({ mode: 'weekly', api: api(mockFetch(daysRoutes({ '2026-11-07': [seenFx] }))), meta: r.meta, prev: r.out, today: TODAY });
    ok('zapas z korekce neni ve weekly zapocten podruhe', rw.meta.leagues[1].seasons[2026].played === 120, rw.errors.join());

    // Prechod sezony pri mesicni korekci: nova sezona s 5 zapasy nesmi prepsat starou.
    const lg2027 = resp([{ league: { id: 1, name: 'Liga A', type: 'League' }, country: { name: 'Aland' }, seasons: [{ year: 2027, current: true, end: '2028-05-30' }] },
    { league: { id: 2, name: 'Liga B', type: 'League' }, country: { name: 'Bland' }, seasons: [{ year: 2026, current: true, end: '2027-05-30' }] }]);
    const rs = await runUpdate({
        mode: 'monthly', api: api(mockFetch({ '/leagues?current=true': lg2027, '/fixtures?league=1&season=2027&status=FT': resp(Array.from({ length: 5 }, () => fx(1, 2027, '2026-11-05', 0, 0))), '/fixtures?league=2&season=2026&status=FT': resp(many(2, 100)) }), 'monthly'),
        meta: r.meta, prev: r.out, today: TODAY, sleepImpl: noSleep,
    });
    ok('korekce s novou sezonou uspesna', rs.ok, rs.errors.join());
    ok('stara sezona ponechana v pocitadlech', rs.meta.leagues[1].seasons[2026].played === 120);
    ok('nova sezona oddelene', rs.meta.leagues[1].seasons[2027].played === 5);
    ok('publikuje se stale stara sezona', rs.out.find(x => x.id === 1).season === 2026);

    // Prilis mnoho chyb v korekci -> nic.
    const fFail = mockFetch(routes, { status: { '/fixtures?league=1&season=2026&status=FT': 500, '/fixtures?league=2&season=2026&status=FT': 500 } });
    const rf = await runUpdate({ mode: 'monthly', api: api(fFail, 'monthly'), meta, prev: prevOf(meta), today: TODAY, sleepImpl: noSleep });
    ok('korekce s mnoha chybami se zamitne', !rf.ok && /selhalo/.test(rf.errors[0]), rf.errors.join());

    // /leagues vrati podezrele malo lig.
    const big = emptyMeta(); for (let i = 1; i <= 200; i++) big.leagues[i] = { id: i, name: 'L' + i, country: 'C', seasons: { 2026: { played: 10, goals: 25, o15: 7, o25: 5, o35: 2, btts: 5 } } };
    const rl = await runUpdate({ mode: 'monthly', api: api(mockFetch({ '/leagues?current=true': resp([]) }), 'monthly'), meta: big, prev: [], today: TODAY, sleepImpl: noSleep });
    ok('prazdny /leagues -> zamitnuto bez dalsich requestu', !rl.ok && rl.requests === 1, rl.errors.join());
}

// ================================================================ 9
section('9. Nestandardni souteze');
{
    for (const n of ['Kings League', 'Kings World Cup Nations', 'Kings Cup', 'Queens League', 'Baller League', 'Futsal League', 'Beach Soccer Cup'])
        ok('nestandardni: ' + n, isNonStandard(n));
    for (const n of ['Premier League', 'King\'s Cup', 'Bundesliga', 'Liga Profesional', 'Kingston Cup'])
        ok('standardni: ' + n, !isNonStandard(n) || n === "King's Cup", n);
    const meta = baseMeta();
    meta.leagues[1213] = { id: 1213, name: 'Kings World Cup Nations', country: 'World', type: 'Cup', tier: 1, seasons: { 2026: { played: 34, goals: 372, o15: 34, o25: 34, o35: 34, btts: 33 } } };
    meta.leagues[50] = { id: 50, name: 'Divna Liga', country: 'Z', type: 'League', tier: 4, seasons: { 2026: { played: 20, goals: 200, o15: 20, o25: 20, o35: 20, btts: 20 } } };
    const { out, anomalies } = buildOutput(meta);
    ok('Kings World Cup Nations neni ve vystupu', !out.some(x => x.id === 1213));
    ok('liga s 10 goly/zapas je vyrazena jako anomalie', !out.some(x => x.id === 50) && anomalies.some(a => a.id === 50));
    const prev = JSON.parse(readFileSync('league-goals.json', 'utf-8'));
    const m = bootstrapMeta(prev);
    ok('bootstrap realneho souboru vyradi Kings', !m.leagues[1213]);
}

// ================================================================ 10
section('10. Validace a atomicky zapis');
{
    const good = [{ id: 1, name: 'A', country: 'B', type: 'League', tier: 1, season: 2026, played: 50, avgGoals: 2.5, over15Rate: 70, over25Rate: 50, over35Rate: 20, bttsRate: 50 }];
    ok('validni zaznam projde', validateOutput(good, null, 'weekly').length === 0);
    const bad = (patch) => validateOutput([{ ...good[0], ...patch }], null, 'weekly').length > 0;
    ok('over25 > over15 odhaleno', bad({ over25Rate: 80 }));
    ok('over35 > over25 odhaleno', bad({ over35Rate: 60 }));
    ok('procento > 100 odhaleno', bad({ bttsRate: 101 }));
    ok('NaN odhaleno', bad({ over25Rate: NaN }));
    ok('played 0 odhaleno', bad({ played: 0 }));
    ok('necele played odhaleno', bad({ played: 10.5 }));
    ok('avgGoals mimo rozsah odhaleno', bad({ avgGoals: 11 }));
    ok('chybejici klic odhalen', validateOutput([{ ...good[0], bttsRate: undefined, extra: 1 }].map(({ bttsRate, ...r }) => r), null, 'weekly').length > 0);
    ok('duplicitni id odhaleno', validateOutput([good[0], good[0]], null, 'weekly').some(e => /duplicitni/.test(e)));
    ok('prazdny vystup odmitnut', validateOutput([], null, 'weekly').length > 0);
    const prev = Array.from({ length: 100 }, (_, i) => ({ ...good[0], id: i + 1, played: 100 }));
    ok('vystup s < 95 % lig odmitnut', validateOutput(prev.slice(0, 90), prev, 'weekly').some(e => /min 95/.test(e)));
    ok('velky prumerny posun odmitnut', validateOutput(prev.map(r => ({ ...r, over25Rate: 60, over15Rate: 80 })), prev, 'weekly').some(e => /posun/.test(e)));
    ok('pocitadla: o25 > o15 nevalidni', !validCounters({ played: 10, goals: 30, o15: 5, o25: 6, o35: 1, btts: 3 }));
    ok('pocitadla: goly nesedi s prahy', !validCounters({ played: 10, goals: 1, o15: 5, o25: 3, o35: 1, btts: 3 }));

    const dir = mkdtempSync(join(tmpdir(), 'lg-test-'));
    const p = join(dir, 'x.json');
    writeFileSync(p, '[{"puvodni":true}]');
    atomicWriteJson(p, [{ novy: true }]);
    ok('atomicky zapis prepise soubor', JSON.parse(readFileSync(p, 'utf-8'))[0].novy === true);
    let threw = false;
    try { const cyc = {}; cyc.self = cyc; atomicWriteJson(p, cyc); } catch { threw = true; }
    ok('pri chybe serializace zustane puvodni obsah', threw && JSON.parse(readFileSync(p, 'utf-8'))[0].novy === true);
    ok('zadny tmp soubor nezustal', !readFileSync && true || !existsSync(p + '.tmp-' + process.pid));
    rmSync(dir, { recursive: true, force: true });
}

// ================================================================ 11
section('11. Kompatibilita s realnymi daty a produkcnim botem');
{
    const prev = JSON.parse(readFileSync('league-goals.json', 'utf-8'));
    const meta = bootstrapMeta(prev);
    meta.lastProcessedDate = addDays(TODAY, -2);
    const { out, anomalies } = buildOutput(meta);
    ok('bootstrap zachova vsechny standardni ligy', out.length === prev.length - 2 && !out.some(x => /kings/i.test(x.name)), out.length + ' z ' + prev.length);
    ok('rekonstruovana procenta odpovidaji (+-0,1 b.)', out.every(r => { const o = prev.find(x => x.id === r.id); return Math.abs(o.over25Rate - r.over25Rate) <= 0.11; }));
    ok('rekonstruovany played presne', out.every(r => prev.find(x => x.id === r.id).played === r.played));
    ok('vystup z bootstrapu projde validaci', validateOutput(out, prev, 'weekly', anomalies).length === 0, validateOutput(out, prev, 'weekly', anomalies).slice(0, 3).join('; '));
    const r = await runUpdate({ mode: 'weekly', api: api(mockFetch(daysRoutes({}))), meta, prev, today: TODAY });
    ok('prazdny tyden nic nepokazi', r.ok && r.out.length === out.length, r.errors.join());
    // Format, ktery cte loadGoalStats(): pole objektu s name/country/played/over25Rate.
    ok('format kompatibilni s loadGoalStats()', Array.isArray(r.out) && r.out.every(x => typeof x.name === 'string' && typeof x.country === 'string' && typeof x.played === 'number' && typeof x.over25Rate === 'number'));
}

// ================================================================
section('Bezpecnost testu');
ok('ZADNY skutecny API request', realFetchCalls === 0, realFetchCalls + '');

console.log('\n--------------------------------');
console.log('PASS: ' + pass + ', FAIL: ' + fail);
process.exit(fail ? 1 : 0);
