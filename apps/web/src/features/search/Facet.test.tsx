import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Facet } from "./SearchPage";

describe("search facets", () => {
  it("[SRCH-FAC-010] 回歸：篩選顯示中文名稱，不是 people.child 這種 key（驗證第 1 輪）", () => {
    const pick = vi.fn();
    render(<Facet label="標籤" values={{ "people.child": 9, 地板: 3 }} names={{ "people.child": "小孩" }} onPick={pick} />);
    expect(screen.queryByText(/people\.child/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /小孩/ }));
    expect(pick).toHaveBeenCalledWith("people.child"); // the filter still uses the key
    expect(screen.getByRole("button", { name: /地板/ })).toBeTruthy(); // a free tag is its own name
  });
});
