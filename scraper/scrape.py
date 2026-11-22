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


JS_NOMI = """
() => [...document.querySelectorAll('table.rz-grid-table tbody tr')].map(tr => {
  const td = tr.querySelectorAll('td')[2];
  const d = td ? td.querySelector('div.fw-bold') : null;
  return d ? d.textContent.trim() : null;
})
"""
RIGHE_ELENCO = "table.rz-grid-table tbody tr"
INTESTAZIONE_GARA = 'span.rz-column-title[title="Gara"]'


class SquadraNonTrovata(RuntimeError):
    def __init__(self, messaggio, nomi):
        super().__init__(messaggio)
        self.nomi = nomi


def scegli_riga(nome, nomi):
    """Indice della riga dell'elenco che corrisponde al nome cercato (la piu precisa)."""
    candidati = [(i, n) for i, n in enumerate(nomi) if n and corrisponde(nome, n)]
    if not candidati:
        return None
    esatti = [c for c in candidati if norm(c[1]) == norm(nome)]
    if esatti:
        return esatti[0][0]
    return min(candidati, key=lambda c: len(norm(c[1]).split()))[0]


def leggi_tabella_partite(page):
    page.wait_for_selector(INTESTAZIONE_GARA, timeout=30000)
    page.wait_for_function(
        "() => document.querySelectorAll('table.rz-grid-table tbody tr div.rid-08').length > 0",
        timeout=30000)
    page.wait_for_timeout(1500)
    righe = page.evaluate(JS_RIGHE)
    partite = righe_a_partite(righe)
    if not partite:
        raise RuntimeError(f"tabella trovata ma nessuna partita leggibile ({len(righe)} righe)")
    return partite


def leggi_squadra(ctx, url, squadra, nome_debug):
    """Apre l'elenco del campionato, clicca sulla squadra e legge la tabella delle partite.
    Restituisce (partite_del_girone, nomi_dell_elenco)."""
    ultimo_errore = None
    for tentativo in (1, 2):
        page = ctx.new_page()
        nomi = []
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector(RIGHE_ELENCO + " td div.fw-bold", timeout=45000)
            page.wait_for_timeout(1000)
            nomi = page.evaluate(JS_NOMI)
            elenco = [n for n in nomi if n]
            i = scegli_riga(squadra["nome"], nomi)
            if i is None:
                raise SquadraNonTrovata(f"squadra '{squadra['nome']}' non trovata nell'elenco del campionato", elenco)
            page.locator(RIGHE_ELENCO).nth(i).locator("td").nth(2).locator("div.fw-bold").click()
            return leggi_tabella_partite(page), elenco
        except SquadraNonTrovata as e:
            print(f"  {e}")
            salva_debug(page, nome_debug)
            raise
        except Exception as e:
            ultimo_errore = e
            print(f"  tentativo {tentativo} fallito: {e}")
            if tentativo == 2:
                salva_debug(page, nome_debug)
                e.nomi = [n for n in nomi if n]
        finally:
            page.close()
        time.sleep(3)
    raise ultimo_errore


def prova(file_html):
    url = pathlib.Path(file_html).resolve().as_uri()
    squadre = json.loads(SQUADRE.read_text(encoding="utf-8"))
    sq = next(s for s in squadre if s["id"] == "arcus-17f")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context()
        tutte, elenco = leggi_squadra(ctx, url, sq, "prova")
        browser.close()
    print(f"Squadre nell'elenco: {len(elenco)}; partite lette: {len(tutte)}")
    print("Partite di", sq["nome"], sq["serie"])
    for r in partite_della_squadra(sq, tutte):
        print(" ", r)


def main():
    squadre = json.loads(SQUADRE.read_text(encoding="utf-8"))
    vecchio = json.loads(DATI.read_text(encoding="utf-8")) if DATI.exists() else {}
    vecchie_partite = vecchio.get("partite", [])

    DEBUG.mkdir(exist_ok=True)
    for f in DEBUG.glob("*"):
        if f.name != ".gitkeep":
            f.unlink()

    partite, problemi, nomi, falliti = [], [], {}, 0
    for i, s in enumerate(squadre):
        codice = s["url"].rstrip("/").split("/")[-2]
        etichetta = f"{s['nome']} {s['serie']}"
        print(f"Leggo {etichetta}: {s['url']}")
        try:
            tutte, elenco = leggi_squadra(RUNTIME["ctx"], s["url"], s, f"{s['id']}")
            nomi[codice] = elenco
            trovate = partite_della_squadra(s, tutte)
            print(f"  ok: {len(tutte)} partite nel girone, {len(trovate)} della squadra")
            if not trovate:
                problemi.append({"squadra": etichetta, "messaggio": "nessuna partita trovata nella tabella"})
            partite += trovate
        except Exception as e:
            falliti += 1
            if getattr(e, "nomi", None):
                nomi[codice] = e.nomi
            print(f"  ERRORE: {e}")
            problemi.append({"squadra": etichetta, "messaggio": str(e)[:160]})
            partite += [p for p in vecchie_partite if p.get("id") == s["id"]]
        if i < len(squadre) - 1:
            time.sleep(2)

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
    if squadre and falliti == len(squadre):
        sys.exit(1)


RUNTIME = {}


def avvia():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        RUNTIME["ctx"] = browser.new_context(locale="it-IT", timezone_id="Europe/Rome")
        try:
            main()
        finally:
            browser.close()


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--prova":
        prova(sys.argv[2])
    else:
        avvia()
