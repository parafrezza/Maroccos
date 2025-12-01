# Stato
echo -n "STATUS" | nc -u -w1 192.168.1.50 7777

# Play / Stop
echo -n "PLAY"  | nc -u -w1 192.168.1.50 7777
echo -n "STOP"  | nc -u -w1 192.168.1.50 7777

# Avanza / Indietro
echo -n "NEXT"  | nc -u -w1 192.168.1.50 7777
echo -n "PREV"  | nc -u -w1 192.168.1.50 7777

# Vai all’indice n (0-based)
echo -n "SET 3" | nc -u -w1 192.168.1.50 7777

# Cambia cartella e ricarica playlist
echo -n "DIR /home/pi/videos" | nc -u -w1 192.168.1.50 7777
echo -n "RELOAD" | nc -u -w1 192.168.1.50 7777

# Loop ON/OFF
echo -n "LOOP on" | nc -u -w1 192.168.1.50 7777
echo -n "LOOP off" | nc -u -w1 192.168.1.50 7777



# Stato (JSON)
curl http://HOST:8080/status

Note: the HUD overlay now shows both the OFF-player build version and, if present, the headless-player version passed via the environment variable 'HEADLESS_VERSION' (displayed as 'Headless: <version>').

# Controlli base
curl -X POST http://HOST:8080/play
curl -X POST http://HOST:8080/stop
curl -X POST http://HOST:8080/next
curl -X POST http://HOST:8080/prev

# Vai a indice N (0-based)
curl -X POST "http://HOST:8080/set?index=3"

# Cambia cartella media (assoluta o relativa) e ricarica
curl -X POST "http://HOST:8080/dir?path=/home/pi/videos"

# Ricarica playlist (ri-scansiona la cartella corrente)
curl -X POST http://HOST:8080/reload

# Loop on/off
curl -X POST "http://HOST:8080/loop?on=1"
curl -X POST "http://HOST:8080/loop?on=0"


https://kr15h.github.io/rpi-of-introduction/#/12/2