// Testy integrace FVE ENGINE -> react2.json (ACCA) / react3.json (QUAD 4).
// Spusteni: node test-engine.mjs
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
    evaluateEngine, planEngineSelection, balanceIntoGroups, toFeedItem, assertFeed, loadEngine,
    selectionToTip, scheduleDecision, pragueDate, pragueLocalToUtc, ACCA_COUNT, QUAD_COUNT,
} from './engine-picks.mjs';

let passed = 0, failed = 0;
async function test(name, fn) {
    try { await fn(); passed++; console.log('OK   ' + name); }
    catch (e) { failed++; console.error('FAIL ' + name + '\n     ' + e.message); }
}

const NOW = new Date('2026-11-08T08:25:00Z'); // 09:25 Praha (CET)
const TODAY = '2026-11-08';
const SELECTIONS = ['1X2:HOME', '1X2:AWAY', 'OU2.5:OVER', 'OU2.5:UNDER'];
function mkPick(i, extra = {}) {
    const sel = SELECTIONS[i % SELECTIONS.length];
    return {
        fixtureId: 1000 + i, leagueId: 300 + (i % 7),
        home: 'Home ' + i, away: 'Away ' + i,
        kickoffUtc: '2026-11-08T' + String(11 + (i % 10)).padStart(2, '0') + ':00:00Z',
        market: sel.split(':')[0], selection: sel,
        odds: Math.round((1.7 + (i % 9) * 0.1) * 100) / 100, bookmaker: 'Pinnacle', ...extra,
    };
}
function mkDoc(n, extra = {}) {
    return { schemaVersion: 1, date: TODAY, timeZone: 'Europe/Prague', status: 'READY', coverage: 'Complete', picks: Array.from({ length: n }, (_, i) => mkPick(i)), ...extra };
}
// Nazev ligy se v produkci doplnuje z API-Football podle leagueId.
const resolveLeague = p => (p.leagueId >= 300 && p.leagueId < 307 ? 'League ' + p.leagueId : '');
const ev = (doc, now = NOW, today = TODAY) => evaluateEngine(doc, { now, today, resolveLeague });
const plan = doc => planEngineSelection(ev(doc), TODAY);
const ids = list => list.map(p => p.fixtureId);

await test('ENGINE 0 zapasu -> oba puvodni vyber', () => {
    const p = plan(mkDoc(0));
    assert.equal(p.accaFromEngine, false); assert.equal(p.quadFromEngine, false);
});
await test('ENGINE 19 zapasu -> oba puvodni vyber', () => {
    const p = plan(mkDoc(19));
    assert.equal(p.count, 19); assert.equal(p.accaFromEngine, false); assert.equal(p.quadFromEngine, false);
});
for (const n of [20, 24]) {
    await test('ENGINE ' + n + ' zapasu -> ACCA ENGINE, QUAD puvodni', () => {
        const p = plan(mkDoc(n));
        assert.equal(p.accaFromEngine, true); assert.equal(p.quadFromEngine, false);
        assert.equal(p.acca.length, ACCA_COUNT); assert.equal(p.quad.length, 0);
    });
}
for (const n of [25, 40]) {
    await test('ENGINE ' + n + ' zapasu -> oba ENGINE, bez spolecnych zapasu', () => {
        const p = plan(mkDoc(n));
        assert.equal(p.accaFromEngine, true); assert.equal(p.quadFromEngine, true);
        assert.equal(p.acca.length, ACCA_COUNT); assert.equal(p.quad.length, QUAD_COUNT);
        assert.equal(new Set(ids(p.acca)).size, ACCA_COUNT);
        assert.equal(new Set(ids(p.quad)).size, QUAD_COUNT);
        const a = new Set(ids(p.acca));
        assert.ok(ids(p.quad).every(id => !a.has(id)), 'ACCA a QUAD sdileji zapas');
    });
}
await test('duplicitni fixtureId se pocita jednou a vybere se deterministicky', () => {
    const doc = mkDoc(20);
    doc.picks.push({ ...doc.picks[0], selection: 'OU2.5:OVER', market: 'OU2.5', odds: 2.5 });
    doc.picks.push({ ...doc.picks[1] });
    const e = ev(doc);
    assert.equal(e.picks.length, 20);
    assert.equal(e.picks.find(p => p.fixtureId === 1000).selection, '1X2:HOME');
    const reversed = { ...doc, picks: [...doc.picks].reverse() };
    assert.deepEqual(ids(plan(doc).acca), ids(plan(reversed).acca));
});
await test('duplicity snizi pocet pod 20 -> puvodni vyber', () => {
    const doc = mkDoc(19); doc.picks.push({ ...doc.picks[3] });
    assert.equal(plan(doc).accaFromEngine, false);
});
await test('neplatny kurz / chybejici udaje / rozehrany zapas / neznama liga se nepocitaji', () => {
    const doc = mkDoc(21);
    doc.picks[0].odds = 1; doc.picks[1].odds = 'abc'; doc.picks[2].odds = null;
    doc.picks[3].home = ''; doc.picks[4].selection = 'BTTS:YES'; doc.picks[5].kickoffUtc = '2026-11-08T08:00:00Z';
    doc.picks[6].kickoffUtc = 'nesmysl'; doc.picks[7].market = 'OU3.5'; doc.picks[8].leagueId = 999;
    assert.equal(ev(doc).picks.length, 12); assert.equal(plan(doc).accaFromEngine, false);
});
await test('vcerejsi datum -> nepouzije se', () => {
    const e = ev(mkDoc(40, { date: '2026-11-07' }));
    assert.equal(e.usable, false); assert.equal(e.final, false);
    assert.equal(plan(mkDoc(40, { date: '2026-11-07' })).accaFromEngine, false);
});
await test('status NO_SELECTION -> finalni, puvodni vyber', () => {
    const e = ev(mkDoc(0, { status: 'NO_SELECTION' }));
    assert.equal(e.usable, false); assert.equal(e.final, true);
});
await test('status NOT_READY -> nefinalni, puvodni vyber', () => {
    const e = ev(mkDoc(40, { status: 'NOT_READY' }));
    assert.equal(e.usable, false); assert.equal(e.final, false);
});
await test('coverage jina nez Complete -> puvodni vyber', () => {
    assert.equal(plan(mkDoc(40, { coverage: 'Partial' })).accaFromEngine, false);
});
await test('neplatny format -> puvodni vyber', () => {
    for (const d of [null, [], 'x', { date: TODAY, status: 'READY', coverage: 'Complete', picks: 'x' }])
        assert.equal(planEngineSelection(ev(d), TODAY).accaFromEngine, false);
});
await test('ENGINE nedostupny -> po limitu 09:30 vrati null', async () => {
    let t = new Date('2026-11-08T08:20:00Z').getTime(), calls = 0;
    const res = await loadEngine({ fetchImpl: async () => { calls++; return { ok: false, status: 404 }; }, now: () => new Date(t), sleepFn: async ms => { t += ms; }, pollMs: 60000, token: '' });
    assert.equal(res.doc, null);
    assert.ok(t <= new Date('2026-11-08T08:30:00Z').getTime(), 'cekalo se po 09:30');
    assert.ok(calls >= 2);
});
await test('aktualni READY -> neceka', async () => {
    let slept = 0;
    const res = await loadEngine({ fetchImpl: async () => ({ ok: true, text: async () => JSON.stringify(mkDoc(30)) }), now: () => NOW, sleepFn: async () => { slept++; }, token: '' });
    assert.equal(slept, 0); assert.equal(res.doc.status, 'READY');
});
await test('vcerejsi soubor -> ceka na dnesni, pak ho pouzije', async () => {
    let t = new Date('2026-11-08T08:20:00Z').getTime(), n = 0;
    const fetchImpl = async () => { n++; const d = n < 7 ? mkDoc(30, { date: '2026-11-07' }) : mkDoc(30); return { ok: true, text: async () => JSON.stringify(d) }; };
    const res = await loadEngine({ fetchImpl, now: () => new Date(t), sleepFn: async ms => { t += ms; }, token: '' });
    assert.equal(res.doc.date, TODAY);
});
await test('ruzne trhy Home/Away/Over/Under se zachovaji beze zmeny kurzu', () => {
    assert.equal(selectionToTip('1X2:HOME'), 'Home win');
    assert.equal(selectionToTip('1X2:AWAY'), 'Away win');
    assert.equal(selectionToTip('OU2.5:OVER'), 'Over 2.5');
    assert.equal(selectionToTip('OU2.5:UNDER'), 'Under 2.5');
    assert.equal(selectionToTip('BTTS:YES'), null);
    const doc = mkDoc(40);
    const p = plan(doc);
    for (const m of [...p.acca, ...p.quad]) {
        const src = doc.picks.find(x => x.fixtureId === m.fixtureId);
        assert.equal(m.odds, String(src.odds)); assert.equal(m.selection, src.selection);
        assert.equal(m.tip, selectionToTip(src.selection));
    }
    const tips = new Set(ev(doc).picks.map(x => x.tip));
    assert.deepEqual([...tips].sort(), ['Away win', 'Home win', 'Over 2.5', 'Under 2.5']);
});
await test('opakovany beh stejneho dne vraci stejny vyber, jiny den jiny', () => {
    assert.deepEqual(plan(mkDoc(40)), plan(mkDoc(40)));
    const other = planEngineSelection(ev(mkDoc(40, { date: '2026-11-09' }), NOW, '2026-11-09'), '2026-11-09');
    assert.notDeepEqual(ids(other.acca).sort(), ids(plan(mkDoc(40)).acca).sort());
});
await test('skupiny maji spravne pocty a format odpovida react2/react3', () => {
    const p = plan(mkDoc(40));
    const r2 = p.acca.map(toFeedItem), r3 = p.quad.map(toFeedItem);
    assertFeed(r2, ACCA_COUNT, 'react2'); assertFeed(r3, QUAD_COUNT, 'react3');
    for (const m of [...r2, ...r3]) assert.deepEqual(Object.keys(m), ['fixtureId', 'league', 'match', 'kickoff', 'tip', 'odds', 'group']);
    assert.deepEqual([...new Set(r2.map(m => m.group))].sort(), [1, 2, 3]);
    assert.deepEqual([...new Set(r3.map(m => m.group))].sort(), [1, 2, 3, 4]);
});
await test('assertFeed odmitne neuplny nebo duplicitni tiket', () => {
    const r2 = plan(mkDoc(40)).acca.map(toFeedItem);
    assert.throws(() => assertFeed(r2.slice(0, 5), ACCA_COUNT, 'react2'));
    assert.throws(() => assertFeed([...r2.slice(0, 5), { ...r2[0], group: 3 }], ACCA_COUNT, 'react2'));
});
await test('kurzove vyvazeni: 1.85-2.40 / 1.95-2.30 / 2.05-2.20', () => {
    const odds = [1.85, 1.95, 2.05, 2.20, 2.30, 2.40];
    const g = balanceIntoGroups(odds.map((o, i) => ({ fixtureId: 10 + i, odds: String(o) })));
    const pairs = [1, 2, 3].map(n => g.filter(x => x.group === n).map(x => Number(x.odds)).sort((a, b) => a - b));
    assert.deepEqual(pairs.sort((a, b) => a[0] - b[0]), [[1.85, 2.40], [1.95, 2.30], [2.05, 2.20]]);
    assert.deepEqual(g.map(x => x.odds).sort(), odds.map(String).sort(), 'kurzy se zmenily');
});
await test('kurzove vyvazeni je optimalni pro 8 zapasu', () => {
    const odds = [1.5, 1.6, 1.9, 2.0, 2.1, 2.6, 3.0, 3.4];
    const g = balanceIntoGroups(odds.map((o, i) => ({ fixtureId: i + 1, odds: String(o) })));
    const prods = [1, 2, 3, 4].map(n => g.filter(x => x.group === n).reduce((a, x) => a * Number(x.odds), 1));
    const spread = Math.max(...prods) - Math.min(...prods);
    const s = [...odds].sort((a, b) => a - b), naive = [0, 1, 2, 3].map(i => s[i] * s[7 - i]);
    assert.ok(spread <= Math.max(...naive) - Math.min(...naive) + 1e-9);
});
await test('cas spusteni: 09:20 Praha v lete i v zime, jen jeden slot', () => {
    const cases = [
        ['2026-07-01T07:20:00Z', '20 7 * * *', true], ['2026-07-01T08:20:00Z', '20 8 * * *', false],
        ['2026-12-01T08:20:00Z', '20 8 * * *', true], ['2026-12-01T07:20:00Z', '20 7 * * *', false],
        ['2026-03-29T07:20:00Z', '20 7 * * *', true], ['2026-03-29T08:20:00Z', '20 8 * * *', false],
        ['2026-10-25T08:20:00Z', '20 8 * * *', true], ['2026-10-25T07:20:00Z', '20 7 * * *', false],
        ['2026-03-28T08:20:00Z', '20 8 * * *', true], ['2026-10-24T07:20:00Z', '20 7 * * *', true],
        ['2026-07-01T09:50:00Z', '50 9 * * *', true], ['2026-12-01T10:50:00Z', '50 10 * * *', true],
        ['2026-12-01T09:50:00Z', '50 9 * * *', false],
    ];
    for (const [iso, cron, run] of cases) {
        const d = scheduleDecision('schedule', cron, new Date(iso));
        assert.equal(d.run, run, iso + ' ' + cron + ': ' + d.reason);
        if (run && d.kind === 'main') {
            const local = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Prague', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(iso));
            assert.equal(local, '09:20');
        }
    }
    assert.equal(scheduleDecision('schedule', '20 7 * * *', new Date('2026-07-01T08:05:00Z')).run, true);
    assert.equal(scheduleDecision('workflow_dispatch', '', new Date()).run, true);
});
await test('limit 09:30 Praha respektuje DST', () => {
    assert.equal(pragueLocalToUtc('2026-07-01', 9, 30).toISOString(), '2026-07-01T07:30:00.000Z');
    assert.equal(pragueLocalToUtc('2026-12-01', 9, 30).toISOString(), '2026-12-01T08:30:00.000Z');
    assert.equal(pragueDate(new Date('2026-11-08T23:30:00Z')), '2026-11-09');
});


// --- Doplnujici kontroly -----------------------------------------------------
const pollWith = async docs => {
    let t = Date.parse('2026-07-01T07:20:00Z'), calls = 0; // 09:20 CEST
    const res = await loadEngine({
        fetchImpl: async () => { const d = docs[Math.min(calls++, docs.length - 1)]; return d ? { ok: true, text: async () => JSON.stringify(d) } : { ok: false, status: 404 }; },
        now: () => new Date(t), sleepFn: async ms => { t += ms; }, pollMs: 60000, token: '',
    });
    return { res, calls, end: new Date(t).toISOString() };
};
const summerDoc = extra => ({ ...mkDoc(30), date: '2026-07-01', ...extra });
await test('polling: READY bez Complete ceka do 09:30', async () => {
    const r = await pollWith([summerDoc({ coverage: 'Partial' })]);
    assert.ok(r.calls >= 10, 'pokusu ' + r.calls); assert.ok(r.end <= '2026-07-01T07:30:00.000Z');
});
await test('polling: RUNNING ceka, pak READY/Complete rozhodne hned', async () => {
    const r = await pollWith([summerDoc({ status: 'RUNNING' }), summerDoc({ status: 'RUNNING' }), summerDoc({})]);
    assert.equal(r.calls, 3); assert.equal(r.res.doc.status, 'READY');
});
await test('polling: NO_SELECTION rozhodne hned', async () => {
    const r = await pollWith([summerDoc({ status: 'NO_SELECTION', picks: [] })]);
    assert.equal(r.calls, 1);
});
await test('polling: vcerejsi engine.json ceka do 09:30', async () => {
    const r = await pollWith([summerDoc({ date: '2026-06-30' })]);
    assert.ok(r.calls >= 10);
});
await test('ENGINE kurzy pod MIN_ODDS 1.95 zustanou beze zmeny', () => {
    const d = mkDoc(30); d.picks.forEach((p, i) => { p.odds = i % 2 ? '1.31' : 1.45; });
    const p = plan(d);
    for (const m of [...p.acca, ...p.quad]) assert.ok(m.odds === '1.31' || m.odds === '1.45');
    assert.equal(p.acca.length, 6); assert.equal(p.quad.length, 8);
});
await test('vyznam tipu: Home/Away/Over/Under -> stejny trh a strana, kurz beze zmeny', () => {
    const d = mkDoc(30);
    for (const m of plan(d).acca.concat(plan(d).quad)) {
        const src = d.picks.find(x => x.fixtureId === m.fixtureId);
        assert.equal(m.tip, { '1X2:HOME': 'Home win', '1X2:AWAY': 'Away win', 'OU2.5:OVER': 'Over 2.5', 'OU2.5:UNDER': 'Under 2.5' }[src.selection]);
        assert.equal(m.odds, String(src.odds));
        assert.equal(m.match, src.home + ' - ' + src.away);
        assert.equal(m.kickoff, src.kickoffUtc);
    }
});
await test('integrace: ENGINE soubory mimo globalni dedup a koordinovany vyber', () => {
    const src = readFileSync(new URL('./fetch-matches.mjs', import.meta.url), 'utf8');
    assert.match(src, /mainPickers=\[hotPicker,tipsPicker,\.\.\.\(enginePlan\.accaFromEngine\?\[\]:\[react2Picker\]\),\.\.\.\(enginePlan\.quadFromEngine\?\[\]:\[react3Picker\]\)\]/);
    assert.match(src, /react2=enginePlan\.acca\.map\(toFeedItem\)/);
    assert.match(src, /react3=enginePlan\.quad\.map\(toFeedItem\)/);
    assert.match(src, /react3Picker=mkPicker\('react3\.json',react3Pool/);
    assert.doesNotMatch(src, /enginePlan\.(acca|quad)[^\n]*blockedPicks/);
});

// --- Zaverecna kontrola ------------------------------------------------------
await test('polling: READY/Complete s <20 zapasy rozhodne hned (fallback)', async () => {
    const r = await pollWith([summerDoc({ picks: mkDoc(5).picks })]);
    assert.equal(r.calls, 1); assert.equal(r.end, '2026-07-01T07:20:00.000Z');
    const p = planEngineSelection(evaluateEngine(r.res.doc, { now: new Date(r.end), today: '2026-07-01', resolveLeague }), '2026-07-01');
    assert.equal(p.accaFromEngine, false); assert.equal(p.quadFromEngine, false);
});
await test('pozdni start 10:15 Praha: hotovy engine.json se pouzije', async () => {
    let t = Date.parse('2026-07-01T08:15:00Z'), calls = 0;
    const res = await loadEngine({ fetchImpl: async () => { calls++; return { ok: true, text: async () => JSON.stringify(summerDoc({})) }; }, now: () => new Date(t), sleepFn: async ms => { t += ms; }, token: '' });
    assert.equal(calls, 1); assert.equal(res.doc.status, 'READY');
    const p = planEngineSelection(evaluateEngine(res.doc, { now: new Date(t), today: res.today, resolveLeague }), res.today);
    assert.equal(p.acca.length, 6); assert.equal(p.quad.length, 8);
});
await test('pozdni start 10:15 Praha: nehotovy engine = jeden pokus, bez cekani', async () => {
    let t = Date.parse('2026-07-01T08:15:00Z'), calls = 0, slept = 0;
    await loadEngine({ fetchImpl: async () => { calls++; return { ok: false, status: 404 }; }, now: () => new Date(t), sleepFn: async ms => { slept++; t += ms; }, token: '' });
    assert.equal(calls, 2); assert.equal(slept, 0); // 1 pokus = GitHub API + raw
});
await test('Under 2.5 projde assertFeed beze zmeny', () => {
    const d = mkDoc(30); d.picks.forEach(p => { p.selection = 'OU2.5:UNDER'; p.market = 'OU2.5'; });
    const p = plan(d);
    const r2 = p.acca.map(toFeedItem), r3 = p.quad.map(toFeedItem);
    assertFeed(r2, ACCA_COUNT, 'react2.json'); assertFeed(r3, QUAD_COUNT, 'react3.json');
    for (const m of [...r2, ...r3]) assert.equal(m.tip, 'Under 2.5');
});
await test('integrace: varovani pri prekryvu ACCA x QUAD vypisuje fixtureId', () => {
    const src = readFileSync(new URL('./fetch-matches.mjs', import.meta.url), 'utf8');
    assert.match(src, /VAROVANI: QUAD 4 \(puvodni vyber\) sdili[^\n]*overlap\.join/);
    assert.match(src, /VAROVANI QUAD 4: ACCA zapasy NEVYNECHANY[^\n]*accaIds/);
});

console.log('\n' + passed + ' passed, ' + failed + ' failed');
if (failed) process.exit(1);
