# Experience Budget

Experience Budget is an intent-aware QoS/QoE controller for home broadband. The
current web console is an emulated demo; it does not shape the host's network.
The Linux agent, relay, and real shaping path described in [task.md](./task.md)
are not implemented yet.

## Web console

The Figma Make app remains in `frontend/`:

```sh
cd frontend
pnpm install
pnpm dev
```

By default the UI uses the in-browser `MockTransport`. The `relay` and `direct`
transports are explicit stubs and do not connect to an agent.

## Python environment

Use the project-local virtual environment for Python tools and packages:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
```

Python dependencies for the agent will be declared in `agent/pyproject.toml`
when that package is implemented. Do not install project Python dependencies
into the system interpreter.

## Checks

From the repository root, once the frontend dependencies are installed:

```sh
pnpm lint
pnpm typecheck
pnpm test
pnpm build
pnpm contracts
```

See [task.md](./task.md) for the phase plan, implementation gates, and
limitations that must remain visible in product copy.
