# Guida al video: tracetrim davanti all'MCP di un agente, nel Playground di OpenAI

Nel video ci sono **due server MCP**:

| | cos'è | ruolo nel video |
|---|---|---|
| **billing** | il server di fatturazione che l'agente usa già (simulato, in forma Stripe, dati finti) | "l'MCP dell'agente" |
| **tracetrim** | il nostro, configurato per stare **davanti** a billing | "il nostro tool" |

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

Cosa toglie, tracetrim l'ha imparato osservando le esecuzioni precedenti dello stesso agente: per ogni
tool, quali campi l'agente usa davvero.

## Prima di girare

1. In un terminale WSL (facoltativo ma consigliato, così token e URL non cambiano tra una prova e
   l'altra; il dominio statico gratuito è nella dashboard di ngrok, voce *Domains*):
   ```bash
   export DEMO_TOKEN=scegli-un-segreto
   export NGROK_DOMAIN=tuo-dominio.ngrok-free.app
   ```
2. Avvia tutto:
   ```bash
   cd ~/progetti/tracetrim && docs/demo_playground.sh
   ```
   Lo script stampa i due URL e il token, poi mostra le chiamate in arrivo. Lascialo aperto.
3. Nel Playground crea l'agente: modello a tua scelta, **System message** e **User message** da
   [`demo_prompts_IT.md`](demo_prompts_IT.md).
4. Sempre nel Playground, aggiungi **entrambi** i server (*Tools → Add → MCP server*), con l'header
   `Authorization: Bearer <token>` e approvazione *never*:
   - `billing` → l'URL "billing" stampato dallo script;
   - `tracetrim` → l'URL "tracetrim".

   Tieni acceso solo `billing`.

## Scena 1 — L'agente con il suo MCP

Esegui l'agente. Nel terminale scorrono le righe `[billing]`: sono le chiamate dell'agente al suo
server. Nel Playground annota i token di input consumati.

## Scena 2 — Il report: cosa ha imparato tracetrim

Apri `out/demo/report.html` (da Windows:
`\\wsl.localhost\Debian\home\alepom\progetti\tracetrim\out\demo\report.html`). Mostra la cifra in
alto, "Dove vanno i token", i campi che l'agente non usa mai, e in fondo le **azioni**: quelle
automatiche, già nella configurazione che tracetrim sta usando, e quelle manuali, da valutare.

## Scena 3 — Aggancio tracetrim

Nel Playground spegni `billing` e accendi `tracetrim`. Esegui di nuovo. Nel terminale, per ogni
chiamata, due righe:

```
[billing] stripe_list_charges {"customer": "cus_..."}
[tracetrim] stripe_list_charges                   1,579 ->      783 tokens (-50%)  hidden: 18 groups
[tracetrim]   session: 2 calls, 2,227 -> 1,074 (-52%), 0 expand
```

La prima riga mostra che la chiamata arriva comunque a billing; la seconda quanto tracetrim ha
tolto prima di passarla all'agente. Da mostrare anche: i token di input nel Playground, confrontati con
la scena 1, e le risposte ai cinque ticket, che restano corrette (la tabella delle risposte giuste è in
`demo_prompts_IT.md`).

## Variante con Headroom (facoltativa)

Avvia lo script con `docs/demo_playground.sh --headroom`: tracetrim applica anche Headroom dopo la
proiezione. È una combinazione sperimentale, che lo studio non ha validato sulla qualità delle
risposte; il primo avvio è più lento.

## Attenzioni

- I due server sono protetti dal token finché i tunnel sono aperti; Ctrl+C chiude tutto.
- tracetrim filtra solo ciò che passa da lui: non tocca la ricerca web nativa del Playground.
- Una sola esecuzione per scena non è una misura: presenta i numeri come dimostrazione del meccanismo.
  I risultati sono nel paper.
- Se lo script si ferma con "ngrok did not open both tunnels", il piano ngrok non consente due tunnel
  insieme: guarda `out/demo/ngrok.log` e scrivimi.
