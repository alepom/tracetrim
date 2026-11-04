# Gli agenti AI leggono molto più di quello che usano. Si può imparare cosa tagliare?

*Alessio Pomaro*

> **Di cosa parliamo**
>
> Un agente AI lavora chiamando strumenti: un'API di dati SEO, un catalogo, un sistema di pagamenti, un
> repository di codice. Gli strumenti rispondono con JSON pensati per i programmi, non per i modelli: decine di
> campi, la maggior parte dei quali non serve al compito. Ma l'agente li legge tutti, li paga in token e se li
> porta dietro a ogni turno successivo.

Nel corso sugli agenti AI che ho tenuto all'Accademia di Search On, uno dei punti centrali era proprio questo: il
lavoro più efficace per ridurre i costi di un agente non è comprimere, è **scegliere** quali campi delle risposte
dei tool servono davvero. Lo facciamo a mano, scrivendo una funzione di filtro per ogni tool. Funziona, ma richiede
di sapere quali campi tenere, tool per tool e compito per compito.

La domanda di questo studio è semplice: **quella scelta si può imparare automaticamente, guardando come l'agente
ha lavorato nelle esecuzioni precedenti?**

## Il paper

**"Agents Read More Than They Use: Learning Tool-Output Projections from Execution Traces, and Where They Fail"**
DOI: *(da inserire)*

In breve:
- cinque agenti: tre su API reali (un piano editoriale e un'analisi dei contenuti mancanti con DataForSEO, una
  ricerca di letteratura scientifica su OpenAlex) e due su server simulati che seguono le specifiche pubbliche di
  Stripe e GitHub (assistenza sulla fatturazione e analisi dei fallimenti della CI);
- **due studi con protocollo congelato prima** di ogni esecuzione: ipotesi, metriche e regole fissate in anticipo,
  ogni modifica successiva dichiarata con data e motivo;
- ogni numero del paper è generato dai dati, e codice, protocolli e dati pubblicabili sono disponibili
  ([GitHub](#) · [Zenodo](#)).

È un preprint: non è passato dalla revisione di una rivista.

## L'idea: guardare cosa l'agente riscrive

Se un valore restituito da un tool ricompare più avanti, negli argomenti delle chiamate successive o nella risposta
finale, quel campo è stato usato. Se un campo non ricompare mai, in nessuna esecuzione dello stesso compito, è un
candidato da togliere. Da qui nasce un **filtro imparato** per ogni tool: l'elenco dei campi da tenere.

Il dettaglio importante è come si misura: il filtro si impara da alcune esecuzioni e si prova su **un'esecuzione che
non ha mai visto**. Si contano i token tolti e i *mancati*, cioè le volte in cui il filtro avrebbe tolto un campo che
poi è servito.

## Cosa abbiamo trovato

**Gli agenti usano una piccola parte di ciò che ricevono.** In quattro casi su cinque, i campi usati valgono tra il
17% e il 32% dei token restituiti dai tool. Il filtro imparato, provato su esecuzioni nuove, ha tolto **tra il 63% e il
77%** dei token. Headroom, una libreria open source di compressione strutturale, sulle stesse conversazioni ne toglie
tra il 17% e il 33%.

**Il filtro va imparato dalle tracce complete.** Se si osservano solo le chiamate ai tool, cioè quello che vedrebbe
un proxy MCP messo tra agente e server, i campi persi esplodono: in due casi su cinque il filtro sbaglia in **tutte**
le chiamate, perché quegli agenti usano i dati solo nella risposta finale. Il filtro si può *applicare* in un proxy,
ma va *imparato* da tracce che contengono la risposta.

**Quello che serve dipende dal compito, non solo dal tool.** Lo stesso tool usato da due agenti diversi richiede
campi diversi: un filtro imparato su un compito, applicato all'altro, sbaglia molto di più.

## Dove fallisce, e perché

Rigiocando le stesse conversazioni con le risposte filtrate, sono emersi tre modi precisi in cui il filtro perde
informazione:

1. **Valori trasformati.** La data di fine abbonamento arriva come timestamp (un numero) e l'agente la scrive
   "20 novembre". Il filtro non riconosce la data riscritta, conclude che il campo non serve e lo toglie: l'agente
   risponde al cliente in modo vago.
2. **Campi usati solo per ragionare.** L'agente legge il titolo di una issue per capire quale problema riguarda,
   ma nella risposta scrive solo il numero. Il filtro toglie i titoli, e l'agente non riesce più a collegare un
   fallimento alla issue giusta.
3. **Identificativi inventati.** È il risultato più serio. Senza il campo con l'identificativo di un articolo,
   l'agente non ha scritto "non disponibile": **ha inventato identificativi in sequenza**, partendo dall'esempio
   che aveva nelle istruzioni. Con le risposte intere non era mai successo.

## Le protezioni, e la verifica

Da questi errori sono nate due protezioni: **non togliere mai i campi identificativi** (id, DOI, URL, numeri,
titoli) e **riconoscere i valori trasformati** (date, importi in centesimi, URL ridotti a identificativo). Un
secondo studio, anche questo con protocollo congelato prima, ha verificato che le protezioni eliminano tutte e tre
le perdite, mantenendo un risparmio **tra il 46% e il 61%**.

Due cose non sono andate bene, e il paper le riporta:
- un **quarto meccanismo**: il motivo di un pagamento rifiutato arriva in inglese e l'agente lo traduce in italiano;
  il filtro non riconosce la traduzione e lo toglie. Le protezioni non lo coprono;
- la **compressione strutturale** (riscrivere il JSON in un formato più compatto, TOON) non toglie dati, ma ha
  cambiato il modo in cui il modello ha risolto un caso ambiguo. Un formato meno familiare al modello non è una
  scelta neutra.

Nelle esecuzioni dal vivo, con uno strumento `expand` che permette all'agente di recuperare un campo nascosto, i
token in input fatturati sono scesi del **56%** e del **32%** nei due casi provati, con tutti i controlli superati.
E gli agenti hanno usato `expand` quando serviva.

## Cosa portarsi a casa

- Il problema è reale e misurabile: la maggior parte di ciò che un agente legge non gli serve.
- Imparare cosa togliere funziona, ma il filtro è una **proposta**, non un interruttore automatico: va rivisto da
  chi conosce il compito, perché i suoi punti ciechi sono esattamente i campi che contano in modi che il testo non
  mostra.
- Gli identificativi non si toccano. Mai.
- Serve sempre una via di ritorno: se un campo è stato tolto per errore, l'agente deve poterlo chiedere.

## Lo strumento

Dallo studio è nata **tracetrim**, una libreria open source che:
- genera un **report di telemetria** in italiano e inglese (quali tool riempiono il contesto, quali chiamate
  falliscono e con quale errore, quali campi l'agente non usa mai), utile anche senza filtrare nulla;
- propone una **configurazione** leggibile, da rivedere prima di applicarla;
- la applica con un **proxy MCP** davanti a qualunque server, senza modificare l'agente, con `expand` sempre
  disponibile.

Il codice, la guida rapida e le istruzioni per rifare ogni misura sono nel repository.
