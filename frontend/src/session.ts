export function readSession(key: string): string | null {
  try {
    return sessionStorage.getItem(key)
  } catch {
    return null
  }
}

export function writeSession(key: string, value: string | null) {
  try {
    if (value === null) sessionStorage.removeItem(key)
    else sessionStorage.setItem(key, value)
  } catch {
    // Review remains usable when the browser restricts session storage.
  }
}
