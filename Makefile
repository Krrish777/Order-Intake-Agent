.PHONY: help cli dev emulator seed smoke

export FIRESTORE_EMULATOR_HOST := localhost:8080
export GOOGLE_CLOUD_PROJECT := demo-order-intake-local

help:
	@echo "Targets:"
	@echo "  cli       - Run the agent via adk run (CLI chat against adk_apps/order_intake)"
	@echo "  dev       - Run the agent via adk web (UI over adk_apps — one agent, no sibling noise)"
	@echo "  smoke     - Drive the full pipeline once via Runner against the patterson fixture"
	@echo "  emulator  - Start Firestore emulator (foreground; leave running)"
	@echo "  seed      - Load data/masters/*.json into the running emulator"

cli:
	uv run adk run adk_apps/order_intake

dev:
	uv run adk web adk_apps

smoke:
	uv run python scripts/smoke_run.py data/pdf/patterson_po-28491.wrapper.eml

emulator:
	firebase emulators:start --only firestore

seed:
	uv run python scripts/load_master_data.py
