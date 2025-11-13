#!/usr/bin/env python3
"""
Script per rimuovere UDP_ENABLED e udp_enabled dal config.json
in modo che UDP sia sempre attivo
"""
import json
from pathlib import Path
import os

# Percorsi possibili del config.json
config_locations = [
    Path.home() / ".config" / "headless-player" / "config.json",
    Path(__file__).parent / "config.json",
]

def fix_config(config_path: Path):
    """Rimuove le chiavi UDP_ENABLED e udp_enabled dal config.json"""
    if not config_path.exists():
        return False
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        modified = False
        for key in ["UDP_ENABLED", "udp_enabled"]:
            if key in data:
                del data[key]
                modified = True
                print(f"✓ Rimossa chiave '{key}' da {config_path}")
        
        if modified:
            # Backup
            backup_path = config_path.with_suffix('.json.backup')
            config_path.rename(backup_path)
            print(f"  Backup salvato in {backup_path}")
            
            # Salva config pulito
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"  Config aggiornato: {config_path}")
            return True
        else:
            print(f"✓ {config_path} non contiene chiavi UDP_ENABLED (OK)")
            return False
            
    except Exception as e:
        print(f"❌ Errore elaborando {config_path}: {e}")
        return False

if __name__ == "__main__":
    print("=== Fix UDP Config ===")
    print("Rimuovo le chiavi UDP_ENABLED dai file di configurazione...\n")
    
    found = False
    for location in config_locations:
        if location.exists():
            found = True
            fix_config(location)
            print()
    
    if not found:
        print("ℹ️  Nessun file config.json trovato (va bene, UDP userà i default)")
    
    print("\n✓ Fatto! Riavvia headless-player per applicare le modifiche.")
