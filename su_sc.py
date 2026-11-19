# -*- coding: utf-8 -*-


import argparse
import csv
import os
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "http://34.231.211.160:8080"
PAGE_SIZE = 100          
PAUSA_SEG = 0.5          


ENTIDADES = [
    "categoria",
    "deporte",
    "elemento",
    "empresa",
    "equipo",
    "etapa",
    "evento",
    "marca",
    "pais",
    "programa",
    "senal",        
    "medioGrafico",  
    "torneo",
]


def login(session):
    usuario = os.environ.get("AUDIT_USER")
    clave = os.environ.get("AUDIT_PASS")
    if not usuario or not clave:
        print("[!] Faltan las variables de entorno USER y PASS")
        sys.exit(1)
    session.get(f"{BASE}/", timeout=30)  # sesion inicial
    r = session.post(
        f"{BASE}/j_spring_security_check",
        data={"j_username": usuario, "j_password": clave},
        timeout=30,
    )
    r.raise_for_status()
    print(f"Login OK como {usuario}")


def parsear_lista(html):
    """Devuelve (headers, filas) de la tabla de lista de Grails.
    Cada fila es un dict header->valor, mas 'id_interno' sacado del link show."""
    soup = BeautifulSoup(html, "html.parser")
    # La tabla de datos es la unica con <thead>; la otra (filterPaneTable) no lo tiene.
    tabla = None
    for t in soup.find_all("table"):
        if t.find("thead") is not None and t.find("tbody") is not None:
            tabla = t
            break
    if tabla is None:
        return [], []

    headers = [th.get_text(strip=True) for th in tabla.find("thead").find_all("th")]
    filas = []
    for tr in tabla.find("tbody").find_all("tr"):
        celdas = tr.find_all("td")
        fila = {}
        id_interno = ""
        for head, td in zip(headers, celdas):
            img = td.find("img")
            if img is not None:
                # columna de logo: guardamos la URL de la imagen
                fila[head] = img.get("src", "")
            else:
                fila[head] = td.get_text(strip=True)
            a = td.find("a")
            if a is not None and "/show/" in a.get("href", ""):
                id_interno = a["href"].rstrip("/").split("/")[-1]
        fila["id_interno"] = id_interno
        filas.append(fila)
    return headers, filas


def scrapear_entidad(session, entidad, outdir):
    offset = 0
    todas = []
    headers = []
    while True:
        url = f"{BASE}/{entidad}/list?offset={offset}&max={PAGE_SIZE}"
        r = session.get(url, timeout=30)
        if r.status_code == 404:
            print(f"  [!] {entidad}: 404 (el path no existe, revisar nombre)")
            return False
        r.raise_for_status()
        if "Cerrar" not in r.text and "logout" not in r.text.lower():
            print("  [!] Parece que la sesion expiro (no veo 'Cerrar sesion'). Renova el JSESSIONID.")
            sys.exit(1)
        h, filas = parsear_lista(r.text)
        if h and not headers:
            headers = h
        todas.extend(filas)
        print(f"  {entidad}: offset {offset}, +{len(filas)} filas (total {len(todas)})")
        if len(filas) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        time.sleep(PAUSA_SEG)

    if not todas:
        print(f"  [!] {entidad}: sin filas")
        return False

    columnas = ["id_interno"] + headers
    destino = outdir / f"{entidad}.csv"
    with open(destino, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columnas, extrasaction="ignore")
        w.writeheader()
        w.writerows(todas)
    print(f"  OK -> {destino} ({len(todas)} registros)")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entidades", nargs="*", default=ENTIDADES,
                    help="Lista de entidades a bajar (default: todas)")
    args = ap.parse_args()

    outdir = Path("descarga_audit")
    outdir.mkdir(exist_ok=True)

    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0"
    login(s)

    fallidas = []
    for ent in args.entidades:
        print(f"\n== {ent} ==")
        try:
            if not scrapear_entidad(s, ent, outdir):
                fallidas.append(ent)
        except Exception as e:
            print(f"  [!] {ent}: error {e}")
            fallidas.append(ent)

    print("\n--- Resumen ---")
    print(f"OK: {len(args.entidades) - len(fallidas)} entidades")
    if fallidas:
        print(f"Fallaron: {', '.join(fallidas)}")



if __name__ == "__main__":
    main()
