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

1. In un terminale WSL scegli un token che puoi mostrare nel video (compare nel terminale e nel
   Playground; dopo la registrazione non serve più):
   ```bash
   export DEMO_TOKEN=demo-accademia-2026
   ```
   Il dominio gratuito di ngrok è fisso (lo trovi nella dashboard di ngrok, voce *Domains*): gli URL
   non cambiano tra una prova e l'altra.
2. Avvia tutto, partendo da zero:
   ```bash
   cd ~/progetti/tracetrim && docs/demo_playground.sh --reset
   ```
   Lo script avvia billing, tracetrim (che legge billing da `out/demo/mcp.json`, come farebbe
   qualunque utente) e un solo tunnel; stampa gli indirizzi e poi mostra le chiamate in arrivo.
   Lascialo aperto. Gli indirizzi sono:

   | | URL |
   |---|---|
   | billing | `https://<tuo-dominio>.ngrok-free.dev/billing/mcp` |
   | tracetrim | `https://<tuo-dominio>.ngrok-free.dev/tracetrim/mcp` |
   | report dal vivo | `http://127.0.0.1:8799/report?token=<token>` |

   Ciò che tracetrim impara resta in `out/demo/auto/` anche se riavvii lo script: `--reset` serve
   per ricominciare da zero, per esempio dopo una prova.

## L'agente nel Playground

Su platform.openai.com → **Playground** → **Chat** (Responses):

| impostazione | valore |
|---|---|
| Modello | `gpt-5.6-terra` (quello delle prove; un altro modello va riprovato prima) |
| Reasoning effort | predefinito |
| System message (*Developer message*) | il blocco "System message" di [`demo_prompts_IT.md`](demo_prompts_IT.md) |
| Tools | i due server MCP qui sotto |

I due server: *Tools → + Add → MCP server → Custom*.

| campo | billing | tracetrim |
|---|---|---|
| URL | `…ngrok-free.dev/billing/mcp` | `…ngrok-free.dev/tracetrim/mcp` |
| Label | `billing` | `tracetrim` |
| Authentication / header | `Authorization: Bearer <token>` | `Authorization: Bearer <token>` |
| Approval | *Never* (nessuna conferma per chiamata) | *Never* |

Tieni acceso solo `billing`. Il messaggio dell'utente è il blocco "User message" di
`demo_prompts_IT.md`: incollalo a ogni esecuzione, **con la chat svuotata** (ogni esecuzione deve
partire da zero).

Il report: apri l'indirizzo "report dal vivo" in un browser di Windows. Si aggiorna da solo ogni 10
secondi; all'inizio dice che tracetrim sta osservando. Se `127.0.0.1` non risponde da Windows, usa
`https://…ngrok-free.dev/tracetrim/report?token=<token>` e clicca *Visit Site* nella pagina di avviso
di ngrok.

## Dove si vede cosa

| cosa | dove |
|---|---|
| le chiamate che arrivano a billing | terminale, righe `[billing]` |
| quanto tracetrim toglie a ogni chiamata | terminale, righe `[tracetrim] … -> … tokens (-NN%)` |
| il bilancio di ogni esecuzione e cosa ha imparato | terminale, `run N closed …` e la riga sotto |
| la telemetria: strumenti, campi mai usati, azioni | report dal vivo |
| il momento in cui parte il filtro automatico | terminale `trimming N tools`; report, riquadro "Modalità automatica" |
| il costo per l'agente | Playground: token di input di ogni risposta |

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

Da mostrare: la riga di chiusura dell'esecuzione (circa −44% sui risultati degli strumenti), i token di
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
  scesi del 44% circa dopo l'apprendimento (`results/checks/auto_check_mcp.json`), una richiesta di `expand` per esecuzione, 5 ticket su 5 superano il test a parole chiave in
  ogni esecuzione. Il modello del Playground può comportarsi diversamente: fai una prova prima di girare.
- Se l'agente non chiama `tracetrim_done`, quell'esecuzione resta solo nella telemetria (si chiude
  dopo 5 minuti di pausa) e non conta per l'apprendimento.
- Una sola esecuzione per scena non è una misura: presenta i numeri come dimostrazione del meccanismo.
  I risultati sono nel paper.
- Un solo tunnel, due percorsi: il piano gratuito di ngrok ha un solo dominio, e due tunnel sullo stesso
  dominio si dividerebbero le richieste a caso. Se lo script si ferma con "ngrok did not open the
  tunnel", guarda `out/demo/ngrok.log`.
