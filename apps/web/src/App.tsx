import { RouterProvider } from "@tanstack/react-router";
import { BootGate } from "./components/BootGate";
import { router } from "./router";

export function App() {
  return (
    <BootGate>
      <RouterProvider router={router} />
    </BootGate>
  );
}
