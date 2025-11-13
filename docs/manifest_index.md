# Manifest index.json (Maroccos)

Questo documento descrive il formato di `index.json`, il manifest pubblicato dal server di aggiornamento per orchestrare update multi‑asset e multi‑piattaforma.

## Panoramica

- Manifest versione: `manifest_version` (stringa), attualmente "1.0.0".
- Componenti: ogni componente (es. `headless_player`, `off_player`, `provision_script`) dichiara una `version` e una lista di build per OS/arch.
- Ogni build contiene uno o più `assets` con URL, tipo (zip/tar.gz/exe/msi/ps1), dimensione e `sha256`.
- Opzionalmente `recommendations` suggerisce la matrice OS/arch → componenti→versione preferita.

## Esempio

```json
{
  "manifest_version": "1.0.0",
  "generated_at": "2025-11-10T13:37:00Z",
  "source": "https://updates.example.com/maroccos/",
  "components": [
    {
      "id": "headless_player",
      "version": "v0.2.5",
      "builds": [
        {
          "os": "windows",
          "arch": "amd64",
          "assets": [
            {
              "id": "headless_zip",
              "type": "zip",
              "url": "https://updates.example.com/maroccos/headless/v0.2.5/headless-player-v0.2.5-win64.zip",
              "size": 1234567,
              "sha256": "e3b0c44298fc1c149afb...000000000000000000000000000000000000000000000000"
            }
          ]
        }
      ]
    },
    {
      "id": "off_player",
      "version": "v1.4.0",
      "builds": [
        {
          "os": "windows",
          "arch": "amd64",
          "assets": [
            { "id": "off_installer", "type": "exe", "url": "https://.../OFF-player-v1.4.0-Setup.exe", "size": 9876543, "sha256": "..." }
          ]
        }
      ]
    },
    {
      "id": "provision_script",
      "version": "2025.11.10",
      "builds": [
        {
          "os": "windows",
          "arch": "amd64",
          "assets": [
            { "id": "provision_ps1", "type": "ps1", "url": "https://.../provision_player.ps1", "size": 20480, "sha256": "..." }
          ]
        }
      ]
    }
  ],
  "recommendations": [
    { "os": "windows", "arch": "amd64", "components": { "headless_player": "v0.2.5", "off_player": "v1.4.0", "provision_script": "2025.11.10" } }
  ]
}
```

## Contract

- URL devono essere raggiungibili via HTTP(S) e stabili.
- `sha256` è obbligatorio per ogni asset e viene verificato dal player.
- Il player usa `/components` per dichiarare lo stato locale e costruisce un piano da manifest.
- La GUI confronta manifest vs `/components` e invia il piano al player via `/update/plan` → `/update/fetch`.

## Metriche (lato player)

- `/status` espone:
  - `update_stage`, `update_status`, `update_progress`, `update_bytes_*`.
  - `update_assets[]` con tempi per asset (download_* e speed).
  - `update_metrics` con `started_at`, `download_started_at`, `download_ended_at`, `applying_started_at`, `completed_at`.

## Schema

Vedi `docs/manifest_index.schema.json` (JSON Schema draft 2020‑12).