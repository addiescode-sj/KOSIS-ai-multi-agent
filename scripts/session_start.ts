#!/usr/bin/env node
// SessionStart hook: inject current state so a new session resumes from
// files, not from previous conversation history.
import { existsSync, globSync, readFileSync } from "node:fs";
import { join } from "node:path";

const projectDir = process.env.CLAUDE_PROJECT_DIR ?? ".";

const currentState = join(projectDir, "docs", "current-state.md");
if (existsSync(currentState)) {
  console.log("=== docs/current-state.md ===");
  console.log(readFileSync(currentState, "utf8"));
}

// List plans that still have unchecked tasks
for (const tasksFile of globSync("docs/plans/*/TASKS.md", { cwd: projectDir })) {
  const remaining = readFileSync(join(projectDir, tasksFile), "utf8")
    .split("\n")
    .filter((line) => line.startsWith("- [ ]")).length;
  if (remaining > 0) {
    console.log(`OPEN PLAN: ${tasksFile} (${remaining} tasks remaining)`);
  }
}
