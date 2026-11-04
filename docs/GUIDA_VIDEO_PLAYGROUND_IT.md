# Guida al video: tracetrim davanti all'MCP di un agente, nel Playground di OpenAI

Nel video ci sono **due server MCP**:

| | cos'è | ruolo nel video |
|---|---|---|
| **billing** | il server di fatturazione che l'agente usa già (simulato, in forma Stripe, dati finti) | "l'MCP dell'agente" |
| **tracetrim** | il nostro, in modalità automatica, **davanti** a billing | "il nostro tool" |

Lo script avvia entrambi, li rende raggiungibili dal Playground con due tunnel ngrok e mostra in un
solo terminale le chiamate che arrivano a ciascuno. Tutto resta acceso dall'inizio alla fine: le scene
si cambiano nel Playground.

## Come funziona (da dire nel video)

tracetrim non affianca billing: **si mette davanti**. Verso l'agente è un server MCP che offre gli
stessi tool di billing, con gli stessi nomi, più `expand`; verso billing è un client che inoltra ogni
chiamata così com'è. Quando billing risponde, tracetrim toglie i campi che l'agente non usa e passa
all'agente una risposta più leggera. Per questo, quando si aggancia tracetrim, il collegamento
diretto a billing si **spegne**: se restassero accesi entrambi l'agente vedrebbe due copie degli stessi
tool e potrebbe scavalcare tracetrim.

Cosa togliere, tracetrim lo impara da solo, nel video stesso: osserva le prime esecuzioni (3, per la
demo) senza cambiare nulla; alla fine di ognuna l'agente gli passa la risposta finale con lo strumento
`tracetrim_done`, e tracetrim confronta i dati ricevuti con ciò che l'agente ha scritto. Dopo la terza
applica da sé le azioni sicure (taglia i campi mai usati, tiene identificativi ed `expand`), e da lì in
poi l'agente riceve risposte più leggere. All'utente resta una riga in più nel prompt.

## Prima di girare

1. In un terminale WSL (facoltativo ma consigliato, così token e URL non cambiano tra una prova e
   l'altra; il dominio statico gratuito è nella dashboard di ngrok, voce *Domains*):
   ```bash
   export DEMO_TOKEN=scegli-un-segreto
   export NGROK_DOMAIN=tuo-dominio.ngrok-free.app
   ```
2. Avvia tutto, partendo da zero:
   ```bash
   cd ~/progetti/tracetrim && docs/demo_playground.sh --reset
   ```
   Lo script stampa i due URL, il token e l'indirizzo del report dal vivo, poi mostra le chiamate in
   arrivo. Lascialo aperto. Ciò che tracetrim impara resta in `out/demo/auto/` anche se riavvii lo
   script: usa `--reset` solo per ricominciare da zero (per esempio dopo una prova).
3. Nel Playground crea l'agente: modello a tua scelta, **System message** e **User message** da
   [`demo_prompts_IT.md`](demo_prompts_IT.md).
4. Sempre nel Playground, aggiungi **entrambi** i server (*Tools → Add → MCP server*), con l'header
   `Authorization: Bearer <token>` e approvazione *never*:
   - `billing` → l'URL "billing" stampato dallo script;
   - `tracetrim` → l'URL "tracetrim".

   Tieni acceso solo `billing`.
5. Apri nel browser l'indirizzo del report stampato dallo script
   (`http://127.0.0.1:8799/report?token=...`): si aggiorna da solo ogni 10 secondi. All'inizio dice
   che tracetrim sta osservando.

## Scena 1 — L'agente con il suo MCP

Esegui l'agente. Nel terminale scorrono le righe `[billing]`: sono le chiamate dell'agente al suo
server. Nel Playground annota i token di input consumati.

## Scena 2 — Aggancio tracetrim: osserva

Nel Playground spegni `billing` e accendi `tracetrim`. Esegui l'agente **tre volte**, svuotando la chat
ogni volta (ogni esecuzione dev'essere nuova, non il seguito della precedente). Nel
video basta mostrarne una per intero e accelerare le altre.

Da mostrare:
- nel Playground, tra gli strumenti, gli stessi di billing più `expand` e `tracetrim_done`; alla fine
  dell'esecuzione l'agente chiama `tracetrim_done` con la sua risposta;
- nel terminale, per ogni chiamata, la riga `[billing]` (la chiamata arriva comunque al server) e la
  riga `[tracetrim]` con `+0%`: per ora non toglie nulla;
- a fine esecuzione:
  ```
  [tracetrim] run 1 closed (with answer): 10 calls, 16,561 -> 16,561 tokens (+0%), 0 expand
  [tracetrim]   1 complete runs · report updated · learning: 2 more complete runs before trimming
  ```
- il report che si riempie: la cifra in alto, "Dove vanno i token", i campi che l'agente non usa mai.

## Scena 3 — tracetrim filtra da solo

Dopo la terza esecuzione il terminale dice `trimming 4 tools`. Esegui di nuovo (chat vuota). Ora:

```
[billing] stripe_list_charges {"customer": "cus_..."}
[tracetrim] stripe_list_charges                   1,579 ->      783 tokens (-50%)  hidden: 18 groups
[tracetrim]   this run: 2 calls, 2,227 -> 1,074 (-52%), 0 expand · session: ...
```

Da mostrare: la riga di chiusura dell'esecuzione (circa −50% sui risultati degli strumenti), i token di
input nel Playground confrontati con la scena 1, e le risposte ai cinque ticket, che restano corrette
(la tabella delle risposte giuste è in `demo_prompts_IT.md`).

## Scena 4 — Il report: automatico e manuale

Nel report, in alto, "Modalità automatica: il filtro è attivo". In fondo le **azioni**: quelle
automatiche, che tracetrim ha già applicato, e quelle manuali, che restano da valutare a chi gestisce
l'agente. Se l'agente chiedesse troppo spesso `expand` per un tool, tracetrim smetterebbe di filtrarlo
da solo e il report lo segnalerebbe.

## Variante con Headroom (facoltativa)

Avvia lo script con `docs/demo_playground.sh --headroom`: tracetrim applica anche Headroom dopo la
proiezione. È una combinazione sperimentale, che lo studio non ha validato sulla qualità delle
risposte; il primo avvio è più lento.

## Attenzioni

- I due server sono protetti dal token finché i tunnel sono aperti; Ctrl+C chiude tutto.
- tracetrim filtra solo ciò che passa da lui: non tocca la ricerca web nativa del Playground.
- In una prova con un agente vero (5 esecuzioni, 3 di osservazione) i risultati degli strumenti sono
  scesi del 50% circa dopo l'apprendimento, nessuna richiesta di `expand`, 5 ticket su 5 corretti in
  ogni esecuzione. Il modello del Playground può comportarsi diversamente: fai una prova prima di girare.
- Se l'agente non chiama `tracetrim_done`, quell'esecuzione resta solo nella telemetria (si chiude
  dopo 5 minuti di pausa) e non conta per l'apprendimento.
- Una sola esecuzione per scena non è una misura: presenta i numeri come dimostrazione del meccanismo.
  I risultati sono nel paper.
- Se lo script si ferma con "ngrok did not open both tunnels", il piano ngrok non consente due tunnel
  insieme: guarda `out/demo/ngrok.log` e scrivimi.
