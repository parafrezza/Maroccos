// main.cpp -RF 2025 
#include "ofMain.h"
#include "ofApp.h"
#include <memory>
#include <string>
#include <utility>
// Usa EGLWindow SOLO se esplicitamente richiesto: molte build di oF su Linux ARM non includono ofAppEGLWindow
// Definisci OF_USE_EGLWINDOW in config.make per abilitarlo, altrimenti fallback a GLFW.
#if defined(OF_USE_EGLWINDOW)
#include "ofAppEGLWindow.h"
#endif


namespace {
class Utf8WarningFilterChannel : public ofBaseLoggerChannel {
public:
    explicit Utf8WarningFilterChannel(std::shared_ptr<ofBaseLoggerChannel> delegate)
        : delegate_(std::move(delegate)) {}

    void log(ofLogLevel level, const std::string & module, const std::string & message) override {
        if(module == "ofInit" && message.find("MSYS2 has limited support for UTF-8") != std::string::npos){
            // Sopprime il warning ridondante emesso da oF quando buildato via MSYS2/mingw.
            return;
        }
        if(delegate_){
            delegate_->log(level, module, message);
        }
    }

private:
    std::shared_ptr<ofBaseLoggerChannel> delegate_;
};

void installUtf8WarningFilter(){
#if defined(TARGET_WIN32) && !defined(_MSC_VER)
    auto current = ofGetLoggerChannel();
    if(std::dynamic_pointer_cast<Utf8WarningFilterChannel>(current)){
        return;
    }
    if(!current){
        current = std::make_shared<ofConsoleLoggerChannel>();
    }
    ofSetLoggerChannel(std::make_shared<Utf8WarningFilterChannel>(std::move(current)));
#endif
}
} // namespace

int main(){
    installUtf8WarningFilter();
#if defined(OF_USE_EGLWINDOW)
    // Finestra EGL/KMS (richiede che libopenFrameworks sia stato compilato con ofAppEGLWindow)
    auto eglWindow = std::make_shared<ofAppEGLWindow>();
    ofSetupOpenGL(eglWindow, 1280, 720, OF_FULLSCREEN);
    ofRunApp(eglWindow, std::make_shared<ofApp>());
    ofRunMainLoop();
#else
    // Default: GLFW (funziona con X11 e con xvfb-run in headless)
    ofGLFWWindowSettings settings;
    settings.setGLVersion(3,2);
    settings.windowMode = OF_FULLSCREEN;
    settings.decorated = false;
    auto window = ofCreateWindow(settings);
    ofRunApp(window, std::make_shared<ofApp>());
    ofRunMainLoop();
#endif
}