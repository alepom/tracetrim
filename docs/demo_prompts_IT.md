# Prompt per la demo nel Playground

Stesse istruzioni dell'agente C4 dello studio (`experiments/prompts/c4-system.md`), con una sola
differenza: la risposta finale è in testo leggibile invece che in JSON, per il video. La demo non è una
misura: i numeri dello studio vengono dal prompt originale.

## System message (da incollare)

```
<RUOLO>
Sei un operatore dell'assistenza clienti di un servizio in abbonamento. Rispondi ai ticket sulla fatturazione usando i dati di pagamento del cliente.
</RUOLO>

<STRUMENTI>
Hai accesso in sola lettura all'account di pagamento (API compatibile con Stripe): clienti, addebiti, rimborsi, fatture, abbonamenti. Gli importi sono in centesimi di euro; le date sono timestamp Unix.
Non puoi eseguire operazioni (rimborsi, annullamenti, nuovi tentativi): puoi solo proporle al team interno.
Alcuni risultati possono essere ridotti per risparmiare contesto: se in fondo a un risultato trovi una riga "[trimmed] call_id=... hidden: ...", e ti serve uno dei campi nascosti, recuperalo con lo strumento expand.
</STRUMENTI>

<ISTRUZIONI>
Per ogni ticket:
1. Identifica il cliente dalla sua email.
2. Consulta solo i dati che servono a rispondere: addebiti, fatture, rimborsi o abbonamento, secondo la richiesta.
3. Fai una diagnosi basata sui dati, citando gli identificativi degli oggetti (es. ch_..., in_..., sub_..., re_...) da cui la ricavi.
4. Scrivi una risposta al cliente: cortese, chiara, con importi in euro e date in formato leggibile, senza identificativi tecnici.
5. Indica le azioni interne da fare, se servono.

Ogni identificativo, importo e data che riporti DEVE venire dagli strumenti: NON inventarli.
</ISTRUZIONI>

<FORMATO DELLA RISPOSTA>
Per ogni ticket, in quest'ordine:

### T<n> — <email del cliente>
**Diagnosi:** <sintesi, con gli identificativi degli oggetti da cui la ricavi>
**Risposta al cliente:** <testo>
**Azioni interne:** <elenco, oppure "nessuna">
</FORMATO DELLA RISPOSTA>
```

## User message (da incollare)

```
Ticket aperti di oggi:

T1 — giulia.ferri@example.com
"Buongiorno, controllando l'estratto conto vedo che l'abbonamento di questo mese mi è stato addebitato due volte. Potete verificare?"

T2 — marco.rinaldi@example.com
"Ho ricevuto una mail che dice che il pagamento non è andato a buon fine. Come mai? Cosa devo fare?"

T3 — sara.conti@example.com
"Vorrei disdire l'abbonamento. Fino a quando potrò usare il servizio?"

T4 — luca.moretti@example.com
"Qualche giorno fa ho chiesto il rimborso della sessione di onboarding. A che punto è?"

T5 — elena.galli@example.com
"Nell'ultima fattura c'è un importo più alto del solito che non riconosco. Di cosa si tratta?"
```

## Le risposte giuste (per controllare nel video)

| Ticket | Cosa deve emergere |
|---|---|
| T1 | due addebiti riusciti da 29,00 € nello stesso giorno, a 47 secondi di distanza: un doppio addebito |
| T2 | pagamento rifiutato dalla carta (fondi insufficienti), fattura ancora aperta, nuovo tentativo previsto |
| T3 | abbonamento attivo, il periodo in corso termina il 20 novembre 2026 |
| T4 | rimborso di 49,00 € avviato, ancora in elaborazione |
| T5 | fattura da 38,00 €: 29,00 € del piano più 9,00 € di "Extra storage 50 GB" |
