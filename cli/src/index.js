#!/usr/bin/env node
"use strict";

const { Command } = require("commander");
const path = require("path");
const fs = require("fs");
const os = require("os");
const { spawn, spawnSync } = require("child_process");

const PACKAGE_ROOT = path.resolve(__dirname, "..");
const PYTHON_BIN =
  process.env.EB_PYTHON || path.join(PACKAGE_ROOT, "..", ".venv", "bin", "python");
const AGENT_MODULE = path.join(PACKAGE_ROOT, "..", "agent", "eb_agent");

function dataDir() {
  if (process.env.EB_DATA_DIR) return process.env.EB_DATA_DIR;
  if (typeof process.getuid === "function" && process.getuid() === 0)
    return "/var/lib/eb-agent";
  return path.join(os.homedir(), ".local", "share", "eb-agent");
}

function childEnv() {
  return {
    ...process.env,
    EB_DATA_DIR: process.env.EB_DATA_DIR || dataDir(),
    PYTHONPATH: path.join(PACKAGE_ROOT, "..", "agent"),
  };
}

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
    env: childEnv(),
  });
  if (result.error) {
    console.error(`eb-agent: failed to run ${cmd.join(" ")}: ${result.error.message}`);
    process.exit(1);
  }
  if (result.stderr) process.stderr.write(result.stderr);
  return result.stdout;
}

function pythonModule(module, args, options) {
  return python(["-m", module, ...args], options);
}

function ensureAgentDir() {
  if (!fs.existsSync(AGENT_MODULE)) {
    console.error(
      `eb-agent: agent module not found at ${AGENT_MODULE}. Run from the monorepo root.`
    );
    process.exit(1);
  }
}

function pidPath() {
  return path.join(dataDir(), "eb-agentd.pid");
}

function logPath() {
  return path.join(dataDir(), "logs", "agent.log");
}

function isRunning(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

// ----------------------------------------------------------------- init

program
  .command("init")
  .description("Initialize the agent: generate identity, print pairing key + link")
  .option("--docker", "use Docker/WSL2 lab mode (Windows default)")
  .option("--mode <mode>", "gateway | bridge | lab | monitor")
  .option("--site-url <url>", "pairing site URL", "https://example.com")
  .action((opts) => {
    ensureAgentDir();
    const args = [];
    if (opts.docker) args.push("--docker");
    if (opts.mode) args.push("--mode", opts.mode);
    process.stdout.write(
      pythonModule("eb_agent.cli", ["init", ...args], { input: null })
    );
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
    process.stdout.write(pythonModule("eb_agent.enforcer.doctor", args));
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
    process.stdout.write(pythonModule("eb_agent.config", args));
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
    process.stdout.write(pythonModule("eb_agent.config", ["mode", "set", mode]));
  });

// ----------------------------------------------------------------- pair

program
  .command("pair")
  .description("Generate a new single-use pairing key")
  .option("--site-url <url>", "pairing site URL", "https://example.com")
  .action((opts) => {
    ensureAgentDir();
    process.env.EB_SITE_URL = opts.siteUrl;
    process.stdout.write(pythonModule("eb_agent.cli", ["pair"]));
  });

// ----------------------------------------------------------------- start/stop/restart/status

program
  .command("start")
  .description("Start the agent daemon (API server + control loops)")
  .option("--port <port>", "API port", "7443")
  .option("--host <host>", "API host", "0.0.0.0")
  .action((opts) => {
    ensureAgentDir();
    const pid = pidPath();
    if (fs.existsSync(pid)) {
      const existing = Number(fs.readFileSync(pid, "utf8").trim());
      if (existing && isRunning(existing)) {
        console.log(`Agent already running (pid ${existing}).`);
        return;
      }
      fs.rmSync(pid, { force: true });
    }
    fs.mkdirSync(path.dirname(logPath()), { recursive: true });
    const out = fs.openSync(logPath(), "a");
    const child = spawn(
      PYTHON_BIN,
      ["-m", "eb_agent.api", "serve"],
      {
        cwd: PACKAGE_ROOT,
        detached: true,
        stdio: ["ignore", out, out],
        env: {
          ...childEnv(),
          EB_AGENT_PORT: opts.port,
          EB_AGENT_HOST: opts.host,
        },
      }
    );
    child.unref();
    fs.writeFileSync(pid, String(child.pid));
    console.log(`Started agent (pid ${child.pid}); logs: ${logPath()}`);
  });

program
  .command("stop")
  .description("Stop the agent daemon")
  .action(() => {
    ensureAgentDir();
    process.stdout.write(pythonModule("eb_agent.cli", ["stop"]));
  });

program
  .command("restart")
  .description("Restart the agent daemon")
  .option("--port <port>", "API port", "7443")
  .option("--host <host>", "API host", "0.0.0.0")
  .action((opts) => {
    ensureAgentDir();
    pythonModule("eb_agent.cli", ["stop"]);
    const startArgs = ["start"];
    if (opts.port) startArgs.push("--port", opts.port);
    if (opts.host) startArgs.push("--host", opts.host);
    // Re-enter this CLI so the detached spawn logic exists in one place.
    const result = spawnSync(process.execPath, [__filename, ...startArgs], {
      stdio: "inherit",
      env: process.env,
    });
    process.exit(result.status ?? 1);
  });

program
  .command("status")
  .description("Show agent status")
  .action(() => {
    ensureAgentDir();
    const result = spawnSync(PYTHON_BIN, ["-m", "eb_agent.cli", "status"], {
      cwd: PACKAGE_ROOT,
      stdio: "inherit",
      env: childEnv(),
    });
    process.exit(result.status ?? 1);
  });

// ----------------------------------------------------------------- safe-reset

program
  .command("safe-reset")
  .description("Reset to safe state (or --restore adaptive mode)")
  .option("--restore", "restore adaptive mode")
  .action((opts) => {
    ensureAgentDir();
    const args = ["safe-reset"];
    if (opts.restore) args.push("--restore");
    process.stdout.write(pythonModule("eb_agent.cli", args));
  });

// ----------------------------------------------------------------- lab

program
  .command("lab")
  .description("Lab commands")
  .argument("subcmd", "run | reset | list")
  .action((subcmd) => {
    ensureAgentDir();
    process.stdout.write(pythonModule("eb_agent.lab.harness", [subcmd]));
  });

// ----------------------------------------------------------------- logs

program
  .command("logs")
  .description("Show agent logs")
  .option("-f, --follow", "follow logs")
  .action((opts) => {
    const file = logPath();
    if (!fs.existsSync(file)) {
      console.log(`No log file yet at ${file}. Start the agent first.`);
      return;
    }
    if (opts.follow) {
      const child = spawn("tail", ["-f", file], { stdio: "inherit" });
      child.on("exit", (code) => process.exit(code ?? 0));
      return;
    }
    process.stdout.write(fs.readFileSync(file, "utf8"));
  });

// ----------------------------------------------------------------- export-diagnostics

program
  .command("export-diagnostics")
  .description("Export a redacted diagnostics bundle")
  .action(() => {
    ensureAgentDir();
    process.stdout.write(pythonModule("eb_agent.cli", ["export-diagnostics"]));
  });

// ----------------------------------------------------------------- sessions

program
  .command("sessions")
  .description("List active agent sessions")
  .action(() => {
    ensureAgentDir();
    process.stdout.write(pythonModule("eb_agent.cli", ["sessions"]));
  });

// ----------------------------------------------------------------- rotate-keys

program
  .command("rotate-keys")
  .description("Rotate agent agreement keys")
  .action(() => {
    ensureAgentDir();
    process.stdout.write(pythonModule("eb_agent.cli", ["rotate-keys"]));
  });

// ----------------------------------------------------------------- update

program
  .command("update")
  .description("Check for agent updates")
  .action(() => {
    ensureAgentDir();
    process.stdout.write(pythonModule("eb_agent.cli", ["update"]));
  });

// ----------------------------------------------------------------- uninstall

program
  .command("uninstall")
  .description("Uninstall the agent (reverts shaping first)")
  .option("--purge", "also remove stored data")
  .option("--yes", "confirm uninstall without prompting")
  .action((opts) => {
    ensureAgentDir();
    const args = ["uninstall"];
    if (opts.purge) args.push("--purge");
    if (opts.yes) args.push("--yes");
    process.stdout.write(pythonModule("eb_agent.cli", args));
  });

program.parseAsync(process.argv).catch((err) => {
  console.error(err.message);
  process.exit(1);
});
