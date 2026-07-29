import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SettingEditor } from "./SettingEditor";

describe("SettingEditor", () => {
  it("loads stored array values into the editable text area", () => {
    render(
      <SettingEditor
        schema={{ type: "array", items: { type: "string" } }}
        configured
        currentValue={["findata-test/demo_random", "findata-plugins/tushare_stock_basic"]}
        hasCurrentValue
        onSet={vi.fn().mockResolvedValue(undefined)}
        onUnset={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect((screen.getByLabelText("array value") as HTMLTextAreaElement).value).toBe(
      "findata-test/demo_random\nfindata-plugins/tushare_stock_basic",
    );
  });
});
