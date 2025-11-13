import time
import traceback

import uvicorn

try:
    from app import app as fastapi_app, APP_PORT
except Exception as e:
    print("[DEBUG] Import fallito:", e, flush=True)
    traceback.print_exc()
    try:
        input("Premi Invio per chiudere…")
    except Exception:
        time.sleep(10)
    raise

if __name__ == "__main__":
    try:
        print("[DEBUG] Avvio headless-player in modalità console…", flush=True)
        uvicorn.run(fastapi_app, host="0.0.0.0", port=APP_PORT, log_level="debug")
    except Exception as e:
        print("[DEBUG] Eccezione non gestita:", e, flush=True)
        traceback.print_exc()
        try:
            input("Premi Invio per chiudere…")
        except Exception:
            time.sleep(10)
        raise
