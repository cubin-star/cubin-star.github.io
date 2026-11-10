// Testy zalozniho behu (backup-plan.mjs) a rezimu HOT ONLY. Spusteni: node test-backup.mjs
import { readFileSync } from 'fs';
import { decideRun, isTodayContent, assertHotOnlyChanges, DAILY_FILES } from './backup-plan.mjs';
import { shouldWriteFile, isHotOnly, HOT_ONLY_WRITABLE } from './fetch-matches.mjs';

let pass = 0, fail = 0;
const ok = (c, n) => { if (c) pass++; else { fail++; console.error('FAIL: ' + n); } };
const throws = (fn, n) => { try { fn(); fail++; console.error('FAIL (nevyhodilo): ' + n); } catch { pass++; } };

const TODAY = '2026-11-11', YDAY = '2026-11-10';
// Realny cas commitu hlavniho behu (09:20 Praha + cekani na ENGINE) ~ 08:26 UTC v zime.
const CT = day => day + 'T08:26:53Z';
const rec = (kickoff, extra = {}) => ({ fixtureId: 1, league: 'L', match: 'A - B', kickoff, tip: 'Over 2.5', odds: '2.10', ...extra });
const items = day => [rec(day + 'T19:00:00+01:00'), rec(day + 'T21:00:00+01:00')];
const hotItems = day => Array.from({ length: 6 }, (_, i) => rec(day + 'T1' + i + ':00:00+01:00', { group: 'A' }));
const st = (commitDay, contentDay = commitDay) => ({ commitTime: commitDay ? CT(commitDay) : null, items: contentDay ? items(contentDay) : null });
const hst = (commitDay, contentDay = commitDay) => ({ commitTime: commitDay ? CT(commitDay) : null, items: contentDay ? hotItems(contentDay) : null });
const all = (day, hotDay) => {
    const s = { 'hot.json': hst(hotDay) };
    for (const f of DAILY_FILES) s[f] = st(day);
    return s;
};
const d = (state, kind = 'backup', extra = {}) => decideRun({ kind, today: TODAY, state, ...extra });

// --- A: rano kompletne uspesne -> skip
ok(d(all(TODAY, TODAY)).mode === 'skip', 'A: vse dnesni -> skip');
ok(d(all(TODAY, TODAY), 'main').mode === 'skip', 'A: opozdeny hlavni beh po uspechu -> skip');
// --- B: rano ok, hot.json chybi / stary / nekompletni -> hot-only
ok(d(all(TODAY, YDAY)).mode === 'hot-only', 'B: hot vcerejsi -> hot-only');
ok(d({ ...all(TODAY, YDAY), 'hot.json': { commitTime: CT(TODAY), items: hotItems(YDAY) } }).mode === 'hot-only', 'B: hot s dnesnim commitem ale starym obsahem -> hot-only');
ok(d({ ...all(TODAY, YDAY), 'hot.json': { commitTime: null, items: null } }).mode === 'hot-only', 'B: hot chybi -> hot-only');
ok(d({ ...all(TODAY, TODAY), 'hot.json': { commitTime: CT(TODAY), items: hotItems(TODAY).slice(0, 4) } }).mode === 'hot-only', 'B: dnesni hot.json se 4 zapasy -> hot-only');
// --- C: rano nic -> full
ok(d(all(YDAY, YDAY)).mode === 'full', 'C: nic dnes -> full');
ok(d({ 'hot.json': st(null, null), ...Object.fromEntries(DAILY_FILES.map(f => [f, st(null, null)])) }).mode === 'full', 'C: chybejici historie -> full');
// --- nejasny stav -> blocked (nic se neprepisuje)
{
    const s = all(TODAY, YDAY); s['react3.json'] = st(YDAY);
    ok(d(s).mode === 'blocked', 'blocked: react3 vcerejsi, react2 dnesni');
    const s2 = all(YDAY, YDAY); s2['react2.json'] = st(TODAY);
    ok(d(s2).mode === 'blocked', 'blocked: jen ACCA dnes -> neprepisovat');
    const s3 = all(TODAY, YDAY); s3['react2.json'] = { commitTime: CT(TODAY), items: items(YDAY) };
    ok(d(s3).mode === 'blocked', 'blocked: ACCA commit dnes se starym obsahem');
    const s4 = all(TODAY, YDAY); s4['react2.json'] = { commitTime: CT(TODAY), items: null };
    ok(d(s4).mode === 'blocked', 'blocked: ACCA nelze precist');
    const s5 = all(YDAY, TODAY);
    ok(d(s5).mode === 'blocked', 'blocked: jen hot dnes, ostatni ne');
    const s6 = all(TODAY, YDAY); s6['react3.json'] = { commitTime: CT(TODAY), items: [{ kickoff: TODAY + 'T19:00:00' }] };
    ok(d(s6).mode === 'blocked', 'blocked: QUAD bez casove zony -> neurcitelne');
    const s7 = all(TODAY, YDAY); s7['react2.json'] = { commitTime: CT(TODAY), items: [...items(TODAY), rec(TODAY + 'T19:00:00+01:00', { match: '' })] };
    ok(d(s7).mode === 'blocked', 'blocked: ACCA s neuplnym zaznamem');
    ok(/neprepisuje/.test(d(s).reason), 'blocked: duvod v logu');
}
// --- rucni spusteni (workflow_dispatch)
ok(d(all(TODAY, TODAY), 'manual').mode === 'full', 'manual: vychozi rezim full (puvodni chovani)');
ok(d(all(TODAY, TODAY), 'manual', { manualMode: 'full' }).mode === 'full', 'manual: FULL explicitne');
ok(d(all(TODAY, TODAY), 'manual', { manualMode: 'hot-only' }).mode === 'skip', 'manual HOT ONLY: kompletni dnesni hot bez potvrzeni -> skip');
ok(d(all(TODAY, TODAY), 'manual', { manualMode: 'hot-only', force: false }).mode === 'skip', 'manual HOT ONLY: force=false -> skip');
ok(d(all(TODAY, TODAY), 'manual', { manualMode: 'hot-only', force: true }).mode === 'hot-only', 'manual HOT ONLY: s potvrzenim -> hot-only');
ok(d(all(TODAY, YDAY), 'manual', { manualMode: 'hot-only' }).mode === 'hot-only', 'manual HOT ONLY: stary hot -> hot-only');
ok(d({ ...all(TODAY, TODAY), 'hot.json': { commitTime: CT(TODAY), items: hotItems(TODAY).slice(0, 4) } }, 'manual', { manualMode: 'hot-only' }).mode === 'hot-only', 'manual HOT ONLY: 4 zapasy -> hot-only');
ok(d(all(YDAY, YDAY), 'manual', { manualMode: 'hot-only' }).mode === 'hot-only', 'manual HOT ONLY: nikdy nevraci full');
ok(d({ ...all(TODAY, TODAY), 'hot.json': { commitTime: CT(TODAY), items: [...hotItems(TODAY).slice(0, 5), rec(TODAY + 'T19:00:00')] } }, 'manual', { manualMode: 'hot-only' }).mode === 'hot-only', 'manual HOT ONLY: hot s neuplnym zaznamem neni kompletni');
// --- opakovane spusteni: po uspesnem HOT ONLY -> skip
{
    const s = all(TODAY, YDAY);
    ok(d(s).mode === 'hot-only', 'opakovani: 1. zaloha hot-only');
    s['hot.json'] = hst(TODAY);
    ok(d(s).mode === 'skip', 'opakovani: 2. beh po uspechu -> skip');
    s['hot.json'] = st(YDAY);
    ok(d(s).mode === 'hot-only', 'opakovani: HOT ONLY selhal -> dalsi beh opet jen hot-only, ACCA/QUAD nedotceny');
}
// --- zmena ENGINE mezi rannim a zaloznim behem: rozhodnuti nezavisi na engine.json
{
    const s = all(TODAY, YDAY);
    const m1 = d(s).mode; s.engineChanged = true;
    ok(m1 === 'hot-only' && d(s).mode === 'hot-only', 'engine: zmena engine.json nemeni rezim ani neprepise ACCA/QUAD');
}

// --- isTodayContent: realne formaty (origin/main 2026-11-10, commit react2 08:26:53Z)
{
    const C = '2026-11-10T08:26:53Z', T = '2026-11-10';
    const hot = Array.from({ length: 4 }, () => ({ fixtureId: 1, league: 'L', match: 'A - B', kickoff: '2026-11-10T15:00:00+01:00', tip: 'Over 2.5', odds: '2.05', group: 'A' }));
    const best = [{ league: 'L', match: 'A - B', kickoff: '2026-11-10T16:00:00+01:00', tip: 'Over 2.5', odds: '2.40' }];
    const react1 = [{ fixtureId: 1, league: 'L', match: 'A - B', kickoff: '2026-11-10T15:00:00+01:00', tip: 'Over 2.5', odds: '2.0' }];
    const react2 = [rec('2026-11-10T22:30:00+01:00', { group: 'A' }), rec('2026-11-11T00:30:00+01:00', { group: 'B' })];
    const tips = [{ league: 'L', match: 'A - B', kickoff: '2026-11-10T20:00:00+01:00', tip: 'Over 2.5', odds: '2.2' }];
    for (const [n, a] of [['hot', hot], ['best', best], ['react1', react1], ['react2 (pres pulnoc)', react2], ['tips', tips]])
        ok(isTodayContent(a, T, C), 'format: ' + n + ' z origin/main je dnesni');
    ok(!isTodayContent(react2, '2026-11-11', C), 'format: vcerejsi commit neni dnesni');
    ok(!isTodayContent([], T, C), 'format: live1 prazdny -> nelze dokazat');
}
// --- isTodayContent: okno generatoru [commit - 6 h, commit + 24 h]
{
    const C = CT(TODAY), c = Date.parse(C);
    const at = ms => [rec(new Date(ms).toISOString())];
    ok(isTodayContent(at(c + 24 * 3600e3), TODAY, C), 'okno: presne +24 h');
    ok(!isTodayContent(at(c + 24 * 3600e3 + 1000), TODAY, C), 'okno: +24 h +1 s mimo');
    ok(isTodayContent(at(c - 6 * 3600e3), TODAY, C), 'okno: start behu -6 h');
    ok(!isTodayContent(at(c - 6 * 3600e3 - 1000), TODAY, C), 'okno: pred startem behu mimo');
    ok(!isTodayContent([rec('2026-11-12T20:00:00+01:00')], TODAY, C), 'okno: zitrejsi vecer (mimo 24 h) neni dnesni vyber');
    ok(isTodayContent([rec('2026-11-11T23:30:00Z')], TODAY, C), 'okno: UTC -> Praha 12.11. 00:30 v okne');
    ok(!isTodayContent([...items(TODAY), rec('2026-11-20T19:00:00+01:00')], TODAY, C), 'okno: jediny zapas mimo okno -> cely soubor neni dnesni');
}
// --- isTodayContent: neuplna / chybna data
{
    const C = CT(TODAY);
    ok(isTodayContent(items(TODAY), TODAY, C), 'obsah: dnesni');
    ok(!isTodayContent(items(YDAY), TODAY, C), 'obsah: vcerejsi');
    ok(!isTodayContent(null, TODAY, C) && !isTodayContent({}, TODAY, C) && !isTodayContent('x', TODAY, C), 'obsah: chybi / neni pole');
    ok(!isTodayContent(items(TODAY), TODAY, null) && !isTodayContent(items(TODAY), TODAY, 'x'), 'obsah: chybi datum commitu');
    ok(!isTodayContent(items(TODAY), TODAY, CT(YDAY)), 'obsah: commit vcera');
    ok(!isTodayContent([rec('x')], TODAY, C), 'obsah: neplatny kickoff');
    ok(!isTodayContent([rec(TODAY + 'T19:00:00')], TODAY, C), 'obsah: kickoff bez casove zony');
    ok(!isTodayContent([rec(TODAY)], TODAY, C), 'obsah: kickoff jen datum');
    ok(!isTodayContent([rec(TODAY + 'T25:00:00+01:00')], TODAY, C), 'obsah: kickoff neplatna hodina');
    ok(!isTodayContent([{ kickoff: TODAY + 'T19:00:00+01:00' }], TODAY, C), 'obsah: chybi match/tip');
    ok(!isTodayContent([rec(TODAY + 'T19:00:00+01:00', { tip: '' })], TODAY, C), 'obsah: prazdny tip');
    ok(!isTodayContent([...items(TODAY), null], TODAY, C), 'obsah: null zaznam');
    ok(!isTodayContent([rec(TODAY + 'T19:00:00+01:00', { kickoff: undefined })], TODAY, C), 'obsah: chybi kickoff');
}
// --- Europe/Prague: hranice dne a prechod letniho/zimniho casu
{
    const k = day => [rec(day + 'T18:00:00+02:00')];
    // zima: pulnoc CET = 23:00 UTC
    ok(isTodayContent(items(TODAY), TODAY, '2026-11-10T23:00:00Z'), 'Praha zima: 00:00 CET je novy den');
    ok(!isTodayContent(items(TODAY), TODAY, '2026-11-10T22:59:59Z'), 'Praha zima: 23:59:59 CET je predchozi den');
    // leto: pulnoc CEST = 22:00 UTC
    ok(isTodayContent(k('2026-07-15'), '2026-07-15', '2026-07-14T22:00:00Z'), 'Praha leto: 00:00 CEST je novy den');
    ok(!isTodayContent(k('2026-07-15'), '2026-07-15', '2026-07-14T21:59:59Z'), 'Praha leto: 23:59:59 CEST je predchozi den');
    // jaro 29.3.2026: 02:00 CET -> 03:00 CEST (01:00 UTC)
    ok(isTodayContent([rec('2026-03-29T20:00:00+02:00')], '2026-03-29', '2026-03-28T23:00:00Z'), 'DST jaro: 00:00 CET 29.3.');
    ok(!isTodayContent([rec('2026-03-29T20:00:00+02:00')], '2026-03-29', '2026-03-28T22:59:59Z'), 'DST jaro: 23:59:59 CET 28.3.');
    ok(isTodayContent([rec('2026-03-29T20:00:00+02:00')], '2026-03-29', '2026-03-29T07:20:00Z'), 'DST jaro: hlavni beh 09:20 CEST');
    // podzim 25.10.2026: 03:00 CEST -> 02:00 CET (01:00 UTC)
    ok(isTodayContent([rec('2026-10-25T20:00:00+01:00')], '2026-10-25', '2026-10-24T22:00:00Z'), 'DST podzim: 00:00 CEST 25.10.');
    ok(!isTodayContent([rec('2026-10-25T20:00:00+01:00')], '2026-10-25', '2026-10-24T21:59:59Z'), 'DST podzim: 23:59:59 CEST 24.10.');
    ok(isTodayContent([rec('2026-10-25T20:00:00+01:00')], '2026-10-25', '2026-10-25T08:20:00Z'), 'DST podzim: hlavni beh 09:20 CET');
    ok(isTodayContent([rec('2026-10-26T08:00:00+01:00')], '2026-10-25', '2026-10-25T08:20:00Z'), 'DST podzim: rano dalsiho dne v 24h okne (den ma 25 h)');
    // zaloha 11:50 Praha (posledni slot) - stale stejny den v obou rezimech
    ok(isTodayContent(items('2026-03-30'), '2026-03-30', '2026-03-30T09:50:00Z'), 'DST: zaloha 11:50 CEST');
    ok(isTodayContent(items(TODAY), TODAY, '2026-11-11T10:50:00Z'), 'DST: zaloha 11:50 CET');
}

// --- ochrana souboru HOT ONLY
ok(isHotOnly({ HOT_ONLY: '1' }) && !isHotOnly({}) && !isHotOnly({ HOT_ONLY: '0' }), 'HOT_ONLY prepinac');
for (const f of ['react1.json', 'react2.json', 'react3.json', 'tips-kombik.json', 'live1.json', 'league-goals.json'])
    ok(!shouldWriteFile(f, true) && shouldWriteFile(f, false), 'ochrana: ' + f + ' v HOT ONLY nezapsan, jinak ano');
ok(shouldWriteFile('hot.json', true) && shouldWriteFile('best.json', true), 'HOT ONLY: hot+best povoleny');
ok(HOT_ONLY_WRITABLE.size === 2, 'HOT ONLY: jen 2 soubory');
assertHotOnlyChanges(['hot.json', 'best.json'], hotItems(TODAY)); pass++;
assertHotOnlyChanges(['hot.json'], hotItems(TODAY)); pass++;
assertHotOnlyChanges([], null); pass++; // neuspesny HOT ONLY: nic nezmeneno -> nic se necommituje
throws(() => assertHotOnlyChanges(['hot.json', 'react2.json'], hotItems(TODAY)), 'commit: react2 v HOT ONLY odmitnut');
for (const f of ['react1.json', 'react3.json', 'tips-kombik.json', 'live1.json', 'league-goals.json'])
    throws(() => assertHotOnlyChanges(['hot.json', f], hotItems(TODAY)), 'commit: ' + f + ' v HOT ONLY odmitnut');
throws(() => assertHotOnlyChanges(['best.json'], hotItems(YDAY)), 'commit: samotny (stary) best.json bez hot.json odmitnut');
throws(() => assertHotOnlyChanges(['hot.json', 'best.json'], hotItems(TODAY).slice(0, 4)), 'commit: hot.json se 4 zapasy odmitnut');
throws(() => assertHotOnlyChanges(['hot.json', 'best.json'], null), 'commit: necitelny hot.json odmitnut');

// --- staticke kontroly fetch-matches.mjs a workflow
{
    const src = readFileSync(new URL('./fetch-matches.mjs', import.meta.url), 'utf-8');
    const writes = [...src.matchAll(/(writeFileSync|writeJsonAtomic)\(\s*('([^']+)'|TIPS_FILE)/g)].map(m => m[3] || 'tips-kombik.json');
    const lines = src.split(/\r?\n/);
    for (const name of ['tips-kombik.json', 'react1.json', 'react2.json', 'react3.json', 'live1.json']) {
        const re = name === 'tips-kombik.json' ? /writeFileSync\(TIPS_FILE/ : new RegExp("(writeFileSync|writeJsonAtomic)\\('" + name.replace('.', '\\.') + "'");
        const ln = lines.filter(l => re.test(l));
        ok(ln.length === 1 && /shouldWriteFile\(/.test(ln[0]), 'zdroj: ' + name + ' zapis chranen HOT ONLY');
    }
    ok(writes.filter(w => !['hot.json', 'best.json', 'tips-kombik.json', 'react1.json', 'react2.json', 'react3.json', 'live1.json'].includes(w) && !w.endsWith('.tmp')).length === 0, 'zdroj: zadny jiny zapis JSON');
    ok(/HOT_ONLY\?\{doc:null[^}]*\}:await loadEngine/.test(src), 'zdroj: HOT ONLY nenacita FVE ENGINE');
    // neuspesny THE OVER 6: return pred zapisem best.json i hot.json (stary best se nezmeni)
    const iFail = src.indexOf("THE OVER 6 SELHAL"), iRet = src.indexOf('return;', iFail);
    const iBest = src.indexOf("writeFileSync('best.json'"), iHot = src.indexOf("writeFileSync('hot.json'");
    ok(iFail > 0 && iRet > iFail && iRet < iBest && iBest < iHot, 'zdroj: pri selhani se best.json ani hot.json nezapisuji');
    ok(src.split("writeFileSync('best.json'").length === 2 && src.split("writeFileSync('hot.json'").length === 2, 'zdroj: best/hot zapisovany jen na jednom miste');

    const wf = readFileSync(new URL('./.github/workflows/daily-picks.yml', import.meta.url), 'utf-8').replace(/\r/g, '');
    ok(/node backup-plan\.mjs\n/.test(wf), 'workflow: rozhoduje backup-plan.mjs');
    ok(!/git log -1 --format=%cd[^\n]*-- hot\.json/.test(wf), 'workflow: uz ne jen datum commitu hot.json');
    ok(/HOT_ONLY: \$\{\{ steps\.guard\.outputs\.mode == 'hot-only'/.test(wf), 'workflow: HOT_ONLY z rezimu');
    ok(/hot-only" \]; then\s+git add hot\.json best\.json\s+node backup-plan\.mjs --assert-hot-only \|\| exit 1/.test(wf), 'workflow: HOT ONLY stage jen hot+best a kontrola');
    ok(/cancel-in-progress: false/.test(wf) && /group: daily-picks/.test(wf), 'workflow: soubeh serializovan');
    ok(/git pull --ff-only[^\n]*\|\| \{[^\n]*skip=true/.test(wf), 'workflow: neuspesny pull => beh se neprovede');
    ok(/node test-backup\.mjs/.test(wf), 'workflow: test-backup v Run tests');
    ok(/workflow_dispatch:[^\n]*\n\s+inputs:\n\s+mode:/.test(wf), 'workflow: workflow_dispatch ma vstup mode');
    ok(/mode:[\s\S]*?default: full[\s\S]*?type: choice[\s\S]*?options:\s*\n\s+- full\n\s+- hot-only/.test(wf), 'workflow: volby full/hot-only, vychozi full');
    ok(/confirm_hot:[\s\S]*?default: false[\s\S]*?type: boolean/.test(wf), 'workflow: potvrzeni pregenerovani, vychozi false');
    ok(/MANUAL_MODE: \$\{\{ github\.event\.inputs\.mode \|\| 'full' \}\}/.test(wf) && /FORCE: \$\{\{ github\.event\.inputs\.confirm_hot \|\| 'false' \}\}/.test(wf), 'workflow: vstupy predany planovaci');
    ok(!/mode=full" >> "\$GITHUB_OUTPUT"/.test(wf), 'workflow: manual uz neobchazi planovac');
}

console.log('\ntest-backup: ' + pass + ' passed, ' + fail + ' failed');
if (fail) process.exit(1);
