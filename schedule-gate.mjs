// Rozhodne, zda planovany beh daily-picks.yml ma generovat (09:20 Europe/Prague
// v lete i v zime). Pouziti v GitHub Actions: node schedule-gate.mjs
// Vstup: EVENT_NAME, EVENT_SCHEDULE. Vystup do GITHUB_OUTPUT: run=true|false, kind=main|backup|manual.
import { appendFileSync } from 'fs';
import { scheduleDecision } from './engine-picks.mjs';

const d = scheduleDecision(process.env.EVENT_NAME, process.env.EVENT_SCHEDULE, new Date());
console.log('Gate: run=' + d.run + ' kind=' + d.kind + ' (' + d.reason + ')');
if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT, 'run=' + d.run + '\nkind=' + d.kind + '\n');
