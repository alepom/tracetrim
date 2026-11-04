# Leggimi prima

Questa cartella contiene tutto quello che serve per pubblicare lo studio
**"Agents Read More Than They Use: Learning Tool-Output Projections from Execution Traces, and Where They Fail"**
e la libreria **tracetrim 0.3.0**. Nulla è stato pubblicato: repository, record Zenodo e release li crei tu.

## In che ordine leggere

| # | File | Perché |
|---|---|---|
| 1 | [`../paper/main.pdf`](../paper/main.pdf) | Il paper. |
| 2 | [`AZIONI_RICHIESTE_ALL_AUTORE.md`](AZIONI_RICHIESTE_ALL_AUTORE.md) | Le poche decisioni che spettano a te. |
| 3 | [`GUIDA_PUBBLICAZIONE_IT.md`](GUIDA_PUBBLICAZIONE_IT.md) | I passi su GitHub e Zenodo, con i testi da incollare. |
| 4 | [`articolo_divulgativo_IT.md`](articolo_divulgativo_IT.md) | L'articolo per il tuo pubblico (i DOI vanno inseriti dopo). |
| 5 | [`../docs/GUIDA_VIDEO_PLAYGROUND_IT.md`](../docs/GUIDA_VIDEO_PLAYGROUND_IT.md) | La guida al video con il proxy e il Playground. |

## Comandi utili (dalla cartella del progetto)

```bash
.venv/bin/python -m experiments.publication check      # segreti, dominio cliente, URL di terzi, segnaposto
.venv/bin/python -m experiments.export_data            # rigenera dataset/ (tracce e record derivati)
.venv/bin/python -m experiments.publication package    # zip per Zenodo + SHA256SUMS.txt
```

Il paper si ricompila con `cd paper && ~/.local/bin/tectonic -X compile main.tex`, dopo aver
rigenerato i numeri con `.venv/bin/python -m experiments.paper_numbers`.
