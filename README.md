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

## Reproducible lab (Phase 1)

The lab creates real Linux namespaces, veth links, and `tc`/`netem` shaping
inside a private user+network namespace. It never touches the host's real WAN.

```sh
make agent-install
make lab-demo        # 4 devices x 2 controllers -> reports/lab/baseline.csv
make reset-lab
make test-agent      # fast unit tests
make test-netns      # real veth/netem/CAKE integration (no sudo)
```

See [`docs/lab.md`](docs/lab.md) for the topology, workloads, CSV columns, and
limitations. All lab data is marked **EMULATED**.

## Python environment

Use the project-local virtual environment for Python tools and packages:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
make agent-install
```

Python dependencies are declared in `agent/pyproject.toml` and installed only
into `.venv`. Do not install project Python dependencies into the system
interpreter.

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
