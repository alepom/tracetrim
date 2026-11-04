# Guida alla pubblicazione, passo per passo

L'ordine conta: i DOI si **riservano** prima, si inseriscono nei materiali, e solo alla fine si pubblica.

## 1. Controllo e pacchetti

```bash
cd ~/progetti/tracetrim
.venv/bin/python -m experiments.publication check
.venv/bin/python -m experiments.export_data
.venv/bin/python -m experiments.publication package
```

Il risultato è in `publication/packages/`:

```
1_paper/              tracetrim_paper.pdf, paper_sources.zip
2_software_and_data/  tracetrim_software.zip, tracetrim_dataset.zip
SHA256SUMS.txt
```

## 2. Repository GitHub

1. Crea il repository vuoto `alepom/tracetrim` (pubblico, **senza** README, licenza o .gitignore: ci sono già).
2. Dal progetto:
   ```bash
   cd ~/progetti/tracetrim
   git remote add origin git@github.com:alepom/tracetrim.git
   git push -u origin master --tags
   ```
   Vengono pubblicati anche i tag `protocol-v1`, `protocol-v2`, `protocol-v3`: dimostrano che i protocolli erano
   congelati prima delle esecuzioni.
3. Non creare ancora la release: la crei dopo aver inserito i DOI (passo 5).

## 3. Due bozze su Zenodo

Su <https://zenodo.org/uploads/new>, due volte. In entrambe: **Reserve DOI**, poi **Save draft**. **Non premere
Publish.**

### Bozza 1 — Paper

| Campo | Valore |
|---|---|
| Files | `1_paper/tracetrim_paper.pdf`, `1_paper/paper_sources.zip` |
| Resource type | Publication → Preprint |
| Title | `Agents Read More Than They Use: Learning Tool-Output Projections from Execution Traces, and Where They Fail` |
| Creators | Pomaro, Alessio — ORCID `0009-0008-6975-996X` — Affiliation `Independent Researcher` |
| License | Creative Commons Attribution 4.0 International |
| Keywords | `LLM agents`, `tool use`, `Model Context Protocol`, `context compression`, `tool outputs`, `hallucination`, `pre-registration` |
| Language | English |

**Description**:

```
Preprint, not peer reviewed.

Agents built on large language models read tool outputs written for programs: most of what an API returns never informs the agent's answer, yet it is paid for at every turn. We learn, from an agent's own execution traces, which fields of each tool output it uses, and remove the rest. On five agents (three on real APIs, two on simulated servers following public OpenAPI descriptions), a projection learned from seven runs and applied to an unseen one removed 63% to 77% of tool-output tokens in four cases and 12% in the fifth; in live runs with an expand tool for hidden fields, billed input tokens fell by 56% and 32% with every quality gate passed. Learning only from tool calls, as a gateway would, missed most of the fields agents use. Replays with projected outputs, under a protocol frozen in advance, located the failures precisely: values the agent transforms before writing them, fields it uses only to reason, and, without an identifier field, agents inventing identifiers. A second pre-registered study showed that two safeguards, keeping identifying fields and recognising transformed values, removed those losses while keeping savings of 46% to 61%; a post-hoc check found a fourth mechanism, translated values, not covered. We release the library, with a bilingual telemetry report and an MCP proxy that applies a reviewed projection in front of any server.

The author set the research question and the constraints of the study; an AI coding agent (Claude Code) proposed the protocols, wrote the code, ran the experiments and drafted the manuscript under the author's direction. See the "AI assistance and authorship" section of the paper.
```

### Bozza 2 — Software e dati

| Campo | Valore |
|---|---|
| Files | `2_software_and_data/tracetrim_software.zip`, `2_software_and_data/tracetrim_dataset.zip`, `SHA256SUMS.txt` |
| Resource type | Software |
| Title | `tracetrim 0.2.0: learn which fields of tool outputs an agent uses, and trim the rest (code, protocols and data)` |
| Creators | come sopra |
| License | MIT (codice); nel campo descrizione si indica CC BY 4.0 per dati e documentazione (vedi `LICENSES.md`) |
| Version | `0.2.0` |
| Related works | *is supplement to* → il DOI della bozza 1 |

**Description**: il primo paragrafo del README, più: "The dataset contains full traces of the literature-scan case
(OpenAlex, CC0) and of the two simulated cases, and derived per-call records for the two cases built on the
DataForSEO API, whose results are not redistributed. See LICENSES.md."

## 4. Inserire i DOI

In `src/tracetrim/about.py`: `REPOSITORY`, `PAPER_DOI`, `SOFTWARE_DOI`, `LICENSE = "MIT"`. In `CITATION.cff`:
`repository-code`, `doi` (software) e `doi` della `preferred-citation` (paper). Nell'articolo: i due link.

Poi:

```bash
.venv/bin/python -m experiments.paper_numbers
(cd paper && ~/.local/bin/tectonic -X compile main.tex)
git commit -am "DOIs and repository" && git push
.venv/bin/python -m experiments.publication package
```

Sostituisci i file nelle due bozze Zenodo con quelli nuovi (il PDF ora riporta i DOI).

## 5. Pubblicare

1. Su GitHub crea la release `v0.2.0` dal tag omonimo, allegando `tracetrim_dataset.zip`.
2. Su Zenodo premi **Publish** sulla bozza 2, poi sulla bozza 1.
3. Pubblica l'articolo con i link definitivi.

## Verifiche finali

- I DOI si risolvono (`https://doi.org/...`).
- `SHA256SUMS.txt` corrisponde ai file caricati.
- Nel repository pubblico non ci sono `traces/`, `out/`, `legal/` né `.env` (sono in `.gitignore`).
