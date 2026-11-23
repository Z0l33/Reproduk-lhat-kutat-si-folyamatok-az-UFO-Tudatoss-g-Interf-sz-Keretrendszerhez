# Reprodukálható kutatási folyamat — UFO Tudatosság–Interfész Keretrendszer

Angol változat: [README.en.md](README.en.md)

Ez a csomag a keretrendszer empirikus állításait **ellenőrizhető** eljárásokká alakítja. Magáról a
keretrendszerről semmilyen eredményt nem tartalmaz, és nem is szabad úgy olvasni: az adatkészlet-jegyzék
üres, a mellékelt adatkészletek pedig kizárólag szintetikus tesztadatok.

## Mit csinál

| Lépés | Modul | Garancia |
|---|---|---|
| Letöltés | `zenodo.py` | Csak a `data/manifest/datasets.json`-ban felsorolt rekordokból tölt le. A verzió-DOI-nak `10.5281/zenodo.<record_id>` alakúnak kell lennie, és a Zenodo API-nak ugyanazt az azonosítót és DOI-t kell visszaadnia. |
| Integritás | `hashing.py`, `zenodo.py` | Minden letöltést ellenőriz a **Zenodo által közölt** ellenőrzőösszeggel (tudomásom szerint MD5; azzal az algoritmussal ellenőrzi, amelyet az API megnevez) **és** a jegyzékben **rögzített SHA-256-tal**. A sikertelen fájlt törli, nem tartja meg. |
| Származás (provenance) | `provenance.py` | Adatkészletenként egy JSON (`provenance/<dataset_id>.json`): rekord, DOI, időbélyegek (az első letöltés is), fájlméret, SHA-256, forrás-ellenőrzőösszeg, ellenőrzési állapot, eszközverzió. |
| Validáció | `validation.py` | Oszlop-, típus-, tartomány-, hiányzóérték- és duplikátum-ellenőrzés. |
| Szivárgás | `validation.py` | Alany több csoportban; alany vagy azonos sor a tanító és a teszthalmazban; a teszt nem későbbi a tanításnál (időoszlop esetén); az eredmény közel tökéletes „proxy” változói. Bármely találat letiltja az elemzést. |
| Statisztika | `stats.py` | Hedges-g, percentilis bootstrap CI, permutációs p-érték (rögzített seed, determinisztikus), ítélet az előzetesen regisztrált SESOI-hoz képest. |
| Bizonyíték-besorolás | `evidence.py` | Az egyetlen hely, ahol eldől, mi számít pozitív bizonyítéknak. |

## Feltáró és megerősítő elemzés

Egy elemzés csak akkor **megerősítő (konfirmatív)**, ha *mindez* teljesül; különben **feltáró (exploratív)**, vagyis hipotézisgeneráló, és sosem bizonyíték:

1. van teljes előzetes regisztrációs (prereg) fájl;
2. van benne külső `registry_id` (nem üres, nem helyőrző);
3. a `registered_at` korábbi, mint az adatkészlet *első letöltése*;
4. minden elemzési paraméter (csoportok, kimenet, irány, alfa, SESOI, permutációk, bootstrap, seed, replikációs követelmény) megegyezik a regisztrálttal;
5. arra az adatkészletre hivatkozik, amelyre regisztrálták.

## Ami soha nem számíthat pozitív bizonyítéknak

Érvénytelen adat · adatszivárgás · nem ellenőrzött adatkészlet · szintetikus adat · feltáró elemzés · nulla eredmény · a SESOI-n belül nullával egyenértékű hatás · szignifikáns, de a SESOI-t a CI nem haladja meg · rossz irányú hatás · sikertelen replikáció · érvénytelen („nem független”) replikáció. Csak a `confirmatory_positive_replicated` és (ha a prereg lemond a replikációról) a `confirmatory_positive_unreplicated` számít. Alapértelmezés szerint a replikáció **kötelező**, ezért egyetlen megerősítő eredmény `awaiting_independent_replication` állapotú, és nem számít.

A replikáció csak akkor érvényes, ha más adatkészletet **és** más Zenodo-rekordot **és** eltérő fájltartalmat használ, maga is előzetesen regisztrált (`replicates_prereg_sha256` az eredetire mutat), és megerősítő jellegű. Ugyanazon adat újrafuttatása „érvénytelen”, nem replikáció.

## Használat

```bash
pip install -e ".[dev]"
python -m pytest -q                                     # tesztek (offline)
python -m ufo_pipeline validate-manifest                # a data/manifest/datasets.json ellenőrzése
python -m ufo_pipeline fetch                            # hálózat + valódi jegyzékbejegyzések kellenek
python -m ufo_pipeline analyze --config configs/sajat.json --out results
python -m ufo_pipeline replicate --original results/a.json --replication results/b.json --out results/a.replikacio.json
python -m ufo_pipeline synthetic --scenario true_effect # kizárólag szintetikus TESZTadat
```

Valódi adatkészlet munkafolyamata: bejegyzés a jegyzékbe → `fetch` (állapot: `unpinned`; a fájl átnézése után jegyezd fel a jelentett SHA-256-ot) → rögzítsd ezt a SHA-256-ot a jegyzékben → újra `fetch` (`verified` kell legyen) → írd meg és regisztráld külső helyen a preregisztrációt → másold a `configs/analysis.TEMPLATE.json`-t, `analyze`.

## Korlátok (olvasd el)

- **Élő Zenodóval nem tesztelt.** A letöltést kizárólag hamis API-válaszokkal fejlesztettem és teszteltem (a mezőnevek a Zenodo records API-ját követik, ahogy ismerem: `id`, `doi`, `conceptdoi`, `files[].key/size/checksum/links.self`). Futtasd egyszer a `verify-datasets` munkafolyamatot egy valódi rekorddal, és jelezd az eltéréseket.
- A tárolón belüli prereg-időbélyeg önbevallás. Regisztrálj külső helyen (OSF, Zenodo-feltöltés…), és az azonosítót írd a `registry_id`-ba; a kód nem tudja ellenőrizni, hogy a regisztráció létezik-e, vagy hogy az adat valóban ismeretlen volt-e.
- Első bizalom elve (trust-on-first-use): a SHA-256-rögzítés annyit ér, amennyire alaposan átnézted a fájlt rögzítéskor.
- A statisztika két csoport folytonos kimenetének összehasonlítását fedi le. Más elrendezéshez új, előre regisztrált elemzőkód kell.
- A szivárgásészlelés a felsorolt mintákat találja meg; a találat hiánya nem bizonyítja a szivárgás hiányát.
- A statisztikai ítélet módszertani kapu. Az, hogy átmegy rajta, semmit nem mond arról, *miért* létezik a hatás.
