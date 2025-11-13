// main.cpp -RF 2025 
#include "ofMain.h"
#include "ofApp.h"
// Usa EGLWindow SOLO se esplicitamente richiesto: molte build di oF su Linux ARM non includono ofAppEGLWindow
// Definisci OF_USE_EGLWINDOW in config.make per abilitarlo, altrimenti fallback a GLFW.
#if defined(OF_USE_EGLWINDOW)
#include "ofAppEGLWindow.h"
#endif


int main(){
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