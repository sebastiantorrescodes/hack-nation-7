import { useState } from "react";

/** useState that survives closing the side panel (per-browser convenience, e.g. the user's name). */
export function useStored(key: string, initial = ""): [string, (v: string) => void] {
  const [value, setValue] = useState(() => {
    try {
      return localStorage.getItem(key) ?? initial;
    } catch {
      return initial;
    }
  });
  const set = (v: string) => {
    setValue(v);
    try {
      localStorage.setItem(key, v);
    } catch {
      // storage unavailable: keep it for this session only
    }
  };
  return [value, set];
}
