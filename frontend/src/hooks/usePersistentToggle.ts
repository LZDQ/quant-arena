import { useCallback, useState } from "react";

/**
 * A boolean toggle backed by localStorage. Stored as "1"/"0"; any read/write
 * failure (e.g. private mode) degrades to in-memory state that just won't
 * persist. `defaultOpen` is used when there is no stored value.
 */
export function usePersistentToggle(
  key: string,
  defaultOpen: boolean,
): readonly [boolean, () => void] {
  const [open, setOpen] = useState<boolean>(() => {
    try {
      const stored = localStorage.getItem(key);
      if (stored == null) return defaultOpen;
      return stored !== "0";
    } catch {
      return defaultOpen;
    }
  });

  const toggle = useCallback(() => {
    setOpen((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(key, next ? "1" : "0");
      } catch {
        // localStorage unavailable — fold just won't persist.
      }
      return next;
    });
  }, [key]);

  return [open, toggle] as const;
}
