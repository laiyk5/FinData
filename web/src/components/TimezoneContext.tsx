import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { getConfig, getConfigKeys } from "../api";

/**
 * Provides the workspace display timezone, resolved once at the shell level:
 * the configured `display.timezone` value wins; when unconfigured, the
 * server-probed default from the config-keys item is used; browser local is
 * the last resort (Intl treats undefined as the local zone).
 */
const TimezoneContext = createContext<string | undefined>(undefined);

export function TimezoneProvider({ children }: { children: ReactNode }) {
  const [timezone, setTimezone] = useState<string | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getConfig(), getConfigKeys()])
      .then(([config, keys]) => {
        if (cancelled) return;
        const configured = config.values["display.timezone"];
        if (typeof configured === "string" && configured.trim() !== "") {
          setTimezone(configured.trim());
          return;
        }
        const declared = keys.items.find((k) => k.key === "display.timezone");
        if (typeof declared?.default === "string" && declared.default.trim() !== "") {
          setTimezone(declared.default.trim());
        }
      })
      .catch(() => undefined); // fall back to browser local
    return () => {
      cancelled = true;
    };
  }, []);

  return <TimezoneContext.Provider value={timezone}>{children}</TimezoneContext.Provider>;
}

export function useDisplayTimezone(): string | undefined {
  return useContext(TimezoneContext);
}
