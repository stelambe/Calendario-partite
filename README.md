# Calendario partite

App per vedere le partite delle proprie squadre, cercando per data. I calendari vengono letti
dal sito della federazione (sol.milano.federvolley.it) tre volte al giorno.

## Come funziona
- `squadre.json`: l'elenco delle squadre (nome, serie, link del calendario).
- `scraper/scrape.py`: apre ogni link con un browser, legge la tabella e tiene le partite delle squadre elencate.
- `.github/workflows/aggiorna.yml`: lancia il programma alle 05:30, 11:30 e 17:30 (ora UTC) e salva `data/partite.json`.
- `index.html`: l'app, legge `data/partite.json`.

## Squadre di PGS Milano
Le squadre con `"fonte": "pgs"` si leggono da volley.pgsmilano.org: il programma sceglie campionato,
categoria e squadra come faresti con il mouse. All'inizio di una nuova stagione cambia il campo
`campionato` (per esempio `Invernale 2026/2027`) nelle squadre PGS. Dopo ogni aggiornamento,
`data/nomi_squadre.json` elenca i campionati, le categorie e le squadre che il sito offre.

## Aggiungere o cambiare una squadra
Apri `squadre.json` su GitHub, premi la matita (Edit), aggiungi una riga uguale alle altre e salva.
Il nome deve comparire nel calendario del sito (maiuscole e punti non contano). Dopo il prossimo
aggiornamento, `data/nomi_squadre.json` elenca i nomi esatti usati dal sito per ogni campionato.

## Aggiornare subito
Scheda Actions > Aggiorna calendari > Run workflow.

## Se qualcosa non va
Se un calendario non si legge, nella cartella `debug` compaiono la pagina salvata e una schermata.
