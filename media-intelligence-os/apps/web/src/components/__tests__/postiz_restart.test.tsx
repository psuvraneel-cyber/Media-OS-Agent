import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), refresh: vi.fn() }),
}));

import { PostizRestartButton, restartMessage, type RestartState } from "@/components/shell/PostizRestartButton";

const base: RestartState = {
  id: "r1",
  status: "PENDING",
  requested_at: "2026-11-17T01:00:00+00:00",
  finished_at: null,
  detail: null,
  helper_late: false,
};

describe("Restart Postiz", () => {
  afterEach(() => vi.restoreAllMocks());

  it("asks for confirmation, then records a request", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify(base), { status: 202, headers: { "Content-Type": "application/json" } }));
    render(<PostizRestartButton />);
    await user.click(screen.getByRole("button", { name: "Restart Postiz" }));
    expect(fetchSpy).not.toHaveBeenCalled(); // nothing happens until confirmed
    await user.click(screen.getByRole("button", { name: "Restart" }));
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0]!;
    expect(String(url)).toBe("/api/v1/system/postiz/restart");
    expect((init as RequestInit).method).toBe("POST");
    expect(await screen.findByRole("status")).toHaveTextContent(/picks it up within a minute/);
  });

  it("explains each state in plain words", () => {
    expect(restartMessage(null)).toBeNull();
    expect(restartMessage({ ...base, helper_late: true })).toMatch(/hasn't picked this up/);
    expect(restartMessage({ ...base, status: "RUNNING" })).toBe("Restarting Postiz…");
    expect(restartMessage({ ...base, status: "DONE", detail: "Postiz restarted." })).toBe("Postiz restarted.");
    expect(restartMessage({ ...base, status: "EXPIRED", detail: "Not picked up." })).toBe("Not picked up.");
  });
});
