import { render, screen, waitFor } from "@testing-library/react";
import { expect, test } from "vitest";
import { AuthProvider, useAuth } from "./auth";

function Probe() {
  const { ready, token } = useAuth();
  return <div>ready:{String(ready)} token:{token ?? "none"}</div>;
}

test("AuthProvider exposes auth context to children", async () => {
  render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  );
  await waitFor(() => expect(screen.getByText(/ready:true/)).toBeInTheDocument());
  expect(screen.getByText(/token:none/)).toBeInTheDocument();
});
