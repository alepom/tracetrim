# Leggimi prima

Questa cartella contiene tutto quello che serve per pubblicare lo studio
**"Agents Read More Than They Use: Learning Tool-Output Projections from Execution Traces, and Where They Fail"**
e la libreria **tracetrim 0.3.4**. Il repository GitHub e le bozze Zenodo esistono già, privati; nulla è pubblico.
I passi che restano sono in un file locale, non pubblicato: `.private/ULTIMI_PASSI_IT.md`. Valgono su qualunque altra
indicazione.

## In che ordine leggere

| # | File | Perché |
|---|---|---|
| 1 | [`../paper/main.pdf`](../paper/main.pdf) | Il paper. |
| 2 | [`GUIDA_PUBBLICAZIONE_IT.md`](GUIDA_PUBBLICAZIONE_IT.md) | I testi da incollare nelle schede Zenodo (i passi su GitHub sono superati da `.private/ULTIMI_PASSI_IT.md`). |
| 3 | [`articolo_divulgativo_IT.md`](articolo_divulgativo_IT.md) | L'articolo per il tuo pubblico (DOI già inseriti). |
| 4 | [`AZIONI_RICHIESTE_ALL_AUTORE.md`](AZIONI_RICHIESTE_ALL_AUTORE.md) | Le decisioni già prese, per riferimento. |
| 5 | [`../docs/GUIDA_VIDEO_PLAYGROUND_IT.md`](../docs/GUIDA_VIDEO_PLAYGROUND_IT.md) | La guida al video con il proxy e il Playground. |

## Comandi utili (dalla cartella del progetto)

```bash
.venv/bin/python -m experiments.publication check      # segreti, dominio cliente, URL di terzi, segnaposto
.venv/bin/python -m experiments.export_data            # rigenera dataset/ (tracce e record derivati)
.venv/bin/python -m experiments.publication package    # zip per Zenodo + SHA256SUMS.txt
```

Il paper si ricompila con `cd paper && ~/.local/bin/tectonic -X compile main.tex`, dopo aver
rigenerato numeri e grafici con `.venv/bin/python -m experiments.paper_numbers` e
`.venv/bin/python -m experiments.paper_figures` (con `PYTHONPATH=src`).
