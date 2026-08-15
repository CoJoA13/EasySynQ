import { spawn } from "node:child_process";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

interface ProbeAttachment {
  name: string;
  contentType: string;
  body?: string;
}

interface ProbeError {
  message?: string;
}

interface ProbeResult {
  status: string;
  errors: ProbeError[];
  stdout: unknown[];
  stderr: unknown[];
  retry: number;
  attachments: ProbeAttachment[];
}

interface ProbeTest {
  expectedStatus: string;
  status: string;
  results: ProbeResult[];
}

interface ProbeSpec {
  title: string;
  ok: boolean;
  tests: ProbeTest[];
}

interface ProbeSuite {
  file: string;
  specs: ProbeSpec[];
}

interface ProbeReport {
  suites: ProbeSuite[];
  errors: unknown[];
  stats: {
    expected: number;
    skipped: number;
    unexpected: number;
    flaky: number;
  };
}

interface ChildRun {
  exitCode: number | null;
  signal: NodeJS.Signals | null;
  stdout: string;
  stderr: string;
  timedOut: boolean;
  elapsedMs: number;
}

const PROBES = [
  {
    title: "rejects an unmatched loopback API request through the installed interceptor",
    marker: {
      url: "http://127.0.0.1:4174/api/v1/browser-harness-probe",
      errorText: "net::ERR_FAILED",
    },
    fatal: "Error: Unexpected API request: GET http://127.0.0.1:4174/api/v1/browser-harness-probe",
  },
  {
    title: "rejects an external HTTPS request through the installed interceptor",
    marker: {
      url: "https://external.invalid/browser-harness-probe",
      errorText: "net::ERR_BLOCKED_BY_CLIENT.Inspector",
    },
    fatal: "Error: Unexpected external request: GET https://external.invalid/browser-harness-probe",
  },
] as const;

function runProbe(probeFile: string, timeoutMs: number): Promise<ChildRun> {
  const webRoot = resolve(import.meta.dirname, "..");
  const cli = resolve(webRoot, "node_modules/@playwright/test/cli.js");
  const environment: NodeJS.ProcessEnv = {
    ...process.env,
    NO_COLOR: "1",
    NODE_NO_WARNINGS: "1",
  };
  delete environment.FORCE_COLOR;

  return new Promise((resolveRun, rejectRun) => {
    const startedAt = Date.now();
    const child = spawn(
      process.execPath,
      [cli, "test", `e2e/${probeFile}`, "--config", "e2e/playwright.probe.config.ts"],
      {
        cwd: webRoot,
        env: environment,
        stdio: ["ignore", "pipe", "pipe"],
        detached: process.platform !== "win32",
      },
    );
    let timedOut = false;
    let closeFired = false;
    let settled = false;
    let killTimeout: NodeJS.Timeout | undefined;

    function clearTimers(): void {
      clearTimeout(timeout);
      if (killTimeout) clearTimeout(killTimeout);
    }

    function signalProbe(signal: NodeJS.Signals): void {
      const pid = child.pid;
      if (!Number.isInteger(pid) || (pid ?? 0) <= 0) {
        throw new Error("Playwright probe has no valid positive process id");
      }
      if (process.platform !== "win32") {
        try {
          process.kill(-(pid as number), signal);
          return;
        } catch {
          // The child can lose its POSIX group before delivery; fall back to its validated PID.
        }
      }
      child.kill(signal);
    }

    const timeout = setTimeout(() => {
      timedOut = true;
      try {
        signalProbe("SIGTERM");
      } catch (error) {
        clearTimers();
        if (!settled) {
          settled = true;
          rejectRun(error);
        }
        return;
      }
      if (!closeFired) {
        killTimeout = setTimeout(() => {
          if (closeFired) return;
          try {
            signalProbe("SIGKILL");
          } catch (error) {
            clearTimers();
            if (!settled) {
              settled = true;
              rejectRun(error);
            }
          }
        }, 2_000);
      }
    }, timeoutMs);
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk: string) => {
      stderr += chunk;
    });
    child.once("error", (error) => {
      clearTimers();
      if (!settled) {
        settled = true;
        rejectRun(error);
      }
    });
    child.once("close", (exitCode, signal) => {
      closeFired = true;
      clearTimers();
      if (settled) return;
      settled = true;
      resolveRun({
        exitCode,
        signal,
        stdout,
        stderr,
        timedOut,
        elapsedMs: Date.now() - startedAt,
      });
    });
  });
}

function decodeAbortMarker(result: ProbeResult): unknown {
  expect(result.attachments.map(({ name, contentType }) => ({ name, contentType }))).toEqual([
    { name: "abort-success", contentType: "application/json" },
    { name: "error-context", contentType: "text/markdown" },
  ]);
  const [attachment] = result.attachments;
  expect(attachment).toMatchObject({
    name: "abort-success",
    contentType: "application/json",
  });
  expect(attachment?.body).toBeDefined();
  return JSON.parse(Buffer.from(attachment?.body ?? "", "base64").toString("utf8"));
}

test("default fail-closed interceptor has exact abort and fatal outcomes", async () => {
  const child = await runProbe("harness-fail-closed.probe.spec.ts", 10_000);

  expect(child.signal).toBeNull();
  expect(child.exitCode).toBe(1);
  expect(child.stderr).toBe("");
  expect(child.timedOut).toBe(false);

  const report = JSON.parse(child.stdout) as ProbeReport;
  expect(report.errors).toEqual([]);
  expect(report.stats).toMatchObject({ expected: 0, skipped: 0, unexpected: 2, flaky: 0 });
  expect(report.suites).toHaveLength(1);
  expect(report.suites[0]?.file).toBe("harness-fail-closed.probe.spec.ts");
  expect(report.suites[0]?.specs).toHaveLength(2);

  for (const [index, probe] of PROBES.entries()) {
    const spec = report.suites[0]?.specs[index];
    expect(spec).toMatchObject({ title: probe.title, ok: false });
    expect(spec?.tests).toHaveLength(1);
    const probeTest = spec?.tests[0];
    expect(probeTest).toMatchObject({ expectedStatus: "passed", status: "unexpected" });
    expect(probeTest?.results).toHaveLength(1);
    const result = probeTest?.results[0];
    expect(result).toMatchObject({ status: "failed", retry: 0, stdout: [], stderr: [] });
    expect(result?.errors).toHaveLength(1);
    expect(result?.errors.map((error) => error.message?.split("\n", 1)[0])).toEqual([probe.fatal]);
    expect(decodeAbortMarker(result as ProbeResult)).toEqual(probe.marker);
  }
});

test("terminates a timed-out probe within the bounded grace period", async () => {
  const child = await runProbe("harness-timeout.probe.spec.ts", 1_000);

  expect(child.timedOut).toBe(true);
  expect(child.signal ?? child.exitCode).not.toBeNull();
  expect(child.elapsedMs).toBeLessThan(8_000);
});
