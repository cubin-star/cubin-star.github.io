// Rozhodnuti planovaneho / rucniho behu daily-picks.yml podle OVERENEHO stavu dnesnich vystupu.
// Rezimy: full (kompletni generovani), hot-only (jen hot.json + best.json),
// skip (nic delat), blocked (nejasny stav - nic neprepisovat).
// Pouziti v GitHub Actions: KIND=main|backup|manual MANUAL_MODE=full|hot-only FORCE=true|false node backup-plan.mjs
import { execFileSync } from 'child_process';
import { readFileSync, existsSync, appendFileSync } from 'fs';
import { pragueDate } from './engine-picks.mjs';

// Soubory ranniho behu, ktere urcuji, zda "ostatni" denni vystupy uz byly publikovany.
export const DAILY_FILES = ['react1.json', 'react2.json', 'react3.json', 'tips-kombik.json'];
// ACCA a QUAD 4 - po publikaci se uz nikdy neprepisuji.
export const PROTECTED_FILES = ['react2.json', 'react3.json'];
export const HOT_ONLY_FILES = ['hot.json', 'best.json'];
export const HOT_COUNT = 6;
// Generator bere zapasy od okamziku startu do +24 h. Commit vznika az po
// generovani (cekani na ENGINE, API), proto se start odhaduje commit - MAX_RUN.
export const WINDOW_MS = 24 * 3600e3;
export const MAX_RUN_MS = 6 * 3600e3; // limit jobu GitHub Actions
// Vykop musi mit explicitni casovou zonu (Z nebo +hh:mm) - jinak nelze urcit okamzik.
const ISO_TZ_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/;

// Obsah je dnesni, kdyz:
//  - commit souboru probehl dnes (Europe/Prague, vcetne DST pres Intl),
//  - je to neprazdne pole a kazda polozka ma match, tip a vykop s casovou zonou,
//  - vsechny vykopy padaji do realneho okna generatoru [commit - MAX_RUN, commit + 24 h].
// Cokoli neurciteho vraci false (vede na BLOCKED / HOT ONLY, nikdy na falesny dnesni stav).
export function isTodayContent(items, today, commitTime) {
    const c = typeof commitTime === 'string' ? Date.parse(commitTime) : NaN;
    if (!Number.isFinite(c) || pragueDate(new Date(c)) !== today) return false;
    if (!Array.isArray(items) || items.length === 0) return false;
    return items.every(m => {
        if (!m || typeof m !== 'object' || !m.match || !m.tip) return false;
        if (typeof m.kickoff !== 'string' || !ISO_TZ_RE.test(m.kickoff)) return false;
        const t = Date.parse(m.kickoff);
        return Number.isFinite(t) && t >= c - MAX_RUN_MS && t <= c + WINDOW_MS;
    });
}

// state: { [file]: { commitTime: ISO|null, items: array|null } }
// opts: { manualMode: 'full'|'hot-only', force: boolean }
export function decideRun({ kind, today, state, manualMode = 'full', force = false }) {
    const fresh = f => isTodayContent(state[f]?.items, today, state[f]?.commitTime);
    const committedToday = f => { const t = Date.parse(state[f]?.commitTime); return Number.isFinite(t) && pragueDate(new Date(t)) === today; };
    // THE OVER 6 je dnesni jen s presne 6 zapasy (backend jinak feed odmitne).
    const hotFresh = fresh('hot.json') && state['hot.json'].items.length === HOT_COUNT;

    if (kind === 'manual') {
        if (manualMode !== 'hot-only') return { mode: 'full', reason: 'rucni spusteni FULL - kompletni generovani (puvodni chovani)' };
        if (hotFresh && !force) return { mode: 'skip', reason: 'rucni HOT ONLY: hot.json je uz dnesni a kompletni - bez potvrzeni force se neprepisuje' };
        return { mode: 'hot-only', reason: 'rucni HOT ONLY' + (hotFresh ? ' (potvrzeno force - pregenerovani)' : '') };
    }
    const dailyFresh = DAILY_FILES.filter(fresh);
    const dailyTouched = DAILY_FILES.filter(committedToday);
    const protectedFresh = PROTECTED_FILES.every(fresh);

    if (hotFresh && dailyFresh.length === DAILY_FILES.length)
        return { mode: 'skip', reason: 'dnesni vystupy vcetne hot.json jsou publikovane' };
    if (protectedFresh && dailyFresh.length === DAILY_FILES.length && !hotFresh)
        return { mode: 'hot-only', reason: 'ostatni dnesni soubory publikovane, hot.json neni dnesni/kompletni' };
    if (dailyTouched.length === 0 && !committedToday('hot.json'))
        return { mode: 'full', reason: 'dnes jeste neprobehlo zadne generovani' };
    return {
        mode: 'blocked',
        reason: 'nejednoznacny stav dnesnich vystupu (commit dnes: ' + [...dailyTouched, ...(committedToday('hot.json') ? ['hot.json'] : [])].join(', ')
            + '; overeny dnesni obsah: ' + [...dailyFresh, ...(hotFresh ? ['hot.json'] : [])].join(', ') + ') - nic se neprepisuje',
    };
}

// Kontrola pred commitem v rezimu hot-only: zmenene smi byt jen hot.json/best.json
// a best.json nikdy bez hot.json (neuspesny vyber nesmi publikovat osamely best.json).
export function assertHotOnlyChanges(changedFiles, hotItems) {
    const files = changedFiles.filter(Boolean);
    const bad = files.filter(f => !HOT_ONLY_FILES.includes(f));
    if (bad.length) throw new Error('HOT ONLY: zmenene nepovolene soubory: ' + bad.join(', '));
    if (files.length === 0) return;
    if (!files.includes('hot.json')) throw new Error('HOT ONLY: best.json zmenen bez hot.json');
    if (!Array.isArray(hotItems) || hotItems.length !== HOT_COUNT) throw new Error('HOT ONLY: hot.json nema ' + HOT_COUNT + ' zapasu');
}

function gitCommitTime(file) {
    try { return execFileSync('git', ['log', '-1', '--format=%cI', 'HEAD', '--', file], { encoding: 'utf-8' }).trim() || null; } catch { return null; }
}
function readItems(file) {
    try { return existsSync(file) ? JSON.parse(readFileSync(file, 'utf-8')) : null; } catch { return null; }
}

if (process.argv[1] && process.argv[1].endsWith('backup-plan.mjs')) {
    if (process.argv[2] === '--assert-hot-only') {
        const changed = execFileSync('git', ['diff', '--cached', '--name-only'], { encoding: 'utf-8' }).split('\n').map(s => s.trim()).filter(Boolean);
        try { assertHotOnlyChanges(changed, readItems('hot.json')); console.log('HOT ONLY: staged ' + (changed.join(', ') || 'nic')); }
        catch (e) { console.log('::error title=HOT ONLY::' + e.message); process.exit(1); }
    } else {
        const today = pragueDate(new Date());
        const state = {};
        for (const f of ['hot.json', ...DAILY_FILES]) state[f] = { commitTime: gitCommitTime(f), items: readItems(f) };
        const d = decideRun({ kind: process.env.KIND || 'main', today, state, manualMode: process.env.MANUAL_MODE || 'full', force: process.env.FORCE === 'true' });
        console.log('Rezim behu: ' + d.mode + ' (' + d.reason + ')');
        for (const f of Object.keys(state)) console.log('   ' + f + ': commit ' + (state[f].commitTime || '-') + ', dnesni obsah ' + isTodayContent(state[f].items, today, state[f].commitTime));
        if (d.mode === 'blocked') {
            console.log('::warning title=Denni vyber - nejasny stav::' + d.reason
                + '. Bezpecny postup: zkontrolujte react2/react3/hot.json v origin/main; je-li ACCA/QUAD 4 dnesni, spustte workflow rucne s rezimem hot-only, jinak rucne FULL.');
        }
        if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT, 'mode=' + d.mode + '\n');
    }
}
