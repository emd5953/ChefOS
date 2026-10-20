"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { Skeleton } from "@/components/ui/skeleton";
import { api, errorMessage, isApiError } from "@/lib/api";
import { homeFor, useAuth } from "@/lib/auth";
import { formatDate } from "@/lib/format";
import { keys } from "@/lib/queries";
import type { InvitationPreview, Session } from "@/lib/types";
import { AuthShell } from "./auth-shell";
import { PasswordInput } from "./password-input";

export function InviteForm({ token }: { token: string }) {
  const router = useRouter();
  const qc = useQueryClient();
  const { setSession } = useAuth();
  const preview = useQuery({
    queryKey: ["invitation", token],
    queryFn: () => api<InvitationPreview>(`/v1/invitations/${encodeURIComponent(token)}`, { redirectOn401: false }),
    retry: false,
  });
  const [name, setName] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const displayName = name ?? preview.data?.employee_name ?? "";

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const errs: Record<string, string> = {};
    if (!displayName.trim()) errs.display_name = "Enter your name";
    if (password.length < 12) errs.password = "Use at least 12 characters";
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setSubmitting(true);
    setFormError(null);
    try {
      const session = await api<Session>(`/v1/invitations/${encodeURIComponent(token)}/accept`, {
        method: "POST",
        body: { display_name: displayName, password },
        redirectOn401: false,
      });
      setSession(session);
      qc.setQueryData(keys.me, session);
      router.replace(homeFor(session.role));
    } catch (err) {
      if (isApiError(err) && Object.keys(err.errors).length) setErrors(err.errors);
      else setFormError(errorMessage(err));
      setSubmitting(false);
    }
  };

  const invalid = isApiError(preview.error) && preview.error.status === 410;

  return (
    <AuthShell
      overline="Invitation"
      title={preview.data ? `Join ${preview.data.org_name}` : "Join your team"}
      subtitle={
        preview.data
          ? `Set a password to see your schedule${preview.data.role === "manager" ? " and manage your team" : ""}.`
          : "Set a password to see your schedule."
      }
      footer={
        <>
          Already have a login?{" "}
          <Link href="/login" className="font-semibold text-foreground underline-offset-4 hover:underline">
            Log in
          </Link>
        </>
      }
    >
      {preview.isPending ? (
        <div className="space-y-4" role="status" aria-label="Loading invitation">
          <Skeleton className="h-11 w-full rounded-xl" />
          <Skeleton className="h-11 w-full rounded-xl" />
          <Skeleton className="h-12 w-full rounded-full" />
        </div>
      ) : preview.isError ? (
        <p role="alert" className="rounded-xl bg-destructive/10 px-3.5 py-3 text-[13px] leading-relaxed text-destructive">
          {invalid
            ? "This invitation link has expired, was revoked, or was already used. Ask your manager for a new one."
            : errorMessage(preview.error)}
        </p>
      ) : (
        <form onSubmit={submit} noValidate className="space-y-4">
          {formError && (
            <p role="alert" className="rounded-xl bg-destructive/10 px-3.5 py-2.5 text-[13px] text-destructive">
              {formError}
            </p>
          )}
          <Field label="Email">
            {(p) => <Input {...p} value={preview.data.email} readOnly disabled />}
          </Field>
          <Field label="Your name" error={errors.display_name}>
            {(p) => <Input {...p} autoComplete="name" value={displayName} onChange={(e) => setName(e.target.value)} />}
          </Field>
          <Field label="Password" error={errors.password} hint="At least 12 characters">
            {(p) => (
              <PasswordInput {...p} autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
            )}
          </Field>
          <Button type="submit" size="lg" className="w-full" loading={submitting}>
            Create login
          </Button>
          <p className="text-center text-xs text-foreground/45">
            Link valid until {formatDate(preview.data.expires_at.slice(0, 10))}.
          </p>
        </form>
      )}
    </AuthShell>
  );
}
