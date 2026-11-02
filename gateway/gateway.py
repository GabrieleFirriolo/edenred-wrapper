"""Edenred browser gateway per agenti.

- Auth: il login lo fa un umano nella finestra Chromium (anche MFA/OTP); la sessione
  resta nel profilo locale. L'agente non vede né digita mai credenziali.
- Agente: legge la pagina e usa mouse/tastiera tramite MCP.
- Guard: ogni richiesta che modifica dati sul portale viene SOSPESA a livello di rete
  finché un umano non la approva dalla pagina locale di approvazione.

Comandi (dalla cartella principale, con .venv/bin/python):
  gateway/gateway.py serve      # server MCP (stdio); il browser si apre al primo uso
  gateway/gateway.py login      # solo login, senza agente
  gateway/gateway.py registra   # tu usi il portale, guard attivo, traffico salvato in un HAR
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import subprocess
import sys
import time
import uuid
import webbrowser
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import ClientSession, web
from mcp.server.fastmcp import FastMCP, Image
from playwright.async_api import Route, async_playwright


# ---------------------------------------------------------------- config
def _load_config_env():
    """Legge config.env dalla cartella principale. Le variabili d'ambiente hanno la precedenza."""
    path = Path(__file__).resolve().parent.parent / "config.env"
    if not path.exists():
        return
    for riga in path.read_text(encoding="utf-8").splitlines():
        riga = riga.strip()
        if riga and not riga.startswith("#") and "=" in riga:
            k, v = riga.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_config_env()


def _default_hosts(url: str) -> str:
    """Dal PORTAL_URL ricava il dominio principale (es. clienti.edenred.it -> edenred.it)."""
    host = urlparse(url).hostname or ""
    return ".".join(host.split(".")[-2:]) if host.count(".") >= 1 else host


PORTAL_URL = os.getenv("PORTAL_URL", "").strip()
ALLOWED_HOSTS = [h.strip() for h in
                 (os.getenv("ALLOWED_HOSTS") or _default_hosts(PORTAL_URL)).split(",") if h.strip()]
LOGIN_URL_RE = re.compile(os.getenv("LOGIN_URL_RE") or r"login|signin|auth|sso", re.I)
# Richieste mutanti lasciate passare senza approvazione (es. ricerche, salvataggio bozza)
ALLOW_RE = [re.compile(p) for p in os.getenv("ALLOW_PATTERNS", "").split(",") if p]
APPROVAL_TIMEOUT = int(os.getenv("APPROVAL_TIMEOUT") or "900")
APPROVAL_PORT = int(os.getenv("APPROVAL_PORT") or "8787")
# Apre da solo la pagina approvazioni nel browser predefinito + notifica macOS.
APPROVAL_OPEN = (os.getenv("APPROVAL_OPEN") or "1") == "1"
NOTIFY_WEBHOOK = os.getenv("NOTIFY_WEBHOOK")  # opzionale: Slack incoming webhook
HEADLESS = (os.getenv("HEADLESS") or "0") == "1"
DATA_DIR = Path(os.getenv("EDENRED_GATEWAY_HOME") or "~/.edenred-gateway").expanduser()
PROFILE_DIR = DATA_DIR / "profile"
LOG_FILE = DATA_DIR / "mutations.log"
HAR_DIR = DATA_DIR / "har"
# RECORD_HAR=1: registra il traffico anche in `serve` (di solito si usa `registra`).
RECORD_HAR = os.getenv("RECORD_HAR") == "1"
VIEWPORT = {"width": 1280, "height": 800}
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def log(*a):
    # Mai su stdout: in modalità `serve` stdout è il canale MCP.
    print("[edenred]", *a, file=sys.stderr, flush=True)


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(DATA_DIR, 0o700)


def check_config():
    if not PORTAL_URL.startswith("https://"):
        raise RuntimeError("PORTAL_URL mancante o non https: compila config.env")
    if not ALLOWED_HOSTS:
        raise RuntimeError("ALLOWED_HOSTS vuoto: compila config.env")


def har_args(tag: str, record: bool) -> dict:
    if not record:
        return {}
    HAR_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(HAR_DIR, 0o700)
    path = HAR_DIR / f"{tag}-{time.strftime('%Y%m%d-%H%M%S')}.har"
    log(f"Registro il traffico in {path} (contiene dati sensibili: non condividerlo)")
    return {"record_har_path": str(path), "record_har_content": "embed",
            "record_har_url_filter": re.compile("|".join(re.escape(h) for h in ALLOWED_HOSTS))}


# ---------------------------------------------------------------- stato
@dataclass
class Pending:
    id: str
    method: str
    url: str
    body: str | None
    screenshot: bytes | None
    created: float = field(default_factory=time.time)
    decision: str | None = None  # approved | rejected | timeout
    event: asyncio.Event = field(default_factory=asyncio.Event)


class State:
    pw = None
    ctx = None
    page = None
    runner = None
    lock: asyncio.Lock | None = None
    pending: dict[str, Pending] = {}
    approval_token = secrets.token_urlsafe(24)


S = State()


def approval_link() -> str:
    return f"http://127.0.0.1:{APPROVAL_PORT}/?t={S.approval_token}"


# ---------------------------------------------------------------- browser (avvio al primo uso)
async def ensure_browser(tag: str = "agent", record: bool = RECORD_HAR):
    """Avvia Chromium e la pagina approvazioni solo quando servono."""
    if S.lock is None:
        S.lock = asyncio.Lock()
    async with S.lock:
        if S.page is not None and not S.page.is_closed():
            return S.page
        check_config()
        ensure_dirs()
        if S.runner is None:
            S.runner = await start_approval_server()
        if S.pw is None:
            S.pw = await async_playwright().start()
        try:
            S.ctx = await S.pw.chromium.launch_persistent_context(
                str(PROFILE_DIR), headless=HEADLESS, viewport=VIEWPORT, service_workers="block",
                **har_args(tag, record))
        except Exception as e:
            if "ProcessSingleton" in str(e) or "SingletonLock" in str(e) or "user data directory is already in use" in str(e):
                raise RuntimeError("Il browser Edenred è già aperto da un'altra sessione "
                                   "(altra chat di Claude o `registra`): chiudila e riprova.") from e
            raise
        S.ctx.on("close", lambda _: _browser_closed())
        await S.ctx.route("**/*", guard)
        S.page = S.ctx.pages[0] if S.ctx.pages else await S.ctx.new_page()
        await S.page.goto(PORTAL_URL)
        return S.page


def _browser_closed():
    S.ctx = None
    S.page = None


async def shutdown():
    for p in S.pending.values():
        p.decision = p.decision or "rejected"
        p.event.set()
    if S.ctx is not None:
        await S.ctx.close()  # qui viene scritto l'eventuale HAR
    if S.runner is not None:
        await S.runner.cleanup()
    if S.pw is not None:
        await S.pw.stop()


# ---------------------------------------------------------------- guard di rete
def _host_ok(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


async def guard(route: Route):
    req = route.request
    host = urlparse(req.url).hostname or ""

    # Il browser dell'agente non può mai toccare la macchina locale (pagina approvazioni).
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        return await route.abort("blockedbyclient")

    # Navigazioni fuori dai domini consentiti: bloccate.
    if req.is_navigation_request() and S.page is not None and req.frame == S.page.main_frame \
            and not _host_ok(req.url):
        return await route.abort("blockedbyclient")

    if req.method not in MUTATING:
        return await route.continue_()

    with LOG_FILE.open("a") as f:  # "learning mode": tutto ciò che modifica dati
        f.write(json.dumps({"t": time.time(), "m": req.method, "u": req.url,
                            "b": (req.post_data or "")[:2000],
                            "esito": None if _host_ok(req.url) else "bloccata: dominio non consentito"}) + "\n")

    # Modifiche verso domini fuori allowlist: bloccate sempre, senza nemmeno chiedere.
    # Se serve (es. login su un altro dominio), si aggiunge il dominio ad ALLOWED_HOSTS.
    if not _host_ok(req.url):
        log(f"BLOCCATA (dominio non consentito): {req.method} {host}")
        return await route.abort("blockedbyclient")

    if any(p.search(req.url) for p in ALLOW_RE):
        return await route.continue_()

    # Sospendi: serve un umano. Prima registra, poi lo screenshot (può essere lento).
    p = Pending(uuid.uuid4().hex[:8], req.method, req.url, req.post_data, None)
    S.pending[p.id] = p
    try:
        p.screenshot = await S.page.screenshot(timeout=5000)
    except Exception:
        pass
    log(f"IN ATTESA DI APPROVAZIONE {p.id}: {p.method} {p.url}")
    await notify(p)
    try:
        await asyncio.wait_for(p.event.wait(), APPROVAL_TIMEOUT)
    except asyncio.TimeoutError:
        p.decision = "timeout"
    if p.decision == "approved":
        await route.continue_()
    else:
        await route.abort("blockedbyclient")


async def notify(p: Pending):
    log(f"Approva o rifiuta qui: {approval_link()}")
    if APPROVAL_OPEN:
        if sys.platform == "darwin":
            # Testo fisso: niente dati della richiesta dentro lo script AppleScript.
            subprocess.run(["osascript", "-e", 'display notification "Una richiesta aspetta '
                            'la tua approvazione" with title "Edenred" sound name "Glass"'],
                           check=False, capture_output=True)
        # Apre al massimo una scheda per gruppo di richieste: la pagina si aggiorna da sola.
        if sum(1 for x in S.pending.values() if x.decision is None) == 1:
            webbrowser.open(approval_link())
    if NOTIFY_WEBHOOK:
        try:
            async with ClientSession() as s:
                await s.post(NOTIFY_WEBHOOK, json={
                    "text": f"Edenred: richiesta da approvare `{p.method} {p.url}`\n{approval_link()}"})
        except Exception as e:
            log("notifica fallita:", e)


# ---------------------------------------------------------------- pagina approvazioni
def _auth(req: web.Request):
    if not secrets.compare_digest(req.query.get("t", ""), S.approval_token):
        raise web.HTTPForbidden()


PAGE_CSS = """
body{font:15px/1.45 -apple-system,system-ui,sans-serif;max-width:960px;margin:24px auto;padding:0 16px;color:#1d1d1f}
section{border:1px solid #d2d2d7;border-radius:12px;padding:16px;margin:16px 0}
section.open{border-color:#ff9f0a;box-shadow:0 0 0 3px #ff9f0a33}
h3{margin:0 0 8px;font-size:14px;word-break:break-all;font-family:ui-monospace,monospace}
button{font-size:16px;padding:10px 18px;border-radius:8px;border:0;margin-right:8px;cursor:pointer}
.ok{background:#34c759;color:#fff}.no{background:#ff3b30;color:#fff}
pre{white-space:pre-wrap;word-break:break-all;background:#f5f5f7;padding:8px;border-radius:8px;max-height:300px;overflow:auto}
img{max-width:100%;border:1px solid #d2d2d7;border-radius:8px;margin-top:8px}
.hint{color:#6e6e73}
"""


async def h_index(req):
    _auth(req)
    t = S.approval_token
    rows = []
    for p in sorted(S.pending.values(), key=lambda x: -x.created):
        body = (p.body or "").replace("&", "&amp;").replace("<", "&lt;")[:5000]
        actions = (f'<form method=post action="/decide/{p.id}?t={t}">'
                   '<button class=ok name=d value=approved>Approva</button>'
                   '<button class=no name=d value=rejected>Rifiuta</button></form>'
                   if p.decision is None else f"<b>{p.decision}</b>")
        shot = f'<img src="/shot/{p.id}?t={t}">' if p.screenshot else ""
        cls = "open" if p.decision is None else ""
        rows.append(f"<section class='{cls}'><h3>{p.method} {p.url}</h3>{actions}"
                    f"<details><summary>Dati inviati</summary><pre>{body}</pre></details>{shot}</section>")
    html = ("<meta charset=utf-8><meta name=viewport content='width=device-width'>"
            "<meta http-equiv=refresh content=4><title>Approvazioni Edenred</title>"
            f"<style>{PAGE_CSS}</style><h2>Approvazioni Edenred</h2>"
            "<p class=hint>Lo screenshot mostra la pagina nel momento della richiesta. "
            "Nel dubbio rifiuta: al massimo l'agente ripete il passaggio.</p>"
            + ("".join(rows) or "<p>Nessuna richiesta in attesa.</p>"))
    return web.Response(text=html, content_type="text/html")


async def h_shot(req):
    _auth(req)
    p = S.pending.get(req.match_info["id"])
    if not p or not p.screenshot:
        raise web.HTTPNotFound()
    return web.Response(body=p.screenshot, content_type="image/png")


async def h_decide(req):
    _auth(req)
    p = S.pending.get(req.match_info["id"])
    data = await req.post()
    if p and p.decision is None and data.get("d") in ("approved", "rejected"):
        p.decision = data["d"]
        p.event.set()
        log(f"{p.id} -> {p.decision}")
    raise web.HTTPFound(f"/?t={S.approval_token}")


async def start_approval_server():
    app = web.Application()
    app.add_routes([web.get("/", h_index), web.get("/shot/{id}", h_shot),
                    web.post("/decide/{id}", h_decide)])
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        await web.TCPSite(runner, "127.0.0.1", APPROVAL_PORT).start()
    except OSError as e:
        await runner.cleanup()
        raise RuntimeError(f"Porta {APPROVAL_PORT} occupata: c'è già un gateway Edenred aperto "
                           "(altra chat di Claude o `registra`). Chiudilo e riprova.") from e
    log(f"Pagina approvazioni: {approval_link()}")
    return runner


# ---------------------------------------------------------------- MCP
@asynccontextmanager
async def lifespan(_server):
    try:
        yield
    finally:
        await shutdown()


mcp = FastMCP("edenred", lifespan=lifespan, instructions=(
    "Controlli un browser sul Portale Clienti Edenred. Prepara gli ordini fino alla conferma. "
    "Preferisci page_text per leggere e click_text/fill per agire; usa screenshot e click(x,y) "
    "solo quando servono. Quando clicchi Conferma/Invia la richiesta viene sospesa finché un "
    "umano non approva dalla pagina che gli si apre: controlla con approval_status e non "
    "riprovare mai una richiesta rifiutata. Se session_status dice logged_out, chiedi "
    "all'utente di fare login nella finestra Chromium: non digitare mai credenziali o OTP."))


def _login_page(url: str) -> bool:
    return bool(LOGIN_URL_RE.search(url))


async def _shot(wait: float = 0.8) -> Image:
    page = await ensure_browser()
    await asyncio.sleep(wait)
    return Image(data=await page.screenshot(), format="png")


@mcp.tool()
async def session_status() -> dict:
    """Apre il browser se serve. Dice URL corrente e se serve il login dell'utente."""
    page = await ensure_browser()
    out = {"url": page.url, "logged_out": _login_page(page.url)}
    if out["logged_out"]:
        out["azione"] = ("Chiedi all'utente di fare login (e OTP) nella finestra Chromium "
                         "'Edenred', poi di scriverti quando ha finito.")
    return out


@mcp.tool()
async def screenshot() -> Image:
    """Schermata attuale (1280x800). Le coordinate di click(x,y) sono in questi pixel."""
    return await _shot(0)


@mcp.tool()
async def page_text(max_chars: int = 15000) -> str:
    """Testo visibile della pagina (tabelle, importi, nomi). Più preciso dello screenshot."""
    page = await ensure_browser()
    text = await page.locator("body").inner_text()
    return f"URL: {page.url}\n\n{text[:max_chars]}" + ("\n[…troncato]" if len(text) > max_chars else "")


@mcp.tool()
async def click_text(text: str, exact: bool = False, nth: int = 0) -> Image:
    """Clicca un pulsante/link/voce con questo testo visibile. nth sceglie tra più risultati."""
    page = await ensure_browser()
    for loc in (page.get_by_role("button", name=text, exact=exact),
                page.get_by_role("link", name=text, exact=exact),
                page.get_by_text(text, exact=exact)):
        if await loc.count() > nth:
            await loc.nth(nth).click(timeout=5000)
            return await _shot()
    raise ValueError(f"Nessun elemento con testo {text!r}: usa screenshot e click(x,y)")


@mcp.tool()
async def fill(field: str, value: str) -> Image:
    """Scrive value nel campo con questa etichetta o placeholder (sostituisce il contenuto)."""
    page = await ensure_browser()
    for loc in (page.get_by_label(field), page.get_by_placeholder(field)):
        if await loc.count():
            await loc.first.fill(value, timeout=5000)
            return await _shot(0.3)
    raise ValueError(f"Nessun campo {field!r}: clicca il campo e usa type_text")


@mcp.tool()
async def click(x: int, y: int) -> Image:
    """Clicca alle coordinate (x, y) e restituisce la nuova schermata."""
    page = await ensure_browser()
    await page.mouse.click(x, y)
    return await _shot()


@mcp.tool()
async def type_text(text: str) -> Image:
    """Scrive testo nel campo con il focus."""
    page = await ensure_browser()
    await page.keyboard.type(text, delay=20)
    return await _shot(0.3)


@mcp.tool()
async def press_key(key: str) -> Image:
    """Preme un tasto (es. Enter, Tab, Escape, Control+A)."""
    page = await ensure_browser()
    await page.keyboard.press(key)
    return await _shot()


@mcp.tool()
async def scroll(dy: int = 600) -> Image:
    """Scorre la pagina (dy negativo = su)."""
    page = await ensure_browser()
    await page.mouse.wheel(0, dy)
    return await _shot(0.4)


@mcp.tool()
async def goto(url: str) -> Image:
    """Apre un URL del portale (solo domini consentiti)."""
    if not _host_ok(url):
        raise ValueError("Dominio non consentito")
    page = await ensure_browser()
    await page.goto(url)
    return await _shot()


@mcp.tool()
async def approval_status() -> list[dict]:
    """Stato delle azioni sospese in attesa di approvazione umana."""
    return [{"id": p.id, "method": p.method, "url": p.url,
             "decision": p.decision or "pending",
             "waiting_s": int(time.time() - p.created)} for p in S.pending.values()]


# ---------------------------------------------------------------- comandi da terminale
def login():
    """Solo login, senza agente né guard (nessuna azione automatica)."""
    from playwright.sync_api import sync_playwright
    check_config()
    ensure_dirs()
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(str(PROFILE_DIR), headless=False,
                                                    viewport=VIEWPORT)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(PORTAL_URL)
        input("Fai login (anche OTP) nella finestra Chromium, poi torna qui e premi Invio… ")
        ctx.close()
    print("Sessione salvata in", PROFILE_DIR)


async def _registra():
    await ensure_browser(tag="registra", record=True)
    print("\nRegistrazione attiva. Nella finestra Chromium:")
    print("  1. fai login se serve")
    print("  2. apri elenco dipendenti e storico ordini")
    print("  3. prepara un ordine: approva i passaggi intermedi, RIFIUTA la conferma finale")
    print(f"\nPagina approvazioni: {approval_link()}")
    if APPROVAL_OPEN:
        webbrowser.open(approval_link())
    print("\nQuando hai finito torna qui e premi Invio (non chiudere la finestra Chromium).")
    await asyncio.get_running_loop().run_in_executor(None, sys.stdin.readline)
    if S.ctx is None:
        print("La finestra è stata chiusa prima: il HAR potrebbe essere incompleto.")
    await shutdown()
    files = sorted(HAR_DIR.glob("registra-*.har"))
    if files:
        print(f"HAR salvato: {files[-1]} ({files[-1].stat().st_size // 1024} KB)")
        print("Ora estrai la struttura con: .venv/bin/python -I tools/har_struttura.py <file.har>")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "login":
        login()
    elif cmd == "registra":
        try:
            asyncio.run(_registra())
        except RuntimeError as e:
            sys.exit(str(e))
    elif cmd == "serve":
        mcp.run()
    else:
        sys.exit(__doc__)
