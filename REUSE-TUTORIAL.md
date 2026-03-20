# Gestire le licenze con REUSE

Guida pratica per rendere un progetto conforme alla specifica [REUSE 3.3](https://reuse.software/spec-3.3/).

REUSE richiede che ogni file tracciato in git abbia informazioni di copyright e licenza associate. Le informazioni possono essere fornite in tre modi: header inline nel file sorgente, file `.license` adiacente, oppure `REUSE.toml` per annotazioni bulk.

## Installazione

Con uv:

```bash
uv add --dev reuse
```

Con pip:

```bash
pip install reuse
```

## 1. Scaricare il testo della licenza

REUSE richiede che il testo completo della licenza sia presente nella directory `LICENSES/` alla radice del progetto, con nome `<SPDX-ID>.txt`.

```bash
reuse download ISC
```

Questo crea `LICENSES/ISC.txt`. Gli identificatori SPDX validi sono elencati su https://spdx.org/licenses/. Per progetti multi-licenza, scaricare tutte le licenze necessarie:

```bash
reuse download MIT
reuse download Apache-2.0
```

## 2. Annotare i file sorgente

Per i file che supportano commenti (Python, YAML, shell, JavaScript, ecc.), `reuse annotate` aggiunge automaticamente un header con copyright e licenza.

```bash
reuse annotate --copyright="Nome Cognome <email@example.com>" --license="ISC" --year=2026 file.py
```

Si possono annotare piu' file alla volta:

```bash
reuse annotate --copyright="Nome Cognome <email@example.com>" --license="ISC" --year=2026 src/*.py tests/*.py
```

Il risultato e' un header come questo all'inizio del file:

```python
# SPDX-FileCopyrightText: 2026 Nome Cognome <email@example.com>
#
# SPDX-License-Identifier: ISC
```

La sintassi del commento si adatta al tipo di file (Python usa `#`, JavaScript usa `//`, HTML usa `<!-- -->`).

### File non riconosciuti

Alcuni formati (come `.mdx`) non vengono riconosciuti automaticamente. In quel caso, specificare lo stile:

```bash
reuse annotate --style=html --copyright="Nome Cognome <email@example.com>" --license="ISC" --year=2026 file.mdx
```

## 3. Annotare file senza supporto per commenti

File binari, config, lock file, e altri formati che non supportano commenti (`.gitignore`, `pyproject.toml`, `uv.lock`, `.json`, `.svg`, ecc.) non possono avere header inline. Per questi si usa `REUSE.toml`, un file da creare a mano nella radice del progetto.

`reuse annotate` non genera e non modifica `REUSE.toml`. Il tool si occupa solo degli header nei file sorgente. `REUSE.toml` va scritto manualmente, ed e' l'unico modo per associare licenze a file che non supportano commenti.

### Come capire quali file servono

Lanciare `reuse lint` dopo aver annotato tutti i sorgenti. L'output elenca i file ancora privi di informazioni di licenza sotto `MISSING COPYRIGHT AND LICENSING INFORMATION`. Quelli sono i file da inserire in `REUSE.toml`.

### Struttura del file

Il campo `version` e' obbligatorio e deve essere `1`. Le annotazioni vanno in blocchi `[[annotations]]`, ciascuno con un `path` (stringa singola o lista), il copyright e la licenza.

```toml
version = 1

[[annotations]]
path = [
    ".gitignore",
    "pyproject.toml",
    "uv.lock",
    "*.json",
]
SPDX-FileCopyrightText = "2026 Nome Cognome <email@example.com>"
SPDX-License-Identifier = "ISC"

[[annotations]]
path = "assets/**"
SPDX-FileCopyrightText = "2026 Nome Cognome <email@example.com>"
SPDX-License-Identifier = "ISC"
```

### Regole sui path

- `*` matcha qualsiasi cosa dentro una singola directory (es. `*.json` matcha `package.json` ma non `src/data.json`)
- `**` matcha ricorsivamente attraverso le directory (es. `docs/**` matcha tutto dentro `docs/` a qualsiasi profondita')
- I path sono relativi alla radice del progetto
- Un blocco puo' avere un singolo path come stringa o una lista di path

### Quando aggiornare REUSE.toml

Ogni volta che si aggiunge un file tracciato in git che non supporta commenti, va inserito in `REUSE.toml`. Il modo piu' semplice e' lanciare periodicamente `reuse lint` e aggiungere i file segnalati come mancanti.

## 4. Verificare la conformita'

```bash
reuse lint
```

Se il progetto e' conforme, l'output termina con:

```
Congratulations! Your project is compliant with version 3.3 of the REUSE Specification :-)
```

Altrimenti, elenca i file mancanti sotto `MISSING COPYRIGHT AND LICENSING INFORMATION`.

## 5. Integrare nel CI

Aggiungere un job al workflow GitHub Actions:

```yaml
reuse:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v6
    - uses: fsfe/reuse-action@v5
```

Questo verifica la conformita' ad ogni push senza bisogno di installare `reuse` nel progetto.

## Casi particolari

### Progetti multi-licenza

Ogni file puo' avere una licenza diversa. Basta specificare l'identificatore corretto nell'header o nel `REUSE.toml`:

```bash
reuse annotate --copyright="Nome Cognome <email@example.com>" --license="MIT" src/lib.py
reuse annotate --copyright="Nome Cognome <email@example.com>" --license="Apache-2.0" src/util.py
```

### Piu' titolari di copyright

Un file puo' avere piu' righe di copyright:

```python
# SPDX-FileCopyrightText: 2024 Alice <alice@example.com>
# SPDX-FileCopyrightText: 2026 Bob <bob@example.com>
#
# SPDX-License-Identifier: ISC
```

### File di terze parti

Se il progetto include file con licenze diverse (font, librerie vendored), scaricare la licenza corrispondente e annotare quei file con la licenza appropriata:

```bash
reuse download OFL-1.1
reuse annotate --copyright="Font Author" --license="OFL-1.1" fonts/example.ttf
```

## Riferimenti

- Specifica REUSE 3.3: https://reuse.software/spec-3.3/
- Tutorial ufficiale: https://reuse.software/tutorial/
- FAQ: https://reuse.software/faq/
- Lista licenze SPDX: https://spdx.org/licenses/
