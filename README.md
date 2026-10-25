# Vládní kontrakty USA a akcie

Program každou hodinu hledá nově udělené vládní kontrakty USA (a navýšení existujících kontraktů) od 500 000 $, přiřadí k firmám burzovní tickery a sleduje, jak se od udělení kontraktu vyvíjí cena akcie. Výsledky zveřejňuje jako webovou stránku a jako Excel ke stažení.

## Co program dělá

| Úloha | Jak často | Zdroj |
|---|---|---|
| Oznámení kontraktů ministerstva obrany (Department of War) | každou hodinu | war.gov (dříve defense.gov), kontrakty nad 7,5 mil. $, zveřejněno týž den |
| Oznámení o udělení zakázek všech federálních úřadů | každé 3 hodiny | SAM.gov API (potřebuje bezplatný klíč, viz krok 6) |
| Kontrakty všech federálních úřadů | každou hodinu | USAspending.gov API (DoD zde se zpožděním cca 90 dní) |
| Kontrakty oznámené samotnými firmami | každou hodinu | SEC EDGAR, hlášení 8-K (ticker je jistý, částka vyčtená z textu) |
| Doplnění mateřské společnosti a popisu | každou hodinu | USAspending.gov API |
| Ceny akcií (aktuální cena, cena při udělení, graf) | každou hodinu | Yahoo Finance (zpoždění cca 15 min) |
| Seznam burzovně obchodovaných firem (directory) | denně | SEC EDGAR + ruční seznam zahraničních kotací |

Kontrakt, který se nejdřív objeví v oznámení DoD nebo na SAM.gov, se později automaticky doplní o údaje z USAspending (spárování podle čísla kontraktu a čísla modifikace). Pokud firma kontrakt sama ohlásí v 8-K a uvede v něm číslo kontraktu, připojí se odkaz na hlášení k existujícímu řádku. Jinak se hlášení zobrazí jako samostatný řádek typu „Hlášení 8-K“. Ve sloupci **Zdroje** vidíš, odkud záznam pochází.

**Nový kontrakt vs. navýšení:** sloupec **Typ** rozlišuje „Nový“ a „Navýšení“ (modifikace existujícího kontraktu, včetně čísla modifikace, např. P00070). Pokud je původní kontrakt také v našem seznamu, navýšení má značku **navazuje**.

## Bezpečnost

- Program běží **výhradně na serverech GitHubu** (GitHub Actions, virtuální počítač, který se po každém běhu smaže). S tvým počítačem nemá žádné spojení a nic na něj neinstaluje.
- Nikdy nepřidávej tzv. „self-hosted runner“ (Settings > Actions > Runners). To je jediný způsob, jak by GitHub Actions mohly běžet na tvém počítači. Program ho nepotřebuje.
- Jediný klíč, který program používá, je volitelný klíč pro SAM.gov. Ukládá se do **GitHub Secrets**, kam nikdo kromě tebe nevidí (ani v logu běhu se nezobrazí). Do souborů v repozitáři nikdy nevkládej hesla ani soukromé údaje, repozitář je veřejný.
- Workflowy se spouštějí jen podle plánu nebo tvým tlačítkem. Cizí lidé je spustit nemohou a návrhy změn (pull requesty) od cizích lidí se samy neprovedou.
- Používáme jen oficiální akce od GitHubu (`actions/...`).

## Instalace krok za krokem

### 1. Vytvoř repozitář
1. Na GitHubu vpravo nahoře klikni na **+** > **New repository**.
2. Název např. `vladni-kontrakty`, zvol **Public**, nic dalšího nezaškrtávej a klikni **Create repository**.

### 2. Nahraj soubory
1. Rozbal stažený ZIP do složky v počítači.
2. Na stránce nového repozitáře klikni na odkaz **uploading an existing file**.
3. Přetáhni do okna **celý obsah** rozbalené složky (všechny soubory i složky `src`, `web`, `data`, `.github`).
4. Dole klikni **Commit changes**.
5. Zkontroluj, že v repozitáři je složka `.github/workflows` se dvěma soubory. Na Macu je složka `.github` ve Finderu skrytá (zobrazíš ji zkratkou Cmd+Shift+tečka). Pokud chybí, vytvoř soubory ručně: **Add file > Create new file**, jako název napiš `.github/workflows/kontrakty.yml`, vlož obsah stejnojmenného souboru ze ZIPu a ulož. Totéž pro `tickery.yml`.

### 3. Uprav kontakt pro SEC
SEC vyžaduje u stahování seznamu tickerů kontakt. Otevři v repozitáři `config.yaml`, klikni na tužku a do `sec_kontakt` vlož svou anonymní adresu z GitHubu (najdeš ji v **Settings > Emails**, vypadá jako `12345678+jmeno@users.noreply.github.com`), např.:
```
sec_kontakt: "GovContractTracker 12345678+jmeno@users.noreply.github.com"
```
Klikni **Commit changes**.

### 4. Zapni webovou stránku (GitHub Pages)
**Settings > Pages > Build and deployment > Source:** vyber **GitHub Actions**.

### 5. Nastav oprávnění pro Actions
**Settings > Actions > General:**
- **Actions permissions:** zvol „Allow *uživatel*, and select non-*uživatel*, actions and reusable workflows“ a zaškrtni **Allow actions created by GitHub**. Ulož.
- **Workflow permissions:** zvol **Read and write permissions**. Ulož.

### 6. API klíč pro SAM.gov (volitelné, doporučeno)
Bez klíče program běží dál, jen se SAM.gov přeskočí (v hlavičce webu bude „SAM.gov: neaktivní“).
1. Přihlas se na **sam.gov** (vpravo nahoře *Sign In*, přihlášení přes Login.gov, registrace je zdarma).
2. Klikni na svůj profil > **Account Details** > v části **API Key** zadej heslo z Login.gov a klikni **Request API Key**. Klíč si zkopíruj.
3. V repozitáři na GitHubu: **Settings > Secrets and variables > Actions > New repository secret**.
4. Name: `SAM_API_KEY`, Secret: vložený klíč. Klikni **Add secret**.

Platnost klíče SAM.gov je omezená (obvykle 90 dní). Když vyprší, uvidíš v hlavičce webu u SAM.gov chybu. Stačí si vygenerovat nový klíč a v kroku 3 ho přepsat (u secretu tlačítko *Update*). Osobní klíč má malý denní limit dotazů, proto se SAM.gov kontroluje jen každé 3 hodiny.

### 7. První spuštění
1. Otevři záložku **Actions** (pokud se zobrazí výzva k povolení workflowů, potvrď ji).
2. Vlevo vyber **Tickery (denně)** > **Run workflow** > **Run workflow**. Počkej, až doběhne (zelená fajfka, cca 1 minuta).
3. Pak vyber **Kontrakty (každou hodinu)** > **Run workflow**. První běh trvá několik minut.
4. Dál už vše běží samo: kontrakty a ceny každou hodinu, tickery jednou denně.

### 8. Kde najdeš výsledky
- **Web:** `https://<tvoje-jmeno>.github.io/vladni-kontrakty/` (přesnou adresu uvidíš v **Settings > Pages**)
- **Excel:** `https://<tvoje-jmeno>.github.io/vladni-kontrakty/kontrakty.xlsx` (tlačítko „Stáhnout Excel“ na webu). List *Kontrakty*, list *Tickery* (directory) a list *Stav*.

## Úpravy

| Co chceš změnit | Soubor |
|---|---|
| Minimální částka, jak daleko do historie, jak dlouho držet záznamy, jak často se ptát SAM.gov | `config.yaml` |
| Ruční přiřazení firmy k tickeru (dceřiné firmy, opravy chybné shody, označení soukromé firmy) | `aliasy.csv` |
| Další zahraniční burzy a firmy do directory | `zahranicni_tickery.csv` |

Soubory upravíš přímo na GitHubu (tužka > Commit changes). Změna se projeví při dalším hodinovém běhu, a to i zpětně u již nalezených kontraktů.

**Typ shody tickeru** ve výstupu: `alias` (ruční seznam), `přesná` (název souhlasí), `začátek názvu` (např. „Lockheed Martin Rotary...“ > Lockheed Martin), `podobnost` (velmi podobný název). U posledních dvou doporučujeme občas zkontrolovat a případné chyby opravit v `aliasy.csv`.

## Co je dobré vědět

- **Zpoždění USAspending:** civilní úřady se objevují v řádu dnů, kontrakty ministerstva obrany cca 90 dní po udělení. Proto kombinujeme se zdrojem DoD, který je okamžitý.
- **Oznámení DoD** obsahují jen kontrakty nad 7,5 mil. $. U kontraktů pro více firem najednou (sdílený strop) je uvedena celková částka stropu, ne částka pro jednu firmu (je to vyznačeno v poznámce).
- **Hlášení 8-K** píšou firmy volnou formou (tisková zpráva). Program vybere větu, která zmiňuje vládní úřad, kontrakt a částku. Občas tak zachytí i něco jiného (např. souhrn za čtvrtletí), proto má typ „Hlášení 8-K“ vlastní filtr a odkaz na původní dokument.
- **Ceny akcií** pochází z Yahoo Finance přes neoficiální rozhraní. Občas může být dočasně nedostupné; program pak ponechá poslední známé hodnoty. Ceny jsou v měně dané burzy (u Londýna v pencích).
- **Plánovač GitHubu** může hodinový běh o několik minut zpozdit, při velkém vytížení i víc.
- GitHub vypíná plánované běhy v repozitáři, ve kterém se 60 dní nic nedělo. Program ale každou hodinu ukládá data, takže by k tomu nemělo docházet. Pokud by se to stalo, v záložce Actions stačí workflow znovu povolit.
- Stav jednotlivých zdrojů (OK / chyba) vidíš v hlavičce webu a na listu *Stav* v Excelu.

## Struktura projektu

```
.github/workflows/kontrakty.yml   hodinový běh (kontrakty, ceny, web)
.github/workflows/tickery.yml     denní aktualizace tickerů
src/                              program v Pythonu
web/index.html                    šablona webové stránky
data/                             uložená data (program je sám aktualizuje)
config.yaml                       nastavení
aliasy.csv                        ruční přiřazení firem k tickerům
zahranicni_tickery.csv            zahraniční kotace
```
