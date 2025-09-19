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
	rm -f data/positions.csv
	rm -f .env.example
	rm -f docs/stories/story-4-alligator-strategy-implementation.md
	rm -f docs/stories/story-5-position-management.md
	rm -f docs/stories/story-6-websocket-refactoring.md
	rm -f docs/stories/story-7-discord-notifications.md
	rm -f qa/gates/screener.story-4-alligator-strategy-implementation.yml
	rm -f qa/gates/screener.story-5-position-management.yml
	rm -f qa/gates/screener.story-6-websocket-refactoring.yml
	rm -f qa/gates/screener.story-7-discord-notifications.yml
