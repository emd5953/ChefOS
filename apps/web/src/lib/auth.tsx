"use client";

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { setCsrfToken } from "./api";
import type { Session } from "./types";

type AuthContextValue = {
  session: Session | null;
  csrfToken: string | null;
  setSession: (s: Session | null) => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSessionState] = useState<Session | null>(null);

  const setSession = useCallback((s: Session | null) => {
    setCsrfToken(s?.csrf_token ?? null);
    setSessionState(s);
  }, []);

  const value = useMemo(
    () => ({ session, csrfToken: session?.csrf_token ?? null, setSession }),
    [session, setSession],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

export function useSession(): Session {
  const { session } = useAuth();
  if (!session) throw new Error("useSession used outside an authenticated shell");
  return session;
}

export function canManage(role: Session["role"]): boolean {
  return role === "owner" || role === "admin";
}

/** Where a role lands after login: employees get the phone view. */
export function homeFor(role: Session["role"]): string {
  return role === "employee" ? "/my" : "/app";
}
