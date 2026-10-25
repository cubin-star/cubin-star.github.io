// Kontrola validace react1.json (fixtureId). Spusteni: node test-react1.mjs
import { assertReact1 } from './fetch-matches.mjs';

const ok = (id, i) => ({ fixtureId: id, league: 'Premier League', match: 'A' + i + ' - B' + i, kickoff: '2026-10-18T18:00:00+00:00', tip: 'Over 2.5', odds: '2.10' });
const valid = [1001, 1002, 1003, 1004, 1005, 1006, 1007].map(ok);

// [popis, vstup, ma projit]
const CASES = [
    ['7 zapasu s unikatnim fixtureId', valid, true],
    ['chybejici fixtureId (null)', valid.map((m, i) => i === 3 ? { ...m, fixtureId: null } : m), false],
    ['chybejici fixtureId (undefined)', valid.map((m, i) => i === 0 ? { ...m, fixtureId: undefined } : m), false],
    ['fixtureId jako retezec', valid.map((m, i) => i === 6 ? { ...m, fixtureId: '1007' } : m), false],
    ['fixtureId nula', valid.map((m, i) => i === 2 ? { ...m, fixtureId: 0 } : m), false],
    ['fixtureId zaporne', valid.map((m, i) => i === 2 ? { ...m, fixtureId: -5 } : m), false],
    ['fixtureId desetinne', valid.map((m, i) => i === 1 ? { ...m, fixtureId: 10.5 } : m), false],
    ['duplicitni fixtureId', valid.map((m, i) => i === 5 ? { ...m, fixtureId: 1001 } : m), false],
    ['jen 6 zapasu', valid.slice(0, 6), false],
    ['8 zapasu', [...valid, ok(1008, 8)], false],
    ['prazdne pole', [], false],
    ['neni pole', null, false],
];

let fail = 0;
for (const [name, input, shouldPass] of CASES) {
    let passed = true;
    try { assertReact1(input); } catch { passed = false; }
    if (passed !== shouldPass) {
        fail++;
        console.log(`CHYBA: ${name} -> ${passed ? 'proslo' : 'selhalo'}, ocekavano ${shouldPass ? 'proslo' : 'selhalo'}`);
    }
}
console.log(`\n${CASES.length - fail}/${CASES.length} OK` + (fail ? `, ${fail} chyb` : ', vse sedi'));
if (fail) process.exit(1);
