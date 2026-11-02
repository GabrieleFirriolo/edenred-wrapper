# gateway

Browser Chromium controllato dall'agente via MCP, con un **blocco a livello di rete**:
tutto ciò che modifica dati sul portale aspetta l'approvazione di una persona.
Installazione e uso: [README principale](../README.md) e la skill `/edenred`.

## Come funziona

```
Claude ──MCP──► gateway ──► Chromium (finestra visibile) ──► Portale Edenred
                   │                 │
                   │          guard: POST/PUT/PATCH/DELETE → IN ATTESA
                   ▼
   pagina Approvazioni (si apre da sola, 127.0.0.1:8787) → Approva / Rifiuta
```

- **Login**: lo fa la persona nella finestra Chromium, anche con OTP. Il gateway non vede
  né salva password: tiene solo i cookie nel profilo `~/.edenred-gateway/profile` (permessi 700).
- **Il blocco è sotto il browser, non nei tool**: anche se l'agente clicca "Conferma ordine",
  la richiesta parte solo dopo l'ok (timeout 15 minuti, poi annullata).
- Il browser dell'agente **non raggiunge localhost** né domini fuori `ALLOWED_HOSTS`, quindi
  non può aprire la pagina Approvazioni. Il token della pagina non passa mai all'agente.
- Le modifiche verso domini **fuori** `ALLOWED_HOSTS` vengono bloccate sempre, senza chiedere.
  Se si blocca qualcosa di legittimo (es. login su un altro dominio), aggiungi il dominio.
- I service worker sono disattivati, così nessuna richiesta sfugge al blocco.
- Il browser parte solo al primo uso di un tool. Può essere aperto da un solo processo
  alla volta (una chat Claude oppure `gateway.py registra`).

## Limiti da conoscere

- **Claude Desktop** non ha accesso al terminale: l'unico modo di agire è il browser, e il
  guard vale sempre.
- **Claude Code** ha un terminale. `.claude/settings.json` gli vieta di leggere le sessioni,
  modificare il guard, chiamare la porta 8787 e approvare ordini dell'API, e `CLAUDE.md` lo
  ribadisce. Sono protezioni sui comandi, non un isolamento del sistema: per l'uso di tutti
  i giorni Claude Desktop è l'opzione più stretta.
- L'agente vede i dati dei dipendenti mostrati nel portale: usa un piano Claude compatibile
  con le policy privacy dell'azienda.

## Taratura (dopo i primi ordini)

All'inizio **ogni** richiesta che modifica dati chiede approvazione, anche quelle innocue
(ricerche, salvataggi di bozza). `.venv/bin/python -I tools/har_struttura.py ~/.edenred-gateway/mutations.log` le elenca senza dati. In
`config.env` → `ALLOW_PATTERNS` (regex separate da virgola) metti solo quelle sicure:

```
ALLOW_PATTERNS=/search,/draft
```

Mai conferma ordine, pagamento o modifiche anagrafiche. Nel dubbio non aggiungere nulla:
approvare una richiesta innocua costa un click.

## Configurazione (`config.env`)

| Variabile | Default | |
|---|---|---|
| `PORTAL_URL` | — | indirizzo del Portale Clienti (obbligatorio) |
| `ALLOWED_HOSTS` | dominio di `PORTAL_URL` | aggiungi il dominio di login se diverso |
| `ALLOW_PATTERNS` | vuoto | vedi Taratura |
| `APPROVAL_OPEN` | `1` | apre la pagina Approvazioni + notifica macOS |
| `HEADLESS` | `0` | `1` = browser invisibile (il login va fatto con `gateway.py login`) |
| `APPROVAL_TIMEOUT` | `900` | secondi prima di annullare una richiesta non decisa |
| `NOTIFY_WEBHOOK` | — | webhook Slack per le richieste da approvare |
| `RECORD_HAR` | — | `1` = registra il traffico anche durante l'uso con Claude |
