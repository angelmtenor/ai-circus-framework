// Who the workspace is shown to: an executive (the story and the showpiece), an analyst (every tab, the default) or
// an engineer (every tab plus how it is built and the API behind it). A per-viewer convenience kept in the
// browser: it may be empty or unavailable (private window, blocked storage), and then the default applies.
import { useCallback, useState } from "react";

export type Audience = "executive" | "analyst" | "engineer";
export const AUDIENCES: { id: Audience; label: string; hint: string }[] = [
  { id: "executive", label: "Executive", hint: "The story, the value and the showpiece tab, nothing technical" },
  { id: "analyst", label: "Analyst", hint: "Every tab: data, models, explanations" },
  { id: "engineer", label: "Engineer", hint: "Every tab plus how it is built: components, API routes, commands" },
];
const KEY = "ai-open.audience";

function stored(): Audience {
  try {
    const value = window.localStorage.getItem(KEY);
    return value === "executive" || value === "engineer" ? value : "analyst";
  } catch {
    return "analyst";
  }
}

export function useAudience(): [Audience, (audience: Audience) => void] {
  const [audience, setState] = useState<Audience>(stored);
  const set = useCallback((next: Audience) => {
    setState(next);
    try {
      window.localStorage.setItem(KEY, next);
    } catch {
      // storage blocked: the choice lasts for this page only
    }
  }, []);
  return [audience, set];
}
