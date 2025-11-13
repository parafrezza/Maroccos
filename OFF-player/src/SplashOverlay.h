#pragma once
#include "ofMain.h"

class SplashOverlay {
public:
	void show(const std::string& text = "") {
		active_ = true;
		text_ = text;
	}
	void hide() { active_ = false; }
	bool isActive() const { return active_; }
	void setText(const std::string& text) { text_ = text; }

	void draw(int width, int height) const {
		if(!active_) return;
		// fondo nero pieno
		ofPushStyle();
		ofSetColor(0, 0, 0, 255);
		ofDrawRectangle(0, 0, width, height);
		// testo opzionale centrato
		if(!text_.empty()){
			ofSetColor(255);
			ofDrawBitmapStringHighlight(text_, 20, 40);
		}
		ofPopStyle();
	}
private:
	bool active_ = false;
	std::string text_;
};
