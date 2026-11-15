# API contracts

`openapi.yaml` is encoded as JSON, which is also valid YAML 1.2. This keeps
contract validation dependency-free and makes the checked-in document usable
by OpenAPI tools. `scripts/drift-check.mjs` checks operation coverage,
camelCase data-model coverage against the frontend's Zod/type declarations,
mutation idempotency headers, and every checked-in JSON example.

The API is a target contract, not evidence that a real agent API is available.
`RelayTransport` and `DirectTransport` remain unimplemented.
