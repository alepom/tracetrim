# Guida al video: tracetrim davanti a un agente nel Playground di OpenAI

Cosa si vede nel video: un agente di assistenza sulla fatturazione lavora nel Playground su un server
MCP; tracetrim, in mezzo, alleggerisce le risposte dei tool, e nel terminale si vede in tempo reale
quanto toglie a ogni chiamata.

Si usa il server simulato del caso C4 dello studio: dati in forma Stripe ma finti, nessuna
credenziale vera dietro il proxy, e per ogni ticket una risposta giusta nota.

## Come funziona (da dire nel video)

Il proxy ha due facce. Verso l'agente è un **server MCP**; verso il server vero è un **client MCP**.
All'avvio chiede al server l'elenco dei suoi tool e lo ripubblica identico, stessi nomi e stessi
parametri, più `expand`. Quando l'agente chiama un tool, il proxy inoltra la chiamata così com'è,
riceve la risposta, la filtra con la configurazione e la restituisce. L'agente non sa che c'è un
proxy: vede gli stessi tool, con risposte più leggere. Se gli serve un campo tolto, lo chiede con
`expand`.

La configurazione l'ha imparata tracetrim da otto esecuzioni registrate dello stesso agente: per ogni
tool, quali campi l'agente usa davvero.

## Prima di girare (una volta)

1. Facoltativo ma consigliato: in un terminale WSL fissa token e dominio, così restano uguali tra una
   scena e l'altra (il dominio statico gratuito si trova nella dashboard di ngrok, alla voce *Domains*):
   ```bash
   export DEMO_TOKEN=scegli-un-segreto
   export NGROK_DOMAIN=tuo-dominio.ngrok-free.app
   ```
   Senza `NGROK_DOMAIN` l'URL cambia a ogni avvio, e va riaggiornato nel Playground.
2. Nel Playground crea l'agente: modello a tua scelta; **System message** e **User message** sono in
   [`demo_prompts_IT.md`](demo_prompts_IT.md), pronti da incollare.

## Scena 1 — Il report

Apri `~/progetti/tracetrim/out/demo/report.html` (da Windows:
`\\wsl.localhost\Debian\home\alepom\progetti\tracetrim\out\demo\report.html`). Mostra la cifra in
alto, il grafico "Dove vanno i token", e la configurazione generata in fondo: è quello che il sistema
ha imparato.

## Scena 2 — L'agente senza filtro (facoltativa, per il confronto)

```bash
cd ~/progetti/tracetrim && docs/demo_playground.sh raw
```

Lo script avvia ngrok e il proxy e stampa i valori da inserire nel Playground:
**Tools → Add → MCP server**, URL `https://…/mcp`, header `Authorization: Bearer …`, approvazione
*never*. Esegui l'agente: nel terminale scorrono le chiamate a pieno peso.

## Scena 3 — L'agente con tracetrim

Ferma con Ctrl+C e rilancia:

```bash
docs/demo_playground.sh trim
```

Se URL e token non sono cambiati (punto 1 di "Prima di girare"), nel Playground basta aggiornare
l'elenco dei tool: compare `expand`. Esegui di nuovo. Nel terminale, una riga per chiamata:

```
[tracetrim] stripe_list_charges                   1,579 ->      783 tokens (-50%)  hidden: 18 groups
[tracetrim]   session: 2 calls, 2,227 -> 1,074 (-52%), 0 expand
```

Da mostrare: le percentuali per chiamata, il totale della sessione, e le risposte ai cinque ticket,
che restano corrette (la tabella delle risposte giuste è in `demo_prompts_IT.md`). Nel Playground,
aprendo una chiamata, si vede la risposta filtrata con la riga finale `[trimmed] … hidden: …`.

## Scena 4 — Con Headroom (facoltativa)

```bash
docs/demo_playground.sh trim-headroom
```

Dopo la proiezione, Headroom comprime ancora la struttura. Il primo avvio è più lento. Da dire:
combinazione sperimentale, che lo studio non ha validato sulla qualità delle risposte.

## Attenzioni

- Il proxy filtra solo ciò che passa da lui: non tocca la ricerca web nativa del Playground.
- Il token protegge il proxy finché il tunnel è aperto; a fine registrazione Ctrl+C chiude entrambi.
- Una sola esecuzione non è una misura: presenta i numeri del video come dimostrazione del
  meccanismo, non come risultato. I risultati sono nel paper.
