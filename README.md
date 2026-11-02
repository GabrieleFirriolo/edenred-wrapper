# edenred-wrapper

Fai preparare a Claude gli **ordini di buoni pasto** dal Portale Clienti Edenred (lato
azienda). Claude usa il portale in una finestra del browser che vedi; **niente viene
salvato o inviato finché non premi tu "Approva"**. Funziona con Claude Code e Claude
Desktop su Mac. Wrapper non ufficiale: verifica che il contratto Edenred consenta
l'automazione del portale.

## Inizio

```bash
git clone <indirizzo-del-repo> edenred-wrapper && cd edenred-wrapper && claude
```

Poi scrivi `/edenred`: Claude installa il necessario, ti chiede l'indirizzo del portale e
ti fa fare il primo login. Password e OTP li scrivi solo tu, nella finestra del portale.

## Uso

Chiedi per esempio *"prepara l'ordine buoni pasto di dicembre come quello di novembre"*.
Prima di ogni salvataggio o invio si apre la pagina **Approvazioni** con una notifica:
controlla e premi **Approva** o **Rifiuta**. Nel dubbio rifiuta.

## Contenuto

- `gateway/`: browser controllato da Claude con blocco delle richieste ([dettagli](gateway/README.md)).
- `tools/har_struttura.py`: estrae da una registrazione solo i nomi dei campi, senza dati
  (serve per sviluppare in futuro una versione a chiamate dirette).
- `.claude/`: la skill `/edenred` e i permessi che impediscono a Claude Code di leggere le
  sessioni, modificare il blocco o approvare da solo.

Dati locali: `config.env` (niente password) e `~/.edenred-gateway/` (sessione del browser,
registrazioni: non condividerla). Per togliere tutto cancella la cartella e `~/.edenred-gateway`.
