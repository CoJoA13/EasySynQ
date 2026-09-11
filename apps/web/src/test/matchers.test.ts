import { axe } from "jest-axe";
import { expect, expectTypeOf, test } from "vitest";

// Compiled by the required web build, but never executed: invalid calls must stay errors.
function invalidMatcherCalls() {
  // @ts-expect-error Unknown matcher names must not become callable.
  expect(document.body).toBeVisibl();
  // @ts-expect-error Attribute names must be strings.
  expect(document.body).toHaveAttribute(123);
  // @ts-expect-error The accessibility matcher accepts no arguments.
  expect({ violations: [] }).toHaveNoViolations("ignored");
}

test("DOM matchers preserve synchronous and asynchronous return types", async () => {
  expectTypeOf(invalidMatcherCalls).toEqualTypeOf<() => void>();
  const button = document.createElement("button");
  button.textContent = "Save changes";
  expect(button).toHaveAccessibleName("Save changes");
  expect(button).toHaveAccessibleName(/Save/);
  expect(button).toHaveAccessibleName(expect.stringContaining("changes"));

  const direct = expect(document.body).toBeVisible();
  expectTypeOf(direct).toEqualTypeOf<void>();

  const negated = expect(document.createElement("div")).not.toBeVisible();
  expectTypeOf(negated).toEqualTypeOf<void>();

  const resolved = expect(Promise.resolve(document.body)).resolves.toBeVisible();
  expectTypeOf(resolved).toEqualTypeOf<Promise<void>>();
  await resolved;

  const rejected = expect(Promise.reject(document.body)).rejects.toBeVisible();
  expectTypeOf(rejected).toEqualTypeOf<Promise<void>>();
  await rejected;
});

test("accessibility matchers preserve synchronous and asynchronous return types", async () => {
  const clean = await axe("<main><h1>Accessible heading</h1></main>");
  const violations = await axe("<main><button></button></main>", {
    runOnly: { type: "rule", values: ["button-name"] },
  });
  expect(clean.violations).toHaveLength(0);
  expect(violations.violations).toHaveLength(1);

  const direct = expect(clean).toHaveNoViolations();
  expectTypeOf(direct).toEqualTypeOf<void>();

  const negated = expect(violations).not.toHaveNoViolations();
  expectTypeOf(negated).toEqualTypeOf<void>();

  const resolved = expect(Promise.resolve(clean)).resolves.toHaveNoViolations();
  expectTypeOf(resolved).toEqualTypeOf<Promise<void>>();
  await resolved;

  const rejected = expect(Promise.reject(clean)).rejects.toHaveNoViolations();
  expectTypeOf(rejected).toEqualTypeOf<Promise<void>>();
  await rejected;
});

test("registered matchers still reject DOM and accessibility failures", async () => {
  expect(() => expect(document.createElement("div")).toBeVisible()).toThrow();

  const violations = await axe("<main><button></button></main>", {
    runOnly: { type: "rule", values: ["button-name"] },
  });
  expect(violations.violations).toHaveLength(1);
  expect(() => expect(violations).toHaveNoViolations()).toThrow(/button-name/);
});
