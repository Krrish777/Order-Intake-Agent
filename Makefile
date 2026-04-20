.PHONY: help cli dev emulator seed

export FIRESTORE_EMULATOR_HOST := localhost:8080
export GOOGLE_CLOUD_PROJECT := demo-order-intake-local

help:
	@echo "Targets:"
	@echo "  cli       - Run the agent with command-line mode"
	@echo "  dev       - Run the agent with web interface"
	@echo "  emulator  - Start Firestore emulator (foreground; leave running)"
	@echo "  seed      - Load data/masters/*.json into the running emulator"

cli:
	uv adk run backend/my_agent

dev:
	uv adk web backend/my_agent

emulator:
	firebase emulators:start --only firestore

seed:
	uv run python scripts/load_master_data.py
