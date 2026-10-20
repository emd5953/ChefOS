"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { api, errorMessage } from "@/lib/api";
import { homeFor, useAuth } from "@/lib/auth";
import { applyApiErrors } from "@/lib/forms";
import { keys } from "@/lib/queries";
import type { Session } from "@/lib/types";
import { AuthShell } from "./auth-shell";
import { PasswordInput } from "./password-input";

const schema = z.object({
  email: z.string().min(1, "Enter your email").email("Enter a valid email"),
  password: z.string().min(1, "Enter your password"),
});
type Values = z.infer<typeof schema>;

export function LoginForm() {
  const router = useRouter();
  const qc = useQueryClient();
  const { setSession } = useAuth();
  const [formError, setFormError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    setError,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<Values>({ resolver: zodResolver(schema), defaultValues: { email: "", password: "" } });

  const onSubmit = handleSubmit(async (values) => {
    setFormError(null);
    try {
      const session = await api<Session>("/v1/auth/login", {
        method: "POST",
        body: values,
        redirectOn401: false,
      });
      setSession(session);
      qc.setQueryData(keys.me, session);
      router.push(homeFor(session.role));
    } catch (e) {
      if (!applyApiErrors(e, setError, ["email", "password"])) setFormError(errorMessage(e));
    }
  });

  return (
    <AuthShell
      overline="Welcome back"
      title="Log in to ChefOS"
      subtitle="Pick up where service left off."
      footer={
        <>
          New to ChefOS?{" "}
          <Link href="/signup" className="font-semibold text-foreground underline-offset-4 hover:underline">
            Create a workspace
          </Link>
        </>
      }
    >
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        {formError && (
          <p role="alert" className="rounded-xl bg-destructive/10 px-3.5 py-2.5 text-[13px] text-destructive">
            {formError}
          </p>
        )}
        <Field label="Email" error={errors.email?.message}>
          {(p) => <Input {...p} type="email" autoComplete="email" placeholder="you@restaurant.com" {...register("email")} />}
        </Field>
        <Field label="Password" error={errors.password?.message}>
          {(p) => <PasswordInput {...p} autoComplete="current-password" {...register("password")} />}
        </Field>
        <Button type="submit" size="lg" className="w-full" loading={isSubmitting}>
          Log in
        </Button>
      </form>
      <div className="mt-6 rounded-2xl border border-dashed border-border px-4 py-3 text-xs leading-relaxed text-foreground/55">
        <span className="eyebrow mr-2 text-[10px]">Demo</span>
        <button
          type="button"
          onClick={() => {
            setValue("email", "owner@chefos.local");
            setValue("password", "ChefOS-demo-2026!");
          }}
          className="text-left underline-offset-4 transition-colors hover:text-foreground hover:underline"
        >
          owner@chefos.local / ChefOS-demo-2026! <span className="text-foreground/35">(click to fill)</span>
        </button>
        <p className="mt-1">
          Same password for the manager <span className="font-medium">gm@chefos.local</span> and the employee{" "}
          <span className="font-medium">maya@chefos.local</span> (phone view).
        </p>
      </div>
    </AuthShell>
  );
}
