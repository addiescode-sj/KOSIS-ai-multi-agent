import type { Config } from "jest";
import nextJest from "next/jest.js";

// next/jest: SWC transform, tsconfig paths (@/), next.config and .env, server-only mocked.
const createJestConfig = nextJest({ dir: "./" });

const config: Config = {
  testEnvironment: "node", // pure logic only (lib/); no DOM tests yet
  testMatch: ["<rootDir>/lib/**/*.test.ts"],
  watchman: false, // file crawling without watchman, same on every machine
};

export default createJestConfig(config);
