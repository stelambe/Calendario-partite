#!/usr/bin/env bash
# Salva data/ e debug/ nel ramo main anche se un altro aggiornamento ha scritto nel frattempo.
# I file in data/ e debug/ vengono rigenerati interamente a ogni lettura, quindi si riparte
# sempre dall'ultima versione di main e ci si rimettono sopra i file appena generati.
set -u
git config user.name "aggiornamento-automatico"
git config user.email "actions@users.noreply.github.com"

mkdir -p data debug
USCITA="$(mktemp -d)"
cp -r data "$USCITA/data"
cp -r debug "$USCITA/debug"

for tentativo in 1 2 3 4; do
  git fetch -q origin main
  git reset -q --hard origin/main
  rm -rf data debug
  cp -r "$USCITA/data" data
  cp -r "$USCITA/debug" debug
  touch debug/.gitkeep
  git add -A data debug
  if git diff --cached --quiet; then
    echo "Nessuna modifica da salvare"
    exit 0
  fi
  git commit -q -m "Aggiorna calendari"
  if git push -q origin HEAD:main; then
    echo "Dati salvati"
    exit 0
  fi
  echo "Salvataggio rifiutato (tentativo $tentativo), riprovo"
  sleep 5
done
echo "Non sono riuscito a salvare i dati"
exit 1
