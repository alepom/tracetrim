# Guida al video: tracetrim davanti a un agente nel Playground di OpenAI

Obiettivo del video: mostrare lo stesso agente con le risposte dei tool **intere** e **filtrate**,
e far vedere che l'agente recupera da solo un campo nascosto con `expand`.

Si usa il server simulato di **C4** (assistenza fatturazione, dati in forma Stripe ma finti): dietro
il proxy non ci sono credenziali vere, e lo scenario ha una risposta giusta nota per ogni ticket.

## Cosa serve

- la cartella `~/progetti/tracetrim` con il suo `.venv`;
- `ngrok` (già installato in `/usr/local/bin/ngrok`, con il tuo dominio statico in `ngrok.yml`);
- il Playground di OpenAI con il modello che vuoi mostrare.

## Come funziona il proxy (da dire nel video)

Il proxy ha due facce. Verso l'agente è un **server MCP**; verso il server vero è un **client MCP**.
All'avvio chiede al server vero l'elenco dei suoi tool e lo ripubblica identico, stessi nomi e stessi
parametri, aggiungendo `expand`. Quando l'agente chiama un tool, il proxy inoltra la chiamata così
com'è, riceve la risposta, la filtra e la restituisce. L'agente non sa che c'è un proxy: vede gli
stessi tool di sempre, con risposte più leggere. Un proxy per ogni server.

Mentre l'agente lavora, nel terminale del proxy compare una riga per ogni chiamata:

```
[tracetrim] stripe_list_charges                   1,579 ->      783 tokens (-50%)  hidden: 18 groups
[tracetrim]   session: 2 calls, 2,227 -> 1,074 (-52%), 0 expand
[tracetrim] expand c3                          the agent asked for data[].items (282 tokens)
```

È la scena in cui si vede il sistema lavorare: tieni il terminale accanto al Playground.

## 1. La configurazione (una volta sola)

Dalle 8 esecuzioni della fase A di C4, due configurazioni: quella attuale (0.2) e quella
"ingenua" della versione 0.1, che serve a mostrare il problema che lo studio ha trovato.

```bash
cd ~/progetti/tracetrim
.venv/bin/tracetrim report traces/C4/A*.jsonl -o out/demo/report.html
.venv/bin/tracetrim report traces/C4/A*.jsonl -o out/demo/v01/report.html --v01
```

Apri `out/demo/report.html`: è la prima scena del video (dove vanno i token, quali campi
l'agente non usa mai, le azioni proposte).

## 2. Il proxy, prima senza filtro

Scegli tu un token segreto (non usare quello dell'esempio):

```bash
cd ~/progetti/tracetrim
.venv/bin/tracetrim proxy --upstream-cmd "$PWD/.venv/bin/python -m experiments.simulated.server_c4" \
  --http 8799 --token SEGRETO-DEMO --allowed-host TUO-DOMINIO.ngrok-free.app
```

Senza `--config` il proxy lascia passare tutto: è il braccio "risposte intere".

In un secondo terminale:

```bash
ngrok http --url=TUO-DOMINIO.ngrok-free.app 8799
```

## 3. Il Playground

1. **Tools → Add → MCP server**
   - URL: `https://TUO-DOMINIO.ngrok-free.app/mcp`
   - header: `Authorization` = `Bearer SEGRETO-DEMO`
   - approvazione: *never*
2. **System message**: il contenuto di `experiments/prompts/c4-system.md`.
3. **User message**: il contenuto di `experiments/prompts/c4-user.md`.
4. Esegui. Nel pannello delle chiamate si vedono le risposte dei tool, lunghe; annota i token di
   input della risposta.

## 4. Scena A — il filtro ingenuo (0.1) e `expand`

Ferma il proxy (Ctrl+C) e rilancialo con la configurazione 0.1:

```bash
.venv/bin/tracetrim proxy --upstream-cmd "$PWD/.venv/bin/python -m experiments.simulated.server_c4" \
  --config out/demo/v01/report.config.json --http 8799 --token SEGRETO-DEMO --allowed-host TUO-DOMINIO.ngrok-free.app
```

Nel Playground aggiorna l'elenco dei tool (compare `expand`) ed esegui di nuovo. Cosa mostrare:
- le risposte dei tool sono più corte e finiscono con una riga
  `[trimmed] call_id=c3 hidden: data[].items (18 fields), ...`;
- la 0.1 ha tagliato la data di fine periodo, perché l'agente la scrive come "20 novembre" e non
  come il timestamp che riceve: per il ticket T3 l'agente dovrebbe chiamare `expand` su
  `data[].items` per recuperarla. Nello studio lo ha fatto in due esecuzioni su tre: se in
  quella del video non succede, è un risultato da raccontare, non da nascondere;
- i token di input, confrontati con il primo giro.

## 5. Scena B — la 0.2

Rilancia il proxy con `--config out/demo/report.config.json`. La 0.2 riconosce che il timestamp
è usato come data e non lo taglia: T3 ha la sua data senza `expand`, e il filtro toglie comunque
la maggior parte del payload.

## 5b. Scena C — con Headroom (facoltativa)

Headroom è installato nell'ambiente di tracetrim. Aggiungi `--structural headroom` al comando della
scena B: il proxy applica prima la proiezione e poi la compressione di Headroom, e il registro mostra
i token dopo entrambe. Da dire nel video: è una combinazione **sperimentale**, che lo studio non ha
validato sulla qualità delle risposte; il primo avvio è più lento perché Headroom si inizializza.

## 6. Chiudere il cerchio (facoltativo)

Aggiungi `--record out/demo/live.jsonl` al proxy, salva la risposta finale dell'agente come ultima
riga `{"kind": "message", "text": "..."}` del file, e rigenera il report: è il ciclo
osserva → impara → rivedi → applica.

## Attenzioni

- Il proxy filtra solo ciò che passa da lui: non tocca la ricerca web nativa del Playground.
- Con un URL pubblico usa **sempre** `--token`: senza, chiunque conosca l'URL potrebbe usare il
  server dietro il proxy.
- Una sola esecuzione per lato non dimostra niente sulla qualità (nello studio, anche con le
  risposte intere l'agente ha sbagliato un ticket in due esecuzioni su tre): nel video presentalo
  come dimostrazione del meccanismo, non come prova.
- I numeri del video non vanno confrontati con quelli del paper: modello, Playground e numero di
  esecuzioni sono diversi.
