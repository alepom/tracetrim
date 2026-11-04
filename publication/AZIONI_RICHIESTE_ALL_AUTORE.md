# Azioni richieste all'autore

Elenco breve. Le analisi e i pacchetti non dipendono da queste risposte: servono per paper, `CITATION.cff`, schede
Zenodo e repository.

## Già ricavato (da confermare)

| # | Voce | Valore applicato | Fonte | Conferma |
|---|---|---|---|---|
| 1 | Autore | Alessio Pomaro, unico autore | paper precedente | ☐ |
| 2 | Affiliazione | Independent Researcher | paper precedente | ☐ |
| 3 | ORCID | 0009-0008-6975-996X | paper precedente | ☐ |
| 4 | Email pubblica | info@alessiopomaro.it (in `CITATION.cff`) | paper precedente | ☐ |
| 5 | Licenze | MIT per il codice; CC BY 4.0 per paper, documentazione, risultati e dataset | come nel paper precedente | ☐ |
| 6 | Finanziamenti e conflitti | nessun finanziamento esterno; nessun rapporto con OpenAI, DataForSEO, OpenAlex, Stripe, GitHub, Headroom, TOON | dichiarazione nel paper | ☐ |
| 7 | Dichiarazione sull'uso dell'AI | sezione "AI assistance and authorship" del paper | come nel paper precedente | ☐ |

## Da decidere

| # | Voce | Proposta |
|---|---|---|
| 8 | Nome della libreria e del repository | `tracetrim`, repository `github.com/alepom/tracetrim` (il nome è provvisorio: verifica che sia libero su GitHub e su PyPI) |
| 9 | Pubblicare anche su PyPI | facoltativo: rende possibile `pip install tracetrim`; si può fare dopo |
| 10 | `protocol.md` cita il tetto di spesa | il paper non riporta il budget; nel repository il protocollo resta com'era quando è stato congelato (consigliato: lasciarlo, è il documento storico) |
| 11 | Dove pubblicare il dataset | nel record Zenodo del software (consigliato), non in git |

## Azioni esterne (le fai tu, seguendo la guida)

- Creare il repository GitHub e pubblicare la release `v0.3.4`.
- Creare le due bozze Zenodo (paper e software+dati) e riservare i DOI.
- Inserire i DOI: `src/tracetrim/about.py` (`PAPER_DOI`, `SOFTWARE_DOI`, `REPOSITORY`, `LICENSE`), `CITATION.cff`,
  l'articolo; poi ricompilare il paper e rifare i pacchetti.
- Premere *Publish* sui due record Zenodo.
