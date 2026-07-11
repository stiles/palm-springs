.PHONY: install update update-climate check

BUCKET ?= stilesdata.com
PREFIX ?= palm-springs
AWS_PROFILE_NAME ?=
PROFILE_ARG = $(if $(strip $(AWS_PROFILE_NAME)),--profile "$(AWS_PROFILE_NAME)",)

install:
	python -m pip install -r requirements.txt

update:
	python download.py
	aws s3 sync .build/data "s3://$(BUCKET)/$(PREFIX)/data" \
		--delete \
		--only-show-errors $(PROFILE_ARG)

update-climate:
	python climate.py
	aws s3 sync .build/climate "s3://$(BUCKET)/$(PREFIX)/climate" \
		--delete \
		--only-show-errors $(PROFILE_ARG)

check:
	python -m compileall -q download.py census.py climate.py
	python -m json.tool sources.json >/dev/null
	python -m json.tool derived-sources.json >/dev/null
	python -m json.tool census.json >/dev/null
	python -m json.tool climate.json >/dev/null
	python -m pytest -q
