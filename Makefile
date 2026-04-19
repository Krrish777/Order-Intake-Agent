.PHONY: help
help:
	@echo "Targets:"
	@echo " cli       - Run the agent with command-line mode"
	@echo " dev       - Run the agent with web interface"

cli:
	uv adk run backend/my_agent

dev:
	uv adk web backend/my_agent

