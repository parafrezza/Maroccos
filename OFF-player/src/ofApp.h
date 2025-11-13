#pragma once
#include "ofMain.h"
#include "ofxNetwork.h"
#include "SplashOverlay.h"
#include <vector>

// --- avanti dichiarazione
class HttpControlServer;

struct PlaylistItem {
    ofFile file;
    bool isImage = false;
};

struct AppConfig {
    std::string mediaDir = "media";
    int udpPort = 7777;
    int httpPort = 8080;          // <— NUOVO
    bool startPaused = true;
    bool loopEach = true;
    int targetFps = 25;
    bool autoReloadOnChange = true;
};

class ofApp : public ofBaseApp {
public:
    void setup() override;
    void update() override;
    void draw() override;
    void keyPressed(int key) override;
    void exit() override;

    // controllo
    void play();
    void stop();
    void pause();
    void resume();
    void next();
    void prev();
    void setIndex(size_t idx);
    void reloadPlaylist();
    void setDir(const std::string& dir);
    void playFileAbsolute(const std::string& absPath);
    // visual fades
    void fadeIn(float seconds);
    void fadeToBlack(float seconds);
    void transitionToIndex(size_t idx, float seconds);
    // brightness overlay (0..1) and go_to_start helpers
    void setBrightness(float value01, float seconds);
    void goToStart();
    // LED test pattern
    void startLedTest(int width, int height, int offsetX, int offsetY, float speed);
    void stopLedTest();

    // rete
    void setupUDP();
    void pollUDP();

    // util
    void loadConfig();
    void saveDefaultConfigIfMissing();
    void buildPlaylist();
    bool loadCurrent();
    std::string statusString() const;
    std::string playlistJSON() const;

    // accessori per HTTP
    const AppConfig& getConfig() const { return cfg; }
    bool isLooping() const { return cfg.loopEach; }

private:
    struct OverlayInfo {
        std::string text;
        float lastUpdate = 0.0f;
        float updateInterval = 1.0f;
    };

    struct DisplayInfo {
        int width = 0;
        int height = 0;
        float refreshHz = 0.0f;
        bool interlaced = false;
        bool valid = false;
        std::string source;
    };

    void updateOverlayInfo(bool force = false);
    std::vector<std::string> gatherLocalIPs() const;
    std::string formattedStatusLine() const;
    void updateLedTestAnimation();
    void updateLedTestArea();
    void drawLedTestPattern();
    DisplayInfo detectDisplayInfo() const;
    DisplayInfo getDisplayInfoCached() const;
    std::string formatDisplayInfo(const DisplayInfo& info) const;

    AppConfig cfg;
    std::vector<PlaylistItem> playlist;
    size_t currentIndex = 0;

    ofVideoPlayer player;
    ofImage currentImage;
    bool currentIsImage = false;
    bool isPlaying = false;

    ofxUDPManager udp;

    // --- HTTP server
    std::shared_ptr<HttpControlServer> httpServer;  // <— NUOVO
    SplashOverlay splash;                           // overlay splash controllabile via HTTP

    // Fade state
    bool fading = false;
    float fadeFrom = 0.0f;        // 0..255 (alpha)
    float fadeTo = 0.0f;          // 0..255
    float fadeStartTime = 0.0f;   // seconds
    float fadeDuration = 0.0f;    // seconds
    std::function<void()> fadeCallback; // called when fade completes

    // Brightness tween state (0..1)
    float brightnessCur_ = 1.0f;
    float brightnessStart_ = 1.0f;
    float brightnessTarget_ = 1.0f;
    double brightnessStartTime_ = 0.0;
    double brightnessDuration_ = 0.0;

    // Overlay HUD
    OverlayInfo overlayInfo;
    bool overlayVisible = false;
    ofTrueTypeFont overlayFont;
    bool overlayFontLoaded = false;
    mutable DisplayInfo displayInfoCache;
    mutable float displayInfoLastProbe = 0.0f;
    struct LedTestState {
        bool active = false;
        int requestedWidth = 0;
        int requestedHeight = 0;
        int requestedOffsetX = 0;
        int requestedOffsetY = 0;
        ofRectangle area{0, 0, 0, 0};
        float speed = 160.0f;
        float phase = 0.0f;
        float colorPhase = 0.0f;
        float lastUpdate = 0.0f;
        bool restoreWasPlaying = false;
        bool splashWasActive = false;
    } ledTest;
#ifdef _WIN32
    bool wsaInitialized = false;
#endif
};

