import React, { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { Dropdown } from "@/components/ui/Dropdown";
import { moveItem, planSlots, reelStage } from "@/components/views/StudioView";
import type { ReelItem } from "@/lib/types";

function Harness() {
  const [v, setV] = useState("");
  return (
    <>
      <Dropdown
        ariaLabel="Platform"
        value={v}
        onChange={setV}
        options={[
          { value: "", label: "All platforms" },
          { value: "INSTAGRAM", label: "Instagram" },
          { value: "YOUTUBE", label: "YouTube", disabled: true },
          { value: "FACEBOOK", label: "Facebook" },
        ]}
      />
      <output data-testid="value">{v}</output>
    </>
  );
}

describe("Dropdown", () => {
  it("opens, picks with the mouse and the keyboard, skips disabled options, closes on Escape", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const button = screen.getByRole("button", { name: "Platform" });
    expect(button).toHaveTextContent("All platforms");

    await user.click(button);
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    await user.click(screen.getByRole("option", { name: "Instagram" }));
    expect(screen.getByTestId("value")).toHaveTextContent("INSTAGRAM");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();

    // Keyboard: open, ArrowDown skips the disabled YouTube, Enter picks Facebook.
    button.focus();
    await user.keyboard("{ArrowDown}");
    await user.keyboard("{ArrowDown}{Enter}");
    expect(screen.getByTestId("value")).toHaveTextContent("FACEBOOK");

    await user.click(button);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(button).toHaveFocus();
  });
});

describe("Studio helpers", () => {
  it("plans evenly spaced posting times", () => {
    const slots = planSlots("2026-11-20T19:00", 1440, 3);
    expect(slots.map((d) => d.getDate())).toEqual([20, 21, 22]);
    expect(slots.every((d) => d.getHours() === 19)).toBe(true);
    expect(planSlots("not a date", 60, 2)).toEqual([]);
  });

  it("describes where a reel is in plain words", () => {
    const base = {
      id: "r",
      format: "REEL",
      status: "QA_PASSED",
      approval_status: "PENDING",
      title: "R",
      hook: null,
      language: null,
      pillar: null,
      hook_type: null,
      slide_count: 0,
      qa_passed: true,
      qa_warnings: 0,
      score_total: null,
      score_label: null,
      thumbnail_asset_id: null,
      created_at: "",
      schedules: [],
      scheduling: false,
      can_schedule: false,
      caption: "",
      hashtags: [],
      slide_asset_ids: [],
      video_asset_id: "v",
      duration_seconds: 10,
      width: 1080,
      height: 1920,
      file_size: 1,
      qa_report: null,
    } as ReelItem;
    expect(reelStage(base).label).toBe("Needs review");
    expect(reelStage({ ...base, qa_passed: false }).label).toBe("Can't be posted");
    expect(reelStage({ ...base, approval_status: "APPROVED", can_schedule: true }).group).toBe("ready");
    const sched = { platform: "INSTAGRAM", status: "SCHEDULED", scheduled_at: "", timezone: "", post_url: null };
    expect(reelStage({ ...base, approval_status: "APPROVED", schedules: [sched] }).label).toBe("Scheduled");
    expect(
      reelStage({ ...base, approval_status: "APPROVED", schedules: [{ ...sched, status: "PUBLISHED" }] }).label,
    ).toBe("Published");
  });
});

describe("Carousel ordering", () => {
  it("moves a slide earlier or later and ignores moves past either end", () => {
    expect(moveItem(["a", "b", "c"], 2, -1)).toEqual(["a", "c", "b"]);
    expect(moveItem(["a", "b", "c"], 0, 1)).toEqual(["b", "a", "c"]);
    expect(moveItem(["a", "b"], 0, -1)).toEqual(["a", "b"]);
    expect(moveItem(["a", "b"], 1, 1)).toEqual(["a", "b"]);
  });
});
