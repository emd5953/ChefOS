"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { api } from "./api";
import { useAuth } from "./auth";
import { clearMyWeekCache } from "./offline";

export function useLogout() {
  const router = useRouter();
  const qc = useQueryClient();
  const { setSession } = useAuth();
  return async () => {
    try {
      await api<void>("/v1/auth/logout", { method: "POST", redirectOn401: false });
    } catch {
      // Cookie may already be gone; proceed to the login screen regardless.
    }
    setSession(null);
    clearMyWeekCache();
    qc.clear();
    router.replace("/login");
  };
}
