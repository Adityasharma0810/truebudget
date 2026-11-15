import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const contract = JSON.parse(
  await readFile(path.join(root, "packages/contracts/openapi.yaml"), "utf8"),
);
const frontendTypes = await readFile(path.join(root, "frontend/src/types/index.ts"), "utf8");
const schemas = contract.components?.schemas;
assert.ok(schemas, "OpenAPI components.schemas is required");
assert.match(contract.openapi, /^3\.1\./, "OpenAPI 3.1 is required");

const expectedOperations = {
  "/capabilities": ["get"],
  "/state/snapshot": ["get"],
  "/stream": [],
  "/metrics": ["get"],
  "/devices": ["get", "patch"],
  "/flows": ["get"],
  "/flows/{id}/correct": ["post"],
  "/intents": ["get", "post", "delete"],
  "/intents/{id}": ["delete"],
  "/conflicts": ["get"],
  "/conflicts/{id}/accept": ["post"],
  "/conflicts/{id}/adjust": ["post"],
  "/conflicts/{id}/cancel": ["post"],
  "/decisions": ["get"],
  "/decisions/verify": ["get"],
  "/health/diagnosis": ["get"],
  "/demand/schedules": ["get", "post", "delete"],
  "/demand/forecast": ["get"],
  "/links": ["get"],
  "/paths/evaluate": ["post"],
  "/scenarios/{name}": ["post"],
  "/scenarios/stop": ["post"],
  "/experiments": ["get", "post"],
  "/experiments/{id}": ["get"],
  "/experiments/{id}/report": ["get"],
  "/settings": ["get", "put"],
  "/safe-state/reset": ["post"],
  "/ml/metrics": ["get"],
  "/ml/corrections": ["get", "post"],
  "/ml/retrain": ["post"],
  "/ml/rollback": ["post"],
  "/qoe/formulas": ["get"],
  "/ask": ["post"],
  "/models/updates": ["get", "post"],
  "/agent/info": ["get"],
  "/agent/logs": ["get"],
  "/agent/update-check": ["post"],
  "/sessions": ["get", "delete"],
  "/keys/rotate": ["post"],
  "/audit": ["get"],
  "/metrics/internal": ["get"],
};

const methods = new Set(["get", "post", "put", "patch", "delete", "options", "head"]);
const operations = [];
for (const [route, expectedMethods] of Object.entries(expectedOperations)) {
  const pathItem = contract.paths[route];
  assert.ok(pathItem, `Missing API route ${route}`);
  for (const method of expectedMethods) {
    const operation = pathItem[method];
    assert.ok(operation?.operationId, `Missing ${method.toUpperCase()} ${route}`);
    assert.ok(operation.responses?.default, `Missing error response for ${method} ${route}`);
    operations.push({ route, method, operation });
  }
}

for (const [route, pathItem] of Object.entries(contract.paths)) {
  assert.ok(route in expectedOperations, `Undocumented API route ${route}`);
  for (const method of Object.keys(pathItem).filter((key) => methods.has(key))) {
    assert.ok(
      expectedOperations[route].includes(method),
      `Unexpected ${method.toUpperCase()} ${route}`,
    );
  }
}

for (const { route, method, operation } of operations) {
  if (["post", "put", "patch", "delete"].includes(method)) {
    assert.ok(
      operation.parameters?.some((parameter) => parameter.$ref?.endsWith("/IdempotencyKey")),
      `Missing Idempotency-Key on ${method.toUpperCase()} ${route}`,
    );
  }
}

assert.ok(contract.components.parameters?.Page, "Pagination page parameter is required");
assert.ok(contract.components.parameters?.PageSize, "Pagination pageSize parameter is required");
assert.ok(contract.components.responses?.RateLimited, "HTTP 429 response is required");
assert.ok(
  contract.components.responses.RateLimited.headers?.["Retry-After"],
  "RateLimited response must include Retry-After",
);
assert.equal(
  contract["x-rate-limit-response"]?.$ref,
  "#/components/responses/RateLimited",
  "The API-wide rate-limit response must be declared",
);
const snapshotResponse = contract.paths["/state/snapshot"].get.responses["200"];
const resolvedSnapshotResponse = snapshotResponse.$ref
  ? contract.components.responses[snapshotResponse.$ref.split("/").at(-1)]
  : snapshotResponse;
assert.ok(resolvedSnapshotResponse?.headers?.ETag, "Snapshot response must include ETag");

const declaredSchemas = new Set(
  [...frontendTypes.matchAll(/export const (\w+)Schema =/g)].map(([, name]) =>
    name.replace(/Schema$/, ""),
  ),
);
for (const [, name] of frontendTypes.matchAll(/export interface (\w+)/g)) {
  declaredSchemas.add(name);
}
for (const [, name] of frontendTypes.matchAll(/export type (\w+) =/g)) {
  if (schemas[name]) declaredSchemas.add(name);
}
declaredSchemas.delete("MockControl");
for (const name of declaredSchemas) {
  assert.ok(schemas[name], `Frontend type ${name} is missing from OpenAPI`);
}

function resolveSchema(schema) {
  if (!schema.$ref) return schema;
  const prefix = "#/components/schemas/";
  assert.ok(schema.$ref.startsWith(prefix), `Unsupported schema reference: ${schema.$ref}`);
  const resolved = schemas[schema.$ref.slice(prefix.length)];
  assert.ok(resolved, `Unknown schema reference: ${schema.$ref}`);
  return resolved;
}

function assertCamelCase(schema, location = "schema") {
  const resolved = resolveSchema(schema);
  for (const [name, property] of Object.entries(resolved.properties ?? {})) {
    assert.match(name, /^[a-z][A-Za-z0-9]*$/, `${location}.${name} must be camelCase`);
    assertCamelCase(property, `${location}.${name}`);
  }
  if (resolved.items) assertCamelCase(resolved.items, `${location}[]`);
  if (resolved.additionalProperties && typeof resolved.additionalProperties === "object") {
    assertCamelCase(resolved.additionalProperties, `${location}.*`);
  }
}

for (const [name, schema] of Object.entries(schemas)) {
  assertCamelCase(schema, name);
}

function validate(value, originalSchema, location) {
  const schema = resolveSchema(originalSchema);
  if (schema.type) {
    const acceptedTypes = Array.isArray(schema.type) ? schema.type : [schema.type];
    const matchesType = acceptedTypes.some((type) => {
      if (type === "null") return value === null;
      if (type === "array") return Array.isArray(value);
      if (type === "object") return value !== null && typeof value === "object" && !Array.isArray(value);
      if (type === "integer") return Number.isInteger(value);
      if (type === "number") return typeof value === "number" && Number.isFinite(value);
      return typeof value === type;
    });
    assert.ok(matchesType, `${location} must be ${acceptedTypes.join(" or ")}`);
  }
  if ("const" in schema) assert.equal(value, schema.const, `${location} has the wrong constant`);
  if (schema.enum) assert.ok(schema.enum.includes(value), `${location} is outside its enum`);
  if (typeof value === "number") {
    if (schema.minimum !== undefined) assert.ok(value >= schema.minimum, `${location} is too small`);
    if (schema.maximum !== undefined) assert.ok(value <= schema.maximum, `${location} is too large`);
  }
  if (typeof value === "string" && schema.minLength !== undefined) {
    assert.ok(value.length >= schema.minLength, `${location} is too short`);
  }
  if (Array.isArray(value) && schema.items) {
    value.forEach((item, index) => validate(item, schema.items, `${location}[${index}]`));
  }
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    for (const name of schema.required ?? []) {
      assert.ok(name in value, `${location}.${name} is required`);
    }
    for (const [name, propertySchema] of Object.entries(schema.properties ?? {})) {
      if (name in value) validate(value[name], propertySchema, `${location}.${name}`);
    }
    if (schema.additionalProperties && typeof schema.additionalProperties === "object") {
      for (const [name, propertyValue] of Object.entries(value)) {
        if (!(name in (schema.properties ?? {}))) {
          validate(propertyValue, schema.additionalProperties, `${location}.${name}`);
        }
      }
    }
  }
}

const examplesPath = path.join(root, "packages/contracts/examples");
const exampleFiles = (await readdir(examplesPath)).filter((file) => file.endsWith(".json"));
assert.ok(exampleFiles.length, "At least one JSON example is required");
for (const file of exampleFiles) {
  const name = file.slice(0, -".json".length);
  const schemaName = name[0].toUpperCase() + name.slice(1);
  assert.ok(schemas[schemaName], `No OpenAPI schema for example ${file}`);
  const example = JSON.parse(await readFile(path.join(examplesPath, file), "utf8"));
  validate(example, { $ref: `#/components/schemas/${schemaName}` }, file);
}

console.log(
  `Contract drift check passed: ${operations.length} operations, ${declaredSchemas.size} frontend schemas, ${exampleFiles.length} examples.`,
);
