import type { TestingLibraryMatchers } from "@testing-library/jest-dom/matchers";
import "vitest";

declare module "vitest" {
  // The first jest-dom parameter allows additional expected values, including asymmetric
  // matchers. Keep that extension open as in its adapter; R controls the assertion result.
  interface Matchers<
    R extends void | Promise<void> = void | Promise<void>,
    // eslint-disable-next-line @typescript-eslint/no-unused-vars -- Match Vitest's received-value parameter.
    T = unknown,
  > extends TestingLibraryMatchers<unknown, R> {
    toHaveNoViolations(): R;
  }
}
