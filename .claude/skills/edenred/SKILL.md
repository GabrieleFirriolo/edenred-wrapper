---
name: edenred
description: Setup guidato e uso del gateway Edenred (ordini di buoni pasto dal Portale Clienti, con approvazione umana). Usala per /edenred, per installare o configurare, e quando l'utente chiede di preparare, ripetere o controllare un ordine di buoni pasto / ticket restaurant, o di consultare dipendenti e storico ordini.
---

# Edenred

Parli con una persona **probabilmente non tecnica**: un passo alla volta, niente gergo,
una riga su cosa è successo dopo ogni passo. Se un comando fallisce, sistemalo tu se puoi.
Rispetta le regole di `CLAUDE.md`: mai chiedere, digitare o salvare password, OTP, cookie;
mai aprire la pagina Approvazioni (porta 8787); mai leggere `~/.edenred-gateway/` o file `.har`.

Prima decidi in che fase sei: se il tool `session_status` del server `edenred` non è
disponibile, oppure `.venv` o `config.env` mancano → **Setup**. Altrimenti → **Uso**.

## Setup

1. Claude Code deve essere aperto nella cartella principale del repo (quella con `CLAUDE.md`):
   lì valgono permessi e collegamento. Se no, fai riaprire `claude` lì.
2. Installa (qualche minuto la prima volta):
   ```bash
   python3 -m venv .venv            # serve Python 3.11+: se manca, proponi `brew install python@3.12`
   .venv/bin/python -m pip install -q -r requirements.txt
   .venv/bin/python -m playwright install chromium
   cp -n config.example.env config.env
   ```
   Se manca Homebrew, indica https://brew.sh e fai eseguire l'installazione all'utente nel
   Terminale (chiede la password del Mac: non farlo tu).
3. Chiedi: *"Qual è l'indirizzo con cui entri nel Portale Clienti Edenred, dove fai gli
   ordini? Copialo dalla barra degli indirizzi, solo l'indirizzo."* Deve iniziare con
   `https://`; togli tutto dopo `?`. Scrivilo in `config.env` → `PORTAL_URL=`.
   Lascia vuoti `ALLOWED_HOSTS` (si ricava dall'indirizzo) e `ALLOW_PATTERNS`.
   Imposta `RECORD_HAR=1`: le prove del primo giorno vengono registrate sul Mac per
   sviluppare la versione API (vedi l'ultima sezione). Dillo all'utente in una riga.
4. Collegamento:
   - **Claude Code**: è già in `.mcp.json`. L'utente chiude (`/exit`), riapre `claude` qui,
     approva il server "edenred" se richiesto, controlla con `/mcp`.
   - **Claude Desktop** (se lo usa): aggiungi a
     `~/Library/Application Support/Claude/claude_desktop_config.json`, dopo averne fatto
     una copia di backup, sotto `mcpServers`:
     `"edenred": {"command": "<assoluto>/.venv/bin/python", "args": ["<assoluto>/gateway/gateway.py", "serve"]}`.
     Poi Cmd+Q e riapri Claude Desktop.
   - claude.ai nel browser non è supportato (bisognerebbe esporre su internet l'accesso al portale).
5. Nella nuova sessione: `session_status` apre la finestra Chromium. Se `logged_out`, dì
   *"Fai login nella finestra che si è aperta (anche l'OTP), poi scrivimi 'fatto'"*.
   Se il login passa da un altro dominio e la pagina non carica, aggiungi quel dominio ad
   `ALLOWED_HOSTS` in `config.env` e fai riavviare Claude.
6. Prova in sola lettura: con `page_text` apri e riassumi elenco dipendenti e storico
   ordini (con il dettaglio di un ordine).
7. Prova senza spendere: prepara un ordine piccolo fino alla conferma (procedura in "Uso").
   Prima di cliccare Conferma dì: *"Questa è una prova: nella pagina Approvazioni approva i
   passaggi intermedi e premi **Rifiuta** sulla conferma finale. Non arriva niente a Edenred."*
8. Spiega in tre righe: come chiedere un ordine; che prima di ogni salvataggio o invio si
   apre la pagina Approvazioni con una notifica; che nel dubbio si rifiuta.
9. Chiudi la registrazione (sezione "Dati per la versione API").

## Uso: preparare un ordine

1. Leggi `note/portale.md` se esiste (appunti di navigazione delle volte precedenti).
2. `session_status`; se serve login, chiedilo all'utente come sopra.
3. Chiarisci con **una** domanda: mese, chi riceve i buoni, quanti, valore. Se dice "come il
   mese scorso", ricavalo dallo storico (`page_text`) e proponi una tabella da confermare.
   Non inventare numeri.
4. Naviga: leggi con `page_text`, agisci con `click_text` e `fill`; `screenshot` +
   `click(x,y)` solo se servono. Verifica con `page_text` dopo le azioni importanti. Non
   aprire anagrafiche, impostazioni o pagamenti se non richiesto. Se il portale ha l'import
   da file Excel/CSV, segnalalo all'utente.
5. Al riepilogo del portale: confrontalo con la richiesta (persone, quantità, valori,
   totale). Se non coincide fermati. Mostra una tabella con il **totale**, poi:
   *"Clicco Conferma: si apre la pagina Approvazioni, controlla il totale e approva solo se
   è giusto."*
6. Se la pagina non avanza è in attesa: guarda `approval_status`. `approved` → leggi l'esito
   (numero ordine) e riportalo. `rejected`/`timeout` → **non riprovare**, chiedi cosa cambiare.
7. Se richieste innocue (ricerche, bozze) chiedono approvazione ogni volta, suggerisci di
   guardare `.venv/bin/python -I tools/har_struttura.py ~/.edenred-gateway/mutations.log`
   e aggiungerle ad `ALLOW_PATTERNS` solo se l'utente lo chiede. Mai conferme o pagamenti.
8. Alla fine aggiorna `note/portale.md` con la sola **navigazione** (menu, nomi dei pulsanti,
   passaggi, quali richieste chiedono approvazione). Mai nomi, importi, codici fiscali.

## Dati per la versione API (`api/`)

Con `RECORD_HAR=1` il traffico del portale finisce in `~/.edenred-gateway/har/`. Il file
viene scritto **solo quando Claude si chiude**. La conferma rifiutata non arriva a Edenred,
ma la registrazione ne conserva il contenuto: è quello che serve.

1. Togli `RECORD_HAR=1` da `config.env` (lascia `RECORD_HAR=`): le sessioni future non
   devono registrare.
2. Fai chiudere Claude con `/exit` (non la finestra Chromium con la X) e riaprire `claude`.
3. Nella nuova sessione esegui:
   `.venv/bin/python -I tools/har_struttura.py ~/.edenred-gateway/har/*.har -o struttura.json`
   Se compaiono avvisi "Da controllare", verifica in `struttura.json` che non ci siano valori
   reali; se ci sono, dillo e non proseguire.
4. Dì all'utente di inviare **solo `struttura.json`** a chi sviluppa la versione API. I file
   `.har` contengono cookie e dati dei dipendenti: restano sul Mac e non li leggi mai.

Se manca un HAR (prove fatte senza `RECORD_HAR`), alternativa da **Terminale**, con le chat
Claude chiuse: `.venv/bin/python gateway/gateway.py registra`, stesso giro, Invio per chiudere.
