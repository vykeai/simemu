.PHONY: build publish publish-test clean test lint

build:
	python3 -m build

publish: build
	python3 -m twine upload dist/*

publish-test: build
	python3 -m twine upload --repository testpypi dist/*

clean:
	rm -rf dist/ build/ *.egg-info simemu.egg-info

test:
	python3 -m pytest tests/ -x -q

lint:
	python3 -m py_compile simemu/cli.py simemu/session.py simemu/state.py
