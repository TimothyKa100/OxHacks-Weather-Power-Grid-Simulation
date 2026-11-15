# One-line entry points. `make install` once, then `make run`.
PY := .venv/bin/python

.PHONY: install run test test-all train
install:
	python3 -m venv .venv
	$(PY) -m pip install -r requirements-dev.txt
	npm ci --prefix frontend

run:            ## build the UI and serve it with the API on http://127.0.0.1:5001
	$(PY) scripts/serve_preview.py

test:           ## fast tests (about a minute)
	$(PY) -m pytest -q -m "not slow"
	npm test --prefix frontend

test-all:       ## everything, including three full-storm simulations (about 8 minutes)
	$(PY) -m pytest -q
	npm test --prefix frontend

train:          ## rebuild the dataset, train and evaluate both corridor models (needs the 18 GB data archive in data/)
	$(PY) -m failure_model gb-dataset
	$(PY) -m failure_model gb-train
	$(PY) -m failure_model gb-train-v2
