import { writeFileSync, existsSync, readFileSync } from 'fs';

const API_KEY = process.env.API_FOOTBALL_KEY1;

const FOOTBALL_API = 'https://v3.football.api-sports.io';
// === Kurzova pasma =========================================================
// Drive byly pasy dva oddelene ostruvky (1.75-1.90 a 2.10-2.50) a mezi nimi
// mrtva zona, do ktere padala vetsina Over 2.5 ze slusnych ligy (Championship
// @ 2.05 nebyla pouzitelna nikde). Nove je to souvisle spektrum, jehoz useky se
// prekryvaji - o zarazeni zapasu tak rozhoduje kvalita souteze, ne kurz.
const ODDS_COLLECT_MIN = 1.70;
const ODDS_COLLECT_MAX = 2.60;
const MIN_ODDS = 1.95;   // hot.json
const MAX_ODDS = 2.60;
const PICK_COUNT = 6;
// Samostatny denni vyber do tips.json - nizsi kurzovy pas, primarne tier 1+2,
// pri nedostatku se dobira z tieru 3 a 4. Max jeden zapas z ligy.
const TIPS_MIN_ODDS = 1.70;
const TIPS_MAX_ODDS = 2.00;
const TIPS_PICK_COUNT = 2;
// Samostatny denni vyber do react1.json - stejna vyberova logika i kurzovy pas
// jako react2.json (vazeny vyber tieru, stropy, kvota jadra, limit zeme/ligy),
// tipy Over 2.5 / Home win / Away win. Zapasy se nesmi kryt s react2.
const REACT1_MIN_ODDS = 1.95;
const REACT1_MAX_ODDS = 2.60;
const REACT1_PICK_COUNT = 7;
// Na rozdil od react2 neni slozeni pevne - garantovane je jen minimum Over 2.5,
// zbytek tiketu se dobere z obou trhu dohromady (muze tedy vyjit i vic Over).
const REACT1_MIN_OVER = 3;
// Minimalni pocet bookmakeru, kteri musi dany kurz nabizet. Misto medianu (ktery
// zapasy na hrane pasma zbytecne strhaval dolu) se bere nejvyssi kurz, na kterem
// se shodne aspon tolik sazkovek - chrani pred ojedinelou chybou v kurzu.
const MIN_BOOKMAKERS = 3;
// Samostatne kurzove pasmo pro tipy Home win / Away win. Over 2.5 se drzi
// v sirsim pasmu jednotlivych vyberu, vitez ma vlastni, uzsi hranice.
const WINNER_MIN_ODDS = 1.75;
const WINNER_MAX_ODDS = 2.25;
// react2.json - stejna vyberova logika i kurzovy pas jako hot.json (2.1-2.5, vazeny
// vyber tieru, stropy, limit zeme/ligy, rozdeleni do skupin), ale tipy jsou jako
// u react1.json, tedy Over 2.5 / Home win / Away win.
const REACT2_PICK_COUNT = 6;
// Pevne slozeni react2 tiketu: 3x Over 2.5 a 3x Home/Away win. Pri nedostatku
// kandidatu v jednom trhu se zbytek dobere z druheho, aby tiket nebyl kratsi.
const REACT2_OVER_COUNT = 3;
// react3.json - stejny kurzovy pas i vyberova logika jako react2.json, ale 8 zapasu
// ve 4 skupinach po dvou. Garantovane je minimum Over 2.5 (REACT3_MIN_OVER), zbytek
// se dobere z obou trhu dohromady. Dedup je spolecny s ostatnimi vystupy - stejny
// zapas se smi objevit i jinde, ale jen s jinym tipem.
const REACT3_PICK_COUNT = 8;
const REACT3_MIN_OVER = 4;
// Max pocet vybranych zapasu z jedne zeme - brani zaplaveni tiketu jednou destinaci.
const MAX_PER_COUNTRY = 2;
// === Globalni limit ligy napric hlavnimi JSON ==============================
// Krome lokalniho limitu (max 1 zapas z ligy v JEDNOM souboru) plati jeste
// globalni strop na pocet UNIKATNICH fixture z jedne ligy napric hlavni
// skupinou (hot, tips-kombik, react2, react3). Stejny zapas ve dvou souborech
// s jinym tipem se pocita JEDNOU - proto se drzi refcount, ne prosty citac.
const LEAGUE_GLOBAL_CAP = 2;
// Postupne uvolneni, kdyz pri CAP=2 uz nelze naplnit vsechny soubory. Uvolnuje
// se az po vycerpani alternativ i prerozdeleni. Infinity = limit vypnut.
const LEAGUE_CAP_RELAX_STEPS = [2, 3, 4, Infinity];
// Vahy pro vazeny nahodny vyber TIERU (ne jednotlivych zapasu) - vyssi tier ma
// vyrazne vetsi sanci, ale nizsi nejsou vyloucene.
// T1 = top evropske ligy + UEFA pohary, T2 = druhe ligy/domaci pohary, T3 = ostatni Evropa, T4 = zbytek sveta.
// Sance pri dostupnych vsech tierech: T1 ~66 %, T2 ~23 %, T3 ~9 %, T4 ~1,6 %.
const TIER_WEIGHTS = { 1: 40, 2: 14, 3: 5, 4: 1 };
// Tvrdy strop na pocet vybranych zapasu z daneho tieru. Resi to, ze T4 ma vzdy
// nejvic kandidatu - i kdyby jich bylo 200, projde max 1. Stropy se uvolni az
// v druhe fazi, kdyby se jinak nepodarilo naplnit PICK_COUNT.
const MAX_PER_TIER = { 1: 6, 2: 4, 3: 2, 4: 1 };
// Z tieru se nikdy nevybere vic nez tento podil jeho kandidatu (min. 1 zapas).
// Bez toho by pri 4 kandidatech v T1 a stropu 6 byl vyber deterministicky -
// vzali by se proste vsichni ctyri. Takhle se ze 4 kandidatu vyberou max 2.
const MAX_TIER_SHARE = 0.5;
// === Vahy vazeneho nahodneho losu KANDIDATU ================================
// Faktory se SCITAJI jako odchylky od 1.0 (ne nasobi), aby se nemohly
// nekontrolovane eskalovat: w = BASE*(1+suma), s podlahou W_FLOOR. Kazdy
// kandidat, ktery prosel filtry a tvrdymi limity, ma nenulovou sanci.
const W_BASE = 1.0;
const W_FLOOR = 0.05;
// Kvalita souteze podle tieru: -15 % az +15 %. Nejde o vyrazeni nizsich tieru,
// vazeny los TIERU (TIER_WEIGHTS) zustava beze zmeny nad timto.
const LEAGUE_TIER_ADJ = { 1: 0.15, 2: 0.07, 3: 0, 4: -0.15 };
// Bonus za pocet vhodnych UNIKATNICH fixture v lize: 0 az +15 %, tlumeny
// logaritmem. Liga s 5 kandidaty ma vyssi sanci nez liga se 2, ale ne 2,5x.
const W_COUNT_MAX = 0.15;
const W_COUNT_SATURATION = 6;
// === Golova preference pro Over 2.5 ========================================
// Golovost se uz NESCITA s ostatnimi faktory, ale aplikuje se MULTIPLIKATIVNE
// na jejich vysledek. Duvod: pri scitani ji slo prebit tierem a poctem
// kandidatu (merenim overeno - golove chuda T1 liga se 6 kandidaty mela vyssi
// vahu nez velmi golova T4 liga). Nasobeni to znemoznuje: u Over 2.5 ma
// golovost vzdy posledni slovo.
//
//   w = max(W_FLOOR, zakladni_vaha * (1+G_STRENGTH)^z)
//   z = clamp((efektivni_rate - G_PIVOT)/G_SPAN, -G_CLAMP, +G_CLAMP)
//
// Skala je SPOJITA A OBOUSTRANNA kolem G_PIVOT: liga pod 50 % dostava zapornou
// mocninu (penalizaci), liga nad 50 % kladnou. Driv byl bonus oriznuty na 0 pod
// 50 % a na maximum nad 75 %, takze liga s 31 % mela stejnou vahu jako liga
// s 50 % a liga s 80 % stejnou jako liga se 75 %.
const G_PIVOT = 50;      // stred skaly v % Over 2.5 (prumerna liga)
const G_SPAN = 20;       // o kolik bodu se musi rate lisit pro plny krok
const G_STRENGTH = 1.5;  // sila: z=+1 => x2.5, z=-1 => x0.4
// Strop mocniny. Bez nej by liga s extremni hodnotou dostala nekolikanasobek a
// vyber by prestal byt nahodny. Pri G_CLAMP=1.5 je nejvetsi mozny pomer mezi
// nejgolovejsi a nejchudsi ligou (1+G_STRENGTH)^3 = ~15,6x - silna preference,
// ale zadna liga neni z losovani vyrazena.
const G_CLAMP = 1.5;
// Bayesovske vyhlazeni (shrinkage). Misto tvrdeho vypnuti vahy pod hranici
// vzorku se namereny podil stahuje k prumeru podle poctu odehranych zapasu:
//   efektivni = (rate*played + G_PRIOR_RATE*100*G_PRIOR) / (played + G_PRIOR)
// Liga s malym vzorkem tak neni ignorovana (drive slo o 53 % vsech lig!), jen
// je jeji odchylka od prumeru brana opatrneji. Pri velkem vzorku jde vliv
// prioru k nule a pouzije se prakticky namerena hodnota.
const G_PRIOR = 60;        // sila prioru vyjadrena v "odehranych zapasech"
const G_PRIOR_RATE = 0.50; // prior = prumerna liga (50 % Over 2.5)
// Penalizace uz zastoupene ligy podle poctu jejich unikatnich fixture v hlavni
// skupine. Index = pocet unikatnich fixture; od 2 vys plati -35 %.
// Penalizace je MEKKA - jen snizuje vahu v losovani, nikdy kandidata nevyradi
// (vysledna vaha ma podlahu W_FLOOR). Zapocita se VZDY JEN JEDNOU: kdyz je
// k dispozici vlastni registr i read-only registr hlavni skupiny, bere se
// vyssi z obou poctu a penalizace se aplikuje jedinkrat.
const W_PRESENCE_PENALTY = [0, -0.20, -0.35];
// === Jadro "prezentovatelnych" soutezi =====================================
// Tier je jen vaha v losovani - kdyz v T1/T2 nic neni, vahy jsou k nicemu a
// vyhraje alzirska druha liga. Jadro je tvrda podminka: v kazdem tiketu musi
// byt aspon CORE_MIN_* zapasu ze souteze, kterou divak zna. Jadro = T1 a T2
// (top ligy, jejich druhe ligy, UEFA pohary, domaci pohary top zemi) plus
// vyjmenovane silne mimoevropske ligy.
const CORE_EXTRA_COUNTRIES = new Set([
    'usa','brazil','argentina','mexico','japan','south korea','saudi arabia','australia',
]);
function isCore(m){
    if(m.tier===1||m.tier===2)return true;
    // Z mimoevropskych zemi jen nejvyssi soutez (T3), ne pohary a nizsi divize.
    return m.tier===3&&CORE_EXTRA_COUNTRIES.has(norm(m.country));
}
// Minimalni pocet zapasu z jadra v jednotlivych vyberech. Kdyz jich tolik
// neexistuje, kvota se snizi na to, co je k dispozici - tiket ma vzdy plny
// pocet zapasu, jen je slabsi.
const CORE_MIN_HOT = 4;
const CORE_MIN_REACT2 = 4;
const CORE_MIN_REACT3 = 4;
const CORE_MIN_REACT1 = 4;
const CORE_MIN_TIPS = 2;
// === Golovy filtr (merkky blacklist) =======================================
// Namereno skriptem measure-goals.mjs do league-goals.json. Pouziva se POUZE
// na tipy Over 2.5 - v ligach, kde historicky padaji 3+ goly pod touto hranici,
// je i kurz 2,3 past (Argentina ~38 %, Libertadores ~33 %, Ghana ~23 %).
// Home win / Away win se netyka, tam golovost nic nevypovida.
const GOALS_FILE = 'league-goals.json';
// Vystupni soubor denniho vyberu tipu. Zamerne NENI 'tips.json' - ten v repu
// cubin-star.github.io spravuje jiny bot (generate_fotbal.py) a ma jinou
// strukturu klicu. Kombik cte vyhradne tento soubor.
const TIPS_FILE = 'tips-kombik.json';
// Pod touto uspesnosti Over 2.5 (v %) se soutez povazuje za golove chudou.
const MIN_OVER25_RATE = 40;
// Mekci prah NAD tvrdym MIN_OVER25_RATE. Liga mezi MIN_OVER25_RATE a touto
// hodnotou je "prumerna" - Over 2.5 v ni neni past, ale ani nic extra. Ve
// vyberech, ktere umi i Home/Away win (react1/2/3), se u takoveho zapasu Over
// zahodi a nechá se jen tip na viteze. Over 2.5 tak miri do ligy, kde goly
// opravdu padaji (napr. japonska druha liga uz da viteze, ne Over).
// Degradace se provede jen tehdy, kdyz ma zapas v kurzovem pasmu alternativu a
// kdyz po ni v poolu zbyde dost kandidatu - jinak se Over ponecha.
const OVER_PREFER_RATE = 50;
// Tvrdy filtr je ODSTUPNOVANY podle velikosti vzorku - cim mene zapasu,
// tim mene veri statistice a tim shovivavejsi je prah:
//   played >= 60            -> vyrazeni pod MIN_OVER25_RATE (40 %)
//   30 <= played < 60       -> vyrazeni pod MID_OVER25_RATE (35 %)
//   played < 30             -> ZADNE tvrde vyrazeni
// U nejmensich vzorku se spolehame vyhradne na multiplikativni golovou vahu
// s Bayesovskym vyhlazenim (goalMultiplier): liga s 29 zapasy a 27 % projde
// filtrem, ale v losovani dostane vyrazne nizsi vahu. Tvrde vyrazeni by u tak
// maleho vzorku bylo hazeni minci.
const GOALS_MIN_SAMPLE = 60;
// Stredni vzorek: 30-59 odehranych zapasu.
const GOALS_MID_SAMPLE = 30;
const MID_OVER25_RATE = 35;
// Pri uvolneni tvrdeho filtru se vraci jen chybejici pocet kandidatu plus tato
// rezerva - cast poolu totiz jeste padne na limit ligy/zeme nebo deduplikaci.
const GOAL_FALLBACK_SLACK = 3;
// Ukrajina je vyrazena kvuli valce - zapasy se casto prerusuji poplachy,
// nedostanou se do tempa a vysledky jsou tim nepredvidatelne.
const EXCLUDED_COUNTRIES = new Set(['Russia', 'Belarus', 'Ukraine']);
// Normalizovana podoba vyrazenych zemi pro leagueTier() - zajisti, ze se
// zadna jejich soutez (vcetne poharu) nedostane do zadneho tieru.
const WAR_COUNTRIES = new Set(['russia', 'belarus', 'ukraine']);
const TZ = 'Europe/Prague';
const USER_AGENT = 'kombik-bot/1.0 (+github-actions)';
// Zdroje (URL), jejichz zapasy NESMI byt vybrany (jine boty vybiraji podobne) -> dedup
const DEDUP_URLS = [
    'https://raw.githubusercontent.com/cubin-star/cubin-star.github.io/main/fotbal.json',
    'https://raw.githubusercontent.com/cubin-star/cubin-star.github.io/main/live2.json',
];

let reqCount = 0;
let fixtureFetchErrors = 0;
let fixtureFetchAttempts = 0;

// === Klasifikace soutezi ====================================================
// Misto krehkeho porovnavani presnych nazvu 'Nazev|Zeme' se soutez rozklada na
// (skupina zeme) x (uroven souteze), coz je odolne vuci diakritice, sponzorskym
// nazvum a drobnym odchylkam v API.

// Normalizace: odstrani diakritiku, prevede na mala pismena, interpunkci na mezery
// a oddeli cislice od pismen ('LaLiga2' -> 'laliga 2', '2. Bundesliga' -> '2 bundesliga').
function norm(s){
    return String(s||'')
        .replace(/ı/g,'i').replace(/İ/g,'i').replace(/ø/g,'o').replace(/Ø/g,'o')
        .replace(/đ/g,'d').replace(/Đ/g,'d').replace(/ß/g,'ss')
        .replace(/ł/g,'l').replace(/Ł/g,'l').replace(/æ/g,'ae').replace(/Æ/g,'ae')
        .normalize('NFD').replace(/[\u0300-\u036f]/g,'')
        .toLowerCase()
        .replace(/[^a-z0-9]+/g,' ')
        .replace(/([a-z])(\d)/g,'$1 $2')
        .replace(/\s+/g,' ')
        .trim();
}

// Souteze, ktere nikdy nechceme (mladez, zeny, rezervy, pratelaky, futsal...).
const EXCLUDE_RE = [
    /\bu ?\d{2}\b/, /\byouth\b/, /\bjunior/, /\bjuvenil/, /\bprimavera\b/,
    /\breserve/, /\bacademy\b/, /\bdevelopment\b/,
    /\bamateur/, /\bamatoer/, /\bveterans\b/,
    // Rezervni / farmarske / mladeznicke souteze schovane pod nazvem hlavni ligy
    /\bpremier league 2\b/, /\bpremier league cup\b/, /\bpremier league international cup\b/, /\bnext pro\b/, /\belite league\b/,
    // Zenske souteze - 'wk league' (J. Korea), 'femenil' (Mexiko), 'kvinde' (Dansko)...
    /\bwk league\b/, /\bwomen\b/, /\bfemen/, /\bfeminin/, /\bfrauen/,
    /\bdamallsvenskan\b/, /\belitettan\b/, /\bkvinde/, /\btoppserien\b/, /\bnwsl\b/, /\bkansallinen liiga\b/,
    /\bwsl\b/, /\busl super league\b/, /\bfa wsl\b/, /\bnorthern super league\b/,
    /\bfriendlies clubs\b/, /\bclub friendl/, /\bfutsal\b/, /\bbeach\b/, /\besoccer\b/, /\bindoor\b/,
    // Vyberove/olympijske turnaje jsou de facto U23 (muzi) - stejna nepredvidatelnost jako mladez.
    /\basian games\b/, /\bolympic/, /\bpan american\b/,
];

// Evropske zeme - jejich nejvyssi soutez a pohar patri do T3 (resp. T2 u T1-zemi).
// Bez tohoto delitka koncila "Premier League (Mongolia)" ve stejnem tieru jako
// "Premier Division (Ireland)".
// Trpaslici federace (San Marino, Gibraltar, Andorra, Liechtenstein) tu zamerne
// nejsou - jejich ligy jsou poloamaterske a patri do T4.
const EUROPEAN_COUNTRIES = new Set([
    'albania','armenia','austria','azerbaijan','belarus','belgium','bosnia','bosnia and herzegovina',
    'bulgaria','croatia','cyprus','czech republic','denmark','england','estonia','faroe islands','finland',
    'france','georgia','germany','greece','hungary','iceland','ireland','israel','italy','kosovo',
    'latvia','lithuania','luxembourg','macedonia','north macedonia','malta','moldova',
    'montenegro','netherlands','northern ireland','norway','poland','portugal','republic of ireland','romania',
    'russia','scotland','serbia','slovakia','slovenia','spain','sweden','switzerland','turkey',
    'ukraine','wales',
]);

// Mimoevropske zeme se silnou a sledovanou nejvyssi soutezi - rovnez T3.
// Zbytek sveta (Mongolsko, Bhutan, Banglades, Eswatini...) spadne do T4.
const STRONG_NON_EU_COUNTRIES = new Set([
    'argentina','brazil','chile','colombia','uruguay','peru','paraguay','mexico','usa','japan','south korea','china',
    'australia','saudi arabia','united arab emirates','qatar','iran','egypt','morocco','south africa','tunisia','algeria',
]);

// Rozpoznani poharu bez nutnosti znat nazev (fallback, kdyz chybi league.type z API).
const CUP_RE = /\bcup\b|\bcupen\b|\bpokal\b|\bpokalen\b|\bcopa\b|\bcoupe\b|\bcoppa\b|\bbeker\b|\btaca\b|\bkupa\b|\bkupasi\b|\bkubok\b|\bpuchar\b|\bpohar\b|\bkypello\b|\btrophy\b|\bsupercup\b|\bsuper cup\b/;
// Pohary nizsich/mladeznickych urovni - nepatri mezi hlavni domaci pohary (T2).
const LOW_CUP_RE = /\bfa trophy\b|\bfa vase\b|\bleague trophy\b|\befl trophy\b|\bpremier league cup\b|\bnational league cup\b|\bchallenge cup\b|\bcopa federacion\b|\bregional|\bcopa paulista\b|\bcopa rio\b|\bcopa santa catarina\b|\bcopa gaucha\b|\bsuper copa international\b|\bcoppa italia serie [cd]\b/;

// Brazilske statni ligy (Campeonato Carioca, Gaucho, Mineiro, Paulista...) -
// druhe a nizsi divize techto regionalnich soutezi jsou poloamaterske.
const STATE_LEAGUE_RE = /\bcarioca\b|\bgaucho\b|\bmineiro\b|\bpaulista\b|\bpaulistao\b|\bcatarinense\b|\bparanaense\b|\bparaense\b|\bparaibano\b|\bcearense\b|\bbaiano\b|\bpernambucano\b|\bgoiano\b|\bbrasiliense\b|\bmatogrossense\b|\bsergipano\b|\balagoano\b|\bpotiguar\b|\bcapixaba\b|\bamazonense\b|\bmaranhense\b|\bpiauiense\b|\bacreano\b|\brondoniense\b|\broraimense\b|\btocantinense\b|\bsul matogrossense\b/;

// Zeme, kde nazev neprozradi uroven ligy (Championship, 1. Division...) -> rucni mapa.
// Klic = normalizovana zeme, hodnota = [uroven, [normalizovane podretezce nazvu]].
const LEVEL_OVERRIDES = {
    'england':   [[1,['premier league']],[2,['championship']],[3,['league one']],[4,['league two']],[5,['national league','non league','isthmian','northern premier','southern league']]],
    'scotland':  [[1,['premiership']],[2,['championship']],[3,['league one']],[4,['league two']],[5,['highland league','lowland league']]],
    'netherlands':[[2,['eerste divisie']],[3,['tweede divisie']],[4,['derde divisie']]],
    'belgium':   [[2,['challenger pro league','first division b']]],
    'portugal':  [[2,['liga de honra','segunda liga','liga portugal 2']],[3,['campeonato de portugal']]],
    'spain':     [[3,['primera federacion','primera division rfef']],[4,['segunda federacion','segunda division rfef']],[5,['tercera']],[2,['segunda division','laliga 2','la liga 2']]],
    'italy':     [[3,['serie c']],[4,['serie d']]],
    'germany':   [[4,['regionalliga']],[5,['oberliga']]],
    'france':    [[3,['national 1','championnat national']],[4,['national 2']],[5,['national 3']]],
    'turkey':    [[1,['super lig']],[2,['1 lig','first league']],[3,['2 lig']],[4,['3 lig']]],
    'denmark':   [[1,['superliga','superligaen']],[2,['1 division','1 divisionen']],[3,['2 division']]],
    'sweden':    [[1,['allsvenskan']],[2,['superettan']],[3,['ettan']]],
    'norway':    [[1,['eliteserien']],[2,['obos ligaen','1 divisjon','1 division']],[3,['2 divisjon']]],
    'czech republic':[[1,['czech liga','fortuna liga','first league']],[2,['fnl','narodni liga','national football league']]],
    'slovakia':  [[1,['super liga','nike liga','fortuna liga','fortuna 1 liga']],[2,['2 liga']]],
    'poland':    [[1,['ekstraklasa']],[2,['i liga','1 liga']],[3,['ii liga','2 liga']]],
    'austria':   [[1,['bundesliga']],[2,['2 liga']],[3,['regionalliga']]],
    'switzerland':[[1,['super league']],[2,['challenge league']],[3,['promotion league']],[4,['1 liga classic','1 liga']]],
    'greece':    [[1,['super league 1']],[2,['super league 2']],[3,['gamma ethniki']],[4,['delta ethniki']]],
    'ireland':   [[1,['premier division']],[2,['first division']]],
    'republic of ireland':[[1,['premier division']],[2,['first division']]],
    'usa':       [[2,['usl championship','nwsl']],[3,['usl league one','mls next pro']],[4,['usl league two','nisa']]],
    'brazil':    [[2,['serie b']],[3,['serie c']],[4,['serie d']]],
    'japan':     [[2,['j 2 league']],[3,['j 3 league']]],
    'south korea':[[2,['k league 2']]],
    'croatia':   [[2,['prva nl','first nl','druga']]],
    'israel':    [[2,['liga leumit']],[3,['liga alef']],[4,['liga bet','liga gimel']]],
    'ukraine':   [[2,['persha liga','first league']],[3,['druha liga']]],
    'estonia':   [[1,['meistriliiga']],[2,['esiliiga a','esiliiga']],[3,['esiliiga b']]],
    'lithuania': [[1,['a lyga']],[2,['1 lyga','pirma lyga']]],
    'latvia':    [[1,['virsliga']],[2,['1 liga','first league']]],
    'argentina': [[2,['primera nacional']],[3,['primera b metropolitana']],[4,['primera c','primera d','torneo federal']]],
    'paraguay':  [[2,['division intermedia']]],
    'costa rica':[[2,['liga de ascenso','segunda division']]],
    'colombia':  [[2,['primera b','torneo']]],
    'chile':     [[2,['primera b']]],
    'bolivia':   [[2,['nacional b']]],
    'saudi arabia':[[2,['division 1','first division']]],
    'wales':     [[1,['cymru premier']],[2,['faw championship','cymru north','cymru south']]],
    'iran':      [[1,['persian gulf pro league']],[2,['azadegan league']]],
    'india':     [[1,['indian super league']],[2,['i league']],[3,['calcutta','premier division','state league']]],
    'northern ireland':[[1,['premiership']],[2,['championship']]],
    'serbia':    [[1,['super liga','superliga']],[2,['prva liga']],[3,['srpska liga']]],
    'slovenia':  [[1,['prva liga','1 snl']],[2,['2 snl']],[3,['3 snl']]],
    'azerbaijan':[[1,['premyer liqa']],[2,['birinci dasta','first division']]],
    'albania':   [[1,['kategoria superiore','superiore']],[2,['1st division','kategoria e pare','first division']]],
    'bosnia':    [[1,['premijer liga']],[2,['1st league','first league','prva liga']]],
    'bosnia and herzegovina':[[1,['premijer liga']],[2,['1st league','first league','prva liga']]],
    'uzbekistan':[[1,['super league','superliga']],[2,['pro league']]],
    'kazakhstan':[[1,['premier league']],[2,['1 division','first division']]],
    'mexico':    [[1,['liga mx']],[2,['liga de expansion','expansion mx','ascenso mx']],[3,['liga premier','primera premier','serie a']],[4,['serie b','liga tdp']]],
    'china':     [[1,['super league']],[2,['league one']],[3,['league two']]],
    'moldova':   [[1,['super liga','divizia nationala','national division']],[2,['liga 1']]],
    'south africa':[[1,['premier soccer league','premiership','psl']],[2,['1st division','first division','motsepe']],[3,['diski challenge','diski']]],
    'united arab emirates':[[1,['pro league','uae league','adnoc']],[2,['division 1','first division']]],
    'finland':   [[1,['veikkausliiga']],[2,['ykkosliiga','ykkonen']],[3,['kakkonen']]],
    'malta':     [[1,['premier league']],[2,['challenge league','first division']]],
    'faroe islands':[[1,['betrideildin','meistaradeildin','premier league']],[2,['1 deild']],[3,['2 deild']]],
    'australia': [[1,['a league']],[2,['npl','state league','premier league']]],
    'armenia':   [[1,['premier league']],[2,['first league']]],
    'macedonia': [[1,['first league','prva liga']],[2,['second league','vtora liga']]],
    'north macedonia':[[1,['first league','prva liga']],[2,['second league','vtora liga']]],
    'ghana':     [[1,['premier league']],[2,['division one league','division one']]],
    'kosovo':    [[1,['superliga','super league']],[2,['liga e pare','first league']]],
};

// Genericka detekce urovne z cisla/slova v nazvu. Poradi od nejnizsi urovne.
const GENERIC_LEVELS = [
    [5,[/\b(5|v)\b/,/quinta/,/oberliga/]],
    [4,[/\b(4|iv)\b/,/cuarta/,/quarta/,/quatrieme/,/regionalliga/,/\bd\b/]],
    [3,[/\b(3|iii)\b/,/tercera/,/terza/,/troisieme/,/dritte/,/third/,/treca/,/\bc\b/]],
    [2,[/\b(2|ii)\b/,/segunda/,/seconda/,/second/,/zweite/,/deuxieme/,/druga/,/druha/,/masodik/,/\bb\b/]],
];

// Shoda podretezce na hranicich slov. Prosty includes() nestaci - 'III Liga'
// obsahuje 'I Liga' a 'Liga III' obsahuje 'Liga II', takze 3. ligy padaly do T2.
const wordReCache = new Map();
function hasWord(n,p){
    let re=wordReCache.get(p);
    if(!re){re=new RegExp('\\b'+p.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'\\b');wordReCache.set(p,re);}
    return re.test(n);
}

// Vrati uroven ligy (1 = nejvyssi soutez zeme). Default 1 - nizsi souteze maji
// prakticky vzdy cislo nebo poradove slovo v nazvu.
function leagueLevel(n,c){
    const ov=LEVEL_OVERRIDES[c];
    if(ov){for(const [lvl,pats] of ov){for(const p of pats){if(hasWord(n,p))return lvl;}}}
    for(const [lvl,res] of GENERIC_LEVELS){for(const re of res){if(re.test(n))return lvl;}}
    return 1;
}

// Explicitni seznam soutezi pro tier 1 a tier 2.
// Klic = normalizovana zeme (viz norm()), hodnota = normalizovane podretezce nazvu.
// U kazde ligy jsou uvedeny i alternativni nazvy, ktere API-Football pouziva
// nebo pouzivalo (sponzorske nazvy se meni kazdou sezonu).
// POZOR: T2 se vyhodnocuje PRED T1, aby 'Liga Portugal 2' nespadla pod 'Liga Portugal'.
const TIER1_LEAGUES = {
    'england':      ['premier league'],
    'czech republic':['chance liga','czech liga','fortuna liga','1 liga','first league'],
    'france':       ['ligue 1'],
    'italy':        ['serie a'],
    'germany':      ['bundesliga'],
    'spain':        ['la liga','laliga'],
    'belgium':      ['jupiler pro league','first division a'],
    'denmark':      ['superliga','superligaen'],
    'croatia':      ['hnl','prva liga'],
    'japan':        ['j 1 league','j1 league','j league'],
    'south korea':  ['k league 1'],
    'netherlands':  ['eredivisie'],
    'norway':       ['eliteserien'],
    'poland':       ['ekstraklasa'],
    'portugal':     ['liga portugal','primeira liga'],
    'austria':      ['bundesliga'],
    'romania':      ['superliga','liga i'],
    'greece':       ['super league 1'],
    'scotland':     ['premiership'],
    'serbia':       ['super liga','superliga'],
    'sweden':       ['allsvenskan'],
    'switzerland':  ['super league'],
    'turkey':       ['super lig','superliga'],
};

const TIER2_LEAGUES = {
    'england':      ['championship'],
    'brazil':       ['serie a'],
    'argentina':    ['liga profesional','primera division','torneo betano'],
    'finland':      ['veikkausliiga'],
    'ireland':      ['premier division'],
    'republic of ireland':['premier division'],
    'israel':       ['ligat ha al','ligat haal','premier league'],
    'slovakia':     ['nike liga','super liga','fortuna liga'],
    'slovenia':     ['prva liga','1 snl'],
    'denmark':      ['1 division','1 divisionen'],
    'belgium':      ['challenger pro league','first division b'],
    'croatia':      ['prva nl','first nl','druga hnl'],
    'czech republic':['fnl','narodni liga','national football league','2 liga'],
    'france':       ['ligue 2'],
    'greece':       ['super league 2'],
    'italy':        ['serie b'],
    'japan':        ['j 2 league'],
    'south korea':  ['k league 2'],
    'serbia':       ['prva liga'],
    'cyprus':       ['1 division','1 divizion','first division'],
    'hungary':      ['nb i','nb 1','otp bank liga'],
    'germany':      ['2 bundesliga'],
    'usa':          ['major league soccer','mls'],
    'mexico':       ['liga mx'],
    'netherlands':  ['eerste divisie'],
    'norway':       ['obos ligaen','obos','1 divisjon','1 division'],
    'poland':       ['i liga','1 liga'],
    'portugal':     ['liga portugal 2','segunda liga','liga de honra'],
    'austria':      ['2 liga'],
    'romania':      ['liga ii','liga 2'],
    'scotland':     ['championship'],
    'spain':        ['segunda division','laliga 2','la liga 2'],
    'sweden':       ['superettan'],
    'switzerland':  ['challenge league'],
    'turkey':       ['1 lig','first league'],
    'ukraine':      ['premier league'],
};

// Treti urovne, ktere jsou presto dobre obsazene a sledovane. Bez tohoto seznamu
// by spadly do T4 spolecne s pullige z Lesotha (leagueLevel je zaradi jako uroven 3).
const TIER3_LEAGUES = {
    'england':      ['league one'],
    'germany':      ['3 liga','3 bundesliga'],
    'france':       ['national 1','championnat national'],
    'spain':        ['primera federacion'],
};

// Klubove kontinentalni pohary mimo UEFA - nejsou to reprezentacni souteze,
// takze nepatri do T2 vedle Africkeho poharu naroduu.
const CONT_CLUB_RE = /\bcaf champions league\b|\bcaf confederation cup\b|\bafc champions league\b|\bafc cup\b|\bconcacaf champions\b|\bcopa libertadores\b|\bcopa sudamericana\b|\bleagues cup\b/;

// Reprezentacni souteze (country = 'World'). Evropske a svetove -> T1,
// ostatni svetadily -> T2. Duvod: pri reprezentacni prestavce nehraji klubove
// souteze z T1/T2 a bez tohoto by zbyly jen zapasy z T3/T4.
const NAT_T1_RE = /\buefa nations league\b|\beuro championship\b|\beuropean championship\b|\bworld cup\b|\bfinalissima\b/;
const NAT_T2_RE = /\bafrica cup of nations\b|\bafcon\b|\basian cup\b|\bcopa america\b|\bgold cup\b|\bconcacaf nations league\b|\bconcacaf championship\b|\bcaf cup of nations\b|\bafc asian\b|\bafc championship\b|\boceania nations cup\b|\bofc nations cup\b/;

function matchesList(list,c,n){
    const pats=list[c];
    if(!pats)return false;
    for(const p of pats){if(hasWord(n,p))return true;}
    return false;
}

function leagueTier(name,country,type){
    const n=norm(name),c=norm(country);
    if(EXCLUDE_RE.some(re=>re.test(n)))return 0;
    // Valkou zasazene zeme - zapasy se prerusuji leteckymi poplachy, vysledky
    // jsou proto nepredvidatelne. Vyradit vcetne poharu a nizsich soutezi.
    if(WAR_COUNTRIES.has(c))return 0;
    // Pohary nizsich urovni odchytit drive, nez se nazev chytne na seznam T1/T2
    // ('Premier League Cup' obsahuje 'premier league').
    if(LOW_CUP_RE.test(n))return 4;
    // Spanelske RFEF souteze jsou az 3.-5. uroven, ale nazev obsahuje
    // 'segunda division' / 'primera division' ze seznamu T1/T2 -> odchytit driv.
    if(c==='spain'&&/\brfef\b|\bfederacion\b/.test(n))return 4;
    // Brazilske/argentinske statni a regionalni ligy nizsich urovni (Carioca B2,
    // Gaucho - 3, Paraibano 2, Catarinense - 3, Mineiro - 3...) jsou poloamaterske
    // a v log zaplavuji T4. Nazev je 'statni liga' + cislo urovne -> vyradit.
    if(STATE_LEAGUE_RE.test(n)){
        if(/\b([2-9]|b\d?|a[2-9])\b/.test(n))return 0;
        // Nejvyssi statni divize (Paulista A1, Carioca, Mineiro) jsou sice kvalitni,
        // ale je to regionalni soutez - nepatri do T3 vedle nejvyssich ligy zemi.
        return 4;
    }
    if(c==='world'){
        if(/uefa champions league|uefa europa league|uefa (europa )?conference league/.test(n))return 1;
        // Klubove kontinentalni pohary mimo UEFA -> T3 (musi byt pred NAT_T2_RE).
        if(CONT_CLUB_RE.test(n))return 3;
        // Evropske reprezentacni souteze + svetove kvalifikace -> T1. Bez toho by
        // pri reprezentacni prestavce byly T1 i T2 prazdne a bral by se jen T4.
        if(NAT_T1_RE.test(n))return 1;
        // Reprezentacni souteze ostatnich svetadilu -> T2.
        if(NAT_T2_RE.test(n))return 2;
        if(/uefa super cup/.test(n))return 2;
        // Mezinarodni pratelaky reprezentaci - rotujici sestavy, nejhorsi
        // predvidatelnost ze vsech. Povolene, ale az v T4.
        if(/\bfriendl/.test(n))return 4;
        return 4;
    }
    // Pratelske zapasy mimo reprezentacni uroven (country != World) nechceme.
    if(/\bfriendl/.test(n))return 0;
    const isStrong=EUROPEAN_COUNTRIES.has(c)||STRONG_NON_EU_COUNTRIES.has(c);
    // Typ z API je spolehlivejsi nez nazev - nektere ligy maji v nazvu 'copa'
    // ('Copa de Primera' v Paraguayi je liga, ne pohar).
    const isCup=type?norm(type)==='cup':CUP_RE.test(n);
    // Pohary se vyhodnocuji PRED seznamy T1/T2 a nikdy nejdou vys nez T2 -
    // T1 je vyhrazeny pro top prvni ligy ('J-League Cup' by jinak sel do T1).
    if(isCup){
        // Hlavni domaci pohar zeme s ligou v T1 -> T2 (viz popis tieru v hlavicce).
        // Ostatni evropske pohary -> T3, mimoevropske -> T4; pohar je ze vsech
        // soutezi nejmene predvidatelny, takze u slabsich zemi nema co delat vys.
        if(TIER1_LEAGUES[c])return 2;
        return EUROPEAN_COUNTRIES.has(c)?3:4;
    }
    // T2 se testuje jako prvni - jeho nazvy jsou casto nadmnozinou tech z T1
    // ('liga portugal 2' obsahuje 'liga portugal', '2 bundesliga' obsahuje 'bundesliga').
    if(matchesList(TIER2_LEAGUES,c,n))return 2;
    if(matchesList(TIER1_LEAGUES,c,n))return 1;
    if(matchesList(TIER3_LEAGUES,c,n))return 3;
    // Zbytek: nejvyssi soutez evropske/silne zeme -> T3, jinak T4.
    if(leagueLevel(n,c)!==1)return 4;
    return isStrong?3:4;
}

export { leagueTier, norm, leagueLevel };
function maskKey(k){if(!k)return'(none)';if(k.length<=8)return'***';return k.slice(0,4)+'...'+k.slice(-4)+' (len='+k.length+')';}
function sleep(ms){return new Promise(r=>setTimeout(r,ms));}
// Zdroj nahody. V produkci Math.random, v testech lze pres setRng() nasadit
// deterministicky generator (mulberry32) a vyber tak reprodukovat.
let rng=Math.random;
export function setRng(fn){rng=fn||Math.random;}
export function makeSeededRng(seed){
    let a=(seed>>>0)||1;
    return function(){
        a=(a+0x6D2B79F5)>>>0;
        let t=Math.imul(a^(a>>>15),1|a);
        t=(t+Math.imul(t^(t>>>7),61|t))^t;
        return ((t^(t>>>14))>>>0)/4294967296;
    };
}
function shuffle(arr){for(let i=arr.length-1;i>0;i--){const j=Math.floor(rng()*(i+1));[arr[i],arr[j]]=[arr[j],arr[i]];}return arr;}
function fmtDate(d){return d.toISOString().split('T')[0];}
// Stredni (medianovy) kurz - pro sudy pocet prumer dvou prostrednich hodnot
function median(arr){if(!arr||arr.length===0)return NaN;const s=[...arr].sort((a,b)=>a-b);const mid=Math.floor(s.length/2);return s.length%2!==0?s[mid]:(s[mid-1]+s[mid])/2;}
// Nejvyssi kurz, ktery nabizi aspon MIN_BOOKMAKERS sazkovek. Median zapasy na
// hrane pasma zbytecne strhaval dolu (2.05 a 2.15 -> 2.10), tohle naopak vezme
// nejlepsi realne dostupny kurz, ale jen pokud ho potvrdi vic bookmakeru.
export function bestOdds(arr){
    if(!arr||arr.length===0)return NaN;
    const s=[...arr].sort((a,b)=>b-a);
    if(s.length<MIN_BOOKMAKERS)return median(s);
    return s[MIN_BOOKMAKERS-1];
}
// --- Deduplikace proti existujicim json souborum jinych botu -----------------
function normTeam(s){return String(s||'').toLowerCase().replace(/\s+/g,' ').trim();}
function normDate(s){return String(s||'').slice(0,10);}
function matchKeysFromEntry(m){
    const keys=[];
    const id=m?.fixtureId??m?.fixture?.id??m?.id;
    if(id!==undefined&&id!==null&&id!=='')keys.push('id:'+id);
    // 'match' bywa "Home - Away"
    let home=m?.home??m?.teams?.home?.name;
    let away=m?.away??m?.teams?.away?.name;
    if((!home||!away)&&typeof m?.match==='string'&&m.match.includes(' - ')){
        const [h,a]=m.match.split(' - ');home=home||h;away=away||a;
    }
    const date=normDate(m?.kickoff??m?.fixture?.date??m?.date);
    if(home&&away&&date)keys.push('na:'+normTeam(home)+'|'+normTeam(away)+'|'+date);
    if(home&&away)keys.push('teams:'+normTeam(home)+'|'+normTeam(away));
    return keys;
}
function loadDedupKeysFromFiles(paths){
    const set=new Set();
    for(const p of paths){
        if(!existsSync(p)){console.log('Dedup: '+p+' neexistuje, preskakuji.');continue;}
        try{
            const raw=readFileSync(p,'utf-8');
            const data=JSON.parse(raw);
            const arr=Array.isArray(data)?data:(data.matches??data.items??data.picks??[]);
            let n=0;
            for(const m of arr){for(const k of matchKeysFromEntry(m)){set.add(k);}n++;}
            console.log('Dedup: '+p+' -> '+n+' zaznamu, '+set.size+' klicu celkem');
        }catch(e){console.warn('Dedup: nelze precist '+p+': '+e.message);}
    }
    return set;
}
function isDuplicate(candidate,dedupSet){
    for(const k of matchKeysFromEntry(candidate)){if(dedupSet.has(k))return true;}
    return false;
}
async function loadDedupKeysFromUrls(urls){
    const set=new Set();
    for(const u of urls){
        try{
            const res=await fetch(u,{headers:{'Accept':'application/json','User-Agent':USER_AGENT,'Cache-Control':'no-cache'}});
            if(!res.ok){console.warn('Dedup: '+u+' HTTP '+res.status+', preskakuji.');continue;}
            const data=await res.json();
            const arr=Array.isArray(data)?data:(data.matches??data.items??data.picks??[]);
            let n=0;
            for(const m of arr){for(const k of matchKeysFromEntry(m)){set.add(k);}n++;}
            console.log('Dedup: '+u+' -> '+n+' zaznamu, '+set.size+' klicu celkem');
        }catch(e){console.warn('Dedup: nelze stahnout '+u+': '+e.message);}
    }
    return set;
}
// ---------------------------------------------------------------------------

async function apiFetch(path){
    const url=FOOTBALL_API+path;
    for(let attempt=1;attempt<=3;attempt++){
        try{
            const res=await fetch(url,{headers:{'x-apisports-key':API_KEY,'Accept':'application/json','User-Agent':USER_AGENT}});
            reqCount++;
            if(res.status===429){console.warn('  [429] waiting '+(5*attempt)+'s...');await sleep(5000*attempt);continue;}
            if(res.status===403){let b='';try{b=(await res.text()).slice(0,400);}catch{}console.warn('  [403] '+path.split('?')[0]+' key='+maskKey(API_KEY)+' body='+b);if(attempt<3){await sleep(3000*attempt);continue;}return{response:[],paging:{total:0},__error:403};}
            if(!res.ok){let b='';try{b=(await res.text()).slice(0,200);}catch{}console.warn('  HTTP '+res.status+': '+path.split('?')[0]+' body='+b);return{response:[],paging:{total:0},__error:res.status};}
            const data=await res.json();
            if(data.errors&&Object.keys(data.errors).length>0){const e=JSON.stringify(data.errors);if(e.includes('rateLimit')||e.includes('Too many')){console.warn('  [RATE] '+e+' waiting '+(5*attempt)+'s...');await sleep(5000*attempt);continue;}console.warn('  ',e);return{response:[],paging:{total:0},__error:'api-errors'};}
            return data;
        }catch(e){console.warn('  Fetch error:',e.message);return{response:[],paging:{total:0},__error:'exception'};}
    }
    console.warn('  [FAIL] Max retries: '+path.split('?')[0]);
    return{response:[],paging:{total:0},__error:'max-retries'};
}

async function getFixtures(date){fixtureFetchAttempts++;const data=await apiFetch('/fixtures?date='+date+'&timezone='+TZ+'&status=NS');if(data.__error)fixtureFetchErrors++;return data.response||[];}

// Metadata vsech soutezi (hlavne league.type = 'League' / 'Cup'), ktere /fixtures nevraci.
async function getLeagueTypes(){
    const data=await apiFetch('/leagues');
    const map=new Map();
    for(const it of data.response||[]){if(it?.league?.id!=null)map.set(it.league.id,it.league.type);}
    console.log('Metadata soutezi: '+map.size+' lig nacteno');
    return map;
}

async function getLeagueOdds(leagueId,season,date,bet=5){
    let all=[],page=1,totalPages=1;
    do{const data=await apiFetch('/odds?league='+leagueId+'&season='+season+'&date='+date+'&bet='+bet+'&page='+page);all.push(...(data.response||[]));totalPages=data.paging?.total||0;page++;if(page<=totalPages)await sleep(450);}while(page<=totalPages);
    return all;
}

function generatePairings(indices){const results=[];function recurse(rem,cur){if(rem.length===0){results.push([...cur]);return;}if(rem.length===1){results.push([...cur,[rem[0]]]);return;}const first=rem[0],rest=rem.slice(1);for(let i=0;i<rest.length;i++){cur.push([first,rest[i]]);recurse(rest.filter((_,j)=>j!==i),cur);cur.pop();}}recurse(indices,[]);return results;}

// Nacte namerenou golovost soutezi (league-goals.json). Kdyz soubor chybi nebo
// je rozbity, vrati prazdnou mapu - filtr se pak proste neuplatni a bot bezi
// presne jako driv.
function loadGoalStats(){
    const map=new Map();
    try{
        if(!existsSync(GOALS_FILE)){console.log('Golove statistiky: '+GOALS_FILE+' nenalezen, filtr se neuplatni');return map;}
        const data=JSON.parse(readFileSync(GOALS_FILE,'utf-8'));
        for(const r of data||[]){
            if(r?.name==null||r?.country==null)continue;
            if(typeof r.over25Rate!=='number'||typeof r.played!=='number')continue;
            map.set(norm(r.name)+'|'+norm(r.country),{over25Rate:r.over25Rate,played:r.played});
        }
        console.log('Golove statistiky: '+map.size+' soutezi nacteno');
    }catch(e){console.warn('Golove statistiky: chyba cteni ('+e.message+'), filtr se neuplatni');}
    return map;
}

// true = soutez je golove chuda a tip Over 2.5 v ni nedava smysl.
// Rozhoduje jen pri dostatecnem vzorku; neznama soutez projde.
export function isLowScoring(goalStats,leagueName,country){
    const st=goalStats.get(norm(leagueName)+'|'+norm(country));
    // Chybejici nebo nevalidni statistika nikdy nevede k vyrazeni - neznalost
    // neni dukaz. Takova liga jde dal a rozhodne o ni az vazeny los.
    if(!st||typeof st.over25Rate!=='number'||!Number.isFinite(st.over25Rate))return false;
    const played=Number.isFinite(st.played)?st.played:0;
    // Prahy jsou striktni: presne 40 % (resp. 35 %) jeste PROCHAZI.
    if(played>=GOALS_MIN_SAMPLE)return st.over25Rate<MIN_OVER25_RATE;
    if(played>=GOALS_MID_SAMPLE)return st.over25Rate<MID_OVER25_RATE;
    return false;   // maly vzorek: resi jen vaha, ne tvrdy filtr
}

// Mekky filtr: golove chude souteze z poolu odstrani jen tehdy, kdyz po
// odstraneni zbyde dost kandidatu na naplneni tiketu. Ve slaby den (ctvrtek,
// pauza na reprezentaci) se filtr MUSI castecne uvolnit, aby aplikace
// nezustaly prazdne.
//
// Uvolneni je ale POSTUPNE, ne vse-nebo-nic. Driv se pri nedostatku vracel cely
// puvodni pool, takze i v silnem dni s jedinym chybejicim kandidatem se do hry
// vratily VSECHNY golove chude ligy. Nyni se vyfiltrovani kandidati pripoji az
// NA KONEC a jen v poctu, ktery skutecne chybi - a serazeni od nejmene spatnych
// (nejvyssi over25Rate napred). Vysledne poradi uz vyber nijak nedeterminuje
// (los je vazeny), ale pool obsahuje minimum golove slabych zapasu.
export function applyGoalFilter(pool,goalStats,needed,label){
    if(goalStats.size===0)return pool;
    const kept=[],dropped=[];
    for(const m of pool){
        // Filtrujeme vyhradne Over 2.5 - u Home/Away win golovost nevypovida nic.
        if(m.tip==='Over 2.5'&&isLowScoring(goalStats,m.league,m.country))dropped.push(m);
        else kept.push(m);
    }
    if(dropped.length===0)return pool;
    console.log('Golovy filtr ('+label+'): odstraneno '+dropped.length+' tipu z golove chudych soutezi (zbyva '+kept.length+')');
    for(const m of dropped)console.log('   - LOW: '+m.match+' | '+m.league+' ('+m.country+')');
    if(kept.length>=needed)return kept;

    // Nedostatek kandidatu - vracime jen tolik nejlepsich z vyrazenych, kolik
    // chybi (plus mala rezerva, protoze cast poolu padne na limity lig/zemi).
    const missing=needed-kept.length;
    const rescue=[...dropped].sort((a,b)=>rawOver25Rate(goalStats,b)-rawOver25Rate(goalStats,a))
        .slice(0,missing+GOAL_FALLBACK_SLACK);
    console.log('Golovy filtr ('+label+'): UVOLNEN - po filtru zbylo jen '+kept.length
        +' z '+needed+' potrebnych (slaby den), vraceno '+rescue.length+' nejlepsich z vyrazenych');
    for(const m of rescue){
        console.log('   + ZPET: '+m.match+' | '+m.league+' ('+m.country+') - Over 2.5 '
            +rawOver25Rate(goalStats,m).toFixed(1)+' %');
    }
    // Vyfiltrovani jdou az za vyhovujici, takze maji prednost vzdy ti lepsi.
    return [...kept,...rescue];
}

// Namerena golovost bez prahu vzorku. Slouzi k SERAZENI vyrazenych kandidatu
// pri uvolneni filtru - potrebujeme vedet, kteri jsou nejmene spatni. Neznama
// liga dostane 0, aby sla uplne nakonec (vyrazena byt nemohla, takze se sem
// dostane jen vyjimecne).
function rawOver25Rate(goalStats,m){
    const st=goalStats.get(norm(m.league)+'|'+norm(m.country));
    return st&&Number.isFinite(st.over25Rate)?st.over25Rate:0;
}

// Preference golovejsich soutezi: mezi dvema stejne dobrymi kandidaty na Over 2.5
// vyhrava liga s vyssim over25Rate. Tip zustava vzdy Over 2.5 - u lig pod
// MIN_OVER25_RATE se nenahrazuje jinym trhem, jen jde na konec fronty.
function over25Rate(goalStats,m){
    const st=goalStats.get(norm(m.league)+'|'+norm(m.country));
    if(!st||st.played<GOALS_MIN_SAMPLE)return 50;
    return st.over25Rate;
}

// Pro smisene pooly (Over 2.5 + Home/Away win): Over 2.5 se prednostne bere z lig
// s vysokou golovosti (nad OVER_PREFER_RATE). Neni to zakaz - kdyz takovych lig
// neni dost, vyber sahne i do lig s nizsim procentem a tiket se naplni. Zapasy z
// prumerne golovych lig se tak do tiketu dostanou spis pres Home/Away win.
const isGoalRichOver=goalStats=>m=>m.tip==='Over 2.5'&&over25Rate(goalStats,m)>=OVER_PREFER_RATE;
// POZN.: drivejsi sortByGoals() byl odstranen. Golovost uz neurcuje poradi
// kandidatu ve fronte (vyber bral pres pop() vzdy nejgolovejsi zapas) - nyni
// vstupuje VYHRADNE jako golovy nasobitel vahy ve weightedPick()
// (goalMultiplier/G_STRENGTH). Tvrde
// filtry golovosti (applyGoalFilter/isLowScoring) i fazove deleni pres
// isGoalRichOver zustavaji zachovany jako samostatna pojistka.

function balanceGroups(picks){
    const indices=picks.map((_,i)=>i);const allP=generatePairings(indices);
    let bestP=null,bestD=Infinity;
    for(const p of allP){const gO=p.map(pair=>pair.reduce((a,idx)=>a*parseFloat(picks[idx].odds),1));const d=Math.max(...gO)-Math.min(...gO);if(d<bestD){bestD=d;bestP=p;}}
    if(!bestP)return picks.map((p,i)=>({...p,group:Math.floor(i/2)+1}));
    const result=[];for(let g=0;g<bestP.length;g++){for(const idx of bestP[g])result.push({...picks[idx],group:g+1});}return result;
}

// === Koordinovany vyber napric vystupy ======================================
// Identita tipu je dvojice fixtureId|tip. Globalni dedup (blockedPicks) zakazuje
// stejnou dvojici ve dvou souborech, lokalni (ownFixtures) zakazuje tyz zapas
// dvakrat v jednom tiketu - a to vzdy, i pri uvolnenem dedupu.
export const pickKey=m=>m.fixtureId+'|'+m.tip;

// Stabilni identita ligy. Primarne leagueId z API, pri jeho absenci fallback na
// nazev+zeme. Prefixy 'id:' a 'nm:' zarucuji, ze ciselne ID nikdy nekoliduje
// s textovym fallbackem (liga pojmenovana "123" neni liga s id 123).
export function leagueKey(m){
    const id=m?.leagueId;
    if(id!=null&&id!==''&&Number.isFinite(Number(id)))return 'id:'+Number(id);
    return 'nm:'+norm(m?.league)+'|'+norm(m?.country);
}

// Registr unikatnich fixture podle ligy napric celou skupinou souboru.
// Drzi refcount na fixture: stejny zapas ve dvou souborech s JINYM tipem
// zvedne refcount na 2, ale do limitu ligy se pocita porad jako JEDEN zapas.
// Teprve pokles refcountu na 0 ligu opravdu uvolni.
export function createLeagueRegistry(cap=LEAGUE_GLOBAL_CAP){
    const leagues=new Map();
    const state={cap,relaxLevel:0,relaxations:[]};
    const bucket=k=>{let b=leagues.get(k);if(!b){b=new Map();leagues.set(k,b);}return b;};
    return{
        state,
        get cap(){return state.cap;},
        // Pocet UNIKATNICH fixture ligy (ne poctu tipu).
        count(m){const b=leagues.get(leagueKey(m));return b?b.size:0;},
        // Novy fixture by ligu pretahl pres limit? Uz zapocitany fixture projde vzdy.
        wouldExceed(m){
            const b=leagues.get(leagueKey(m));
            if(!b)return false;
            if(b.has(m.fixtureId))return false;
            return b.size>=state.cap;
        },
        add(m){
            const b=bucket(leagueKey(m));
            b.set(m.fixtureId,(b.get(m.fixtureId)||0)+1);
        },
        remove(m){
            const k=leagueKey(m),b=leagues.get(k);
            if(!b)return;
            const n=(b.get(m.fixtureId)||0)-1;
            if(n>0)b.set(m.fixtureId,n);
            else{b.delete(m.fixtureId);if(b.size===0)leagues.delete(k);}
        },
        relax(reason){
            if(state.relaxLevel>=LEAGUE_CAP_RELAX_STEPS.length-1)return false;
            state.relaxLevel++;
            state.cap=LEAGUE_CAP_RELAX_STEPS[state.relaxLevel];
            state.relaxations.push({cap:state.cap,reason});
            return true;
        },
        snapshot(){
            const out=[];
            for(const [k,b] of leagues)out.push({league:k,fixtures:b.size});
            return out.sort((a,b)=>b.fixtures-a.fixtures);
        },
    };
}

// Bayesovsky vyhlazena golovost ligy v %. Vraci null, kdyz o lize nic nevime
// (pak se golovy nasobitel vubec neuplatni a kandidat zustava neutralni).
// Mala liga neni ignorovana, jen stazena k prumeru: liga s 29 zapasy a 27,6 %
// vyjde ~42,5 %, liga s 224 zapasy a 51,3 % zustane ~51,0 %.
export function effectiveOver25(st){
    if(!st||typeof st.over25Rate!=='number')return null;
    const p=Math.max(0,st.played||0);
    return (st.over25Rate*p+G_PRIOR_RATE*100*G_PRIOR)/(p+G_PRIOR);
}

// Multiplikativni golovy nasobitel pro Over 2.5. Pro ostatni trhy vraci 1
// (= zadny vliv), protoze u Home/Away win golovost nevypovida nic.
export function goalMultiplier(m,goalStats){
    if(m.tip!=='Over 2.5'||!goalStats||goalStats.size===0)return 1;
    const eff=effectiveOver25(goalStats.get(norm(m.league)+'|'+norm(m.country)));
    if(eff==null)return 1;
    const z=Math.max(-G_CLAMP,Math.min(G_CLAMP,(eff-G_PIVOT)/G_SPAN));
    return Math.pow(1+G_STRENGTH,z);
}

// Vaha jednoho kandidata pro vazeny los. Tier, pocet kandidatu a zastoupeni
// ligy se SCITAJI jako odchylky od 1.0, takze se nemohou nasobit do extremu.
// Golovost se na ten vysledek az nakonec NASOBI - jen tak ji nelze prebit
// ostatnimi faktory. Vysledek nikdy neklesne pod W_FLOOR, takze kazdy
// kandidat, ktery prosel filtry, ma sanci.
export function candidateWeight(m,{goalStats,leagueCounts,registry,presenceRegistry}={}){
    let adj=0;
    adj+=LEAGUE_TIER_ADJ[m.tier]??0;
    const n=leagueCounts?.get(leagueKey(m))||0;
    if(n>1)adj+=W_COUNT_MAX*Math.min(1,Math.log(n)/Math.log(W_COUNT_SATURATION));
    // Zastoupeni ligy. presenceRegistry je READ-ONLY pohled na jiny registr
    // (u react1 je to registr hlavni skupiny) - slouzi jen k vazeni, nic do nej
    // nezapisujeme. Oba pocty se neSCITAJI: bere se maximum a penalizace se
    // aplikuje prave jednou, takze stejne zastoupeni nemuze penalizovat dvakrat.
    if(registry||presenceRegistry){
        const c=Math.max(registry?registry.count(m):0,presenceRegistry?presenceRegistry.count(m):0);
        adj+=W_PRESENCE_PENALTY[Math.min(c,W_PRESENCE_PENALTY.length-1)];
    }
    // Golovost az nakonec a MULTIPLIKATIVNE (u jinych trhu je nasobitel 1).
    return Math.max(W_FLOOR,W_BASE*(1+adj)*goalMultiplier(m,goalStats));
}

// Vazeny nahodny vyber jednoho prvku. Nahrazuje puvodni deterministicke
// pop() po sortByGoals - golovost uz neurcuje poradi, je jen jednou z vah.
export function weightedPick(list,ctx){
    if(!list||list.length===0)return null;
    let total=0;
    const w=new Array(list.length);
    for(let i=0;i<list.length;i++){w[i]=candidateWeight(list[i],ctx);total+=w[i];}
    if(!(total>0))return list[Math.floor(rng()*list.length)];
    let r=rng()*total;
    for(let i=0;i<list.length;i++){r-=w[i];if(r<=0)return list[i];}
    return list[list.length-1];
}

// Picker drzi stav jednoho vystupniho souboru. Vyber se nedela najednou, ale po
// jednom zapase (pickOne), aby mohl driver stridat soubory a zadny nemel
// prednost jen diky poradi generovani.
export function createPicker({name,pool,want,phases,coreMin=0,blockedPicks,goalStats,tierStrategy='weighted',priority='hlavni skupina',registry=null,presenceRegistry=null}){
    const selected=[],entries=[],usedLeagues=new Set(),countries=new Map(),ownFixtures=new Set();
    const tierTotals={1:0,2:0,3:0,4:0};
    const stats={pool:pool.length,poolFixtures:new Set(pool.map(m=>m.fixtureId)).size,rejLeague:0,rejCountry:0,rejLeagueGlobal:0,dedupConflicts:0,redistributed:0,deepRedistributed:0,capRelaxed:0,phaseFallbacks:0,dupAllowed:0};
    const coreAvail=pool.filter(isCore).length;
    const coreQuota=Math.min(coreMin,coreAvail,want);
    // Pocet vhodnych UNIKATNICH fixture na ligu v poolu - podklad pro bonus za
    // velikost nabidky. Jeden zapas se dvema tipy se pocita jen jednou.
    const leagueCounts=new Map();
    {
        const seenPerLeague=new Map();
        for(const m of pool){
            const k=leagueKey(m);
            let s=seenPerLeague.get(k);
            if(!s){s=new Set();seenPerLeague.set(k,s);}
            s.add(m.fixtureId);
        }
        for(const [k,s] of seenPerLeague)leagueCounts.set(k,s.size);
    }
    const wctx=()=>({goalStats,leagueCounts,registry,presenceRegistry});

    const isFull=()=>selected.length>=want;
    const coreCount=()=>selected.filter(isCore).length;
    const pendingPhases=()=>phases.filter(ph=>selected.filter(ph.countsAs).length<ph.want);

    function baseEligible(m,relaxed){
        if(ownFixtures.has(m.fixtureId))return false;
        if(!relaxed&&blockedPicks.has(pickKey(m))){stats.dedupConflicts++;return false;}
        // Lokalni limit: max 1 zapas z ligy v TOMTO souboru. Plati vzdy, i po
        // uvolneni globalniho limitu.
        if(usedLeagues.has(m.league+'|'+m.country)){stats.rejLeague++;return false;}
        // Globalni limit: max N unikatnich fixture z ligy napric skupinou.
        if(registry&&registry.wouldExceed(m)){stats.rejLeagueGlobal++;return false;}
        if((countries.get(m.country)||0)>=MAX_PER_COUNTRY){stats.rejCountry++;return false;}
        return true;
    }
    // Kose se stavi az v okamziku vyberu z aktualniho stavu poolu - kandidat se
    // tedy nikdy "nespotrebuje" tim, ze ho vyber zahodil kvuli limitu ligy/zeme.
    function buildBuckets(phase,relaxed,exclude){
        const b={1:[],2:[],3:[],4:[]};
        const seen=new Set();
        for(const m of pool){
            const k=pickKey(m);
            if(exclude&&exclude.has(k))continue;
            if(seen.has(k))continue;
            if(!phase.market(m))continue;
            if(!baseEligible(m,relaxed))continue;
            seen.add(k);
            if(b[m.tier])b[m.tier].push(m);
        }
        // Jen promichani - zadne razeni podle golovosti. Golovost uz neurcuje
        // poradi fronty, vstupuje vyhradne jako jedna z vah ve weightedPick().
        for(const t of [1,2,3,4])shuffle(b[t]);
        return b;
    }
    function drawTier(b,useCaps){
        const caps={};
        for(const t of [1,2,3,4]){
            const n=b[t].length+tierTotals[t];
            caps[t]=n===0?0:Math.max(1,Math.min(MAX_PER_TIER[t]??want,Math.floor(n*MAX_TIER_SHARE)));
        }
        const avail=[1,2,3,4].filter(t=>b[t].length>0&&(!useCaps||tierTotals[t]<caps[t]));
        if(avail.length===0)return 0;
        const totalW=avail.reduce((a,t)=>a+(TIER_WEIGHTS[t]||1),0);
        let r=rng()*totalW,tier=avail[avail.length-1];
        for(const t of avail){r-=(TIER_WEIGHTS[t]||1);if(r<=0){tier=t;break;}}
        return tier;
    }
    function take(m,tier,phaseLabel,meta={}){
        const dup=blockedPicks.has(pickKey(m));
        usedLeagues.add(m.league+'|'+m.country);
        ownFixtures.add(m.fixtureId);
        blockedPicks.add(pickKey(m));
        countries.set(m.country,(countries.get(m.country)||0)+1);
        if(registry)registry.add(m);
        tierTotals[tier]++;
        const entry={m,tier,phase:phaseLabel,capRelaxed:!!meta.capRelaxed,duplicate:dup,reason:meta.reason||''};
        selected.push(m);entries.push(entry);
        if(dup)stats.dupAllowed++;
        return entry;
    }
    // Preferencni strategie (tips-kombik.json): zadny vazeny los TIERU, poradi
    // skupin jadro -> T1+T2 -> T3+T4 zustava tvrde. Vazeny los se uplatni jen
    // UVNITR prave vybrane skupiny, takze kvoty ani poradi skupin neobchazi.
    function pickByPreference(b,coreOnly){
        const groups=coreOnly?[[1,2,3,4]]:[[1,2],[3,4]];
        for(const g of groups){
            const list=[].concat(...g.map(t=>b[t]));
            if(list.length===0)continue;
            return weightedPick(list,wctx());
        }
        return null;
    }
    function attempt(phase,relaxed,exclude){
        const all=buildBuckets(phase,relaxed,exclude);
        let b=all,coreOnly=false;
        if(coreCount()<coreQuota){
            const core={1:[],2:[],3:[],4:[]};
            let any=false;
            for(const t of [1,2,3,4]){core[t]=all[t].filter(isCore);if(core[t].length)any=true;}
            if(any){b=core;coreOnly=true;}
        }
        if(tierStrategy==='preference'){
            let m=pickByPreference(b,coreOnly);
            if(!m&&coreOnly)m=pickByPreference(all,false);
            if(!m)return null;
            return take(m,m.tier,phase.label,{reason:relaxed?'uvolneny dedup':''});
        }
        let tier=drawTier(b,true),capRelaxed=false;
        if(!tier&&(b[1].length||b[2].length||b[3].length||b[4].length)){tier=drawTier(b,false);capRelaxed=!!tier;if(capRelaxed)stats.capRelaxed++;}
        if(!tier&&coreOnly){b=all;tier=drawTier(b,true)||drawTier(b,false);}
        if(!tier)return null;
        return take(weightedPick(b[tier],wctx()),tier,phase.label,{capRelaxed,reason:relaxed?'uvolneny dedup':''});
    }
    // Vybere jeden zapas. Kdyz aktualni faze nema kandidaty, zkusi dalsi
    // nenaplnenou fazi - vyber se tak nikdy nezasekne.
    function pickOne(relaxed=false,exclude=null){
        if(isFull())return null;
        const ph=pendingPhases();
        for(let i=0;i<ph.length;i++){
            const e=attempt(ph[i],relaxed,exclude);
            if(e){if(i>0)stats.phaseFallbacks++;return e;}
        }
        return null;
    }
    function ownsPick(key){return entries.some(e=>pickKey(e.m)===key);}
    function restore(e){
        usedLeagues.add(e.m.league+'|'+e.m.country);
        ownFixtures.add(e.m.fixtureId);
        blockedPicks.add(pickKey(e.m));
        countries.set(e.m.country,(countries.get(e.m.country)||0)+1);
        if(registry)registry.add(e.m);
        tierTotals[e.tier]++;
        selected.push(e.m);entries.push(e);
    }
    // Uvolni dvojici pro jiny soubor, ale JEN kdyz se tento soubor dokaze
    // doplnit jinym kandidatem - jinak se vse vrati zpet.
    function tryYield(key){
        const idx=entries.findIndex(e=>pickKey(e.m)===key);
        if(idx<0)return false;
        const e=entries[idx];
        entries.splice(idx,1);selected.splice(selected.indexOf(e.m),1);
        usedLeagues.delete(e.m.league+'|'+e.m.country);
        ownFixtures.delete(e.m.fixtureId);
        countries.set(e.m.country,(countries.get(e.m.country)||0)-1);
        if(registry)registry.remove(e.m);
        tierTotals[e.tier]--;
        blockedPicks.delete(key);
        const repl=pickOne(false,new Set([key]));
        if(repl){repl.reason='prerozdeleni';stats.redistributed++;return true;}
        restore(e);
        return false;
    }
    function canTakeIgnoringDedup(m,phase){
        if(!phase.market(m))return false;
        if(ownFixtures.has(m.fixtureId))return false;
        if(usedLeagues.has(m.league+'|'+m.country))return false;
        if(registry&&registry.wouldExceed(m))return false;
        if((countries.get(m.country)||0)>=MAX_PER_COUNTRY)return false;
        return true;
    }
    // --- Podpora viceurovnoveho prerozdeleni --------------------------------
    // detach() vyjme uz vybranou dvojici (aby si ji mohl vzit jiny soubor),
    // restoreEntry() ji vrati zpet, kdyz se retez prerozdeleni nepodari.
    function detach(key){
        const idx=entries.findIndex(e=>pickKey(e.m)===key);
        if(idx<0)return null;
        const e=entries[idx];
        entries.splice(idx,1);selected.splice(selected.indexOf(e.m),1);
        usedLeagues.delete(e.m.league+'|'+e.m.country);
        ownFixtures.delete(e.m.fixtureId);
        countries.set(e.m.country,(countries.get(e.m.country)||0)-1);
        if(registry)registry.remove(e.m);
        tierTotals[e.tier]--;
        blockedPicks.delete(key);
        return e;
    }
    // Vezme konkretni dvojici - az pote, co ji retez prerozdeleni uvolnil.
    // Tvrda omezeni (faze/trh, zapas, liga, zeme) plati i tady.
    function takeSpecific(m,reason){
        if(isFull())return null;
        if(blockedPicks.has(pickKey(m)))return null;
        const phase=pendingPhases().find(ph=>canTakeIgnoringDedup(m,ph));
        if(!phase)return null;
        return take(m,m.tier,phase.label,{reason:reason||'prerozdeleni'});
    }
    // Kandidati, ktere by tento soubor vzal, kdyby je nedrzel nekdo jiny.
    function blockedOptions(exclude){
        const out=[],seen=new Set();
        for(const ph of pendingPhases()){
            for(const m of pool){
                const k=pickKey(m);
                if(seen.has(k))continue;
                if(exclude&&exclude.has(k))continue;
                if(!blockedPicks.has(k))continue;
                if(!canTakeIgnoringDedup(m,ph))continue;
                seen.add(k);out.push(m);
            }
        }
        return shuffle(out);
    }
    return{name,priority,pool,want,stats,selected,entries,tierTotals,coreQuota,leagueCounts,registry,isFull,pickOne,ownsPick,tryYield,canTakeIgnoringDedup,pendingPhases,detach,restoreEntry:restore,takeSpecific,blockedOptions,isBlocked:k=>blockedPicks.has(k)};
}

// Limity hledani prerozdeleni - brani exponencialnimu vybuchu i zacykleni.
const REDIST_MAX_DEPTH=4;
const REDIST_MAX_STEPS=500;

// Uvolni dvojici 'key' od jejiho majitele tak, ze majitel dostane nahradu.
// Nahrada je bud volny kandidat, nebo (rekurzivne) kandidat drzeny nekym dalsim
// - tim vznika augmentacni retez A<-B<-C, ktery vyresi konflikt bez duplicit.
function freeKey(key,scope,depth,visited,state){
    if(++state.steps>state.maxSteps){state.limitHit=true;return false;}
    const owner=scope.find(o=>o.ownsPick(key));
    // Klic drzi nekdo mimo scope (napr. uzamcena hlavni skupina pri react1) -
    // uvolnit ho nelze, jinak bychom hlasili uspech a pak marne volali take().
    if(!owner)return !(scope[0]&&scope[0].isBlocked(key));
    const e=owner.detach(key);
    if(!e)return false;
    if(owner.pickOne(false,visited)){owner.stats.redistributed++;return true;}
    if(depth<state.maxDepth){
        for(const m2 of owner.blockedOptions(visited)){
            const k2=pickKey(m2);
            visited.add(k2);
            if(freeKey(k2,scope,depth+1,visited,state)){
                if(owner.takeSpecific(m2,'prerozdeleni (retez)')){
                    owner.stats.redistributed++;owner.stats.deepRedistributed++;
                    return true;
                }
            }
            visited.delete(k2);
            if(state.limitHit)break;
        }
    }
    owner.restoreEntry(e);
    return false;
}

// Pokusi se vyresit konflikt globalniho dedupu prerozdelenim uvnitr 'scope'.
function tryRedistribute(p,scope,state){
    for(const m of p.blockedOptions(null)){
        if(state.steps>state.maxSteps){state.limitHit=true;return false;}
        const key=pickKey(m);
        const visited=new Set([key]);
        if(!freeKey(key,scope,1,visited,state))continue;
        const e=p.takeSpecific(m,'prerozdeleni');
        if(e){
            p.stats.redistributed++;
            if(visited.size>1)p.stats.deepRedistributed++;
            return true;
        }
    }
    return false;
}

// Driver: v kazdem kole si kazdy nenaplneny soubor vezme jeden zapas a poradi
// v kole je nahodna permutace - zadny soubor tak nema systematickou prednost.
// opts.redistributeScope omezuje, ci vybery se smi prerozdelovat (react1.json
// smi sahat jen na sve vlastni, hlavni skupina je v te chvili uzamcena).
export function runCoordinatedSelection(pickers,log=()=>{},opts={}){
    const scope=opts.redistributeScope||pickers;
    const registry=opts.registry||null;
    const state={steps:0,maxSteps:opts.maxSteps??REDIST_MAX_STEPS,maxDepth:opts.maxDepth??REDIST_MAX_DEPTH,limitHit:false};
    // Jedno kolo = normalni vyber + viceurovnove prerozdeleni. Duplicitni
    // fallback se NEpousti - ten je az uplne posledni zachrana.
    function fillRound(){
        for(;;){
            const pending=pickers.filter(p=>!p.isFull());
            if(pending.length===0)break;
            // Poradi v kole je nahodna permutace - ani prvni kolo tak nezvyhodnuje
            // soubor, ktery je v poli drive.
            const order=shuffle(pending.slice());
            let progress=false;
            for(const p of order){if(!p.isFull()&&p.pickOne())progress=true;}
            if(!progress)break;
        }
        // Faze 2 - viceurovnove prerozdeleni kandidatu (bez duplicit).
        for(;;){
            let progress=false;
            for(const p of shuffle(pickers.slice())){
                if(p.isFull())continue;
                if(tryRedistribute(p,scope,state)){progress=true;log(p.name+': konflikt vyresen prerozdelenim kandidatu');}
            }
            if(!progress)break;
        }
    }
    fillRound();
    // Faze 2b - postupne uvolneni globalniho limitu ligy (2 -> 3 -> 4 -> bez
    // limitu). Spousti se AZ kdyz uz ani prerozdeleni nedokaze soubory naplnit,
    // takze limit 2 zustava silnou preferenci, ne prekazkou naplneni.
    // Lokalni limit (1 zapas z ligy v jednom JSON) plati i po uvolneni.
    if(registry){
        while(pickers.some(p=>!p.isFull())){
            const missing=pickers.filter(p=>!p.isFull()).map(p=>p.name+' ('+p.selected.length+'/'+p.want+')').join(', ');
            if(!registry.relax('nenaplneno: '+missing))break;
            log('globalni limit ligy uvolnen na '+(registry.cap===Infinity?'bez limitu':registry.cap)+' - '+missing);
            const before=pickers.reduce((a,p)=>a+p.selected.length,0);
            fillRound();
            if(pickers.reduce((a,p)=>a+p.selected.length,0)===before&&registry.cap===Infinity)break;
        }
    }
    // Faze 3 - posledni zachrana: povolena duplicita dvojice zapas+tip napric
    // soubory. Zakaz stejneho zapasu v jednom tiketu plati i tady (ownFixtures).
    for(const p of pickers){
        while(!p.isFull()){
            const e=p.pickOne(true);
            if(!e)break;
            e.reason=state.limitHit
                ?'duplicita - hledani prerozdeleni doslo na limit, nedostatek NEOVEREN'
                :'duplicita - prokazany nedostatek unikatnich kandidatu';
            log(p.name+': FALLBACK '+e.reason+' - '+e.m.match+' | '+e.m.tip+' @ '+e.m.odds);
        }
    }
    return{pickers,limitHit:state.limitHit,steps:state.steps,capRelaxations:registry?registry.state.relaxations:[]};
}

// Pojistka pred zapisem react1.json: bez platneho fixtureId nedokaze SOCVERO
// naparovat DailyTip (ExternalMatchId) a vyhodnoceni Winning Examples spadne.
// Radeji nic nezapsat nez zapsat soubor bez ID.
export function assertReact1(picks){
    const errors=[];
    if(!Array.isArray(picks))errors.push('react1 neni pole');
    else{
        if(picks.length!==REACT1_PICK_COUNT)errors.push('ocekavano '+REACT1_PICK_COUNT+' zapasu, nalezeno '+picks.length);
        picks.forEach((p,i)=>{
            const id=p?.fixtureId;
            if(!Number.isInteger(id)||id<=0)errors.push('zaznam #'+(i+1)+' ('+(p?.match??'?')+') ma neplatne fixtureId: '+JSON.stringify(id));
        });
        const ids=picks.map(p=>p?.fixtureId).filter(id=>Number.isInteger(id)&&id>0);
        if(new Set(ids).size!==ids.length)errors.push('fixtureId nejsou unikatni: '+ids.join(', '));
    }
    if(errors.length){
        const msg='FATAL: react1.json neprosel validaci - '+errors.join('; ');
        console.error(msg);
        throw new Error(msg);
    }
    console.log('react1 validace OK: '+picks.length+' zapasu s unikatnim fixtureId');
}

async function main(){
    console.log('Kombik Bot - fetch-matches\n');

    // 1) Nacti existujici vybery jinych botu (fotbal.json, live2.json z GitHubu) -> dedup set
    const dedupSet=await loadDedupKeysFromUrls(DEDUP_URLS);
    console.log('Dedup klicu celkem: '+dedupSet.size+'\n');

    const leagueTypes=await getLeagueTypes();
    await sleep(450);

    const now=new Date(),max24h=new Date(now.getTime()+24*60*60*1000);
    console.log('Window: '+now.toUTCString()+' -> '+max24h.toUTCString()+' (24h)\n');
    const dates=new Set();
    for(let d=new Date(now);d<=max24h;d=new Date(d.getTime()+24*60*60*1000))dates.add(fmtDate(d));
    let fixtures=[];
    for(const d of dates){console.log('Fixtures '+d+'...');fixtures.push(...await getFixtures(d));await sleep(450);}
    console.log('   '+fixtures.length+' scheduled matches\n');
    if(fixtureFetchAttempts>0&&fixtureFetchErrors===fixtureFetchAttempts){console.error('FATAL: All /fixtures failed. key='+maskKey(API_KEY));process.exit(2);}
    fixtures=fixtures.filter(f=>{const t=new Date(f.fixture.date);return t>=now&&t<=max24h&&!EXCLUDED_COUNTRIES.has(f.league.country);});
    console.log('   '+fixtures.length+' in 24h window (excl. RU/BY)');
    const fixtureMap=new Map(),leagueMap=new Map();
    for(const f of fixtures){fixtureMap.set(f.fixture.id,f);const key=f.league.id+'_'+f.league.season;if(!leagueMap.has(key))leagueMap.set(key,{id:f.league.id,season:f.league.season,name:f.league.name,country:f.league.country,type:leagueTypes.get(f.league.id),tier:leagueTier(f.league.name,f.league.country,leagueTypes.get(f.league.id)),dates:new Set()});leagueMap.get(key).dates.add(fmtDate(new Date(f.fixture.date)));}
    console.log('   '+leagueMap.size+' leagues');
    // Diagnostika: prehled zarazeni soutezi do tieru (pro ladeni klasifikace)
    for(let t=0;t<=4;t++){
        const names=[...leagueMap.values()].filter(l=>l.tier===t).map(l=>l.name+' ('+l.country+')');
        if(names.length)console.log('   T'+t+(t===0?' [VYRAZENO]':'')+': '+names.join(', '));
    }
    // Vyrad mladez/zeny/rezervy/pratelaky uplne - nema smysl na ne platit /odds dotazy.
    for(const [k,lg] of leagueMap){if(lg.tier===0)leagueMap.delete(k);}
    console.log('   '+leagueMap.size+' leagues po vyrazeni\n');
    // Kandidati se sbiraji jen dvakrat - podle trhu, ne podle vystupniho souboru.
    // Kurzove pasmo je siroke (ODDS_COLLECT_MIN-MAX) a jednotlive vybery si z nej
    // pak ukrajuji svuj usek.
    const overCandidateMap=new Map();
    // Match Winner kandidati se klicuji kombinaci fixtureId|tip - jeden zapas
    // tak muze mit zaznam pro Home win i Away win.
    const winnerCandidateMap=new Map();
    // Sbiraji se VSECHNY platne kurzy daneho trhu (ne jen ty v pasmu) - orez pred
    // vypoctem driv posouval bestOdds dolu. Kurzove pasmo se aplikuje az na
    // vyslednou hodnotu. Jeden bookmaker smi prispet jen jednim kurzem na trh,
    // aby se duplicitni zaznamy v API nepocitaly vickrat.
    function addCandidate(map,mKey,lg,fix,odd,tip='Over 2.5',bookId){
        if(!map.has(mKey))map.set(mKey,{fixtureId:fix.fixture.id,leagueId:lg.id,season:lg.season,league:lg.name,country:lg.country,match:fix.teams.home.name+' - '+fix.teams.away.name,kickoff:fix.fixture.date,tip,tier:lg.tier,allOdds:[],books:new Set()});
        const c=map.get(mKey);
        const bk=bookId==null?'anon:'+c.allOdds.length:'bm:'+bookId;
        if(c.books.has(bk))return;
        c.books.add(bk);
        c.allOdds.push(odd);
    }
    for(const[,lg]of leagueMap){for(const d of lg.dates){const oddsData=await getLeagueOdds(lg.id,lg.season,d);for(const entry of oddsData){const fix=fixtureMap.get(entry.fixture?.id);if(!fix)continue;const mKey=fix.fixture.id;for(const bm of entry.bookmakers||[]){for(const bet of bm.bets||[]){for(const v of bet.values||[]){if(v.value!=='Over 2.5')continue;const odd=parseFloat(v.odd);if(isNaN(odd)||odd<=1)continue;addCandidate(overCandidateMap,mKey,lg,fix,odd,'Over 2.5',bm.id);}}}};await sleep(450);}}
    // Extra kolo /odds s bet=1 (Match Winner) - pouzito pro react1.json a react2.json,
    // ostatni soubory zustavaji ciste na Over 2.5.
    for(const[,lg]of leagueMap){for(const d of lg.dates){const oddsData=await getLeagueOdds(lg.id,lg.season,d,1);for(const entry of oddsData){const fix=fixtureMap.get(entry.fixture?.id);if(!fix)continue;for(const bm of entry.bookmakers||[]){for(const bet of bm.bets||[]){for(const v of bet.values||[]){const tip=v.value==='Home'?'Home win':(v.value==='Away'?'Away win':null);if(!tip)continue;const odd=parseFloat(v.odd);if(isNaN(odd)||odd<=1)continue;addCandidate(winnerCandidateMap,fix.fixture.id+'|'+tip,lg,fix,odd,tip,bm.id);}}}};await sleep(450);}}
    // Kurz = nejlepsi hodnota potvrzena aspon MIN_BOOKMAKERS sazkovkami. Nasbiralo
    // se siroke pasmo ODDS_COLLECT_MIN-MAX, jednotlive vybery si z nej ukrajuji
    // svuj usek - useky se prekryvaji, takze dobry zapas nevypadne kvuli kurzu.
    const overAll=[...overCandidateMap.values()].map(m=>({...m,odds:bestOdds(m.allOdds).toFixed(2)}));
    const winnerAll=[...winnerCandidateMap.values()].map(m=>({...m,odds:bestOdds(m.allOdds).toFixed(2)}));
    const inBand=(m,lo,hi)=>parseFloat(m.odds)>=lo&&parseFloat(m.odds)<=hi;
    // Home/Away win ma vlastni kurzove pasmo (WINNER_MIN_ODDS-WINNER_MAX_ODDS),
    // nezavisle na pasmu jednotlivych vyberu, ktere plati pro Over 2.5.
    const winnerBand=winnerAll.filter(m=>inBand(m,WINNER_MIN_ODDS,WINNER_MAX_ODDS));
    let pool=overAll.filter(m=>inBand(m,MIN_ODDS,MAX_ODDS));
    let tipsPool=overAll.filter(m=>inBand(m,TIPS_MIN_ODDS,TIPS_MAX_ODDS));
    let react1Pool=[...overAll.filter(m=>inBand(m,REACT1_MIN_ODDS,REACT1_MAX_ODDS)),...winnerBand];
    let react2Pool=[...overAll.filter(m=>inBand(m,MIN_ODDS,MAX_ODDS)),...winnerBand];
    console.log('Winner candidates: '+winnerBand.length+' (Home/Away win, odds '+WINNER_MIN_ODDS+'-'+WINNER_MAX_ODDS+')');
    console.log('React2 candidates: '+react2Pool.length+' (Over 2.5 '+MIN_ODDS+'-'+MAX_ODDS+' / Home-Away win '+WINNER_MIN_ODDS+'-'+WINNER_MAX_ODDS+')');
    console.log('React1 candidates: '+react1Pool.length+' (Over 2.5 '+REACT1_MIN_ODDS+'-'+REACT1_MAX_ODDS+' / Home-Away win '+WINNER_MIN_ODDS+'-'+WINNER_MAX_ODDS+')');
    console.log('Tips candidates: '+tipsPool.length+' (Over 2.5, odds '+TIPS_MIN_ODDS+'-'+TIPS_MAX_ODDS+')');
    console.log('Candidates: '+pool.length+' (Over 2.5, odds '+MIN_ODDS+'-'+MAX_ODDS+')');

    // 2) Odfiltruj zapasy, ktere uz vybral fotbal.json / live2.json
    if(dedupSet.size>0){
        const before=pool.length;
        const removed=[];
        pool=pool.filter(m=>{if(isDuplicate(m,dedupSet)){removed.push(m);return false;}return true;});
        console.log('Dedup: odstraneno '+(before-pool.length)+' duplicit (vs fotbal.json/live2.json)');
        for(const m of removed)console.log('   - DUP: '+m.match+' | '+m.league);
        const tipsBefore=tipsPool.length;
        tipsPool=tipsPool.filter(m=>!isDuplicate(m,dedupSet));
        console.log('Dedup (tips): odstraneno '+(tipsBefore-tipsPool.length)+' duplicit');
        const react1Before=react1Pool.length;
        react1Pool=react1Pool.filter(m=>!isDuplicate(m,dedupSet));
        console.log('Dedup (react1): odstraneno '+(react1Before-react1Pool.length)+' duplicit');
        const react2Before=react2Pool.length;
        react2Pool=react2Pool.filter(m=>!isDuplicate(m,dedupSet));
        console.log('Dedup (react2): odstraneno '+(react2Before-react2Pool.length)+' duplicit');
    }

    // 3) Golovy filtr - vyradi Over 2.5 v soutezich, kde historicky skoro nepada
    // 3+ golu. Pri nedostatku kandidatu se sam vypne, aby slaby den neznamenal
    // prazdny vyber.
    const goalStats=loadGoalStats();
    pool=applyGoalFilter(pool,goalStats,PICK_COUNT,'hot');
    tipsPool=applyGoalFilter(tipsPool,goalStats,TIPS_PICK_COUNT,'tips');
    react1Pool=applyGoalFilter(react1Pool,goalStats,REACT1_PICK_COUNT,'react1');
    react2Pool=applyGoalFilter(react2Pool,goalStats,Math.max(REACT2_PICK_COUNT,REACT3_PICK_COUNT),'react2+react3');

    // === Koordinovany vyber pro VSECHNY vystupy =============================
    // Vsechny soubory jsou rovnocenne: vyber bezi po kolech, v kazdem kole si
    // kazdy nenaplneny soubor vezme jeden zapas a poradi v kole je nahodne.
    // Zadny soubor tak nespotrebuje nejlepsi kandidaty jen proto, ze se driv
    // zapisuje. Vazeny los tieru, stropy, kvota jadra, preference golovych lig,
    // limit ligy/zeme i rozdeleni do skupin zustavaji beze zmeny.
    const isOver=m=>m.tip==='Over 2.5';
    const isWinner=m=>m.tip==='Home win'||m.tip==='Away win';
    const anyMarket=()=>true;
    const isRichOver=isGoalRichOver(goalStats);
    const always=()=>true;
    for(const [label,p] of [['hot',pool],['tips',tipsPool],['react1',react1Pool],['react2+react3',react2Pool]]){
        const t={1:0,2:0,3:0,4:0};
        for(const m of p)t[m.tier]=(t[m.tier]||0)+1;
        console.log('Pool '+label+': '+p.length+' tipu / '+new Set(p.map(m=>m.fixtureId)).size+' zapasu | Over 2.5='+p.filter(isOver).length+', Home/Away win='+p.filter(isWinner).length+' | T1='+t[1]+', T2='+t[2]+', T3='+t[3]+', T4='+t[4]+', jadro='+p.filter(isCore).length);
    }

    // Globalni dedup napric VSEMI vystupy je na urovni dvojice zapas+tip.
    // Stejny zapas se smi objevit ve vice souborech, ale pokazde s jinym tipem.
    const blockedPicks=new Set();
    // Globalni registr lig pro HLAVNI skupinu - drzi unikatni fixture na ligu
    // napric hot/tips-kombik/react2/react3. react1 ma vlastni registr, aby
    // nemohl zpetne ovlivnit uz dokoncene hlavni vystupy.
    const mainRegistry=createLeagueRegistry();
    const react1Registry=createLeagueRegistry();
    const mkPicker=(name,p,want,phases,coreMin,extra={})=>createPicker({name,pool:p,want,phases,coreMin,blockedPicks,goalStats,registry:mainRegistry,...extra});
    const hotPicker=mkPicker('hot.json',pool,PICK_COUNT,[
        {label:'Over 2.5',market:anyMarket,countsAs:always,want:PICK_COUNT},
    ],CORE_MIN_HOT);
    // tips-kombik.json si drzi puvodni preferencni logiku jadro -> T1+T2 -> T3+T4
    // (zadny vazeny los tieru), ale zustava rovnocennym clenem hlavni skupiny.
    const tipsPicker=mkPicker(TIPS_FILE,tipsPool,TIPS_PICK_COUNT,[
        {label:'Over 2.5',market:anyMarket,countsAs:always,want:TIPS_PICK_COUNT},
    ],CORE_MIN_TIPS,{tierStrategy:'preference'});
    const react1Picker=mkPicker('react1.json',react1Pool,REACT1_PICK_COUNT,[
        {label:'Over 2.5 (golove ligy)',market:isRichOver,countsAs:isOver,want:REACT1_MIN_OVER},
        {label:'Over 2.5 (ostatni ligy)',market:isOver,countsAs:isOver,want:REACT1_MIN_OVER},
        {label:'dobrani (oba trhy)',market:anyMarket,countsAs:always,want:REACT1_PICK_COUNT},
    // react1 ma vlastni registr (tvrdy limit si pocita jen sam za sebe), ale
    // navic dostava mainRegistry jako READ-ONLY presenceRegistry. Liga, ktera uz
    // ma v hlavnich ctyrech JSON zastoupeni, tim dostane mekkou penalizaci
    // (-20 % za 1 unikatni zapas, -35 % za 2 a vic), nikoli zakaz. Zapis do
    // mainRegistry se nedeje - hlavni skupina zustava nedotcena.
    ],CORE_MIN_REACT1,{priority:'vedlejsi skupina (nejnizsi priorita)',registry:react1Registry,presenceRegistry:mainRegistry});
    const react2Picker=mkPicker('react2.json',react2Pool,REACT2_PICK_COUNT,[
        {label:'Over 2.5 (golove ligy)',market:isRichOver,countsAs:isOver,want:REACT2_OVER_COUNT},
        {label:'Over 2.5 (ostatni ligy)',market:isOver,countsAs:isOver,want:REACT2_OVER_COUNT},
        {label:'Home/Away win',market:isWinner,countsAs:isWinner,want:REACT2_PICK_COUNT-REACT2_OVER_COUNT},
        {label:'dobrani (oba trhy)',market:anyMarket,countsAs:always,want:REACT2_PICK_COUNT},
    ],CORE_MIN_REACT2);
    const react3Picker=mkPicker('react3.json',react2Pool,REACT3_PICK_COUNT,[
        {label:'Over 2.5 (golove ligy)',market:isRichOver,countsAs:isOver,want:REACT3_MIN_OVER},
        {label:'Over 2.5 (ostatni ligy)',market:isOver,countsAs:isOver,want:REACT3_MIN_OVER},
        {label:'dobrani (oba trhy)',market:anyMarket,countsAs:always,want:REACT3_PICK_COUNT},
    ],CORE_MIN_REACT3);
    // Hlavni skupina = 4 rovnocenne soubory. Vyber bezi po kolech s nahodnym
    // poradim, takze zadny z nich nema prednost jen diky poradi generovani.
    const mainPickers=[hotPicker,tipsPicker,react2Picker,react3Picker];
    const pickers=[...mainPickers,react1Picker];
    console.log('\nHlavni skupina (rovnocenne): hot.json, '+TIPS_FILE+', react2.json, react3.json');
    console.log('Vedlejsi skupina (nejnizsi priorita, az po hlavni): react1.json');
    const mainRes=runCoordinatedSelection(mainPickers,msg=>console.log('   '+msg),{registry:mainRegistry});
    // Hlavni skupina je od teto chvile UZAMCENA - react1 smi prerozdelovat jen
    // sve vlastni vybery a prednostne bere zbyle unikatni kandidaty.
    console.log('\nHlavni skupina dokoncena a uzamcena, generuji react1.json...');
    const r1Res=runCoordinatedSelection([react1Picker],msg=>console.log('   '+msg),{redistributeScope:[react1Picker],registry:react1Registry});

    // === Diagnostika ========================================================
    // Jen cteni uz hotoveho vyberu - nespotrebovava zadna nahodna cisla.
    const occurrences=new Map();
    for(const p of pickers){
        for(const m of p.selected){
            if(!occurrences.has(m.fixtureId))occurrences.set(m.fixtureId,[]);
            occurrences.get(m.fixtureId).push({file:p.name,tip:m.tip});
        }
    }
    function report(p){
        const s=p.stats,sel=p.selected;
        const t=p.tierTotals;
        console.log('\n'+p.name+': '+sel.length+'/'+p.want+' zapasu | '+p.priority);
        console.log('   Kandidati: '+s.pool+' tipu / '+s.poolFixtures+' zapasu');
        console.log('   Rozlozeni tieru: T1='+t[1]+', T2='+t[2]+', T3='+t[3]+', T4='+t[4]);
        console.log('   Slozeni trhu: Over 2.5='+sel.filter(isOver).length+', Home/Away win='+sel.filter(isWinner).length);
        console.log('   Z jadra: '+sel.filter(isCore).length+'/'+p.want+' (kvota '+p.coreQuota+')');
        console.log('   Odmitnuto limity: liga(lokalne)='+s.rejLeague+', liga(globalne)='+s.rejLeagueGlobal+', zeme='+s.rejCountry+' | uvolneny strop tieru='+s.capRelaxed+', preskoceni faze='+s.phaseFallbacks);
        console.log('   Konflikty glob. dedupu: '+s.dedupConflicts+' | vyreseno prerozdelenim: '+s.redistributed+' (z toho viceurovnove: '+s.deepRedistributed+') | povolene duplicity: '+s.dupAllowed);
        for(const e of p.entries){
            const m=e.m;
            const others=(occurrences.get(m.fixtureId)||[]).filter(o=>o.file!==p.name).map(o=>o.file+':'+o.tip);
            console.log('   [T'+m.tier+'] '+m.league+' ('+m.country+') | '+m.match+' | '+m.tip+' @ '+m.odds
                +' | faze: '+e.phase
                +(e.capRelaxed?' | fallback: uvolneny strop tieru':'')
                +(e.duplicate?' | FALLBACK: duplicita dvojice zapas+tip':'')
                +(e.reason?' | '+e.reason:'')
                +(others.length?' | jinde: '+others.join(', '):''));
        }
        if(sel.length<p.want)console.log('WARNING: '+p.name+' ma mene nez '+p.want+' zapasu.');
    }
    for(const p of pickers)report(p);
    const sumStat=(list,k)=>list.reduce((a,p)=>a+p.stats[k],0);
    console.log('\nSouhrn koordinace:');
    console.log('   Hlavni skupina: '+mainPickers.map(p=>p.name).join(', '));
    console.log('   Vedlejsi skupina: '+react1Picker.name);
    console.log('   Konflikty glob. dedupu: hlavni='+sumStat(mainPickers,'dedupConflicts')+', react1='+react1Picker.stats.dedupConflicts);
    console.log('   Vyreseno prerozdelenim: hlavni='+sumStat(mainPickers,'redistributed')+', react1='+react1Picker.stats.redistributed);
    console.log('   Z toho viceurovnovych: hlavni='+sumStat(mainPickers,'deepRedistributed')+', react1='+react1Picker.stats.deepRedistributed);
    console.log('   Duplicitni fallbacky: hlavni='+sumStat(mainPickers,'dupAllowed')+', react1='+react1Picker.stats.dupAllowed);
    console.log('   Nedostatek kandidatu: hlavni='+(mainRes.limitHit?'NEOVEREN (limit hledani)':'prokazan')+', react1='+(r1Res.limitHit?'NEOVEREN (limit hledani)':'prokazan')+' | kroky: '+mainRes.steps+'/'+r1Res.steps);

    // === Diverzifikace lig ==================================================
    // Prehled je agregovany (TOP 10 \u002B souhrn), aby log nenarostl o stovky radku.
    const mainSel=new Map();
    for(const p of mainPickers){
        for(const m of p.selected){
            const k=leagueKey(m);
            let e=mainSel.get(k);
            if(!e){e={label:m.league+' ('+m.country+')',fixtures:new Set(),picks:0};mainSel.set(k,e);}
            e.fixtures.add(m.fixtureId);e.picks++;
        }
    }
    const availByLeague=new Map();
    for(const p of mainPickers){
        for(const [k,n] of p.leagueCounts)availByLeague.set(k,Math.max(availByLeague.get(k)||0,n));
    }
    const rows=[...mainSel.entries()].map(([k,e])=>({label:e.label,uniq:e.fixtures.size,picks:e.picks,avail:availByLeague.get(k)||0}))
        .sort((a,b)=>b.uniq-a.uniq||b.avail-a.avail);
    const mainSlots=mainPickers.reduce((a,p)=>a+p.selected.length,0);
    console.log('\nDiverzifikace lig (hlavni skupina):');
    console.log('   Zastoupeno lig: '+rows.length+' | unikatnich zapasu: '+new Set(mainPickers.flatMap(p=>p.selected.map(m=>m.fixtureId))).size+' | obsazenych slotu: '+mainSlots);
    console.log('   Lig s kandidaty celkem: '+availByLeague.size+' | globalni limit na konci: '+(mainRegistry.cap===Infinity?'bez limitu':mainRegistry.cap));
    for(const r of rows.slice(0,10))console.log('   '+r.label+': '+r.uniq+' unikatnich zapasu ('+r.picks+' tipu) z '+r.avail+' dostupnych');
    if(rows.length>10)console.log('   ... a dalsich '+(rows.length-10)+' lig po 1 zapase');
    const over=rows.filter(r=>r.uniq>LEAGUE_GLOBAL_CAP);
    console.log('   Nad zakladnim limitem '+LEAGUE_GLOBAL_CAP+': '+(over.length?over.map(r=>r.label+'='+r.uniq).join(', '):'zadna liga'));
    if(mainRes.capRelaxations.length){
        console.log('   Uvolneni globalniho limitu: '+mainRes.capRelaxations.length+'x');
        for(const r of mainRes.capRelaxations)console.log('      -> '+(r.cap===Infinity?'bez limitu':r.cap)+' | duvod: '+r.reason);
    }else console.log('   Uvolneni globalniho limitu: 0x (limit '+LEAGUE_GLOBAL_CAP+' staci)');
    if(r1Res.capRelaxations.length)console.log('   react1 uvolnil svuj limit '+r1Res.capRelaxations.length+'x (hlavni skupinu to neovlivnilo)');
    const unfilled=pickers.filter(p=>p.selected.length<p.want);
    console.log('   Nenaplnene soubory: '+(unfilled.length?unfilled.map(p=>p.name+' '+p.selected.length+'/'+p.want).join(', '):'zadny'));

    // hot.json pouziva stejny vyber jako driv, jen koordinovany s ostatnimi.
    const selected=hotPicker.selected;

    // === tips-kombik.json ===================================================
    // Pozor: soubor se JMENUJE tips-kombik.json, aby nekolidoval s tips.json,
    // ktery v repu spravuje jiny bot (generate_fotbal.py) s jinou strukturou.
    const tips=tipsPicker.selected.map(m=>({league:m.league,match:m.match,kickoff:m.kickoff,tip:m.tip,odds:m.odds}));
    writeFileSync(TIPS_FILE,JSON.stringify(tips,null,2),'utf-8');
    console.log('\n'+TIPS_FILE+' zapsan: '+tips.length+'/'+TIPS_PICK_COUNT+' zapasu ('+TIPS_MIN_ODDS+'-'+TIPS_MAX_ODDS+')');

    // === react1.json ========================================================
    // fixtureId je originalni ID zapasu z API-Football (fix.fixture.id). SOCVERO
    // podle nej paruje DailyTip (ExternalMatchId) bez hledani podle nazvu.
    const react1=react1Picker.selected.map(m=>({fixtureId:m.fixtureId,league:m.league,match:m.match,kickoff:m.kickoff,tip:m.tip,odds:m.odds}));
    assertReact1(react1);
    writeFileSync('react1.json',JSON.stringify(react1,null,2),'utf-8');
    console.log('react1.json zapsan: '+react1.length+'/'+REACT1_PICK_COUNT+' zapasu ('+REACT1_MIN_ODDS+'-'+REACT1_MAX_ODDS+')');

    // === react2.json ========================================================
    // Promichej, aby v tiketu nesly 3 Over za sebou a pak 3 vitezove - skupiny
    // by jinak vzdy vysly jako "cisty Over" vs "cisty vitez".
    const react2=balanceGroups(shuffle([...react2Picker.selected])).map(m=>({fixtureId:m.fixtureId,league:m.league,match:m.match,kickoff:m.kickoff,tip:m.tip,odds:m.odds,group:m.group}));
    writeFileSync('react2.json',JSON.stringify(react2,null,2),'utf-8');
    console.log('react2.json zapsan: '+react2.length+'/'+REACT2_PICK_COUNT+' zapasu ('+MIN_ODDS+'-'+MAX_ODDS+')');

    // === react3.json ========================================================
    const react3=balanceGroups(shuffle([...react3Picker.selected])).map(m=>({fixtureId:m.fixtureId,league:m.league,match:m.match,kickoff:m.kickoff,tip:m.tip,odds:m.odds,group:m.group}));
    writeFileSync('react3.json',JSON.stringify(react3,null,2),'utf-8');
    console.log('react3.json zapsan: '+react3.length+'/'+REACT3_PICK_COUNT+' zapasu ('+MIN_ODDS+'-'+MAX_ODDS+')');

    // === live1.json: vsechny zapasy z tieru 1 a 2 (bez omezeni poctu) ====
    const live1=pool.filter(m=>m.tier===1||m.tier===2).map(m=>({league:m.league,match:m.match,kickoff:m.kickoff,tip:m.tip,odds:m.odds}));
    writeFileSync('live1.json',JSON.stringify(live1,null,2),'utf-8');
    console.log('live1.json: '+live1.length+' matches (tier 1+2)');
    if(selected.length===0){writeFileSync('hot.json',JSON.stringify([],null,2),'utf-8');writeFileSync('best.json',JSON.stringify([],null,2),'utf-8');console.log('Zadne zapasy. ('+reqCount+' API req)');process.exit(0);}
    const best=[...selected].sort((a,b)=>parseFloat(b.odds)-parseFloat(a.odds)).slice(0,3).map(m=>({league:m.league,match:m.match,kickoff:m.kickoff,tip:m.tip,odds:m.odds}));
    writeFileSync('best.json',JSON.stringify(best,null,2),'utf-8');
    const grouped=balanceGroups(selected);
    // fixtureId je originalni ID zapasu z API-Football (fix.fixture.id) - stejne
    // jako v react2.json/react3.json, aby se vysledky daly parovat primo podle ID.
    const output=grouped.map(m=>({fixtureId:m.fixtureId,league:m.league,match:m.match,kickoff:m.kickoff,tip:m.tip,odds:m.odds,group:m.group}));
    writeFileSync('hot.json',JSON.stringify(output,null,2),'utf-8');
    console.log('\n'+output.length+' matches -> hot.json ('+reqCount+' API req)\n');
    const gc=Math.ceil(output.length/2);
    for(let g=1;g<=gc;g++){const gm=output.filter(m=>m.group===g);const go=gm.reduce((a,m)=>a*parseFloat(m.odds),1);console.log('  Gr.'+g+' ('+go.toFixed(2)+'):');gm.forEach(m=>console.log('     ['+m.league+'] '+m.match+' | '+m.tip+' @ '+m.odds+' | '+m.kickoff));}
}

// Spustit jen pri primem volani skriptu - pri importu (testy klasifikace) ne.
if(process.argv[1]&&process.argv[1].endsWith('fetch-matches.mjs')){
    if(!API_KEY){console.error('Chybi API_FOOTBALL_KEY1 env promenna.');process.exit(1);}
    main().catch(err=>{console.error('Chyba:',err);process.exit(1);});
}
