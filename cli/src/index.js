#!/usr/bin/env node
"use strict";

const { Command } = require("commander");
const path = require("path");
const fs = require("fs");
const { spawnSync } = require("child_process");

const PACKAGE_ROOT = path.resolve(__dirname, "..");
const PYTHON_BIN = process.env.EB_PYTHON || path.join(PACKAGE_ROOT, "..", ".venv", "bin", "python");
const AGENT_MODULE = path.join(PACKAGE_ROOT, "..", "agent", "eb_agent");

const program = new Command();

program
  .name("eb-agent")
  .description("Experience Budget agent CLI")
  .version("0.1.0");

// ----------------------------------------------------------------- helpers

function python(args, { input = null } = {}) {
  const cmd = [PYTHON_BIN, ...args];
  const result = spawnSync(cmd[0], cmd.slice(1), {
    encoding: "utf8",
    input,
    cwd: PACKAGE_ROOT,
    stdio: ["pipe", "pipe", "pipe"],
    env: { ...process.env, PYTHONPATH: path.join(PACKAGE_ROOT, "..", "agent") },
  });
  if (result.error) {
    console.error(`eb-agent: failed to run ${cmd.join(" ")}: ${result.error.message}`);
    process.exit(1);
  }
  if (result.stderr) {
    process.stderr.write(result.stderr);
  }
  return result.stdout;
}

function pythonModule(module, args, { input = null } = {}) {
  return python(["-m", module, ...args], { input });
}

function ensureAgentDir() {
  if (!fs.existsSync(AGENT_MODULE)) {
    console.error(
      `eb-agent: agent module not found at ${AGENT_MODULE}. Run from the monorepo root.`
    );
    process.exit(1);
  }
}

// ----------------------------------------------------------------- init

program
  .command("init")
  .description("Initialize the agent: generate identity, print pairing key + link")
  .option("--docker", "use Docker/WSL2 lab mode (Windows default)")
  .option("--site-url <url>", "pairing site URL", "https://example.com")
  .action((opts) => {
    ensureAgentDir();
    pythonModule("eb_agent.security", ["gen-identity"], {
      input: JSON.stringify({ agent_id: undefined }),
    });
    // Use the security module to generate a pairing key + link
    const out = pythonModule("eb_agent.security", ["pair", "--site-url", opts.siteUrl]);
    console.log(out.trim());
  });

// ----------------------------------------------------------------- doctor

program
  .command("doctor")
  .description("Run environment diagnostics")
  .option("--interface <iface>", "interface to check")
  .action((opts) => {
    ensureAgentDir();
    const args = ["doctor"];
    if (opts.interface) args.push("--interface", opts.interface);
    const out = pythonModule("eb_agent.enforcer.doctor", args);
    console.log(out.trim());
  });

// ----------------------------------------------------------------- config

program
  .command("config")
  .description("Get or set agent configuration")
  .argument("action", "get or set")
  .argument("key", "config key")
  .argument("[value]", "config value (for set)")
  .action((action, key, value) => {
    ensureAgentDir();
    const args = ["config", action, key];
    if (action === "set" && value !== undefined) args.push(value);
    const out = pythonModule("eb_agent.config", args);
    console.log(out.trim());
  });

// ----------------------------------------------------------------- mode

program
  .command("mode")
  .description("Set topology mode")
  .argument("set", "must be 'set'")
  .argument("mode", "gateway | bridge | lab | monitor")
  .action((sub, mode) => {
    if (sub !== "set") {
      console.error("eb-agent: usage: eb-agent mode set <gateway|bridge|lab|monitor>");
      process.exit(1);
    }
    ensureAgentDir();
    const out = pythonModule("eb_agent.config", ["mode", "set", mode]);
    console.log(out.trim());
  });

// ----------------------------------------------------------------- pair

program
  .command("pair")
  .description("Generate a new pairing key")
  .option("--site-url <url>", "pairing site URL", "https://example.com")
  .action((opts) => {
    ensureAgentDir();
    const out = pythonModule("eb_agent.security", ["pair", "--site-url", opts.siteUrl]);
    console.log(out.trim());
  });

// ----------------------------------------------------------------- start/stop/restart/status

program
  .command("start")
  .description("Start the agent daemon (API server + control loops)")
  .option("--port <port>", "API port", "7443")
  .action((opts) => {
    ensureAgentDir();
    const out = pythonModule("eb_agent.api", ["serve"]);
    console.log(out.trim());
  });

program
  .command("stop")
  .description("Stop the agent daemon")
  .action(() => {
    ensureAgentDir();
    const out = pythonModule("eb_agent.core", ["stop"]);
    console.log(out.trim());
  });

program
  .command("status")
  .description("Show agent status")
  .action(() => {
    ensureAgentDir();
    const out = pythonModule("eb_agent.core", ["status"]);
    console.log(out.trim());
  });

// ----------------------------------------------------------------- safe-reset

program
  .command("safe-reset")
  .description("Reset to safe state")
  .action(() => {
    ensureAgentDir();
    const out = pythonModule("eb_agent.enforcer.safe_state", ["reset"]);
    console.log(out.trim());
  });

// ----------------------------------------------------------------- lab

program
  .command("lab")
  .description("Lab commands")
  .argument("subcmd", "run | reset | list")
  .action((subcmd) => {
    ensureAgentDir();
    const out = pythonModule("eb_agent.lab.harness", [subcmd]);
    console.log(out.trim());
  });

// ----------------------------------------------------------------- logs

program
  .command("logs")
  .description("Show agent logs")
  .option("-f, --follow", "follow logs")
  .action((opts) => {
    ensureAgentDir();
    const args = ["logs"];
    if (opts.follow) args.push("-f");
    const out = pythonModule("eb_agent.core", args);
    console.log(out.trim());
  });

// ----------------------------------------------------------------- export-diagnostics

program
  .command("export-diagnostics")
  .description("Export redacted diagnostics bundle")
  .action(() => {
    ensureAgentDir();
    const out = pythonModule("eb_agent.core", ["export-diagnostics"]);
    console.log(out.trim());
  });

// ----------------------------------------------------------------- sessions

program
  .command("sessions")
  .description("List active agent sessions")
  .action(() => {
    ensureAgentDir();
    const out = pythonModule("eb_agent.security", ["sessions"]);
    console.log(out.trim());
  });

// ----------------------------------------------------------------- rotate-keys

program
  .command("rotate-keys")
  .description("Rotate agent agreement keys")
  .action(() => {
    ensureAgentDir();
    const out = pythonModule("eb_agent.security", ["rotate-keys"]);
    console.log(out.trim());
  });

// ----------------------------------------------------------------- update

program
  .command("update")
  .description("Check for agent updates")
  .action(() => {
    ensureAgentDir();
    const out = pythonModule("eb_agent.api", ["update-check"]);
    console.log(out.trim());
  });

// ----------------------------------------------------------------- uninstall

program
  .command("uninstall")
  .description("Uninstall the agent (reverts shaping first)")
  .option("--purge", "also remove stored data")
  .action((opts) => {
    ensureAgentDir();
    const args = ["uninstall"];
    if (opts.purge) args.push("--purge");
    const out = pythonModule("eb_agent.core", args);
    console.log(out.trim());
  });

program.parseAsync(process.argv).catch((err) => {
  console.error(err.message);
  process.exit(1);
});
