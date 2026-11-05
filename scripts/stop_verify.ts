#!/usr/bin/env node
// Stop hook: the agent may not declare completion while verification fails.
// Runs scripts/verify.sh. Non-zero exit -> block the stop (exit 2) so the
// agent keeps working. Uses stop_hook_active to avoid infinite loops.
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { text } from "node:stream/consumers";
import { z } from "zod";

const TIMEOUT_MS = 240_000;

const StopPayload = z.object({ stop_hook_active: z.boolean().optional() });

let stopHookActive = false;
try {
  const parsed = StopPayload.safeParse(JSON.parse(await text(process.stdin)));
  stopHookActive = parsed.success ? (parsed.data.stop_hook_active ?? false) : false;
} catch {
  // Malformed input: fall through and still enforce verification.
}

// Prevent a blocked stop from re-triggering this hook forever.
if (stopHookActive) process.exit(0);

const projectDir = process.env.CLAUDE_PROJECT_DIR ?? ".";
const verify = join(projectDir, "scripts", "verify.sh");
if (!existsSync(verify)) process.exit(0);

const result = spawnSync("bash", [verify], {
  cwd: projectDir,
  encoding: "utf8",
  timeout: TIMEOUT_MS,
});

if (result.signal) {
  console.error("verify.sh timed out; fix or speed up verification.");
  process.exit(2);
}

if (result.status !== 0) {
  const tail = ((result.stdout ?? "") + (result.stderr ?? ""))
    .trim()
    .split("\n")
    .slice(-20)
    .join("\n");
  console.error(`verify.sh failed — work is not done:\n${tail}`);
  process.exit(2);
}
