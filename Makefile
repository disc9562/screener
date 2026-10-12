install:
	pip install -r requirements.txt

run:
	python main.py $(ARGS)

run-fetch-now:
	python main.py --fetch-now

run-local-test:
	python main.py --local-test

run-local-test-fetch-now:
	python main.py --local-test --fetch-now

run-scheduled:
	python main.py

test:
	pytest tests/

clean:
	rm -rf __pycache__
	rm -rf .pytest_cache
	find . -name "*.pyc" -delete
