import type { FieldValues, Path, UseFormSetError } from "react-hook-form";
import { isApiError } from "./api";

/** Map problem+json `errors` onto form fields. Returns true if any field matched. */
export function applyApiErrors<T extends FieldValues>(
  err: unknown,
  setError: UseFormSetError<T>,
  fields: readonly string[],
): boolean {
  if (!isApiError(err)) return false;
  let matched = false;
  for (const [field, message] of Object.entries(err.errors)) {
    if (fields.includes(field)) {
      setError(field as Path<T>, { type: "server", message });
      matched = true;
    }
  }
  return matched;
}
