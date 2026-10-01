PY ?= .venv/bin/python
PIP ?= .venv/bin/pip

.PHONY: bootstrap lint test run-n run-n1 e2e validate validate-remote onboard ai-eval ci clean

bootstrap:            ## venv + editable install with dev tools
	python3 -m venv .venv
	$(PIP) install -q -e ".[dev]"

lint:
	$(PY) -m ruff check src tests

test:
	$(PY) -m pytest

run-n:                ## current release on :8080
	OSS_LAB_RELEASE=n $(PY) -m uvicorn oss_lab.main:app --port 8080

run-n1:               ## candidate release on :8081
	OSS_LAB_RELEASE=n1 $(PY) -m uvicorn oss_lab.main:app --port 8081

e2e:                  ## lifecycle + integrity scenarios against an in-process stack
	$(PY) -m pytest tests/test_lifecycle.py tests/test_reconciliation.py

validate:             ## in-process N vs N+1 readiness report (FROM/TO/COMPONENT)
	$(PY) -m oss_lab.validation.cli --from $(or $(FROM),n) --to $(or $(TO),n1) --component $(or $(COMPONENT),all)

validate-remote:      ## against deployed stacks: make validate-remote N_URL=... N1_URL=...
	$(PY) -m oss_lab.validation.cli --n-url $(N_URL) --n1-url $(N1_URL)

onboard:              ## run the onboarding gate for every package
	$(PY) -m oss_lab.onboarding.cli onboarding

ai-eval:              ## print precision/recall of the failure-prediction model
	$(PY) -c "from oss_lab.ai.predictive import evaluate; print(evaluate())"

ci: lint test validate onboard

clean:                ## remove generated reports only
	find validation/reports -name 'readiness-*' -type f -delete
