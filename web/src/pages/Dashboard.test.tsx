import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import HomePage from "./Dashboard";

const mocks = vi.hoisted(() => ({
  ackEvent: vi.fn().mockResolvedValue({ acknowledged: 1 }),
}));

vi.mock("../api", () => ({
  TERMINAL_STATUSES: new Set(["succeeded", "failed", "canceled"]),
  ackEvent: mocks.ackEvent,
  getSystemStatus: vi.fn(),
  listDatasetsStatus: vi.fn(),
  listEvents: vi.fn(),
  listProviders: vi.fn(),
  listTasks: vi.fn(),
}));

vi.mock("../hooks", () => ({
  useLiveData: () => ({
    data: {
      status: { status: "running", pid: 1, tasks: 0, running_tasks: 0, queue_lengths: {} },
      providers: [],
      unreadEvents: [{
        event_id: "event-1",
        timestamp: 0,
        kind: "task_failed",
        severity: "error",
        message: "Task failed",
        context: { execution_id: "execution-1" },
        acknowledged: false,
      }],
      tasks: [{
        handle_id: "handle-1",
        execution_id: "execution-1",
        dataset: "findata-plugins/example",
        operation: "update",
        owner: "user",
        status: "failed",
        created_at: 0,
        updated_at: 0,
        error: "Task failed",
      }],
      datasets: [],
    },
    error: null,
    lastUpdated: 0,
  }),
}));

vi.mock("../components/TaskActions", () => ({
  RetryTaskButton: () => null,
}));

describe("HomePage", () => {
  afterEach(() => vi.clearAllMocks());

  it("acknowledges failed tasks and removes them from the attention list", async () => {
    render(<MemoryRouter><HomePage /></MemoryRouter>);

    fireEvent.click(screen.getByRole("button", { name: "Acknowledge" }));

    await waitFor(() => {
      expect(mocks.ackEvent).toHaveBeenCalledWith({ event_id: "event-1" });
    });
    expect(screen.queryByText("Task failed")).toBeNull();
  });

  it("opens task details when a failed-task row is clicked", () => {
    function Location() {
      return <output>{useLocation().pathname}</output>;
    }

    render(<MemoryRouter><HomePage /><Location /></MemoryRouter>);

    fireEvent.click(screen.getByRole("link", { name: /Failed task/i }));

    expect(screen.getByText("/tasks/handle-1")).toBeTruthy();
  });
});
