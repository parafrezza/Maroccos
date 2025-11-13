# Modifiche UDP - Sempre Attivo

## Problema Originale
L'UDP era disabilitato (`UDP_ENABLED = False`) e non c'era modo chiaro di capire perché non funzionasse. Il valore veniva salvato in `config.json` e persisteva anche dopo i riavvii.

## Soluzione Implementata
**UDP è ora sempre attivo** e non può più essere disabilitato tramite configurazione.

### Modifiche al Codice

#### 1. Rimossa configurabilità di `UDP_ENABLED`
- **Caricamento config** (riga ~1101): commentato il caricamento di `UDP_ENABLED` da `config.json`
- **Salvataggio config** (riga ~1228): rimosso `"udp_enabled"` da `persist_settings()`
- **API /config** (riga ~8410): rimosso handling di `UDP_ENABLED`

#### 2. UDP sempre attivo all'avvio
- **Startup (lifespan)** (riga ~2164): il thread UDP parte sempre, senza check di `UDP_ENABLED`
- **Startup (on_event)** (riga ~5729): stesso comportamento

#### 3. Semplificato `_udp_thread()`
- Rimosso il check iniziale di `UDP_ENABLED`
- Il loop principale ora è `while True:` invece di `while UDP_ENABLED:`
- L'unico motivo per cui UDP non parte è un errore di bind della porta

#### 4. Aggiornato `/udp/status`
- Restituisce sempre `"enabled": true`
- Include `error_message` se il bind fallisce

### Script Utili

#### `fix_udp_config.py`
Rimuove automaticamente le chiavi `UDP_ENABLED` e `udp_enabled` dai file `config.json` esistenti.

Uso:
```bash
python headless-player/fix_udp_config.py
```

#### `test_udp_status.py`
Verifica lo stato UDP del player.

Uso:
```bash
python headless-player/test_udp_status.py
```

### Comportamento Attuale

**Prima:**
```json
{
  "ok": true,
  "enabled": false,  // ← poteva essere false!
  "configured_port": 7777,
  "actual_port": null,
  "fallback_used": false
}
```

**Ora:**
```json
{
  "ok": true,
  "enabled": true,   // ← sempre true
  "configured_port": 7777,
  "actual_port": 7777,  // ← null solo se bind fallisce
  "fallback_used": false,
  "error_message": null  // ← spiega errori di bind
}
```

### Possibili Errori

L'unico motivo per cui UDP può non funzionare ora è:
1. **Porta già in uso**: un altro processo sta usando la porta 7777
2. **Permessi insufficienti**: l'app non ha permessi per fare bind sulla porta
3. **Errore di rete**: problemi al socket di sistema

In tutti questi casi:
- `actual_port` sarà `null`
- `error_message` conterrà la descrizione dell'errore
- Il log mostrerà `[UDP] Errore bind: ...`

### Migrazione

Per sistemi esistenti con `UDP_ENABLED=false` nel config:

1. **Automatico**: lo script `fix_udp_config.py` rimuove la chiave
2. **Manuale**: elimina il file `~/.config/headless-player/config.json` e riavvia
3. **Il config verrà ricreato senza la chiave `udp_enabled`**

### Test

Dopo il riavvio:
```bash
curl http://localhost:8080/udp/status
```

Dovresti vedere `"enabled": true` e `"actual_port": 7777` (o porta fallback).

Per testare l'invio di comandi UDP:
```bash
echo "STATUS" | nc -u localhost 7777
```

Dovresti ricevere una risposta come:
```
OK playing=0 current= port=7777
```
