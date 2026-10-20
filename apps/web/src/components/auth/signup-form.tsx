"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/field";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { US_TIMEZONES } from "@/lib/dates";
import { useBrowserTimeZone } from "@/lib/use-browser-tz";
import { applyApiErrors } from "@/lib/forms";
import { keys } from "@/lib/queries";
import type { SignupBody, SignupResponse } from "@/lib/types";
import { AuthShell } from "./auth-shell";
import { PasswordInput } from "./password-input";

const schema = z.object({
  display_name: z.string().trim().min(1, "Enter your name"),
  email: z.string().min(1, "Enter your email").email("Enter a valid email"),
  password: z.string().min(12, "Use at least 12 characters"),
  org_name: z.string().trim().min(1, "Enter your restaurant name"),
  location_name: z.string().trim().min(1, "Enter a location name"),
  timezone: z.string().min(1, "Choose a timezone"),
});
type Values = z.infer<typeof schema>;
const FIELDS = Object.keys(schema.shape);

export function SignupForm() {
  const router = useRouter();
  const qc = useQueryClient();
  const { setSession } = useAuth();
  const [formError, setFormError] = useState<string | null>(null);
  const browserZone = useBrowserTimeZone();
  const {
    register,
    handleSubmit,
    setError,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      display_name: "",
      email: "",
      password: "",
      org_name: "",
      location_name: "",
      timezone: "America/New_York",
    },
  });

  // Default to the browser's zone after mount (avoids a hydration mismatch).
  useEffect(() => {
    if (browserZone) setValue("timezone", browserZone);
  }, [browserZone, setValue]);

  const zones = useMemo(() => {
    if (browserZone && !US_TIMEZONES.some((z) => z.value === browserZone)) {
      return [{ value: browserZone, label: browserZone.replaceAll("_", " ") }, ...US_TIMEZONES];
    }
    return US_TIMEZONES;
  }, [browserZone]);

  const onSubmit = handleSubmit(async (values) => {
    setFormError(null);
    try {
      const body: SignupBody = values;
      const res = await api<SignupResponse>("/v1/auth/signup", {
        method: "POST",
        body,
        redirectOn401: false,
      });
      setSession(res);
      qc.setQueryData(keys.me, res);
      router.push("/app");
    } catch (e) {
      if (!applyApiErrors(e, setError, FIELDS)) setFormError(errorMessage(e));
    }
  });

  return (
    <AuthShell
      overline="Get started"
      title="Create your workspace"
      subtitle="Set up your restaurant, then connect your POS."
      footer={
        <>
          Already have an account?{" "}
          <Link href="/login" className="font-semibold text-foreground underline-offset-4 hover:underline">
            Log in
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
        <Field label="Your name" error={errors.display_name?.message}>
          {(p) => <Input {...p} autoComplete="name" placeholder="Alex Rivera" {...register("display_name")} />}
        </Field>
        <Field label="Email" error={errors.email?.message}>
          {(p) => <Input {...p} type="email" autoComplete="email" placeholder="you@restaurant.com" {...register("email")} />}
        </Field>
        <Field label="Password" error={errors.password?.message} hint="At least 12 characters.">
          {(p) => <PasswordInput {...p} autoComplete="new-password" {...register("password")} />}
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Restaurant name" error={errors.org_name?.message}>
            {(p) => <Input {...p} autoComplete="organization" placeholder="Rivera Hospitality" {...register("org_name")} />}
          </Field>
          <Field label="First location" error={errors.location_name?.message}>
            {(p) => <Input {...p} placeholder="Downtown" {...register("location_name")} />}
          </Field>
        </div>
        <Field label="Timezone" error={errors.timezone?.message}>
          {(p) => (
            <Select {...p} {...register("timezone")}>
              {zones.map((z) => (
                <option key={z.value} value={z.value}>
                  {z.label}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Button type="submit" size="lg" className="w-full" loading={isSubmitting}>
          Create workspace
        </Button>
      </form>
    </AuthShell>
  );
}
