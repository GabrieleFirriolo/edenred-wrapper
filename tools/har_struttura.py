"""Estrae da un HAR (o da mutations.log) solo la STRUTTURA delle chiamate al portale.

Tiene: metodo, URL normalizzato (id sostituiti, solo i nomi dei parametri), nomi degli
header e dei cookie, forma dei corpi JSON/form (chiavi + tipo), status e forma delle risposte.
Toglie: tutti i valori (cookie, token, password, OTP, nomi, codici fiscali, importi…).

Uso (sul Mac di chi ha registrato il HAR; solo libreria standard):
  python3 -I har_struttura.py ~/.edenred-gateway/har/*.har --hosts edenred.it -o struttura.json
  python3 -I har_struttura.py ~/.edenred-gateway/mutations.log --hosts edenred.it -o mutazioni.json

Controlla l'output prima di condividerlo: alla fine lo script segnala eventuali stringhe
sospette rimaste.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

STATIC_RE = re.compile(r"\.(js|mjs|css|png|jpe?g|gif|svg|webp|ico|woff2?|ttf|eot|map)$", re.I)
# Header di cui il valore è utile e innocuo; per tutti gli altri si tiene solo il nome.
SAFE_HEADER_VALUES = {"content-type", "accept", "x-requested-with"}
AUTH_HINT_RE = re.compile(r"auth|token|csrf|xsrf|session|bearer|otp|api-?key", re.I)

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
CF_RE = re.compile(r"^[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]$", re.I)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$|^\d{2}/\d{2}/\d{4}$")
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
NUM_RE = re.compile(r"^-?\d+([.,]\d+)?$")
JWT_RE = re.compile(r"^eyJ[\w-]+\.[\w-]+\.[\w-]*$")
TOKENISH_RE = re.compile(r"^[\w\-+/=.~]{20,}$")


# ---------------------------------------------------------------- tipi e forme
def tipo_stringa(s: str) -> str:
    """Descrive una stringa senza riportarne il valore."""
    if s == "":
        return "str(vuota)"
    for nome, rx in (("uuid", UUID_RE), ("codice_fiscale", CF_RE), ("email", EMAIL_RE),
                     ("iban", IBAN_RE), ("datetime", DATETIME_RE), ("data", DATE_RE),
                     ("numero", NUM_RE), ("jwt", JWT_RE)):
        if rx.match(s):
            return f"str({nome})"
    if TOKENISH_RE.match(s):
        return "str(token?)"
    return "str"


def chiave_sicura(k: str) -> str:
    """Le chiavi a volte sono dati (mappe id -> oggetto): in quel caso le maschera."""
    t = tipo_stringa(k)
    if t in ("str", "str(vuota)") and len(k) <= 64:
        return k
    return "{" + t + "}"


def forma(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return tipo_stringa(v)
    if isinstance(v, list):
        if not v:
            return []
        merged = forma(v[0])
        for x in v[1:]:
            merged = unisci(merged, forma(x))
        return [merged]
    if isinstance(v, dict):
        out: dict = {}
        for k, x in v.items():
            ks = chiave_sicura(str(k))
            out[ks] = unisci(out[ks], forma(x)) if ks in out else forma(x)
        return out
    return type(v).__name__


def unisci(a, b):
    """Unisce due forme (per elementi di liste e chiamate ripetute)."""
    if a == b:
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = unisci(out[k], v) if k in out else v
        return out
    if isinstance(a, list) and isinstance(b, list):
        if not a or not b:
            return a or b
        return [unisci(a[0], b[0])]
    if not isinstance(a, str) or not isinstance(b, str):
        return f"{json.dumps(a)} | {json.dumps(b)}"  # forme incompatibili: le tiene entrambe
    return " | ".join(sorted(set(a.split(" | ")) | set(b.split(" | "))))


def forma_corpo(testo: str | None, mime: str = "") -> object:
    if not testo:
        return None
    testo = testo.strip()
    if "json" in mime or testo[:1] in "{[":
        try:
            return {"json": forma(json.loads(testo))}
        except ValueError:
            if testo[:1] in "{[":
                return {"json_troncato_o_non_valido": f"{len(testo)} caratteri"}
    if "x-www-form-urlencoded" in mime or ("=" in testo and " " not in testo[:200]):
        coppie = parse_qsl(testo, keep_blank_values=True)
        if coppie:
            return {"form": {chiave_sicura(k): tipo_stringa(v) for k, v in coppie}}
    return {"testo": f"{len(testo)} caratteri ({mime or 'tipo sconosciuto'})"}


# ---------------------------------------------------------------- URL
def segmento(s: str) -> str:
    t = tipo_stringa(s)
    if t == "str(numero)":
        return "{id}"
    if t in ("str", "str(vuota)") and len(s) <= 40:
        return s
    return "{" + t.removeprefix("str(").removesuffix(")") + "}"


def normalizza_url(url: str) -> tuple[str, str, list[str]]:
    u = urlparse(url)
    path = "/".join(segmento(s) for s in u.path.split("/"))
    query = sorted({chiave_sicura(k) for k, _ in parse_qsl(u.query, keep_blank_values=True)})
    return u.hostname or "", path, query


def host_ok(host: str, hosts: list[str]) -> bool:
    return not hosts or any(host == h or host.endswith("." + h) for h in hosts)


# ---------------------------------------------------------------- HAR
def nomi_header(headers: list[dict]) -> dict:
    out = {}
    for h in headers:
        n = h.get("name", "").lower()
        if n in ("cookie", "set-cookie") or n.startswith(":"):
            continue
        if n in SAFE_HEADER_VALUES:
            out[n] = h.get("value", "").split(";")[0]
        else:
            out[n] = "<auth?>" if AUTH_HINT_RE.search(n) else "<valore>"
    return out


def nomi_set_cookie(headers: list[dict]) -> list[str]:
    nomi = []
    for h in headers:
        if h.get("name", "").lower() == "set-cookie":
            for riga in h.get("value", "").split("\n"):
                nome = riga.split("=", 1)[0].strip()
                if nome:
                    nomi.append(nome)
    return sorted(set(nomi))


def da_har(path: Path, hosts: list[str]):
    har = json.loads(path.read_text(encoding="utf-8"))
    for e in har.get("log", {}).get("entries", []):
        req, res = e.get("request", {}), e.get("response", {}) or {}
        url = req.get("url", "")
        host, p, query = normalizza_url(url)
        if not host_ok(host, hosts) or STATIC_RE.search(urlparse(url).path):
            continue
        post = req.get("postData") or {}
        mime_req = post.get("mimeType", "")
        corpo_req = forma_corpo(post.get("text"), mime_req)
        if corpo_req is None and post.get("params"):
            corpo_req = {"multipart": sorted({chiave_sicura(x.get("name", ""))
                                              for x in post["params"]})}
        content = res.get("content") or {}
        mime_res = content.get("mimeType", "")
        corpo_res = None
        if "json" in mime_res:
            corpo_res = forma_corpo(content.get("text"), mime_res)
        status = res.get("status")
        loc = next((h.get("value", "") for h in res.get("headers", [])
                    if h.get("name", "").lower() == "location"), "")
        yield {
            "metodo": req.get("method"),
            "host": host,
            "path": p,
            "query": query,
            "header_richiesta": nomi_header(req.get("headers", [])),
            "cookie_inviati": sorted({c.get("name", "") for c in req.get("cookies", [])}),
            "corpo_richiesta": corpo_req,
            "status": status if status not in (0, -1, None) else "nessuna risposta (annullata?)",
            "redirect_a": "".join(normalizza_url(loc)[:2]) if loc else None,
            "cookie_impostati": nomi_set_cookie(res.get("headers", [])),
            "tipo_risposta": mime_res.split(";")[0] or None,
            "corpo_risposta": corpo_res,
        }


def da_mutations_log(path: Path, hosts: list[str]):
    for riga in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(riga)
        except ValueError:
            continue
        host, p, query = normalizza_url(r.get("u", ""))
        if not host_ok(host, hosts):
            continue
        b = r.get("b") or ""
        corpo = forma_corpo(b)
        if len(b) >= 2000 and isinstance(corpo, dict):
            corpo["attenzione"] = "corpo troncato a 2000 caratteri da mutations.log"
        yield {"metodo": r.get("m"), "host": host, "path": p, "query": query,
               "corpo_richiesta": corpo}


# ---------------------------------------------------------------- raggruppamento
def raggruppa(voci) -> list[dict]:
    """Una voce per (metodo, host, path), in ordine di prima comparsa."""
    gruppi: dict[tuple, dict] = {}
    for i, v in enumerate(voci):
        k = (v["metodo"], v["host"], v["path"])
        if k not in gruppi:
            gruppi[k] = {"ordine": i, "volte": 0, **v}
            if "status" in v:
                gruppi[k]["status"] = [v["status"]]
        g = gruppi[k]
        g["volte"] += 1
        if g["volte"] == 1:
            continue
        g["query"] = sorted(set(g["query"]) | set(v["query"]))
        for campo in ("corpo_richiesta", "corpo_risposta"):
            if v.get(campo) is not None:
                g[campo] = unisci(g[campo], v[campo]) if g.get(campo) is not None else v[campo]
        for campo in ("cookie_inviati", "cookie_impostati"):
            if campo in v:
                g[campo] = sorted(set(g[campo]) | set(v[campo]))
        if "header_richiesta" in v:
            g["header_richiesta"] = {**g["header_richiesta"], **v["header_richiesta"]}
        if "status" in v and v["status"] not in g["status"]:
            g["status"].append(v["status"])
    return sorted(gruppi.values(), key=lambda g: g["ordine"])


# ---------------------------------------------------------------- controllo finale
SOSPETTI = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("codice fiscale", re.compile(r"\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b", re.I)),
    ("jwt", re.compile(r"eyJ[\w-]{10,}\.[\w-]{10,}")),
    ("iban", re.compile(r"\bIT\d{2}[A-Z]\d{10}[0-9A-Z]{12}\b")),
    ("stringa lunga", re.compile(r"[A-Za-z0-9+/_-]{32,}")),
]


def controllo(testo: str) -> list[str]:
    avvisi = []
    for nome, rx in SOSPETTI:
        for m in rx.finditer(testo):
            avvisi.append(f"{nome}: …{testo[max(0, m.start() - 30):m.start()]}[{len(m.group())} car.]")
    return avvisi


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="+", type=Path, help="file .har o mutations.log")
    ap.add_argument("--hosts", default=os.getenv("ALLOWED_HOSTS", ""),
                    help="domini da tenere, separati da virgola (default: $ALLOWED_HOSTS)")
    ap.add_argument("-o", "--output", type=Path, default=Path("struttura.json"))
    a = ap.parse_args()
    hosts = [h.strip() for h in a.hosts.split(",") if h.strip()]
    if not hosts:
        print("Attenzione: nessun dominio indicato, tengo tutto il traffico.", file=sys.stderr)

    voci = []
    for f in a.file:
        voci += list(da_mutations_log(f, hosts) if f.suffix == ".log" else da_har(f, hosts))
    out = {"sorgenti": [f.name for f in a.file], "domini": hosts, "chiamate": raggruppa(voci)}
    testo = json.dumps(out, indent=2, ensure_ascii=False)
    a.output.write_text(testo + "\n", encoding="utf-8")

    for c in out["chiamate"]:
        print(f'{c["metodo"]:6} {c["host"]}{c["path"]}  x{c["volte"]}  {c.get("status", "")}')
    print(f"\n{len(out['chiamate'])} chiamate distinte → {a.output}")
    avvisi = controllo(testo)
    if avvisi:
        print("\n⚠️  Da controllare prima di condividere (potrebbero essere dati):")
        for x in avvisi:
            print("  ", x)
    else:
        print("Nessuna stringa sospetta trovata. Dai comunque un'occhiata al file.")


if __name__ == "__main__":
    main()
