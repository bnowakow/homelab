.PHONY: help run

.DEFAULT_GOAL := help

MODEL   ?= ls-model.json
TRAFFIC ?= ls-traffic-sample.txt
ARGS    ?=

help:
	@echo "Little Snitch tracking-host monitoring targets"
	@echo ""
	@echo "  run  Compare blocklist hosts with Little Snitch rules and contacted hosts"
	@echo ""
	@echo "Variables (override with make run VAR=value):"
	@echo "  MODEL=$(MODEL)  TRAFFIC=$(TRAFFIC)  ARGS=$(ARGS)"

run:
	./tracking-hosts-vs-littlesnitch.py $(MODEL) --traffic $(TRAFFIC) $(ARGS)
