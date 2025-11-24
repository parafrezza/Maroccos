// source code file for OFF-player -RF 2025
#include "ofApp.h"
#include "HttpControlServer.h"
#include "ofJson.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <iomanip>
#include <sstream>
#ifdef _WIN32
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0600
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#ifndef GLFW_EXPOSE_NATIVE_WIN32
#define GLFW_EXPOSE_NATIVE_WIN32
#endif
#include "ofAppGLFWWindow.h"
#include <GLFW/glfw3native.h>
#else
#include <netdb.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <unistd.h>
#endif

// --- helpers
static bool endsWith(const std::string& str, const std::string& suffix) {
    return str.size() >= suffix.size() &&
           str.compare(str.size() - suffix.size(), suffix.size(), suffix) == 0;
}

static bool hasVideoExt(const std::string& name){
    std::string n = ofToLower(name);
    return endsWith(n, ".mp4")  ||
           endsWith(n, ".mov")  ||
           endsWith(n, ".m4v")  ||
           endsWith(n, ".mkv")  ||
           endsWith(n, ".avi")  ||
           endsWith(n, ".mpg")  ||
           endsWith(n, ".mpeg") ||
           endsWith(n, ".ts")   ||
           endsWith(n, ".m2ts") ||
           endsWith(n, ".webm") ||
           endsWith(n, ".wmv");
}

static bool hasImageExt(const std::string& name){
    std::string n = ofToLower(name);
    return endsWith(n, ".jpg") || endsWith(n, ".jpeg") || endsWith(n, ".png") ||
           endsWith(n, ".bmp") || endsWith(n, ".gif")  || endsWith(n, ".webp") ||
           endsWith(n, ".tif") || endsWith(n, ".tiff");
}

static std::string normalizeIncomingPath(const std::string& rawPath, const std::string& mediaDir){
    std::string trimmed = ofTrim(rawPath);
    if(trimmed.empty()){
        return trimmed;
    }

    std::string candidate = trimmed;
#ifdef _WIN32
    std::replace(candidate.begin(), candidate.end(), '/', '\\');
#else
    std::replace(candidate.begin(), candidate.end(), '\\', '/');
    if(candidate.size() > 2 && candidate[1] == ':' && (candidate[2] == '/' || candidate[2] == '\\')){
        // Windows absolute path received on POSIX: fallback to mediaDir + basename if available
        std::string fallback = ofFilePath::join(mediaDir, ofFilePath::getFileName(candidate));
        if(ofFile::doesFileExist(fallback)){
            return fallback;
        }
    }
#endif
    return candidate;
}

static const char* hudModeLabel(ofApp::HudMode mode){
    switch(mode){
        case ofApp::HudMode::Minimal: return "MINIMAL";
        case ofApp::HudMode::Full: return "FULL";
        default: return "HIDDEN";
    }
}

void ofApp::saveDefaultConfigIfMissing(){
    ofFile f("config.json");
    if(!f.exists()){
        ofJson j;
        j["mediaDir"] = cfg.mediaDir;
        j["udpPort"] = cfg.udpPort;
        j["httpPort"] = cfg.httpPort;                 // <— NUOVO
        j["startPaused"] = cfg.startPaused;
        j["loopEach"] = cfg.loopEach;
        j["targetFps"] = cfg.targetFps;
        j["autoReloadOnChange"] = cfg.autoReloadOnChange;
        ofSavePrettyJson("config.json", j);
        ofLogNotice() << "config.json creato (default).";
    }
}

void ofApp::loadConfig(){
    saveDefaultConfigIfMissing();
    ofJson j = ofLoadJson("config.json");
    if(j.contains("mediaDir")) cfg.mediaDir = j["mediaDir"].get<std::string>();
    if(j.contains("udpPort")) cfg.udpPort = j["udpPort"].get<int>();
    if(j.contains("httpPort")) cfg.httpPort = j["httpPort"].get<int>();   // <— NUOVO
    if(j.contains("startPaused")) cfg.startPaused = j["startPaused"].get<bool>();
    if(j.contains("loopEach")) cfg.loopEach = j["loopEach"].get<bool>();
    if(j.contains("targetFps")) cfg.targetFps = j["targetFps"].get<int>();
    if(j.contains("autoReloadOnChange")) cfg.autoReloadOnChange = j["autoReloadOnChange"].get<bool>();
    if(const char* envMediaDir = std::getenv("MEDIA_DIR")){
        if(envMediaDir[0] != '\0'){
            cfg.mediaDir = envMediaDir;
        }
    }
}

void ofApp::setup(){
    ofSetLogLevel(OF_LOG_NOTICE);
    loadConfig();

    try{
        std::string logDirPath = ofFilePath::join(cfg.mediaDir, "_logs");
        std::filesystem::create_directories(logDirPath);
        std::string logFilePath = ofFilePath::join(logDirPath, "off-player.log");
        ofLogToFile(logFilePath, true);
    }catch(const std::exception& e){
        ofLogWarning() << "Impossibile creare log OFF: " << e.what();
    }

    ofSetFrameRate(cfg.targetFps);
    ofHideCursor();
    ofBackground(0);

    buildPlaylist();
    if(playlist.empty()){
        ofLogWarning() << "Playlist vuota. Metti dei video in \"" << cfg.mediaDir << "\"";
    }else{
        loadCurrent();
    }

    if(!cfg.startPaused){
        play();
    }

    setupUDP();

    // HUD font (fallback su bitmap string in caso di errore)
    try{
        ofTrueTypeFontSettings fontSettings("verdana.ttf", 28);
        fontSettings.antialiased = true;
        fontSettings.dpi = 96;
        fontSettings.addRanges(ofAlphabet::Latin);
        overlayFontLoaded = overlayFont.load(fontSettings);
    }catch(const std::exception& e){
        overlayFontLoaded = overlayFont.load("verdana.ttf", 28, true, true);
        if(!overlayFontLoaded){
            ofLogWarning() << "Overlay font non caricato: " << e.what();
        }
    }
    overlayInfo.updateInterval = 1.0f;
    updateOverlayInfo(true);

    ofLogNotice() << "Comandi UDP (porta " << cfg.udpPort << "): "
                  << "PLAY, STOP, NEXT, PREV, SET <n>, DIR <path>, RELOAD, STATUS, LOOP on|off";
    setupUDP();

    // Mostra splash di default a startup
    // splash.show("Ready");

    // Inizializza Winsock manualmente su Windows (equivalente al bootstrap automatico di Poco)
#ifdef _WIN32
    WSADATA wsaData{};
    int wsaErr = WSAStartup(MAKEWORD(2, 2), &wsaData);
    if(wsaErr != 0){
        ofLogWarning() << "WSAStartup fallita: " << wsaErr;
    }else{
        wsaInitialized = true;
    }
#endif

    // --- HTTP server
    ControlAPI api;
    api.fnPlay      = [this]{ this->play(); };
    api.fnStop      = [this]{ this->stop(); };
    api.fnPause     = [this]{ this->pause(); };
    api.fnResume    = [this]{ this->resume(); };
    api.fnNext      = [this]{ this->next(); };
    api.fnPrev      = [this]{ this->prev(); };
    api.fnSetIndex  = [this](size_t i){ this->setIndex(i); };
    api.fnSetDir    = [this](const std::string& p){ this->setDir(p); };
    api.fnReload    = [this]{ this->reloadPlaylist(); };
    api.fnStatus    = [this]{ return this->statusString(); };
    api.fnPlaylist  = [this]{ return this->playlistJSON(); };
    api.fnLoop      = [this](bool on){
        this->cfg.loopEach = on;
        if(player.isLoaded()){
            player.setLoopState(on ? OF_LOOP_NORMAL : OF_LOOP_NONE);
        }
        ofLogNotice() << "LOOP -> " << (on ? "on":"off");
    };
    api.fnPlayFile  = [this](const std::string& p){ this->playFileAbsolute(p); };
    api.fnSplashShow = [this](const std::string& t){ this->splash.show(t); };
    api.fnSplashHide = [this]{ this->splash.hide(); };
    api.fnSplashText = [this](const std::string& t){ this->splash.setText(t); };
    api.fnHudVisible = [this](bool on){ this->setHudMode(on ? HudMode::Full : HudMode::Hidden); };
    api.fnHudMode = [this](int mode){ this->setHudModeByIndex(mode); };
    // Visual fades
    api.fnFadeIn      = [this](float s){ this->fadeIn(s); };
    api.fnFadeToBlack = [this](float s){ this->fadeToBlack(s); };
    // Visual brightness + go_to_start
    api.fnBrightness  = [this](float v01, float s){ this->setBrightness(v01, s); };
    api.fnGoToStart   = [this]{ this->goToStart(); };
    api.fnLedTestStart = [this](int w, int h, int ox, int oy, float speed){
        this->startLedTest(w, h, ox, oy, speed);
    };
    api.fnLedTestStop = [this]{ this->stopLedTest(); };

    httpServer = std::make_shared<HttpControlServer>();
    if(httpServer->start(cfg.httpPort, api)){
        ofLogNotice() << "HTTP control server in avvio sulla porta " << cfg.httpPort;
    }else{
        ofLogWarning() << "HTTP control server già attivo o impossibile avviare";
    }

    ofLogNotice() << "Comandi UDP (porta " << cfg.udpPort << "): "
                  << "PLAY, STOP, NEXT, PREV, SET <n>, DIR <path>, RELOAD, STATUS, LOOP on|off";

    ofLogNotice() << "API HTTP (porta " << cfg.httpPort << "): "
                  << "GET /status /healthz /splash/* /visual/* | "
                  << "POST /play /stop /next /prev /set?index=N /dir?path=P /reload /loop?on=1|0 /pause /resume";
}

void ofApp::setupUDP(){
    udp.Create();
    // Bind con fallback: se la porta è occupata prova le successive (max +10)
    int basePort = cfg.udpPort;
    int boundPort = -1;
    for(int attempt=0; attempt<10; ++attempt){
        int p = basePort + attempt;
        if(udp.Bind(p)){
            boundPort = p;
            break;
        }
    }
    if(boundPort < 0){
        ofLogWarning() << "UDP bind fallito su porta base " << basePort << " e fallback +10: disabilito comandi UDP";
    }else{
        if(boundPort != basePort){
            ofLogNotice() << "UDP bind fallback: " << basePort << " -> " << boundPort;
            cfg.udpPort = boundPort; // aggiorna cfg runtime
        }else{
            ofLogNotice() << "UDP bind ok su porta " << boundPort;
        }
    }
    udp.SetNonBlocking(true);
}

void ofApp::buildPlaylist(){
    playlist.clear();
    currentImage.clear();
    currentIsImage = false;
    ofDirectory dir(cfg.mediaDir);
    if(!dir.exists()){
        ofDirectory().createDirectory(cfg.mediaDir, true, true);
        ofLogNotice() << "Creata cartella media: " << cfg.mediaDir;
    }
    dir.allowExt("mp4"); dir.allowExt("mov"); dir.allowExt("m4v");
    dir.allowExt("mkv"); dir.allowExt("avi"); dir.allowExt("mpg");
    dir.allowExt("mpeg"); dir.allowExt("ts"); dir.allowExt("m2ts");
    dir.allowExt("webm"); dir.allowExt("wmv");
    dir.allowExt("jpg"); dir.allowExt("jpeg"); dir.allowExt("png");
    dir.allowExt("bmp"); dir.allowExt("gif"); dir.allowExt("webp");
    dir.allowExt("tif"); dir.allowExt("tiff");
    dir.listDir();
    for(const auto& f : dir.getFiles()){
        const std::string name = f.getFileName();
        if(hasVideoExt(name)){
            playlist.push_back({f, false});
        }else if(hasImageExt(name)){
            playlist.push_back({f, true});
        }
    }
    std::sort(playlist.begin(), playlist.end(),
              [](const PlaylistItem& a, const PlaylistItem& b){ return a.file.getBaseName() < b.file.getBaseName(); });
    currentIndex = 0;
    ofLogNotice() << "Playlist: " << playlist.size() << " file.";
}

bool ofApp::loadCurrent(){
    if(playlist.empty() || currentIndex >= playlist.size()){
        return false;
    }
    PlaylistItem& entry = playlist[currentIndex];
    auto path = entry.file.getAbsolutePath();
    bool detectedImage = hasImageExt(entry.file.getFileName());
    entry.isImage = detectedImage;
    currentIsImage = detectedImage;
    ofLogNotice() << "Carico: [" << currentIndex << "] " << path;

    // Reset previous media state
    player.stop();
    player.closeMovie();
    currentImage.clear();

    if(currentIsImage){
        if(!currentImage.load(path)){
            ofLogError() << "Errore nel load immagine: " << path;
            currentIsImage = false;
            return false;
        }
        isPlaying = true;
        return true;
    }

    bool ok = player.load(path);
    if(!ok){
        ofLogError() << "Errore nel load: " << path;
        return false;
    }
    player.setLoopState(cfg.loopEach ? OF_LOOP_NORMAL : OF_LOOP_NONE);
    player.setVolume(0.0f); // silenzio; cambia se vuoi audio
    player.setPaused(false);
    player.play();
    isPlaying = true;
    return true;
}

void ofApp::play(){
    if(playlist.empty()){
        ofLogWarning() << "PLAY: playlist vuota.";
        return;
    }
    bool hasMedia = currentIsImage ? currentImage.isAllocated() : player.isLoaded();
    if(!hasMedia){
        if(!loadCurrent()){
            ofLogWarning() << "PLAY: impossibile caricare il media corrente.";
            return;
        }
    }
    // Nascondi lo splash quando parte la riproduzione
    if(splash.isActive()) splash.hide();
    if(currentIsImage){
        isPlaying = true;
        ofLogNotice() << "PLAY (image)";
        return;
    }
    if(!player.isLoaded()){
        ofLogWarning() << "PLAY: nessun video caricato.";
        return;
    }
    player.setPaused(false);
    player.play();
    isPlaying = true;
    ofLogNotice() << "PLAY";
}

void ofApp::stop(){
    if(currentIsImage){
        isPlaying = false;
    }else{
        if(player.isLoaded()){
            player.setPaused(true);
        }
        isPlaying = false;
    }
    ofLogNotice() << "STOP";
}

void ofApp::pause(){
    if(currentIsImage){
        isPlaying = false;
        ofLogNotice() << "PAUSE (image)";
        return;
    }
    if(player.isLoaded()){
        player.setPaused(true);
        isPlaying = false;
        ofLogNotice() << "PAUSE";
    }
}

void ofApp::resume(){
    if(currentIsImage){
        if(currentImage.isAllocated()){
            isPlaying = true;
            ofLogNotice() << "RESUME (image)";
        }
        return;
    }
    if(player.isLoaded()){
        player.setPaused(false);
        player.play();
        isPlaying = true;
        ofLogNotice() << "RESUME";
    }
}

void ofApp::next(){
    if(playlist.empty()) return;
    size_t nextIdx = (currentIndex + 1) % playlist.size();
    transitionToIndex(nextIdx, 0.5f);
    ofLogNotice() << "NEXT -> index " << nextIdx;
}

void ofApp::prev(){
    if(playlist.empty()) return;
    size_t prevIdx = (currentIndex == 0 ? playlist.size() - 1 : currentIndex - 1);
    transitionToIndex(prevIdx, 0.5f);
    ofLogNotice() << "PREV -> index " << prevIdx;
}

void ofApp::setIndex(size_t idx){
    if(playlist.empty()) return;
    if(idx >= playlist.size()){
        ofLogWarning() << "SET: indice fuori range.";
        return;
    }
    transitionToIndex(idx, 0.5f);
    ofLogNotice() << "SET -> index " << idx;
}

void ofApp::reloadPlaylist(){
    buildPlaylist();
    loadCurrent();
    ofLogNotice() << "RELOAD playlist.";
}

void ofApp::setDir(const std::string& dir){
    cfg.mediaDir = dir;
    ofJson j = ofLoadJson("config.json");
    j["mediaDir"] = cfg.mediaDir;
    ofSavePrettyJson("config.json", j);
    reloadPlaylist();
    ofLogNotice() << "DIR -> " << cfg.mediaDir;
}

void ofApp::playFileAbsolute(const std::string& absPath){
    if(absPath.empty()){
        ofLogError() << "PLAY_FILE: path vuoto";
        return;
    }

    std::string normalized = normalizeIncomingPath(absPath, cfg.mediaDir);
    std::string resolved = normalized;
    if(!ofFile::doesFileExist(resolved)){
        if(!ofFilePath::isAbsolute(resolved)){
            std::string relCandidate = ofFilePath::join(cfg.mediaDir, resolved);
            if(ofFile::doesFileExist(relCandidate)){
                resolved = relCandidate;
            }
        }
    }

    if(!ofFile::doesFileExist(resolved)){
        std::string fallbackName = ofFilePath::getFileName(normalized);
        if(!fallbackName.empty()){
            std::string fallback = ofFilePath::join(cfg.mediaDir, fallbackName);
            if(ofFile::doesFileExist(fallback)){
                resolved = fallback;
            }
        }
    }

    if(!ofFile::doesFileExist(resolved)){
        ofLogError() << "PLAY_FILE: file non trovato -> " << normalized;
        return;
    }

    resolved = ofFilePath::getAbsolutePath(resolved);

    ofLogNotice() << "PLAY_FILE -> " << resolved;

    player.stop();
    player.closeMovie();
    currentImage.clear();

    currentIsImage = hasImageExt(resolved);
    if(currentIsImage){
        if(!currentImage.load(resolved)){
            ofLogError() << "PLAY_FILE: load immagine fallito -> " << resolved;
            currentIsImage = false;
            return;
        }
        isPlaying = true;
    }else{
        if(!player.load(resolved)){
            ofLogError() << "PLAY_FILE: load fallito -> " << resolved;
            return;
        }
        player.setLoopState(cfg.loopEach ? OF_LOOP_NORMAL : OF_LOOP_NONE);
        player.setVolume(0.0f);
        player.setPaused(false);
        player.play();
        isPlaying = true;
    }

    // Aggiorna currentIndex se il file appartiene alla playlist
    for(size_t i = 0; i < playlist.size(); ++i){
        try{
            if(ofFilePath::getAbsolutePath(playlist[i].file.getAbsolutePath()) == ofFilePath::getAbsolutePath(resolved)){
                currentIndex = i;
                playlist[i].isImage = currentIsImage;
                break;
            }
        }catch(...){
            // ignora eventuali eccezioni path
        }
    }
}

std::string ofApp::statusString() const {
    std::ostringstream ss;
    ss << "{";
    ss << "\"count\":" << playlist.size() << ",";
    ss << "\"index\":" << currentIndex << ",";
    ss << "\"playing\":" << (isPlaying ? "true":"false") << ",";
    ss << "\"loop\":" << (cfg.loopEach ? "true":"false") << ",";
    ss << "\"kind\":\"" << (currentIsImage ? "image" : "video") << "\",";
    ss << "\"file\":\"";
    if(!playlist.empty() && currentIndex < playlist.size()){
        // Simple JSON string escaping (quotes, backslash)
        const auto& entry = playlist[currentIndex];
        std::string fname = entry.file.getFileName();
        for (char c : fname) {
            if (c == '"' || c == '\\') ss << '\\';
            ss << c;
        }
    }
    ss << "\",";
    ss << "\"test\":{";
    ss << "\"active\":" << (ledTest.active ? "true" : "false");
    if(ledTest.active){
        ss << ",\"width\":" << static_cast<int>(ledTest.area.getWidth());
        ss << ",\"height\":" << static_cast<int>(ledTest.area.getHeight());
        ss << ",\"offsetX\":" << static_cast<int>(ledTest.area.getX());
        ss << ",\"offsetY\":" << static_cast<int>(ledTest.area.getY());
    }
    ss << "}";
    auto appendEscaped = [&ss](const std::string& value){
        for(char c : value){
            if(c == '"' || c == '\\'){ ss << '\\'; }
            ss << c;
        }
    };
    DisplayInfo disp = getDisplayInfoCached();
    ss << ",\"display\":{";
    if(disp.valid){
        ss << "\"width\":" << disp.width;
        ss << ",\"height\":" << disp.height;
        ss << ",\"refresh_hz\":" << disp.refreshHz;
        ss << ",\"interlaced\":" << (disp.interlaced ? "true" : "false");
        ss << ",\"valid\":true";
        std::string label = formatDisplayInfo(disp);
        ss << ",\"label\":\"";
        appendEscaped(label);
        ss << "\"";
        if(!disp.source.empty()){
            ss << ",\"source\":\"";
            appendEscaped(disp.source);
            ss << "\"";
        }
    }else{
        ss << "\"valid\":false";
    }
    ss << "}";
    ss << ",\"hud\":{\"mode\":" << static_cast<int>(hudMode);
    ss << ",\"visible\":" << (hudMode == HudMode::Hidden ? "false" : "true");
    ss << ",\"label\":\"" << hudModeLabel(hudMode) << "\"}";
    // visual/brightness (0..1)
    ss << ",\"brightness\":" << ofClamp(brightnessCur_, 0.0f, 1.0f);
    ss << "}";
    return ss.str();
}

std::string ofApp::playlistJSON() const {
    std::ostringstream ss;
    ss << "{";
    ss << "\"dir\":\"";
    // escape dir path
    for (char c : cfg.mediaDir) { if (c=='"' || c=='\\') ss << '\\'; ss << c; }
    ss << "\",";
    ss << "\"count\":" << playlist.size() << ",";
    ss << "\"index\":" << currentIndex << ",";
    ss << "\"items\":[";
    for (size_t i=0;i<playlist.size();++i){
        const auto& item = playlist[i];
        const auto& f = item.file;
        std::string name = f.getFileName();
        std::string base = f.getBaseName();
        std::string path = f.getAbsolutePath();
    if(i>0) ss << ",";
    ss << "{";
    ss << "\"name\":\"";
    for(char c: name){ if(c=='\"'||c=='\\') ss<<'\\'; ss<<c; }
    ss << "\",";
    ss << "\"base\":\"";
    for(char c: base){ if(c=='\"'||c=='\\') ss<<'\\'; ss<<c; }
    ss << "\",";
    ss << "\"path\":\"";
    for(char c: path){ if(c=='\"'||c=='\\') ss<<'\\'; ss<<c; }
    ss << "\"";
    ss << ",\"kind\":\"" << (item.isImage ? "image" : "video") << "\"";
    ss << "}";
    }
    ss << "]}";
    return ss.str();
}

// --- visual helpers ---
void ofApp::setBrightness(float value01, float seconds){
    float v = ofClamp(value01, 0.0f, 1.0f);
    if(seconds <= 0.0f){
        brightnessCur_ = v;
        brightnessStart_ = v;
        brightnessTarget_ = v;
        brightnessStartTime_ = ofGetElapsedTimef();
        brightnessDuration_ = 0.0;
        ofLogNotice() << "BRIGHTNESS -> " << v << " (instant)";
        return;
    }
    brightnessStart_ = brightnessCur_;
    brightnessTarget_ = v;
    brightnessStartTime_ = ofGetElapsedTimef();
    brightnessDuration_ = seconds;
    ofLogNotice() << "BRIGHTNESS -> " << v << " in " << seconds << "s";
}

void ofApp::goToStart(){
    if(currentIsImage){
        // For images, just pause the display (no timebase)
        isPlaying = false;
        ofLogNotice() << "GO_TO_START (image)";
        return;
    }
    if(player.isLoaded()){
        player.setPaused(true);
        player.setPosition(0.0); // seek to start
        player.update();
        isPlaying = false;
        ofLogNotice() << "GO_TO_START";
    }
}

void ofApp::setHudMode(HudMode mode){
    HudMode sanitized = HudMode::Hidden;
    if(mode == HudMode::Minimal || mode == HudMode::Full){
        sanitized = mode;
    }
    if(hudMode == sanitized){
        return;
    }
    hudMode = sanitized;
    ofLogNotice() << "Overlay HUD -> " << hudModeLabel(hudMode);
    updateOverlayInfo(true);
}

void ofApp::setHudModeByIndex(int modeIndex){
    if(modeIndex <= static_cast<int>(HudMode::Hidden)){
        setHudMode(HudMode::Hidden);
        return;
    }
    if(modeIndex == static_cast<int>(HudMode::Minimal)){
        setHudMode(HudMode::Minimal);
        return;
    }
    setHudMode(HudMode::Full);
}

void ofApp::pollUDP(){
    char udpMessage[2048];
    memset(udpMessage, 0, sizeof(udpMessage));
    int len = udp.Receive(udpMessage, 2047);
    if(len <= 0) return;

    std::string msg = ofTrim(std::string(udpMessage, len));
    std::string upper = ofToUpper(msg);
    ofLogNotice() << "[UDP] " << msg;

    // Try JSON first
    bool handled = false;
    if(!msg.empty() && msg.front() == '{'){
        try{
            ofJson j = ofJson::parse(msg);
            std::string cmd = ofToLower(j.value("cmd", std::string("")));
            if(cmd == "brightness"){
                // value can be 0..1 or 0..100
                double val = j.value("value", 1.0);
                double seconds = j.value("seconds", 0.0);
                double v01 = val > 1.01 ? (val / 100.0) : val;
                setBrightness(static_cast<float>(v01), static_cast<float>(seconds));
                handled = true;
            }else if(cmd == "go_to_start" || cmd == "goto_start"){
                goToStart();
                handled = true;
            }else if(cmd == "jump" || cmd == "jump_track"){
                int n = j.value("n", 1);
                if(n >= 1) setIndex(static_cast<size_t>(n-1));
                handled = true;
            }
        }catch(const std::exception& e){
            ofLogWarning() << "[UDP] JSON parse error: " << e.what();
        }
    }
    if(handled) return;

    if(upper == "PLAY"){
        play();
    } else if(upper == "STOP"){
        stop();
    } else if(upper == "NEXT"){
        next();
    } else if(upper == "PREV"){
        prev();
    } else if(ofIsStringInString(upper, "JUMP ")){
        auto parts = ofSplitString(msg, " ", true, true);
        if(parts.size() >= 2){
            int oneBased = ofToInt(parts[1]);
            if(oneBased >= 1) setIndex(static_cast<size_t>(oneBased - 1));
        }
    } else if(upper == "GO_TO_START" || upper == "GOTO_START" || upper == "REWIND"){
        goToStart();
    } else if(ofIsStringInString(upper, "SET ")){
        auto parts = ofSplitString(msg, " ", true, true);
        if(parts.size() >= 2){
            int idx = ofToInt(parts[1]);
            setIndex((size_t)idx);
        }
    } else if(ofIsStringInString(upper, "DIR ")){
        auto parts = ofSplitString(msg, " ", true, true);
        if(parts.size() >= 2){
            setDir(parts[1]);
        }
    } else if(upper == "RELOAD"){
        reloadPlaylist();
    } else if(upper == "STATUS"){
        ofLogNotice() << statusString();
    } else if(ofIsStringInString(upper, "BRIGHTNESS ")){
        auto parts = ofSplitString(msg, " ", true, true);
        if(parts.size() >= 2){
            float v = ofToFloat(parts[1]);
            float seconds = 0.0f;
            if(parts.size() >= 3){ seconds = ofToFloat(parts[2]); }
            float v01 = (v > 1.01f ? v/100.0f : v);
            setBrightness(v01, seconds);
        }
    } else if(ofIsStringInString(upper, "LOOP ")){
        auto parts = ofSplitString(msg, " ", true, true);
        if(parts.size() >= 2){
            std::string v = ofToLower(parts[1]);
            cfg.loopEach = (v=="on"||v=="true"||v=="1");
            player.setLoopState(cfg.loopEach ? OF_LOOP_NORMAL : OF_LOOP_NONE);
            ofLogNotice() << "LOOP -> " << (cfg.loopEach ? "on":"off");
        }
    } else {
        ofLogWarning() << "Comando non riconosciuto.";
    }
}

void ofApp::update(){
    pollUDP();
    if(isPlaying && !currentIsImage) player.update();
    // gestisci animazione fade
    if(fading && fadeDuration > 0.0f){
        float t = (ofGetElapsedTimef() - fadeStartTime) / fadeDuration;
        if(t >= 1.0f) t = 1.0f;
        float a = ofLerp(fadeFrom, fadeTo, t);
        fadeFrom = a; // usa fadeFrom come alpha corrente
        if(t >= 1.0f){
            fading = false;
            if(fadeCallback){ auto cb = fadeCallback; fadeCallback = nullptr; cb(); }
        }
    }

    // brightness tween
    if(brightnessDuration_ > 0.0){
        double t = (ofGetElapsedTimef() - brightnessStartTime_) / brightnessDuration_;
        if(t >= 1.0){ t = 1.0; brightnessDuration_ = 0.0; }
        brightnessCur_ = ofLerp(brightnessStart_, brightnessTarget_, static_cast<float>(ofClamp(t, 0.0, 1.0)));
    }else{
        // ensure clamped
        brightnessCur_ = ofClamp(brightnessCur_, 0.0f, 1.0f);
    }

    if(ledTest.active){
        updateLedTestAnimation();
    }

    updateOverlayInfo();
}

void ofApp::draw(){
    ofBackground(0);
    if(ledTest.active){
        drawLedTestPattern();
    }else{
        if(currentIsImage && currentImage.isAllocated()){
            float iw = currentImage.getWidth();
            float ih = currentImage.getHeight();
            if(iw > 0 && ih > 0){
                float sw = ofGetWidth(), sh = ofGetHeight();
                float scale = std::min(sw/iw, sh/ih);
                float dw = iw * scale;
                float dh = ih * scale;
                float x = (sw - dw) * 0.5f;
                float y = (sh - dh) * 0.5f;
                ofSetColor(255);
                currentImage.draw(x, y, dw, dh);
            }
        }else if(player.isLoaded()){
            float vw = player.getWidth();
            float vh = player.getHeight();
            if(vw > 0 && vh > 0){
                // letterbox fit
                float sw = ofGetWidth(), sh = ofGetHeight();
                float scale = std::min(sw/vw, sh/vh);
                float dw = vw*scale, dh = vh*scale;
                float x = (sw - dw) * 0.5f;
                float y = (sh - dh) * 0.5f;
                player.draw(x, y, dw, dh);
            }
        }

        // splash overlay sopra al video se attivo
        splash.draw(ofGetWidth(), ofGetHeight());

        // visual fade/brightness overlay (black)
        float fadeAlpha = ofClamp(fadeFrom, 0.0f, 255.0f);
        float brightnessAlpha = ofClamp((1.0f - ofClamp(brightnessCur_, 0.0f, 1.0f)) * 255.0f, 0.0f, 255.0f);
        float overlayAlpha = std::max(fadeAlpha, brightnessAlpha);
        if(overlayAlpha > 0.5f){
            ofPushStyle();
            ofSetColor(0, 0, 0, overlayAlpha);
            ofDrawRectangle(0, 0, ofGetWidth(), ofGetHeight());
            ofPopStyle();
        }
    }

    auto drawHudBlock = [&](const std::string& text){
        if(text.empty()){ return; }
        std::vector<std::string> lines = ofSplitString(text, "\n", true, true);
        if(lines.empty()){ return; }
        if(overlayFontLoaded){
            const float lineHeight = overlayFont.getLineHeight() * 1.15f;
            std::vector<float> scales(lines.size(), 1.0f);
            int ipIndex = -1;
            for(size_t i = 0; i < lines.size(); ++i){
                if(ofIsStringInString(lines[i], "IP:") && lines[i].find("IP:") == 0){
                    ipIndex = static_cast<int>(i);
                    break;
                }
            }
            if(ipIndex >= 0){
                float width = overlayFont.stringWidth(lines[ipIndex]);
                if(width > 1.0f){
                    const float maxAllowed = std::max(120.0f, ofGetWidth() - 64.0f);
                    float scale = std::min(maxAllowed / width, 12.0f);
                    scale = std::max(scale, 1.5f);
                    scale *= 0.97f;
                    scales[static_cast<size_t>(ipIndex)] = scale;
                }
            }
            float maxWidth = 0.0f;
            float blockHeight = 0.0f;
            for(size_t i = 0; i < lines.size(); ++i){
                float scale = scales[i];
                float lineW = overlayFont.stringWidth(lines[i]) * scale;
                maxWidth = std::max(maxWidth, lineW);
                blockHeight += lineHeight * scale;
            }
            float margin = 32.0f;
            float rectW = maxWidth + 48.0f;
            float rectH = blockHeight + 48.0f;
            float rectX = (ofGetWidth() - rectW) * 0.5f;
            if(rectX < 12.0f){
                rectX = 12.0f;
            }
            if(rectX + rectW > ofGetWidth() - 12.0f){
                rectX = ofGetWidth() - rectW - 12.0f;
            }
            float y = ofGetHeight() - blockHeight - margin;
            float rectY = y - 24.0f;
            float blockCenterX = rectX + rectW * 0.5f;
            ofPushStyle();
            ofSetColor(0, 0, 0, 160);
            ofDrawRectangle(rectX, rectY, rectW, rectH);
            ofSetColor(255);
            float cursorY = y;
            for(size_t i = 0; i < lines.size(); ++i){
                float scale = scales[i];
                float asc = overlayFont.getAscenderHeight() * scale;
                float drawY = cursorY + asc;
                float lineWidth = overlayFont.stringWidth(lines[i]) * scale;
                float lineX = blockCenterX - (lineWidth * 0.5f);
                ofPushMatrix();
                ofTranslate(lineX, drawY);
                ofScale(scale, scale);
                overlayFont.drawString(lines[i], 0.0f, 0.0f);
                ofPopMatrix();
                cursorY += lineHeight * scale;
            }
            ofPopStyle();
        }else{
            static ofBitmapFont bitmapFont;
            const float baseHeight = 18.0f;
            std::vector<float> widths(lines.size(), 0.0f);
            std::vector<float> scales(lines.size(), 1.0f);
            int ipIndex = -1;
            for(size_t i = 0; i < lines.size(); ++i){
                widths[i] = bitmapFont.getBoundingBox(lines[i], 0, 0).getWidth();
                if(ipIndex < 0 && lines[i].rfind("IP:", 0) == 0){
                    ipIndex = static_cast<int>(i);
                }
            }
            if(ipIndex >= 0 && widths[ipIndex] > 1.0f){
                const float maxAllowed = std::max(120.0f, ofGetWidth() - 64.0f);
                float scale = std::min(maxAllowed / widths[ipIndex], 12.0f);
                scale = std::max(scale, 1.5f);
                scale *= 0.97f;
                scales[static_cast<size_t>(ipIndex)] = scale;
            }
            float maxWidth = 0.0f;
            float blockHeight = 0.0f;
            for(size_t i = 0; i < lines.size(); ++i){
                float lineW = widths[i] * scales[i];
                maxWidth = std::max(maxWidth, lineW);
                blockHeight += baseHeight * scales[i];
            }
            float margin = 32.0f;
            float rectW = maxWidth + 48.0f;
            float rectH = blockHeight + 48.0f;
            float rectX = (ofGetWidth() - rectW) * 0.5f;
            if(rectX < 12.0f){
                rectX = 12.0f;
            }
            if(rectX + rectW > ofGetWidth() - 12.0f){
                rectX = ofGetWidth() - rectW - 12.0f;
            }
            float y = ofGetHeight() - blockHeight - margin;
            float rectY = y - 24.0f;
            float blockCenterX = rectX + rectW * 0.5f;
            ofPushStyle();
            ofSetColor(0, 0, 0, 160);
            ofDrawRectangle(rectX, rectY, rectW, rectH);
            ofSetColor(255);
            float cursorY = y;
            for(size_t i = 0; i < lines.size(); ++i){
                float scale = scales[i];
                float lineW = widths[i] * scale;
                float lineX = blockCenterX - (lineW * 0.5f);
                ofPushMatrix();
                ofTranslate(lineX, cursorY);
                ofScale(scale, scale);
                ofDrawBitmapString(lines[i], 0.0f, baseHeight);
                ofPopMatrix();
                cursorY += baseHeight * scale;
            }
            ofPopStyle();
        }
    };

    if(hudMode == HudMode::Minimal){
        drawHudBlock(overlayInfo.minimalText);
    }else if(hudMode == HudMode::Full){
        drawHudBlock(overlayInfo.fullText);
    }
}

void ofApp::startLedTest(int width, int height, int offsetX, int offsetY, float speed){
    bool wasActive = ledTest.active;
    bool prevRestore = ledTest.restoreWasPlaying;
    bool prevSplash = ledTest.splashWasActive;

    ledTest.requestedWidth = width;
    ledTest.requestedHeight = height;
    ledTest.requestedOffsetX = offsetX;
    ledTest.requestedOffsetY = offsetY;
    ledTest.speed = (speed <= 0.0f ? 160.0f : speed);
    ledTest.phase = 0.0f;
    ledTest.colorPhase = 0.0f;
    ledTest.lastUpdate = ofGetElapsedTimef();
    if(!wasActive){
        ledTest.restoreWasPlaying = isPlaying;
        ledTest.splashWasActive = splash.isActive();
        if(ledTest.restoreWasPlaying){
            pause();
        }
        if(ledTest.splashWasActive){
            splash.hide();
        }
    }else{
        ledTest.restoreWasPlaying = prevRestore;
        ledTest.splashWasActive = prevSplash;
    }
    ledTest.active = true;
    updateLedTestArea();
    updateOverlayInfo(true);
    ofLogNotice() << "[LED TEST] ON width=" << static_cast<int>(ledTest.area.getWidth())
                  << " height=" << static_cast<int>(ledTest.area.getHeight())
                  << " offset=" << static_cast<int>(ledTest.area.getX()) << ","
                  << static_cast<int>(ledTest.area.getY());
}

void ofApp::stopLedTest(){
    if(!ledTest.active){
        return;
    }
    ledTest.active = false;
    if(ledTest.restoreWasPlaying){
        resume();
    }
    if(ledTest.splashWasActive){
        splash.show();
    }
    ledTest.restoreWasPlaying = false;
    ledTest.splashWasActive = false;
    ledTest.phase = 0.0f;
    ledTest.colorPhase = 0.0f;
    updateOverlayInfo(true);
    ofLogNotice() << "[LED TEST] OFF";
}

void ofApp::updateLedTestArea(){
    float screenW = static_cast<float>(ofGetWidth());
    float screenH = static_cast<float>(ofGetHeight());
    if(screenW <= 0.0f || screenH <= 0.0f){
        ledTest.area.set(0, 0, 0, 0);
        return;
    }
    float targetW = ledTest.requestedWidth > 0 ? static_cast<float>(ledTest.requestedWidth) : screenW;
    float targetH = ledTest.requestedHeight > 0 ? static_cast<float>(ledTest.requestedHeight) : screenH;
    targetW = ofClamp(targetW, 1.0f, screenW);
    targetH = ofClamp(targetH, 1.0f, screenH);
    float x = static_cast<float>(ledTest.requestedOffsetX);
    float y = static_cast<float>(ledTest.requestedOffsetY);
    x = ofClamp(x, 0.0f, screenW - targetW);
    y = ofClamp(y, 0.0f, screenH - targetH);
    ledTest.area.set(x, y, targetW, targetH);
}

void ofApp::updateLedTestAnimation(){
    float now = ofGetElapsedTimef();
    float dt = now - ledTest.lastUpdate;
    if(dt < 0.0f || dt > 1.0f){
        dt = 0.0f;
    }
    ledTest.lastUpdate = now;
    float cycleWidth = std::max(ledTest.area.getWidth(), 1.0f);
    ledTest.phase += dt * ledTest.speed;
    float wrap = std::max(cycleWidth * 4.0f, 200.0f);
    if(ledTest.phase >= wrap){
        ledTest.phase = std::fmod(ledTest.phase, wrap);
    }
    ledTest.colorPhase += dt;
    if(ledTest.colorPhase >= TWO_PI){
        ledTest.colorPhase = std::fmod(ledTest.colorPhase, TWO_PI);
    }
}

void ofApp::drawLedTestPattern(){
    updateLedTestArea();
    const ofRectangle area = ledTest.area;
    if(area.getWidth() <= 1.0f || area.getHeight() <= 1.0f){
        return;
    }

    ofPushStyle();
    ofFill();
    ofSetColor(12);
    ofDrawRectangle(0, 0, ofGetWidth(), ofGetHeight());

    const std::array<ofColor, 4> palette = {ofColor::red, ofColor::green, ofColor::blue, ofColor::white};
    float stripeWidth = std::max(10.0f, area.getWidth() / (static_cast<float>(palette.size()) * 2.0f));
    float shift = std::fmod(ledTest.phase, stripeWidth);
    int baseIndex = static_cast<int>(std::floor(ledTest.phase / stripeWidth));
    int stripeCount = static_cast<int>(std::ceil(area.getWidth() / stripeWidth)) + 3;
    for(int i = -1; i < stripeCount; ++i){
        float x = area.x + (i * stripeWidth) - shift;
        if(x > area.x + area.width){
            break;
        }
        int paletteIndex = (i - baseIndex) % static_cast<int>(palette.size());
        if(paletteIndex < 0) paletteIndex += static_cast<int>(palette.size());
        ofSetColor(palette[paletteIndex]);
        ofDrawRectangle(x, area.y, stripeWidth + 1.0f, area.height);
    }

    float pulse = (std::sin(ledTest.colorPhase) * 0.5f) + 0.5f;
    ofSetColor(255, static_cast<int>(pulse * 90.0f));
    ofDrawRectangle(area.x, area.y, area.width, area.height);

    float crossX = area.x + std::fmod(ledTest.phase * 0.5f, std::max(1.0f, area.getWidth()));
    float crossY = area.y + std::fmod(ledTest.phase * 0.35f, std::max(1.0f, area.getHeight()));
    ofSetColor(0, 0, 0, 200);
    ofSetLineWidth(4.0f);
    ofDrawLine(crossX, area.y, crossX, area.y + area.height);
    ofDrawLine(area.x, crossY, area.x + area.width, crossY);
    ofSetColor(255);
    ofSetLineWidth(1.5f);
    ofDrawLine(crossX, area.y, crossX, area.y + area.height);
    ofDrawLine(area.x, crossY, area.x + area.width, crossY);

    ofNoFill();
    ofSetColor(255);
    ofSetLineWidth(2.0f);
    ofDrawRectangle(area);

    ofFill();
    std::ostringstream oss;
    oss << "LED TEST " << static_cast<int>(area.getWidth()) << "x" << static_cast<int>(area.getHeight())
        << " @" << static_cast<int>(area.getX()) << "," << static_cast<int>(area.getY());
    ofDrawBitmapStringHighlight(oss.str(), area.x + 16.0f, area.y + 28.0f);

    const std::array<ofColor, 3> swatches = {ofColor::red, ofColor::green, ofColor::blue};
    float box = 18.0f;
    float gap = 6.0f;
    float swY = area.y + area.height - box - 18.0f;
    float swX = area.x + 16.0f;
    for(size_t i = 0; i < swatches.size(); ++i){
        ofSetColor(swatches[i]);
        ofDrawRectangle(swX + (box + gap) * static_cast<float>(i), swY, box, box);
    ofNoFill();
    ofSetColor(0);
    ofDrawRectangle(swX + (box + gap) * static_cast<float>(i), swY, box, box);
    ofFill();
    }

    ofPopStyle();
}

void ofApp::keyPressed(int key){
    ofLogNotice() << "Key pressed -> " << key;
    if(key==' '){ isPlaying ? stop() : play(); }
    if(key==OF_KEY_RIGHT) next();
    if(key==OF_KEY_LEFT) prev();
    if(key=='f' || key=='F'){
        auto window = ofGetWindowPtr();
        if(!window){
            ofToggleFullscreen();
            return;
        }
        bool isFullscreen = (window->getWindowMode() == OF_FULLSCREEN);
        if(isFullscreen){
            window->setFullscreen(false);
            DisplayInfo info = detectDisplayInfo();
            int screenW = info.valid ? info.width : static_cast<int>(ofGetScreenWidth());
            int screenH = info.valid ? info.height : static_cast<int>(ofGetScreenHeight());
            if(screenW <= 0) screenW = static_cast<int>(ofGetWidth());
            if(screenH <= 0) screenH = static_cast<int>(ofGetHeight());
            int targetW = 640;
            int targetH = 360;
            ofSetWindowShape(targetW, targetH);
            if(screenW > 0 && screenH > 0){
                int posX = std::max(0, (screenW - targetW) / 2);
                int posY = std::max(0, (screenH - targetH) / 2);
#ifdef _WIN32
                if(auto windowPtr = ofGetWindowPtr()){
                    if(auto glfwWindow = std::dynamic_pointer_cast<ofAppGLFWWindow>(windowPtr)){
                        if(GLFWwindow* nativeGLFW = glfwWindow->getGLFWWindow()){
                            if(HWND hwnd = glfwGetWin32Window(nativeGLFW)){
                                // Force native window size/position so the OS applies chrome offsets correctly
                                RECT rect{posX, posY, posX + targetW, posY + targetH};
                                DWORD style = GetWindowLong(hwnd, GWL_STYLE);
                                AdjustWindowRectEx(&rect, style, FALSE, GetWindowLong(hwnd, GWL_EXSTYLE));
                                int widthAdj = rect.right - rect.left;
                                int heightAdj = rect.bottom - rect.top;
                                SetWindowPos(hwnd, nullptr, std::max(0, posX), std::max(0, posY), widthAdj, heightAdj, SWP_NOZORDER | SWP_NOACTIVATE);
                            }else{
                                ofSetWindowPosition(posX, posY);
                            }
                        }else{
                            ofSetWindowPosition(posX, posY);
                        }
                    }else{
                        ofSetWindowPosition(posX, posY);
                    }
                }else{
                    ofSetWindowPosition(posX, posY);
                }
#else
                ofSetWindowPosition(posX, posY);
#endif
            }
            ofLogNotice() << "Fullscreen OFF -> windowed " << targetW << "x" << targetH;
        }else{
            window->setFullscreen(true);
            ofLogNotice() << "Windowed -> fullscreen";
        }
    }
    if(key=='r' || key=='R') reloadPlaylist();
    if(key=='t' || key=='T'){
        int next = static_cast<int>(hudMode) + 1;
        if(next > static_cast<int>(HudMode::Full)){
            next = static_cast<int>(HudMode::Hidden);
        }
        setHudModeByIndex(next);
    }
    if(key=='q' || key==27) ofExit();
}

void ofApp::exit(){
    if(httpServer) httpServer->stop();   // <— NUOVO
    udp.Close();
    player.stop();
    player.closeMovie();
    currentImage.clear();

#ifdef _WIN32
    if(wsaInitialized){
        WSACleanup();
        wsaInitialized = false;
    }
#endif
}

// ---- Visual fades implementation ----
void ofApp::fadeIn(float seconds){
    float sec = std::max(0.0f, seconds);
    fading = true;
    // se non abbiamo alpha corrente, considera da pieno nero
    if(fadeFrom <= 0.0f) fadeFrom = 255.0f;
    fadeTo = 0.0f;
    fadeDuration = (sec <= 0.0f ? 0.0001f : sec);
    fadeStartTime = ofGetElapsedTimef();
    fadeCallback = nullptr;
}

void ofApp::fadeToBlack(float seconds){
    float sec = std::max(0.0f, seconds);
    fading = true;
    // normalizza current alpha in range 0..255
    fadeFrom = ofClamp(fadeFrom, 0.0f, 255.0f);
    fadeTo = 255.0f;
    fadeDuration = (sec <= 0.0f ? 0.0001f : sec);
    fadeStartTime = ofGetElapsedTimef();
    fadeCallback = nullptr;
}

void ofApp::transitionToIndex(size_t idx, float seconds){
    float sec = std::max(0.0f, seconds);
    // FTB, poi carica idx, poi fade-in
    fading = true;
    fadeFrom = ofClamp(fadeFrom, 0.0f, 255.0f);
    fadeTo = 255.0f;
    fadeDuration = (sec <= 0.0f ? 0.0001f : sec);
    fadeStartTime = ofGetElapsedTimef();
    fadeCallback = [this, idx, sec]{
        if(idx < playlist.size()){
            currentIndex = idx;
            loadCurrent();
        }
        this->fadeIn(sec);
    };
}

ofApp::DisplayInfo ofApp::detectDisplayInfo() const{
    DisplayInfo info;
#ifdef _WIN32
    HDC hdc = GetDC(nullptr);
    if(hdc){
        int width = GetDeviceCaps(hdc, HORZRES);
        int height = GetDeviceCaps(hdc, VERTRES);
        int refresh = GetDeviceCaps(hdc, VREFRESH);
        ReleaseDC(nullptr, hdc);
        if(width > 0 && height > 0){
            info.width = width;
            info.height = height;
            if(refresh > 0){
                info.refreshHz = static_cast<float>(refresh);
            }
            info.valid = true;
            info.source = "GetDeviceCaps";
        }
    }
#else
    int width = static_cast<int>(ofGetScreenWidth());
    int height = static_cast<int>(ofGetScreenHeight());
    if(width > 0 && height > 0){
        info.width = width;
        info.height = height;
        float target = ofGetTargetFrameRate();
        if(target <= 0.0f){
            target = ofGetFrameRate();
        }
        if(target > 0.0f){
            info.refreshHz = target;
        }
        info.valid = true;
        info.source = "ofGetScreenWidth";
    }
#endif
    return info;
}

ofApp::DisplayInfo ofApp::getDisplayInfoCached() const{
    float now = ofGetElapsedTimef();
    if(!displayInfoCache.valid || (now - displayInfoLastProbe) > 5.0f){
        displayInfoCache = detectDisplayInfo();
        displayInfoLastProbe = now;
    }
    return displayInfoCache;
}

std::string ofApp::formatDisplayInfo(const DisplayInfo& info) const{
    if(!info.valid){
        return "-";
    }
    std::ostringstream ss;
    ss << info.width << "x" << info.height;
    if(info.refreshHz > 0.0f){
        ss << " @ ";
        float rounded = std::round(info.refreshHz);
        if(std::abs(info.refreshHz - rounded) < 0.05f){
            ss << static_cast<int>(rounded);
        }else{
            ss << std::fixed << std::setprecision(2) << info.refreshHz;
        }
        ss << "Hz";
    }
    if(info.interlaced){
        ss << " (interlaced)";
    }
    return ss.str();
}

void ofApp::updateOverlayInfo(bool force){
    float now = ofGetElapsedTimef();
    if(!force && (now - overlayInfo.lastUpdate) < overlayInfo.updateInterval){
        return;
    }
    overlayInfo.lastUpdate = now;

    std::vector<std::string> ips = gatherLocalIPs();
    std::string statusLine = formattedStatusLine();

    std::string ipSummary = "IP: ";
    if(ips.empty()){
        ipSummary += "127.0.0.1";
    }else{
        ipSummary += ofJoinString(ips, ", ");
    }
    std::ostringstream oss;
    const std::string displayIp = ips.empty() ? std::string("127.0.0.1") : ips.front();
    oss << ipSummary;
    oss << "\nStatus: " << statusLine;
    oss << "\nHTTP: http://" << displayIp << ":" << cfg.httpPort;
    oss << "\nUDP: " << displayIp << ":" << 7777 << " (comandi UDP)";
    // oss << "\nUDP: " << displayIp << ":" << cfg.udpPort << " (comandi UDP)";
    oss << "\ncartella media: " << cfg.mediaDir;
    DisplayInfo disp = getDisplayInfoCached();
    if(disp.valid){
        oss << "\nDisplay: " << formatDisplayInfo(disp);
    }
    overlayInfo.fullText = oss.str();

    std::ostringstream minimal;
    minimal << ipSummary;
    minimal << "\nHTTP: http://" << displayIp << ":" << cfg.httpPort;
    minimal << "\nUDP: " << displayIp << ":" << cfg.udpPort;
    overlayInfo.minimalText = minimal.str();
}

std::vector<std::string> ofApp::gatherLocalIPs() const{
    std::vector<std::string> ips;
    char hostname[256] = {0};
    if(gethostname(hostname, sizeof(hostname)) == 0){
        addrinfo hints{};
        hints.ai_family = AF_INET;
        hints.ai_socktype = SOCK_STREAM;
        addrinfo* info = nullptr;
        if(getaddrinfo(hostname, nullptr, &hints, &info) == 0){
            for(auto ptr = info; ptr != nullptr; ptr = ptr->ai_next){
                auto sin = reinterpret_cast<sockaddr_in*>(ptr->ai_addr);
                if(!sin) continue;
                std::string ip = inet_ntoa(sin->sin_addr);
                if(!ip.empty() && ip != "127.0.0.1" && ip != "0.0.0.0" &&
                   std::find(ips.begin(), ips.end(), ip) == ips.end()){
                    ips.push_back(ip);
                }
            }
            freeaddrinfo(info);
        }
    }

    if(ips.empty()){
#ifdef _WIN32
        SOCKET sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        if(sock != INVALID_SOCKET){
            sockaddr_in remote{};
            remote.sin_family = AF_INET;
            remote.sin_port = htons(53);
            remote.sin_addr.s_addr = inet_addr("8.8.8.8");
            if(connect(sock, reinterpret_cast<sockaddr*>(&remote), sizeof(remote)) == 0){
                sockaddr_in local{};
                int len = sizeof(local);
                if(getsockname(sock, reinterpret_cast<sockaddr*>(&local), &len) == 0){
                    std::string ip = inet_ntoa(local.sin_addr);
                    if(!ip.empty() && std::find(ips.begin(), ips.end(), ip) == ips.end()){
                        ips.push_back(ip);
                    }
                }
            }
            closesocket(sock);
        }
#else
        int sockfd = socket(AF_INET, SOCK_DGRAM, 0);
        if(sockfd >= 0){
            sockaddr_in remote{};
            remote.sin_family = AF_INET;
            remote.sin_port = htons(53);
            remote.sin_addr.s_addr = inet_addr("8.8.8.8");
            if(connect(sockfd, reinterpret_cast<sockaddr*>(&remote), sizeof(remote)) == 0){
                sockaddr_in local{};
                socklen_t len = sizeof(local);
                if(getsockname(sockfd, reinterpret_cast<sockaddr*>(&local), &len) == 0){
                    std::string ip = inet_ntoa(local.sin_addr);
                    if(!ip.empty() && std::find(ips.begin(), ips.end(), ip) == ips.end()){
                        ips.push_back(ip);
                    }
                }
            }
            close(sockfd);
        }
#endif
    }

    if(ips.empty()){
        ips.push_back("127.0.0.1");
    }
    return ips;
}

std::string ofApp::formattedStatusLine() const{
    std::ostringstream ss;
    ss << (isPlaying ? "PLAYING" : "STOPPED");
    if(!playlist.empty()){
        ss << "  " << (currentIndex + 1) << "/" << playlist.size();
    const PlaylistItem& entry = playlist[currentIndex];
    std::string filename = entry.file.getFileName();
        if(!filename.empty()){
            ss << "  " << filename;
        }
        if(entry.isImage){
            ss << "  [IMG]";
        }else{
            ss << "  [VID]";
        }
    }
    ss << "  LOOP:" << (cfg.loopEach ? "ON" : "OFF");
    if(ledTest.active){
        ss << "  TEST-LED " << static_cast<int>(ledTest.area.getWidth())
           << "x" << static_cast<int>(ledTest.area.getHeight())
           << " @" << static_cast<int>(ledTest.area.getX())
           << "," << static_cast<int>(ledTest.area.getY());
    }
    return ss.str();
}

