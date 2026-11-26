# -*- coding: utf-8 -*-
import csv
import gzip
import hashlib
import json
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

ORIGEN = Path("descarga_audit")
DESTINO_DB = Path("catalogo.db.gz")
DESTINO_VERSION = Path("catalogo_version.json")
MIN_PROPORCION = 0.9   # si una tabla trae menos del 90% de lo anterior, se aborta
ARCHIVO_OCULTOS = Path("ocultos.csv")
ARCHIVO_REGLAS = Path("reglas_ocultos.json")
ARCHIVO_ETAPAS = Path("etapas.csv")

# tabla: (columnas, funcion que arma cada fila)
ESQUEMA = {
    "pais": "id INTEGER PRIMARY KEY, nombre TEXT, codigo TEXT",
    "deporte": "id INTEGER PRIMARY KEY, nombre TEXT, codigo TEXT",
    "categoria": "id INTEGER PRIMARY KEY, nombre TEXT, codigo TEXT, pais_id INTEGER, deporte_id INTEGER",
    "torneo": "id INTEGER PRIMARY KEY, nombre TEXT, codigo TEXT, categoria_id INTEGER",
    "evento": "id INTEGER PRIMARY KEY, nombre TEXT, codigo TEXT, categoria_id INTEGER, fecha TEXT",
    "senal": "id INTEGER PRIMARY KEY, nombre TEXT, codigo TEXT, pais_id INTEGER",
    "programa": "id INTEGER PRIMARY KEY, nombre TEXT, codigo TEXT, senal_id INTEGER",
    "equipo": "id INTEGER PRIMARY KEY, nombre TEXT, codigo TEXT, pais_id INTEGER, deporte_id INTEGER",
    "etapa": "id INTEGER PRIMARY KEY, nombre TEXT, codigo TEXT, deporte_id INTEGER, orden INTEGER, mostrar TEXT",
    "empresa": "id INTEGER PRIMARY KEY, nombre TEXT, codigo TEXT, activo INTEGER",
    "marca": "id INTEGER PRIMARY KEY, nombre TEXT, codigo TEXT, empresa_id INTEGER",
    "elemento": "id INTEGER PRIMARY KEY, nombre TEXT, codigo TEXT, clasificacion TEXT, subtipo TEXT",
    "medioGrafico": "id INTEGER PRIMARY KEY, nombre TEXT, codigo TEXT, tipo TEXT, pais_id INTEGER",
}

avisos = []


def leer(entidad):
    archivo = ORIGEN / f"{entidad}.csv"
    if not archivo.exists():
        print(f"[!] Falta {archivo}. No se publica nada.")
        sys.exit(1)
    with open(archivo, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def indice(filas):
    """nombre -> id, solo para nombres unicos."""
    vistos = {}
    for r in filas:
        vistos.setdefault(r["Nombre"], []).append(int(r["id_interno"]))
    return {k: v[0] for k, v in vistos.items() if len(v) == 1}


def ref(idx, nombre, contexto):
    if nombre in idx:
        return idx[nombre]
    avisos.append(f"{contexto}: no se pudo resolver '{nombre}'")
    return None


def fecha_iso(texto):
    try:
        return datetime.strptime(texto, "%d/%m/%Y").date().isoformat()
    except ValueError:
        return texto or None


def armar_filas():
    d = {e: leer(e) for e in ESQUEMA}
    i_pais, i_dep = indice(d["pais"]), indice(d["deporte"])
    i_cat, i_emp, i_sen = indice(d["categoria"]), indice(d["empresa"]), indice(d["senal"])

    def cat_id(r, ent):
        if r.get("categoria_id"):
            return int(r["categoria_id"])
        return ref(i_cat, r["Categoria"], f"{ent} {r['id_interno']}")

    t = {}
    t["pais"] = [(int(r["id_interno"]), r["Nombre"], r["Codigo"]) for r in d["pais"]]
    t["deporte"] = [(int(r["id_interno"]), r["Nombre"], r["Codigo"]) for r in d["deporte"]]
    t["categoria"] = [(int(r["id_interno"]), r["Nombre"], r["Codigo"],
                       ref(i_pais, r["Pais"], f"categoria {r['id_interno']}"),
                       ref(i_dep, r["Deporte"], f"categoria {r['id_interno']}"))
                      for r in d["categoria"]]
    t["torneo"] = [(int(r["id_interno"]), r["Nombre"], r["Codigo"], cat_id(r, "torneo"))
                   for r in d["torneo"]]
    t["evento"] = [(int(r["id_interno"]), r["Nombre"], r["Codigo"], cat_id(r, "evento"),
                    fecha_iso(r["Fecha"])) for r in d["evento"]]
    t["senal"] = [(int(r["id_interno"]), r["Nombre"], r["Codigo"],
                   ref(i_pais, r["Pais"], f"senal {r['id_interno']}")) for r in d["senal"]]
    t["programa"] = [(int(r["id_interno"]), r["Nombre"], r["Codigo"],
                      ref(i_sen, r["Senal"], f"programa {r['id_interno']}")) for r in d["programa"]]
    t["equipo"] = [(int(r["id_interno"]), r["Nombre"], r["Codigo"],
                    ref(i_pais, r["Pais"], f"equipo {r['id_interno']}"),
                    ref(i_dep, r["Deporte"], f"equipo {r['id_interno']}")) for r in d["equipo"]]
    etapas_cfg = cargar_etapas()

    def etapa(r):
        cfg = etapas_cfg.get((r["Codigo"] or "").upper())
        orden_base = int(r["Orden"]) if r["Orden"].isdigit() else 0
        orden = cfg[1] if cfg else 1000 + orden_base   # las no listadas van al final
        return (int(r["id_interno"]), r["Nombre"], r["Codigo"],
                ref(i_dep, r["Deporte"], f"etapa {r['id_interno']}"),
                orden, cfg[0] if cfg else None)

    t["etapa"] = [etapa(r) for r in d["etapa"]]
    t["empresa"] = [(int(r["id_interno"]), r["Nombre"], r["Codigo"],
                     1 if r["Activo"] == "True" else 0) for r in d["empresa"]]
    t["marca"] = [(int(r["id_interno"]), r["Nombre"], r["Codigo"],
                   ref(i_emp, r["Empresa"], f"marca {r['id_interno']}")) for r in d["marca"]]
    t["elemento"] = [(int(r["id_interno"]), r["Nombre"], r["Codigo"], r["Clasificacion"],
                      r["Subtipo"]) for r in d["elemento"]]
    t["medioGrafico"] = [(int(r["id_interno"]), r["Nombre"], r["Codigo"], r["Tipo"],
                          ref(i_pais, r["Pais"], f"medioGrafico {r['id_interno']}"))
                         for r in d["medioGrafico"]]
    for nombre in t:
        t[nombre].sort(key=lambda fila: fila[0])
    return t


def cargar_etapas():
    """{CODIGO: (nombre_a_mostrar, orden)} de etapas.csv."""
    cfg = {}
    if not ARCHIVO_ETAPAS.exists():
        return cfg
    with open(ARCHIVO_ETAPAS, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f, delimiter=";"):
            codigo = (r.get("codigo") or "").strip().upper()
            if codigo:
                orden = (r.get("orden") or "").strip()
                cfg[codigo] = ((r.get("mostrar") or "").strip() or None,
                               int(orden) if orden.isdigit() else 999)
    return cfg


def cargar_ocultos():
    """{(tabla, CODIGO)} de ocultos.csv + reglas de reglas_ocultos.json."""
    marcados = set()
    if ARCHIVO_OCULTOS.exists():
        with open(ARCHIVO_OCULTOS, encoding="utf-8-sig") as f:
            for fila in csv.reader(f, delimiter=";"):
                if len(fila) >= 2 and fila[0].strip() and fila[0].strip().lower() != "tabla":
                    marcados.add((fila[0].strip(), fila[1].strip().upper()))
    reglas = {}
    if ARCHIVO_REGLAS.exists():
        reglas = json.loads(ARCHIVO_REGLAS.read_text(encoding="utf-8"))
    return marcados, reglas


def aplicar_ocultos(tablas):
    """Agrega la columna oculto (0/1) al final de cada fila."""
    marcados, reglas = cargar_ocultos()
    antes_de = reglas.get("evento_fecha_antes_de")
    conteo = {}
    for nombre, filas in tablas.items():
        nuevas = []
        for fila in filas:
            oculto = (nombre, (fila[2] or "").upper()) in marcados
            if nombre == "evento" and antes_de and fila[4] and fila[4] < antes_de:
                oculto = True
            nuevas.append(fila + (1 if oculto else 0,))
        tablas[nombre] = nuevas
        conteo[nombre] = sum(f[-1] for f in nuevas)
    no_encontrados = {m for m in marcados
                      if not any((fila[2] or "").upper() == m[1] for fila in tablas.get(m[0], []))}
    for tabla, codigo in sorted(no_encontrados):
        avisos.append(f"ocultos.csv: no existe {tabla} {codigo}")
    return conteo


def calcular_hash(tablas):
    h = hashlib.sha256()
    for nombre in sorted(tablas):
        h.update(nombre.encode())
        h.update(json.dumps(tablas[nombre], ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()


def main():
    tablas = armar_filas()
    ocultos = aplicar_ocultos(tablas)
    conteos = {k: len(v) for k, v in tablas.items()}
    nuevo_hash = calcular_hash(tablas)

    anterior = {}
    if DESTINO_VERSION.exists():
        anterior = json.loads(DESTINO_VERSION.read_text(encoding="utf-8"))

    # Seguridad: si una tabla vino mucho mas chica, algo fallo en el scraping
    for nombre, cant in conteos.items():
        previo = anterior.get("registros", {}).get(nombre, 0)
        if previo and cant < previo * MIN_PROPORCION:
            print(f"[!] {nombre}: {cant} registros contra {previo} anteriores. No se publica.")
            sys.exit(1)

    for a in avisos:
        print(f"  aviso: {a}")

    if anterior.get("hash") == nuevo_hash:
        print("Sin cambios. No se publica nada.")
        return

    tmp = Path("catalogo.db")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    for nombre, columnas in ESQUEMA.items():
        con.execute(f"CREATE TABLE {nombre} ({columnas}, oculto INTEGER DEFAULT 0)")
        marcas = ",".join("?" * len(tablas[nombre][0]))
        con.executemany(f"INSERT INTO {nombre} VALUES ({marcas})", tablas[nombre])
    con.commit()
    con.execute("VACUUM")
    con.close()

    with open(tmp, "rb") as f_in, gzip.GzipFile(DESTINO_DB, "wb", mtime=0) as f_out:
        f_out.write(f_in.read())
    tmp.unlink()

    version = {
        "version": date.today().isoformat(),
        "hash": nuevo_hash,
        "registros": conteos,
        "ocultos": {k: v for k, v in ocultos.items() if v},
    }
    DESTINO_VERSION.write_text(json.dumps(version, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Publicado: version {version['version']}, {DESTINO_DB.stat().st_size // 1024} KB")
    print(json.dumps(conteos, ensure_ascii=False))


if __name__ == "__main__":
    main()
