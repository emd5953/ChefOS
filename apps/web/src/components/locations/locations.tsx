"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, Clock, MapPin, Pencil, Plus } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/field";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { api, errorMessage } from "@/lib/api";
import { canManage, useSession } from "@/lib/auth";
import { US_TIMEZONES } from "@/lib/dates";
import { useBrowserTimeZone } from "@/lib/use-browser-tz";
import { applyApiErrors } from "@/lib/forms";
import { keys } from "@/lib/queries";
import type { List, Location } from "@/lib/types";

const schema = z.object({
  name: z.string().trim().min(1, "Enter a name"),
  address: z.string().trim(),
  timezone: z.string().min(1, "Choose a timezone"),
  business_day_start: z.string().regex(/^\d{2}:\d{2}$/, "Use HH:MM"),
});
type Values = z.infer<typeof schema>;
const FIELDS = Object.keys(schema.shape);

const tzLabel = (tz: string) => US_TIMEZONES.find((z) => z.value === tz)?.label ?? tz.replaceAll("_", " ");

function fmtStart(t: string) {
  const [h = "0", m = "00"] = t.split(":");
  const hr = Number(h);
  return `${hr % 12 === 0 ? 12 : hr % 12}:${m.slice(0, 2)} ${hr < 12 ? "AM" : "PM"}`;
}

export function Locations() {
  const { role } = useSession();
  const manage = canManage(role);
  const qc = useQueryClient();
  const toast = useToast();
  const [editing, setEditing] = useState<Location | "new" | null>(null);
  const [archiving, setArchiving] = useState<Location | null>(null);

  // Fresh read (excludes archived by default).
  const q = useQuery({
    queryKey: keys.locations,
    queryFn: async () => (await api<List<Location>>("/v1/locations")).data,
  });

  const archive = useMutation({
    mutationFn: (id: string) => api<Location>(`/v1/locations/${id}/archive`, { method: "POST" }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: keys.locations });
      toast("Location archived.", "success");
      setArchiving(null);
    },
    onError: (e) => toast(errorMessage(e), "error"),
  });

  const locations = q.data ?? [];

  return (
    <>
      <PageHeader
        overline="Locations"
        title="Locations"
        description="The restaurants in your organization. Each one gets its own sales, labor, and POS link."
        actions={
          manage ? (
            <Button onClick={() => setEditing("new")}>
              <Plus aria-hidden /> Add location
            </Button>
          ) : undefined
        }
      />

      {q.isPending ? (
        <div className="grid gap-4 md:grid-cols-2" role="status" aria-label="Loading locations">
          {Array.from({ length: 2 }, (_, i) => (
            <Skeleton key={i} className="h-40 rounded-card" />
          ))}
        </div>
      ) : q.isError ? (
        <Card>
          <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        </Card>
      ) : locations.length === 0 ? (
        <Card>
          <EmptyState
            icon={<MapPin />}
            title="No locations yet"
            description="Add a location to start connecting a POS and tracking sales."
            action={manage ? <Button onClick={() => setEditing("new")}>Add location</Button> : undefined}
          />
        </Card>
      ) : (
        <ul className="grid gap-4 md:grid-cols-2">
          {locations.map((l) => (
            <li key={l.id}>
              <Card className="flex h-full flex-col p-6 transition-colors duration-150 hover:border-foreground/20">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="truncate text-xl font-semibold tracking-[-0.045em]">{l.name}</h2>
                    <p className="mt-1 text-[13px] text-foreground/55">{l.address || "No address on file"}</p>
                  </div>
                  <Badge>{tzLabel(l.timezone).split(" (")[0]}</Badge>
                </div>
                <dl className="mt-5 grid grid-cols-2 gap-4 text-[13px]">
                  <div>
                    <dt className="eyebrow text-[10px]">Timezone</dt>
                    <dd className="mt-1 text-foreground/80">{l.timezone}</dd>
                  </div>
                  <div>
                    <dt className="eyebrow text-[10px]">Business day starts</dt>
                    <dd className="mt-1 flex items-center gap-1.5 text-foreground/80">
                      <Clock className="size-3.5 text-foreground/40" aria-hidden />
                      {fmtStart(l.business_day_start)}
                    </dd>
                  </div>
                </dl>
                {manage && (
                  <div className="mt-6 flex gap-2 pt-1">
                    <Button variant="outline" size="sm" onClick={() => setEditing(l)}>
                      <Pencil aria-hidden /> Edit<span className="sr-only"> {l.name}</span>
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => setArchiving(l)}>
                      <Archive aria-hidden /> Archive<span className="sr-only"> {l.name}</span>
                    </Button>
                  </div>
                )}
              </Card>
            </li>
          ))}
        </ul>
      )}

      <LocationDialog target={editing} onClose={() => setEditing(null)} />

      <Dialog
        open={!!archiving}
        onOpenChange={(o) => !o && setArchiving(null)}
        title={`Archive ${archiving?.name ?? "location"}?`}
        description="Archived locations disappear from the switcher and stop appearing in reports. Historical data is kept."
      >
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => setArchiving(null)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            loading={archive.isPending}
            onClick={() => archiving && archive.mutate(archiving.id)}
          >
            Archive location
          </Button>
        </div>
      </Dialog>
    </>
  );
}

function LocationDialog({ target, onClose }: { target: Location | "new" | null; onClose: () => void }) {
  return (
    <Dialog
      open={target !== null}
      onOpenChange={(o) => !o && onClose()}
      title={target === "new" ? "Add location" : "Edit location"}
      description={target === "new" ? "Where do you serve guests?" : undefined}
    >
      {/* Remount per target so the form always starts from the right values. */}
      {target !== null && <LocationForm key={target === "new" ? "new" : target.id} target={target} onDone={onClose} />}
    </Dialog>
  );
}

function LocationForm({ target, onDone }: { target: Location | "new"; onDone: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const isNew = target === "new";
  const [formError, setFormError] = useState<string | null>(null);

  const initial: Values = isNew
    ? { name: "", address: "", timezone: "America/New_York", business_day_start: "04:00" }
    : {
        name: target.name,
        address: target.address,
        timezone: target.timezone,
        business_day_start: target.business_day_start.slice(0, 5),
      };

  const {
    register,
    handleSubmit,
    setError,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<Values>({ resolver: zodResolver(schema), defaultValues: initial });

  // New locations default to the browser's zone.
  const detectedZone = useBrowserTimeZone();
  const browserZone = isNew ? detectedZone : null;
  useEffect(() => {
    if (browserZone) setValue("timezone", browserZone);
  }, [browserZone, setValue]);

  const zones = useMemo(() => {
    const extra = [browserZone, isNew ? null : target.timezone].filter(
      (z): z is string => !!z && !US_TIMEZONES.some((u) => u.value === z),
    );
    return [...new Set(extra)].map((z) => ({ value: z, label: z.replaceAll("_", " ") })).concat(US_TIMEZONES);
  }, [browserZone, isNew, target]);

  const onSubmit = handleSubmit(async (values) => {
    setFormError(null);
    try {
      if (isNew) {
        await api<Location>("/v1/locations", { method: "POST", body: values });
      } else {
        await api<Location>(`/v1/locations/${target.id}`, { method: "PATCH", body: values });
      }
      await qc.invalidateQueries({ queryKey: keys.locations });
      toast(isNew ? "Location added." : "Location updated.", "success");
      onDone();
    } catch (e) {
      if (!applyApiErrors(e, setError, FIELDS)) setFormError(errorMessage(e));
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      {formError && (
        <p role="alert" className="rounded-xl bg-destructive/10 px-3.5 py-2.5 text-[13px] text-destructive">
          {formError}
        </p>
      )}
      <Field label="Name" error={errors.name?.message}>
        {(p) => <Input {...p} autoFocus placeholder="Downtown" {...register("name")} />}
      </Field>
      <Field label="Address" error={errors.address?.message}>
        {(p) => <Input {...p} autoComplete="street-address" placeholder="123 Main St, Springfield" {...register("address")} />}
      </Field>
      <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_140px]">
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
        <Field label="Day starts" error={errors.business_day_start?.message} hint="Business day boundary">
          {(p) => <Input {...p} type="time" {...register("business_day_start")} />}
        </Field>
      </div>
      <div className="flex justify-end gap-2 pt-2">
        <Button variant="outline" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" loading={isSubmitting}>
          {isNew ? "Add location" : "Save changes"}
        </Button>
      </div>
    </form>
  );
}
