PY ?= python
SRC ?= icon.png
NAME ?= morocco-player
OUT ?= dist

.PHONY: all windows ico clean package

all: windows

windows: ico

ico:
	$(PY) build_app_icons.py --src "$(SRC)" --name "$(NAME)" --outdir "$(OUT)"

package: ico
	@echo Impacchetto risorse in $(OUT)/$(NAME).zip
	@cd "$(OUT)" && zip -r "$(NAME).zip" "$(NAME)" > NUL 2>&1 || true

clean:
	@echo Pulizia output
	@rm -rf "$(OUT)"
