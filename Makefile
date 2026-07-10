.PHONY: install update check

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

check:
	python -m compileall -q download.py
	python -m json.tool sources.json >/dev/null
