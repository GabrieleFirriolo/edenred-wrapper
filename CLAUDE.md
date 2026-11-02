Gateway per preparare ordini di buoni pasto sul Portale Clienti Edenred, con approvazione
umana prima di ogni invio. Setup e uso: skill `/edenred`.

Regole vincolanti:
- Mai chiedere, digitare o salvare password, OTP, token o cookie: login e OTP li fa l'utente nella finestra del browser.
- Mai approvare al posto dell'utente: niente accesso alla pagina Approvazioni (porta 8787) né al suo token.
- Mai aggiungere percorsi che inviino ordini o modifichino dati senza quell'approvazione.
- Mai indebolire guard, `ALLOWED_HOSTS`, `ALLOW_PATTERNS` o `.claude/settings.json` senza richiesta esplicita; mai far passare conferme d'ordine o pagamenti.
- Mai leggere `~/.edenred-gateway/` o file `.har`, nemmeno tramite script: usa `tools/har_struttura.py`, che estrae solo la struttura.
