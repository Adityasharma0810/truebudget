.PHONY: test lint build contracts

test:
	pnpm test

lint:
	pnpm lint

build:
	pnpm typecheck
	pnpm --filter figma-make-app build

contracts:
	pnpm contracts
