import { useState } from "react";
import { useNavigate } from "react-router";
import { cancelTask, errorMessage, removeTerminalTask, retryTask, type TaskHandle } from "../api";
import { ConfirmDialog } from "./ConfirmDialog";
import { useToast } from "./Toast";

/** Cancel button + confirmation for an active task. */
export function CancelTaskButton({
  task,
  onChanged,
  size = "btn-xs",
}: {
  task: TaskHandle;
  onChanged?: () => void;
  size?: string;
}) {
  const { notify } = useToast();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const submit = async (): Promise<void> => {
    setBusy(true);
    try {
      const r = await cancelTask(task.handle_id);
      notify(
        "success",
        r.already_terminal
          ? `task ${task.handle_id} was already terminal`
          : `cancel requested for task ${task.handle_id}`,
      );
      setConfirming(false);
      onChanged?.();
    } catch (err) {
      notify("error", errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button className={`btn ${size}`} onClick={() => setConfirming(true)}>
        Cancel
      </button>
      <ConfirmDialog
        open={confirming}
        title="Cancel task"
        message={
          <>
            Cancel task <span className="mono">{task.handle_id}</span> ({task.dataset}{" "}
            {task.operation})? Already committed checkpoint batches remain visible.
          </>
        }
        confirmLabel="Cancel task"
        danger
        cliCommand={`findata task cancel ${task.handle_id}`}
        busy={busy}
        onConfirm={() => void submit()}
        onCancel={() => setConfirming(false)}
      />
    </>
  );
}

/** Retry button + confirmation for a failed/canceled task. */
export function RetryTaskButton({
  task,
  size = "btn-xs",
}: {
  task: TaskHandle;
  size?: string;
}) {
  const navigate = useNavigate();
  const { notify } = useToast();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const submit = async (): Promise<void> => {
    setBusy(true);
    try {
      const r = await retryTask(task.handle_id);
      notify("success", `retried as task ${r.handle_id}`);
      setConfirming(false);
      navigate(`/tasks/${encodeURIComponent(r.handle_id)}`);
    } catch (err) {
      notify("error", errorMessage(err));
      setBusy(false);
    }
  };

  return (
    <>
      <button className={`btn ${size}`} onClick={() => setConfirming(true)}>
        Retry
      </button>
      <ConfirmDialog
        open={confirming}
        title="Retry task"
        message={
          <>
            Submit a new handle with the same dataset, operation, and operands as{" "}
            <span className="mono">{task.handle_id}</span>?
          </>
        }
        confirmLabel="Retry"
        cliCommand={`findata task retry ${task.handle_id}`}
        busy={busy}
        onConfirm={() => void submit()}
        onCancel={() => setConfirming(false)}
      />
    </>
  );
}

export function RemoveTerminalTaskButton({ task, onChanged }: { task: TaskHandle; onChanged?: () => void }) {
  const { notify } = useToast();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const submit = async (): Promise<void> => {
    setBusy(true);
    try {
      await removeTerminalTask(task.handle_id);
      notify("success", `removed task ${task.handle_id}`);
      setConfirming(false);
      onChanged?.();
    } catch (err) {
      notify("error", errorMessage(err));
    } finally {
      setBusy(false);
    }
  };
  return <>
    <button className="btn btn-xs" onClick={() => setConfirming(true)}>Remove</button>
    <ConfirmDialog open={confirming} title="Remove task" message={<>Permanently remove terminated task <span className="mono">{task.handle_id}</span> and its retained logs?</>} confirmLabel="Remove task" danger busy={busy} onConfirm={() => void submit()} onCancel={() => setConfirming(false)} />
  </>;
}
