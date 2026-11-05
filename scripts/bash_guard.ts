#!/usr/bin/env node
// PreToolUse hook for Bash: block clearly destructive commands.
// Exit 0 = allow, exit 2 = block (stderr goes back to the agent).
// This is a guardrail, not a security boundary.
import { text } from "node:stream/consumers";
import { z } from "zod";

const PreToolUsePayload = z.object({
  tool_input: z.object({ command: z.string() }).partial().optional(),
});

const BLOCKED: Array<[RegExp, string]> = [
  [/rm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+\/(\s|$)/, "recursive delete of /"],
  [/git\s+push\s+.*--force(\s|$|-)/, "force push"],
  [/git\s+reset\s+--hard\s+origin/, "hard reset to remote"],
  [/(curl|wget)\s+[^|;]*\|\s*(ba)?sh/, "pipe remote script to shell"],
  [/git\s+clean\s+-[a-zA-Z]*f/, "git clean -f"],
];

let command = "";
try {
  const parsed = PreToolUsePayload.safeParse(JSON.parse(await text(process.stdin)));
  command = parsed.success ? (parsed.data.tool_input?.command ?? "") : "";
} catch {
  process.exit(0);
}

for (const [pattern, reason] of BLOCKED) {
  if (pattern.test(command)) {
    console.error(
      `Blocked by harness policy: ${reason}. If truly needed, ask the user to run it manually.`,
    );
    process.exit(2);
  }
}
