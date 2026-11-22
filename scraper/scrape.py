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
    nc, nt = norm(cercato), norm(nome_in_tabella)
    if not nc:
        return False
    return set(nc.split()) <= set(nt.split()) or nc in nt


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
            "id": squadra["id"], "squadra": squadra.get("etichetta", squadra["nome"]), "serie": squadra["serie"],
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



# ---------------------------------------------------------------------
#  PGS Milano (volley.pgsmilano.org): scelta di campionato, categoria e squadra
# ---------------------------------------------------------------------
PGS_URL = "https://volley.pgsmilano.org/calendari"
# la prima voce di ogni menu e un segnaposto vuoto e nascosto (display: none): va ignorata
SEL_LISTA = 'ul[role="listbox"] li[role="option"]:not([style*="display: none"])'

JS_TAB_PGS = """
() => {
  const t = [...document.querySelectorAll('table')].find(t =>
    [...t.querySelectorAll('th')].some(th => th.textContent.trim() === 'Gara'));
  if (!t) return null;
  const h = [...t.querySelectorAll('thead th')].map(th => th.textContent.trim().toLowerCase());
  const r = [...t.querySelectorAll('tbody tr')].map(tr =>
    [...tr.querySelectorAll('td')].map(td => td.textContent.replace(/\\u00a0/g, ' ').trim()));
  return {h, r};
}
"""


class ErrorePgs(RuntimeError):
    def __init__(self, messaggio, info):
        super().__init__(messaggio)
        self.info = info


def varianti_categoria(c):
    n = norm(c)
    v = [n]
    m = re.fullmatch(r"u\s*(\d+)", n)
    if m:
        k = m.group(1)
        v += [f"u{k}", f"u {k}", f"under {k}", f"under{k}"]
    return v


def contiene_parola(testo_norm, variante):
    return re.search(r"(^| )" + re.escape(variante), testo_norm) is not None


def menu(page, aria):
    return page.locator(f'div[role="combobox"][aria-labelledby="{aria}"]')


def leggi_opzioni(page, attesa=15000):
    page.wait_for_selector(SEL_LISTA, timeout=attesa)
    return [t.strip() for t in page.locator(SEL_LISTA).all_inner_texts()]


def chiudi_lista(page):
    """Chiude il menu aperto. Escape non sempre arriva al menu: se resta aperto clicca fuori."""
    for metodo in ("escape", "fuori"):
        if metodo == "escape":
            page.keyboard.press("Escape")
        else:
            page.mouse.click(3, 3)
        try:
            page.wait_for_selector('ul[role="listbox"]', state="detached", timeout=2500)
            return
        except Exception:
            pass


def clicca_opzione(page, i):
    page.locator(SEL_LISTA).nth(i).click()
    try:
        page.wait_for_selector('ul[role="listbox"]', state="detached", timeout=10000)
    except Exception:
        pass
    page.wait_for_timeout(800)


def apri_menu(page, aria):
    menu(page, aria).click()
    return leggi_opzioni(page)


def elenco_squadre(page):
    """Apre il campo Squadra e restituisce le opzioni (riprova se l'elenco non e ancora pronto)."""
    inp = page.locator("#available-teams")
    for _ in range(4):
        inp.click()
        try:
            return leggi_opzioni(page, attesa=6000)
        except Exception:
            chiudi_lista(page)
            page.wait_for_timeout(1500)
    return []


def pgs_righe(tab):
    h = tab["h"]

    def col(nome, default):
        return h.index(nome) if nome in h else default

    i_gara, i_data, i_ora = col("gara", 0), col("data", 2), col("ora", 3)
    i_ind, i_casa, i_osp = col("indirizzo", 4), col("squadra in casa", 5), col("squadra in trasferta", 6)
    partite = []
    for c in tab["r"]:
        if len(c) <= max(i_data, i_ora, i_casa, i_osp):
            continue
        m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", c[i_data])
        if not m:
            continue
        t = re.search(r"(\d{1,2})[.:](\d{2})", c[i_ora])
        partite.append({
            "gara": c[i_gara] if i_gara < len(c) else "",
            "data": f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}",
            "ora": f"{int(t.group(1)):02d}:{t.group(2)}" if t else "",
            "casa": c[i_casa], "ospite": c[i_osp],
            "campo": "",
            "indirizzo": c[i_ind] if i_ind < len(c) else "",
        })
    return partite


def pgs_una_volta(page, squadra, info):
    page.goto(PGS_URL, wait_until="domcontentloaded", timeout=60000)
    box = menu(page, "available-campionati")
    box.wait_for(timeout=45000)
    for _ in range(30):
        if norm(box.inner_text()):
            break
        page.wait_for_timeout(1000)

    # campionato (la stagione)
    voluto = norm(squadra["campionato"])
    if voluto not in norm(box.inner_text()):
        opz = apri_menu(page, "available-campionati")
        info["campionati"] = opz
        scelte = [i for i, t in enumerate(opz) if voluto in norm(t)]
        if not scelte:
            raise ErrorePgs(f"campionato '{squadra['campionato']}' non trovato", info)
        clicca_opzione(page, min(scelte, key=lambda i: len(opz[i])))
    else:
        info["campionati"] = [box.inner_text().strip()]

    # categoria: provo ogni categoria che somiglia a quella cercata, finche trovo la squadra
    opz_cat = apri_menu(page, "available-categorie")
    info["categorie"] = opz_cat
    var = varianti_categoria(squadra["categoria"])
    candidate = [i for i, t in enumerate(opz_cat) if any(contiene_parola(norm(t), v) for v in var)]
    if not candidate:
        chiudi_lista(page)
        raise ErrorePgs(f"categoria '{squadra['categoria']}' non trovata", info)
    for k, i in enumerate(candidate):
        if k > 0:  # il menu e gia aperto la prima volta; poi va riaperto
            apri_menu(page, "available-categorie")
        clicca_opzione(page, i)
        opz_sq = elenco_squadre(page)
        info.setdefault("squadre", {})[opz_cat[i]] = opz_sq
        j = scegli_riga(squadra["nome"], opz_sq)
        if j is None:
            chiudi_lista(page)
            continue
        clicca_opzione(page, j)
        fine = time.time() + 30
        while time.time() < fine:
            tab = page.evaluate(JS_TAB_PGS)
            if tab:
                partite = pgs_righe(tab)
                if any(corrisponde(squadra["nome"], p["casa"]) or corrisponde(squadra["nome"], p["ospite"]) for p in partite):
                    return partite
            page.wait_for_timeout(1000)
        raise ErrorePgs("la tabella delle partite non e comparsa dopo la scelta della squadra", info)
    raise ErrorePgs(f"squadra '{squadra['nome']}' non trovata nelle categorie {[opz_cat[i] for i in candidate]}", info)


def leggi_pgs(ctx, squadra, nome_debug, url=None):
    global PGS_URL
    if url:
        PGS_URL = url
    info = {}
    ultimo = None
    for tentativo in (1, 2):
        page = ctx.new_page()
        try:
            return pgs_una_volta(page, squadra, info), info
        except Exception as e:
            ultimo = e
            print(f"  tentativo {tentativo} fallito: {e}")
            if tentativo == 2:
                salva_debug(page, nome_debug)
        finally:
            page.close()
        time.sleep(3)
    if not isinstance(ultimo, ErrorePgs):
        ultimo = ErrorePgs(str(ultimo), info)
    raise ultimo


def prova_pgs(file_html, id_squadra):
    url = pathlib.Path(file_html).resolve().as_uri()
    squadre = json.loads(SQUADRE.read_text(encoding="utf-8"))
    sq = next(s for s in squadre if s["id"] == id_squadra)
    sq = {**sq, "nome": sq.get("cerca", sq["nome"]), "etichetta": sq["nome"]}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context()
        tutte, info = leggi_pgs(ctx, sq, "prova-pgs", url=url)
        browser.close()
    print(f"Partite lette: {len(tutte)}; categorie viste: {info.get('categorie')}")
    for r in partite_della_squadra(sq, tutte):
        print(" ", r)



# ---------------------------------------------------------------------
#  Calendari regionali FIPAV Lombardia (matchshare.it): una pagina per girone
# ---------------------------------------------------------------------
JS_MATCHSHARE = """
() => [...document.querySelectorAll('tr.match[data-mid]')].map(tr => ({
  gara: tr.dataset.numero || '',
  giorno: tr.dataset.giorno || '',
  ora: tr.dataset.ora || '',
  squadre: [...tr.querySelectorAll('span.squadra')].map(s => s.textContent.replace(/\\s+/g, ' ').trim()),
  luogo: ((tr.querySelector('td.placetd') || {}).textContent || '').replace(/\\s+/g, ' ').trim()
}))
"""


def matchshare_a_partite(righe):
    partite = []
    for r in righe:
        if len(r.get("squadre", [])) < 2:
            continue
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", r.get("giorno", ""))
        if not m:
            continue
        t = re.match(r"(\d{1,2}):(\d{2})", r.get("ora", ""))
        partite.append({
            "gara": r.get("gara", ""),
            "data": f"{m.group(1)}-{m.group(2)}-{m.group(3)}",
            "ora": f"{int(t.group(1)):02d}:{t.group(2)}" if t else "",
            "casa": r["squadre"][0], "ospite": r["squadre"][1],
            "campo": r.get("luogo", ""), "indirizzo": "",
        })
    return partite


def leggi_matchshare(ctx, url, nome_debug):
    """Apre la pagina del girone e legge tutte le partite (la pagina contiene tutte le giornate).
    Restituisce (partite, nomi_delle_squadre)."""
    ultimo = None
    for tentativo in (1, 2):
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector("tr.match[data-mid]", state="attached", timeout=45000)
            partite = matchshare_a_partite(page.evaluate(JS_MATCHSHARE))
            if not partite:
                raise RuntimeError("pagina trovata ma nessuna partita leggibile")
            nomi = sorted({n for q in partite for n in (q["casa"], q["ospite"])})
            return partite, nomi
        except Exception as e:
            ultimo = e
            print(f"  tentativo {tentativo} fallito: {e}")
            if tentativo == 2:
                salva_debug(page, nome_debug)
        finally:
            page.close()
        time.sleep(3)
    raise ultimo


def prova_matchshare(file_html, id_squadra):
    url = pathlib.Path(file_html).resolve().as_uri()
    squadre = json.loads(SQUADRE.read_text(encoding="utf-8"))
    sq = next(s for s in squadre if s["id"] == id_squadra)
    sq = {**sq, "nome": sq.get("cerca", sq["nome"]), "etichetta": sq["nome"]}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context()
        tutte, nomi = leggi_matchshare(ctx, url, "prova-ms")
        browser.close()
    mie = partite_della_squadra(sq, tutte)
    print(f"Partite nel girone: {len(tutte)}; squadre: {len(nomi)}; della squadra: {len(mie)}")
    for r in mie[:4]:
        print(" ", r)


def unisci_info(tot, info):
    for k in ("campionati", "categorie"):
        for v in info.get(k, []):
            if v not in tot[k]:
                tot[k].append(v)
    tot["squadre"].update(info.get("squadre", {}))


def main():
    squadre = json.loads(SQUADRE.read_text(encoding="utf-8"))
    vecchio = json.loads(DATI.read_text(encoding="utf-8")) if DATI.exists() else {}
    vecchie_partite = vecchio.get("partite", [])

    DEBUG.mkdir(exist_ok=True)
    for f in DEBUG.glob("*"):
        if f.name != ".gitkeep":
            f.unlink()

    partite, problemi, nomi, falliti = [], [], {}, 0
    pgs_info = {"campionati": [], "categorie": [], "squadre": {}}
    for i, s0 in enumerate(squadre):
        # "nome" e quello mostrato nell'app; "cerca" (se c'e) e il nome usato sul sito
        s = {**s0, "nome": s0.get("cerca", s0["nome"]), "etichetta": s0["nome"]}
        codice = s["url"].rstrip("/").split("/")[-2]
        etichetta = f"{s0['nome']} {s0['serie']}"
        print(f"Leggo {etichetta}: {s['url']}")
        try:
            if s.get("fonte") == "manuale":
                # calendario inserito a mano (sito non raggiungibile da GitHub)
                f = RADICE / "data" / "manuale" / f"{s['id']}.json"
                trovate = [{"id": s["id"], "squadra": s["etichetta"], "serie": s["serie"], **p}
                           for p in json.loads(f.read_text(encoding="utf-8"))]
                print(f"  manuale: {len(trovate)} partite")
                partite += trovate
                continue
            if s.get("fonte") == "matchshare":
                tutte, elenco = leggi_matchshare(RUNTIME["ctx"], s["url"], s["id"])
                nomi[s["id"]] = elenco
            elif s.get("fonte") == "pgs":
                tutte, info = leggi_pgs(RUNTIME["ctx"], s, s["id"], url=s["url"])
                unisci_info(pgs_info, info)
            else:
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
            if getattr(e, "info", None):
                unisci_info(pgs_info, e.info)
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
    if pgs_info["categorie"] or pgs_info["squadre"]:
        nomi["pgs"] = pgs_info
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
    if len(sys.argv) > 3 and sys.argv[1] == "--prova-ms":
        prova_matchshare(sys.argv[2], sys.argv[3])
    elif len(sys.argv) > 3 and sys.argv[1] == "--prova-pgs":
        prova_pgs(sys.argv[2], sys.argv[3])
    elif len(sys.argv) > 2 and sys.argv[1] == "--prova":
        prova(sys.argv[2])
    else:
        avvia()
