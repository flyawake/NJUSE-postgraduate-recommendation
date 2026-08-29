import { useCallback, useState } from "react";

const KEY = "coding-agent-ui-gesture-swap";

export function readGestureSwap(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

export function useGestureSwap(): [boolean, (value: boolean) => void] {
  const [value, setValueState] = useState<boolean>(readGestureSwap);
  const setValue = useCallback((next: boolean) => {
    setValueState(next);
    try {
      localStorage.setItem(KEY, next ? "1" : "0");
    } catch {
      /* best effort */
    }
  }, []);
  return [value, setValue];
}
