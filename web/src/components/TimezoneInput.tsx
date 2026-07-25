import { useId, useMemo } from "react";

const FALLBACK_TIMEZONES = [
  "UTC",
  "Asia/Shanghai",
  "Asia/Hong_Kong",
  "Asia/Tokyo",
  "Asia/Singapore",
  "Europe/London",
  "Europe/Berlin",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "Australia/Sydney",
];

/** IANA zone list from the runtime, with a small built-in fallback. */
export function availableTimezones(): string[] {
  // Intl.supportedValuesOf is ES2022; older TS libs don't declare it.
  const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] };
  try {
    const zones = intl.supportedValuesOf?.("timeZone");
    return zones && zones.length > 0 ? zones : FALLBACK_TIMEZONES;
  } catch {
    return FALLBACK_TIMEZONES;
  }
}

export function isValidTimezone(zones: string[], value: string): boolean {
  return zones.includes(value.trim());
}

/**
 * Searchable IANA timezone picker (native datalist filtering) — never free
 * text: callers validate with `isValidTimezone` before accepting the value.
 */
export function TimezoneInput({
  value,
  onChange,
  invalid = false,
  placeholder = "IANA timezone",
}: {
  value: string;
  onChange: (value: string) => void;
  invalid?: boolean;
  placeholder?: string;
}) {
  const listId = useId();
  const zones = useMemo(availableTimezones, []);
  return (
    <>
      <input
        type="text"
        list={listId}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label="timezone"
        aria-invalid={invalid}
      />
      <datalist id={listId}>
        {zones.map((z) => (
          <option key={z} value={z} />
        ))}
      </datalist>
    </>
  );
}
