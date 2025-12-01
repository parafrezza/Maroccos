
#pragma once
#include "ofMain.h"
#include "ofAppRunner.h"
#include "ofUtils.h"
#include <Poco/Net/HTTPServer.h>
#include <Poco/Net/HTTPRequestHandler.h>
#include <Poco/Net/HTTPRequestHandlerFactory.h>
#include <Poco/Net/HTTPServerParams.h>
#include <Poco/Net/ServerSocket.h>
#include <Poco/Net/HTMLForm.h>
#include <Poco/Net/HTTPServerRequest.h>
#include <Poco/Net/HTTPServerResponse.h>
#include <Poco/URI.h>
#include <Poco/Thread.h>
#include <Poco/Runnable.h>
#include <memory>
#include <functional>

class ofApp; // forward

// Una piccola facciata per invocare i metodi di controllo in ofApp
struct ControlAPI {
    std::function<void()> fnPlay;
    std::function<void()> fnStop;
    std::function<void()> fnNext;
    std::function<void()> fnPrev;
    std::function<void(size_t)> fnSetIndex;
    std::function<void(const std::string&)> fnSetDir;
    std::function<void()> fnReload;
    std::function<std::string()> fnStatus;
    std::function<std::string()> fnPlaylist; // JSON con lista in ordine
    std::function<void(bool)> fnLoop;
    std::function<void(bool)> fnCenterVideo;
    // Nuove API
    std::function<void()> fnPause;
    std::function<void()> fnResume;
    std::function<void(const std::string&)> fnPlayFile; // path assoluto
    // Splash controls
    std::function<void(const std::string&)> fnSplashShow; // text opzionale
    std::function<void()> fnSplashHide;
    std::function<void(const std::string&)> fnSplashText;
    // Visual fades
    std::function<void(float)> fnFadeIn;      // fade from black to video in seconds
    std::function<void(float)> fnFadeToBlack; // fade to black in seconds
    // Visual brightness + go_to_start
    std::function<void(float,float)> fnBrightness; // value01, seconds
    std::function<void()> fnGoToStart;
    // HUD visibility (overlay texts)
    std::function<void(bool)> fnHudVisible;
    std::function<void(int)> fnHudMode;
    // Version inquiry
    std::function<std::string()> fnVersion;
    std::function<std::string()> fnHeadlessVersion;
    // LED test pattern
    std::function<void(int, int, int, int, float)> fnLedTestStart;
    std::function<void()> fnLedTestStop;
};

class HttpRequestHandler : public Poco::Net::HTTPRequestHandler {
public:
    HttpRequestHandler(ControlAPI api) : api_(std::move(api)) {}

    void handleRequest(Poco::Net::HTTPServerRequest& request,
                       Poco::Net::HTTPServerResponse& response) override;

private:
    ControlAPI api_;

    void sendJSON(Poco::Net::HTTPServerResponse& response, const std::string& json);
    void sendOK(Poco::Net::HTTPServerResponse& response, const std::string& msg = "{\"ok\":true}");
    void sendBad(Poco::Net::HTTPServerResponse& response, const std::string& msg);
};

class HttpRequestHandlerFactory : public Poco::Net::HTTPRequestHandlerFactory {
public:
    HttpRequestHandlerFactory(ControlAPI api) : api_(std::move(api)) {}
    Poco::Net::HTTPRequestHandler* createRequestHandler(const Poco::Net::HTTPServerRequest& request) override {
        return new HttpRequestHandler(api_);
    }
private:
    ControlAPI api_;
};

class HttpServerRunnable : public Poco::Runnable {
public:
    HttpServerRunnable(int port, ControlAPI api);
    void run() override;
    void stop();

private:
    int port_;
    ControlAPI api_;
    std::atomic<bool> running_{true};
};

class HttpControlServer {
public:
    HttpControlServer();
    ~HttpControlServer();

    bool start(int port, ControlAPI api);
    void stop();

private:
    std::unique_ptr<Poco::Thread> thread_;
    std::unique_ptr<HttpServerRunnable> runnable_;
};
