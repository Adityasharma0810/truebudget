PYTHON ?= .venv/bin/python
AGENT_PYTHONPATH := agent

.PHONY: test lint build contracts venv agent-install test-agent test-netns lab-demo reset-lab

test:
	pnpm test

lint:
	pnpm lint

build:
	pnpm typecheck
	pnpm --filter figma-make-app build

contracts:
	pnpm contracts

venv:
	test -x $(PYTHON) || python3 -m venv .venv
	$(PYTHON) -m pip install --upgrade pip

agent-install: venv
	$(PYTHON) -m pip install -e "agent[dev]"

test-agent: agent-install
	cd agent && ../$(PYTHON) -m pytest -m "not netns"

test-netns: agent-install
	unshare -Urn env PYTHONPATH=agent $(PYTHON) -m pytest -m netns agent/tests

lab-demo: agent-install
	unshare -Urn env PYTHONPATH=$(AGENT_PYTHONPATH) $(PYTHON) -m eb_agent.lab.harness \
		--controllers unmanaged,cake_static --seed 42 --duration 12 \
		--out reports/lab/baseline.csv

reset-lab:
	bash scripts/reset_lab.sh
