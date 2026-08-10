import { useCallback, useEffect, useState } from "react";
import { DEFAULT_THEME_ID, getTheme, THEMES } from "./themes";

const STORAGE_KEY = "ai-circus-theme";

function applyTheme(id: string) {
  const theme = getTheme(id);
  const root = document.documentElement;
  root.setAttribute("data-theme", theme.id);
  for (const [key, value] of Object.entries(theme.cssVars)) {
    root.style.setProperty(key, value);
  }
}

/**
 * Active theme is a per-browser preference (localStorage), not shared infra — every
 * user picks their own via Settings.tsx's "Appearance" card, regardless of org/role.
 */
export function useTheme() {
  const [themeId, setThemeIdState] = useState(() => localStorage.getItem(STORAGE_KEY) ?? DEFAULT_THEME_ID);

  useEffect(() => {
    applyTheme(themeId);
  }, [themeId]);

  const setThemeId = useCallback((id: string) => {
    localStorage.setItem(STORAGE_KEY, id);
    setThemeIdState(id);
  }, []);

  return { theme: getTheme(themeId), themes: THEMES, setThemeId };
}
