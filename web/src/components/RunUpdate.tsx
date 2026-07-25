import { useState } from "react";
import { useNavigate } from "react-router";
import { createTask, errorMessage } from "../api";
import { ConfirmDialog } from "./ConfirmDialog";
import { useToast } from "./Toast";

/**
 * Primary "Run update" action: confirms once with the equivalent CLI command,
 * submits the `update` operation, and navigates to the new task.
 */
export function RunUpdateButton({
  dataset,
  disabled = false,
  disabledReason,
  label = "Run update",
}: {
  dataset: string;
  disabled?: boolean;
  /** Server-reported reason shown when the action is not eligible. */
  disabledReason?: string;
  label?: string;
}) {
  const navigate = useNavigate();
  const { notify } = useToast();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const submit = async (): Promise<void> => {
    setBusy(true);
    try {
      const res = await createTask({ dataset, operation: "update", operands: {} });
      notify("success", `update submitted for ${dataset}`);
      setConfirming(false);
      navigate(`/tasks/${encodeURIComponent(res.handle_id)}`);
    } catch (err) {
      notify("error", errorMessage(err));
      setBusy(false);
    }
  };

  return (
    <>
      <button
        className="btn btn-primary"
        disabled={disabled || busy}
        title={disabled ? disabledReason : undefined}
        onClick={() => setConfirming(true)}
      >
        {label}
      </button>
      <ConfirmDialog
        open={confirming}
        title="Run update"
        message={
          <>
            Submit an <span className="mono">update</span> task for{" "}
            <span className="mono">{dataset}</span>?
          </>
        }
        confirmLabel="Run update"
        cliCommand={`findata task run ${dataset} update`}
        busy={busy}
        onConfirm={() => void submit()}
        onCancel={() => setConfirming(false)}
      />
    </>
  );
}
