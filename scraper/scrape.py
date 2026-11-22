#!/usr/bin/env python3
"""Legge i calendari dal sito della federazione e salva data/partite.json.

Uso normale:   python scraper/scrape.py
Prova locale:  python scraper/scrape.py --prova tests/fixture_tabella.html
"""
import json
import pathlib
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

RADICE = pathlib.Path(__file__).resolve().parent.parent
SQUADRE = RADICE / "squadre.json"
DATI = RADICE / "data" / "partite.json"
NOMI = RADICE / "data" / "nomi_squadre.json"
DEBUG = RADICE / "debug"

SELETTORE = "table.rz-grid-table tbody tr"

JS_RIGHE = """
() => [...document.querySelectorAll('table.rz-grid-table tbody tr')].map(tr =>
  [...tr.querySelectorAll('td')].map(td =>
    [...td.querySelectorAll('div.rid-08')].map(d => d.textContent.trim())))
"""


def norm(testo):
    t = unicodedata.normalize("NFD", (testo or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[.'’]", "", t)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def corrisponde(cercato, nome_in_tabella):
    """Vero se tutte le parole del nome cercato compaiono nel nome in tabella."""
    parole = set(norm(cercato).split())
    return bool(parole) and parole <= set(norm(nome_in_tabella).split())


def righe_a_partite(righe):
    partite = []
    for c in righe:
        if len(c) < 6 or not c[1] or len(c[2]) < 2:
            continue
        m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", c[1][0])
        if not m:
            continue
        data = f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
        ora = ""
        if len(c[1]) > 1:
            t = re.search(r"(\d{1,2})[.:](\d{2})\s*$", c[1][1])
            if t:
                ora = f"{int(t.group(1)):02d}:{t.group(2)}"
        partite.append({
            "gara": c[0][0] if c[0] else "",
            "data": data,
            "ora": ora,
            "casa": c[2][0],
            "ospite": c[2][1],
            "campo": c[5][0] if len(c[5]) > 0 else "",
            "indirizzo": c[5][1] if len(c[5]) > 1 else "",
        })
    return partite


def partite_della_squadra(squadra, tutte):
    out = []
    for p in tutte:
        in_casa = corrisponde(squadra["nome"], p["casa"])
        in_ospite = corrisponde(squadra["nome"], p["ospite"])
        if not (in_casa or in_ospite):
            continue
        if in_casa and not in_ospite:
            ct, avv = "Casa", p["ospite"]
        elif in_ospite and not in_casa:
            ct, avv = "Trasferta", p["casa"]
        else:
            ct, avv = "", f"{p['casa']} - {p['ospite']}"
        out.append({
            "id": squadra["id"], "squadra": squadra["nome"], "serie": squadra["serie"],
            "gara": p["gara"], "data": p["data"], "ora": p["ora"],
            "casa_trasferta": ct, "avversario": avv,
            "campo": p["campo"], "indirizzo": p["indirizzo"],
        })
    return out


def salva_debug(page, nome):
    DEBUG.mkdir(exist_ok=True)
    try:
        (DEBUG / f"{nome}.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(DEBUG / f"{nome}.png"), full_page=True)
    except Exception as e:  # il debug non deve mai bloccare il resto
        print(f"  (debug non salvato: {e})")


def leggi_pagina(ctx, url, nome_debug):
    ultimo_errore = None
    for tentativo in (1, 2):
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector(SELETTORE, timeout=45000)
            page.wait_for_timeout(1500)
            righe = page.evaluate(JS_RIGHE)
            partite = righe_a_partite(righe)
            if not partite:
                raise RuntimeError(f"tabella trovata ma nessuna partita leggibile ({len(righe)} righe)")
            return partite
        except Exception as e:
            ultimo_errore = e
            print(f"  tentativo {tentativo} fallito: {e}")
            if tentativo == 2:
                salva_debug(page, nome_debug)
        finally:
            page.close()
        time.sleep(3)
    raise ultimo_errore


def prova(file_html):
    url = pathlib.Path(file_html).resolve().as_uri()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context()
        tutte = leggi_pagina(ctx, url, "prova")
        browser.close()
    print(f"Partite lette: {len(tutte)}")
    for r in tutte:
        print(" ", r)
    squadre = json.loads(SQUADRE.read_text(encoding="utf-8"))
    sq = next(s for s in squadre if s["id"] == "arcus-17f")
    print("Partite di", sq["nome"], sq["serie"])
    for r in partite_della_squadra(sq, tutte):
        print(" ", r)


def main():
    squadre = json.loads(SQUADRE.read_text(encoding="utf-8"))
    vecchio = json.loads(DATI.read_text(encoding="utf-8")) if DATI.exists() else {}
    vecchie_partite = vecchio.get("partite", [])

    per_url, errori, nomi = {}, {}, {}
    url_unici = list(dict.fromkeys(s["url"] for s in squadre))

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(locale="it-IT", timezone_id="Europe/Rome")
        for i, url in enumerate(url_unici):
            codice = url.rstrip("/").split("/")[-2]
            print(f"Leggo {url}")
            try:
                tutte = leggi_pagina(ctx, url, f"campionato-{codice}")
                per_url[url] = tutte
                nomi[codice] = sorted({n for q in tutte for n in (q["casa"], q["ospite"])})
                print(f"  ok: {len(tutte)} partite, {len(nomi[codice])} squadre")
            except Exception as e:
                errori[url] = str(e)
                print(f"  ERRORE: {e}")
            if i < len(url_unici) - 1:
                time.sleep(2)
        browser.close()

    partite, problemi = [], []
    for s in squadre:
        if s["url"] in per_url:
            trovate = partite_della_squadra(s, per_url[s["url"]])
            if not trovate:
                problemi.append({"squadra": f"{s['nome']} {s['serie']}",
                                 "messaggio": "nessuna partita trovata con questo nome: controlla data/nomi_squadre.json"})
            partite += trovate
        else:
            problemi.append({"squadra": f"{s['nome']} {s['serie']}",
                             "messaggio": "sito non raggiungibile, mostro i dati dell'ultimo aggiornamento riuscito"})
            partite += [p for p in vecchie_partite if p.get("id") == s["id"]]

    partite.sort(key=lambda p: (p["data"], p["ora"], p["squadra"]))
    DATI.parent.mkdir(exist_ok=True)
    DATI.write_text(json.dumps({
        "aggiornato": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "problemi": problemi,
        "squadre": [{k: s[k] for k in ("id", "nome", "serie", "url")} for s in squadre],
        "partite": partite,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    if nomi:
        NOMI.write_text(json.dumps(nomi, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"Salvate {len(partite)} partite. Problemi: {len(problemi)}")
    if url_unici and len(errori) == len(url_unici):
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--prova":
        prova(sys.argv[2])
    else:
        main()
