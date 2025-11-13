// Compila l'implementazione di ofAppEGLWindow dentro l'eseguibile quando
// la build di openFrameworks per linuxaarch64 l'ha esclusa dalla lib core.
// Questo permette di usare EGL/KMS anche su aarch64.
// Nota: il percorso è relativo alla root del progetto OFF-player/src.

#ifdef OF_USE_EGLWINDOW
#include "../../OFF-ROOT/openFrameworks/libs/openFrameworks/app/ofAppEGLWindow.cpp"
#endif
