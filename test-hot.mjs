// Testy doplneni THE OVER 6 (hot.json). Spusteni: node test-hot.mjs
import { readFileSync } from 'fs';
import { completeHotSelection, buildHotOutput, assertHot, isValidHotCandidate } from './fetch-matches.mjs';

let pass = 0, fail = 0;
function ok(cond, name) { if (cond) { pass++; } else { fail++; console.error('FAIL: ' + name); } }
function throws(fn, name) { try { fn(); fail++; console.error('FAIL (nevyhodilo): ' + name); } catch { pass++; } }

const NOW = Date.parse('2026-11-10T08:00:00Z');
const KO = '2026-11-10T19:00:00+01:00';
let nextId = 1000;
const mk = (o = {}) => ({ fixtureId: nextId++, leagueId: o.leagueId ?? nextId, league: 'L' + (o.leagueId ?? nextId), country: o.country ?? ('C' + nextId), match: 'A - B ' + nextId, kickoff: KO, tip: 'Over 2.5', odds: '2.10', tier: 2, ...o });
const run = (selected, rescuePool) => completeHotSelection({ selected, rescuePool, now: NOW });
const uniq = a => new Set(a.map(m => m.fixtureId)).size === a.length;

// --- 6 vybranych: zadne doplneni, puvodni vyber beze zmen
{
    const sel = Array.from({ length: 6 }, () => mk());
    const extra = Array.from({ length: 5 }, () => mk());
    const r = run(sel, [...sel, ...extra]);
    ok(r.ok && r.base === 6, '6: ok bez doplneni');
    ok(r.picks.every((m, i) => m.fixtureId === sel[i].fixtureId && m.hotStep === 0), '6: puvodni poradi a zapasy zachovany');
    ok(r.steps.slice(1).every(s => s.added === 0) && r.steps.length === 1, '6: zadny stupen nic nepridal');
    const out = buildHotOutput(r.picks);
    ok(out.length === 6 && Object.keys(out[0]).join() === 'fixtureId,league,match,kickoff,tip,odds,group', '6: puvodni struktura hot.json');
}
// --- 5 vybranych: doplneni 1 ve stupni 1 (prekryv s jinymi JSON)
{
    const sel = Array.from({ length: 5 }, () => mk());
    const overlap = mk();
    const r = run(sel, [...sel, overlap]);
    ok(r.ok && r.base === 5, '5: doplneno na 6');
    ok(r.picks[5].fixtureId === overlap.fixtureId && r.picks[5].hotStep === 1, '5: doplnek ze stupne 1');
    ok(r.steps[1].added === 1 && (r.steps[2]?.added ?? 0) === 0, '5: stupen 2 nebyl potreba');
}
// --- 4 vybrane: stupen 1 nestaci (jen stejne ligy) -> stupen 2
{
    const sel = Array.from({ length: 4 }, () => mk());
    const sameLeague1 = mk({ leagueId: sel[0].leagueId, league: sel[0].league });
    const sameLeague2 = mk({ leagueId: sel[1].leagueId, league: sel[1].league });
    const r = run(sel, [...sel, sameLeague1, sameLeague2]);
    ok(r.ok, '4: doplneno na 6 pres stupen 2');
    ok(r.steps[1].added === 0 && r.steps[2].added === 2, '4: stupen 1 = 0, stupen 2 = 2');
    ok(r.picks.slice(4).every(m => m.hotStep === 2), '4: doplnky oznaceny stupnem 2');
}
// --- postupnost: stupen 1 ma prednost pred stupnem 2
{
    const sel = Array.from({ length: 4 }, () => mk());
    const same = mk({ leagueId: sel[0].leagueId, league: sel[0].league, tier: 1, odds: '2.50' });
    const other = mk({ tier: 4, odds: '1.96' });
    const r = run(sel, [same, other, ...sel]);
    ok(r.ok && r.picks[4].fixtureId === other.fixtureId && r.picks[4].hotStep === 1, 'poradi: nova liga ve stupni 1 drive nez lepsi zapas ze stejne ligy');
    ok(r.picks[5].fixtureId === same.fixtureId && r.picks[5].hotStep === 2, 'poradi: stejna liga az ve stupni 2');
}
// --- 0 vybranych a zadni kandidati: selhani, nic k zapisu
{
    const r = run([], []);
    ok(!r.ok && r.picks.length === 0 && r.candidates === 0, '0: selhani bez kandidatu');
    ok(/0\/6/.test(r.reason) && /kandidatu/.test(r.reason), '0: duvod obsahuje pocty');
    throws(() => buildHotOutput(r.picks), '0: buildHotOutput odmita prazdny vyber');
}
// --- 0 vybranych, ale 6 kandidatu v rescue poolu: doplni vse
{
    const pool = Array.from({ length: 6 }, () => mk());
    const r = run([], pool);
    ok(r.ok && r.base === 0 && r.picks.length === 6, '0: doplneno kompletne z rescue poolu');
}
// --- nedostatek i po uvolneni: zadny nekompletni vystup
{
    const sel = Array.from({ length: 4 }, () => mk());
    const r = run(sel, [...sel, mk()]);
    ok(!r.ok && r.picks.length === 5, 'nedostatek: 5/6 => ok=false');
    ok(r.candidates === 5, 'nedostatek: hlaseny pocet kandidatu');
    throws(() => buildHotOutput(r.picks), 'nedostatek: 5 zapasu neprojde validaci');
}
// --- ochrana proti duplicitam fixtureId
{
    const sel = Array.from({ length: 5 }, () => mk());
    const dupSel = [...sel, { ...sel[0], odds: '2.40' }];
    const r = run(dupSel, [...sel, { ...sel[1], tip: 'Over 2.5', odds: '2.55' }]);
    ok(!r.ok && uniq(r.picks), 'dupl.: duplicita ve vyberu ani v poolu se neprida');
    const extra = mk();
    const r2 = run(dupSel, [...sel, extra, { ...extra, odds: '2.30' }]);
    ok(r2.ok && uniq(r2.picks), 'dupl.: doplneni s duplicitnim poolem = 6 unikatnich');
    const out = buildHotOutput(r2.picks);
    throws(() => assertHot([...out.slice(0, 5), { ...out[0], group: out[5].group }]), 'dupl.: assertHot odmita duplicitni fixtureId');
}
// --- zachovane kvalitativni filtry
{
    const sel = Array.from({ length: 5 }, () => mk());
    const bad = [
        mk({ tip: 'Home win' }), mk({ odds: '1.94' }), mk({ odds: '2.61' }),
        mk({ kickoff: 'nonsense' }), mk({ kickoff: '2026-11-10T06:00:00Z' }), mk({ fixtureId: 0 }),
    ];
    const r = run(sel, [...sel, ...bad]);
    ok(!r.ok && r.picks.length === 5, 'filtry: trh/kurz/cas/ID nejsou uvolneny');
    ok(isValidHotCandidate(mk({ odds: '1.95' }), { now: NOW }), 'filtry: presne 1.95 projde');
    // limit zeme zustava
    const c = Array.from({ length: 5 }, (_, i) => mk({ country: i < 2 ? 'X' : 'Y' + i }));
    const r3 = run(c, [...c, mk({ country: 'X' })]);
    ok(!r3.ok, 'filtry: limit zeme se neuvolnuje');
}
// --- skupiny: 3 skupiny po 2, platne cislovani
{
    const pool = Array.from({ length: 6 }, (_, i) => mk({ odds: (1.95 + i * 0.1).toFixed(2) }));
    const out = buildHotOutput(run([], pool).picks);
    ok([1, 2, 3].every(g => out.filter(m => m.group === g).length === 2), 'skupiny: 3x2');
    ok(uniq(out) && out.every(m => m.tip === 'Over 2.5'), 'skupiny: unikatni Over 2.5');
    throws(() => assertHot(out.map((m, i) => ({ ...m, group: i < 3 ? 1 : 2 }))), 'skupiny: spatne skupiny odmitnuty');
}
// --- staticke kontroly integrace v main()
{
    const src = readFileSync(new URL('./fetch-matches.mjs', import.meta.url), 'utf-8');
    const hotSec = src.slice(src.indexOf('// === hot.json (THE OVER 6)'));
    ok(/if\(!output\)\{[\s\S]*?hot\.json NEBYL AKTUALIZOVAN[\s\S]*?return;\s*\}/.test(hotSec), 'main: pri selhani return pred zapisem');
    ok(hotSec.indexOf("writeFileSync('hot.json'") > hotSec.indexOf('if(!output)'), 'main: zapis hot.json az po kontrole');
    ok(!/writeFileSync\('hot\.json',JSON\.stringify\(\[\]/.test(src), 'main: prazdny hot.json se uz nezapisuje');
    ok(src.indexOf('const hotPoolNoDedup=pool;') < src.indexOf('pool=pool.filter(m=>{if(isDuplicate'), 'main: snapshot pred externim dedupem');
    const failBlock = hotSec.slice(hotSec.indexOf('if(!output){'), hotSec.indexOf('return;', hotSec.indexOf('if(!output){')));
    ok(!/writeFileSync|process\.exit/.test(failBlock), 'main: pri selhani se nezapisuje hot/best a proces se neukoncuje chybou');
    ok(/::error title=THE OVER 6/.test(failBlock) && /PREDCHOZIHO dne/.test(failBlock), 'main: ::error anotace + varovani o predchozim dni');
    ok((src.match(/writeFileSync\('best\.json'/g) || []).length === 1, 'main: best.json zapisovan jen na jednom miste');
    ok(/const best=\[\.\.\.hotRes\.picks\]/.test(hotSec) && hotSec.indexOf("writeFileSync('best.json'") > hotSec.indexOf('if(!output)'), 'main: best.json z finalnich 6 zapasu az po uspechu');
    const wf = readFileSync(new URL('./.github/workflows/daily-picks.yml', import.meta.url), 'utf-8');
    ok(/node test-hot\.mjs/.test(wf), 'workflow: test-hot v Run tests');
    ok(/git log -1 --format=%cd[^\n]*-- hot\.json/.test(wf), 'workflow: zaloha se ridi datem commitu hot.json');
    ok(src.indexOf("writeJsonAtomic('react3.json'") < src.indexOf('// === hot.json (THE OVER 6)'), 'main: react2/react3 zapsany pred hot (nezavisle)');
}

console.log('\ntest-hot: ' + pass + ' passed, ' + fail + ' failed');
if (fail) process.exit(1);
