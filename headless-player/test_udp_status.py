#!/usr/bin/env python3
"""
Test rapido per verificare lo stato UDP del headless-player
"""
import requests
import json

HOST = "localhost"
PORT = 8080

def test_udp_status():
    try:
        url = f"http://{HOST}:{PORT}/udp/status"
        response = requests.get(url, timeout=2)
        status = response.json()
        
        print("=== UDP Status ===")
        print(json.dumps(status, indent=2))
        print()
        
        # Analisi stato
        if not status.get("enabled"):
            print("⚠️ PROBLEMA: UDP è disabilitato (enabled=False)")
            print("   Controlla la configurazione in config.json o la variabile UDP_ENABLED")
        elif status.get("actual_port") is None:
            print("⚠️ PROBLEMA: UDP non è riuscito a fare il bind")
            if status.get("error_message"):
                print(f"   Errore: {status['error_message']}")
        else:
            print(f"✓ UDP attivo sulla porta {status['actual_port']}")
            if status.get("fallback_used"):
                print(f"  (configurata: {status['configured_port']}, fallback usato)")
                
    except requests.exceptions.ConnectionError:
        print(f"❌ Impossibile connettersi a {HOST}:{PORT}")
        print("   Verifica che headless-player sia in esecuzione")
    except Exception as e:
        print(f"❌ Errore: {e}")

if __name__ == "__main__":
    test_udp_status()
