#include "HttpControlServer.h"
#include <sstream>

// ========== HttpRequestHandler ==========
void HttpRequestHandler::handleRequest(Poco::Net::HTTPServerRequest& request,
                                        Poco::Net::HTTPServerResponse& response) {
    Poco::URI uri(request.getURI());
    std::string path = uri.getPath();
    Poco::Net::HTMLForm form(request, request.stream());
    
    if (path == "/play") {
        if (api_.fnPlay) api_.fnPlay();
        sendOK(response);
    } else if (path == "/pause") {
        if (api_.fnPause) api_.fnPause();
        sendOK(response);
    } else if (path == "/resume") {
        if (api_.fnResume) api_.fnResume();
        sendOK(response);
    } else if (path == "/stop") {
        if (api_.fnStop) api_.fnStop();
        sendOK(response);
    } else if (path == "/next") {
        if (api_.fnNext) api_.fnNext();
        sendOK(response);
    } else if (path == "/prev") {
        if (api_.fnPrev) api_.fnPrev();
        sendOK(response);
    } else if (path == "/play_file") {
        std::string p = form.get("path", "");
        if (api_.fnPlayFile) api_.fnPlayFile(p);
        sendOK(response);
    } else if (path == "/set") {
        std::string indexStr = form.get("index", "0");
        size_t idx = std::stoul(indexStr);
        if (api_.fnSetIndex) api_.fnSetIndex(idx);
        sendOK(response);
    } else if (path == "/dir") {
        std::string dir = form.get("path", "");
        if (api_.fnSetDir) api_.fnSetDir(dir);
        sendOK(response);
    } else if (path == "/reload") {
        if (api_.fnReload) api_.fnReload();
        sendOK(response);
    } else if (path == "/loop") {
        std::string onStr = form.get("on", "1");
        bool on = (onStr == "1" || onStr == "true");
        if (api_.fnLoop) api_.fnLoop(on);
        sendOK(response);
    } else if (path == "/status") {
        std::string json = api_.fnStatus ? api_.fnStatus() : "{\"error\":\"no status\"}";
        sendJSON(response, json);
    } else if (path == "/playlist") {
        std::string json = api_.fnPlaylist ? api_.fnPlaylist() : "{\"items\":[]}";
        sendJSON(response, json);
    } else if (path == "/healthz") {
        sendOK(response, "{\"ok\":true}");
        } else if (path == "/splash/show") {
            std::string t = form.get("text", "");
            if (api_.fnSplashShow) api_.fnSplashShow(t);
            sendOK(response);
        } else if (path == "/splash/hide") {
            if (api_.fnSplashHide) api_.fnSplashHide();
            sendOK(response);
        } else if (path == "/splash/text") {
            std::string t = form.get("text", "");
            if (api_.fnSplashText) api_.fnSplashText(t);
            sendOK(response);
    } else if (path == "/hud/visible") {
        std::string onStr = form.get("on", "");
        if (api_.fnHudVisible) {
            if (!onStr.empty()) {
                bool on = (onStr == "1" || onStr == "true");
                api_.fnHudVisible(on);
            } else {
                api_.fnHudVisible(false);
            }
        }
        sendOK(response);
    } else if (path == "/visual/fade_in") {
        std::string s = form.get("seconds", "0.5");
        float sec = ofToFloat(s);
        if (api_.fnFadeIn) api_.fnFadeIn(sec);
        sendOK(response);
    } else if (path == "/visual/ftb") {
        std::string s = form.get("seconds", "0.5");
        float sec = ofToFloat(s);
        if (api_.fnFadeToBlack) api_.fnFadeToBlack(sec);
        sendOK(response);
    } else if (path == "/visual/brightness") {
        // value: accepts 0..1 or 0..100
        std::string vstr = form.get("value", "1");
        std::string sstr = form.get("seconds", "0");
        float v = ofToFloat(vstr);
        float sec = ofToFloat(sstr);
        float v01 = (v > 1.01f ? v/100.0f : v);
        if (api_.fnBrightness) {
            api_.fnBrightness(v01, sec);
            sendOK(response);
        } else {
            sendBad(response, "{\"error\":\"brightness not supported\"}");
        }
    } else if (path == "/go_to_start") {
        if (api_.fnGoToStart) {
            api_.fnGoToStart();
            sendOK(response);
        } else {
            sendBad(response, "{\"error\":\"go_to_start not supported\"}");
        }
    } else if (path == "/test/on") {
        int width = ofToInt(form.get("width", "0"));
        int height = ofToInt(form.get("height", "0"));
        std::string offxStr = form.has("offsetX") ? form.get("offsetX") : form.get("offset_x", "0");
        std::string offyStr = form.has("offsetY") ? form.get("offsetY") : form.get("offset_y", "0");
        int offsetX = ofToInt(offxStr);
        int offsetY = ofToInt(offyStr);
        float speed = ofToFloat(form.get("speed", "160"));
        if (speed <= 0.0f) speed = 160.0f;
        if (api_.fnLedTestStart) {
            api_.fnLedTestStart(width, height, offsetX, offsetY, speed);
        }
        sendOK(response);
    } else if (path == "/test/off") {
        if (api_.fnLedTestStop) {
            api_.fnLedTestStop();
        }
        sendOK(response);
    } else {
        sendBad(response, "{\"error\":\"unknown endpoint\"}");
    }
}

void HttpRequestHandler::sendJSON(Poco::Net::HTTPServerResponse& response, const std::string& json) {
    response.setStatus(Poco::Net::HTTPResponse::HTTP_OK);
    response.setContentType("application/json");
    response.setContentLength(json.size());
    response.send() << json;
}

void HttpRequestHandler::sendOK(Poco::Net::HTTPServerResponse& response, const std::string& msg) {
    sendJSON(response, msg);
}

void HttpRequestHandler::sendBad(Poco::Net::HTTPServerResponse& response, const std::string& msg) {
    response.setStatus(Poco::Net::HTTPResponse::HTTP_BAD_REQUEST);
    response.setContentType("application/json");
    response.setContentLength(msg.size());
    response.send() << msg;
}

// ========== HttpServerRunnable ==========
HttpServerRunnable::HttpServerRunnable(int port, ControlAPI api)
    : port_(port), api_(std::move(api)) {}

void HttpServerRunnable::run() {
    try {
        Poco::Net::ServerSocket svs(port_);
        Poco::Net::HTTPServerParams* params = new Poco::Net::HTTPServerParams;
        params->setMaxQueued(100);
        params->setMaxThreads(4);
        ofLogNotice("HttpServer") << "Avvio HTTP su porta " << port_;
        Poco::Net::HTTPServer srv(new HttpRequestHandlerFactory(api_), svs, params);
        srv.start();
        ofLogNotice("HttpServer") << "Server HTTP attivo su /status /playlist /play ...";
        
        while (running_) {
            Poco::Thread::sleep(100);
        }
        
        srv.stop();
        ofLogNotice("HttpServer") << "Server HTTP arrestato";
    } catch (std::exception& e) {
        ofLogError("HttpServer") << "Exception: " << e.what();
    }
}

void HttpServerRunnable::stop() {
    running_ = false;
}

// ========== HttpControlServer ==========
HttpControlServer::HttpControlServer() {}

HttpControlServer::~HttpControlServer() {
    stop();
}

bool HttpControlServer::start(int port, ControlAPI api) {
    if (thread_) return false; // already running
    
    runnable_ = std::make_unique<HttpServerRunnable>(port, std::move(api));
    thread_ = std::make_unique<Poco::Thread>();
    thread_->start(*runnable_);
    return true;
}

void HttpControlServer::stop() {
    if (runnable_) runnable_->stop();
    if (thread_ && thread_->isRunning()) {
        thread_->join(2000);
    }
    thread_.reset();
    runnable_.reset();
}
