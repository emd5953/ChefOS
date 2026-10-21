"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Field, Select } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { api, errorMessage } from "@/lib/api";
import { canManage, useSession } from "@/lib/auth";
import { useLocations } from "@/lib/queries";

type ApiKey = { id: string; prefix: string; scopes: string[]; location_id: string | null; created_at: string; expires_at: string | null; revoked_at: string | null };

function keyStatus(key: ApiKey): string {
  if (key.revoked_at) return "Revoked";
  if (key.expires_at && new Date(key.expires_at) <= new Date()) return "Expired";
  return "Active";
}

export function ApiKeysCard() {
  const { role } = useSession();
  const allowed = canManage(role);
  const toast = useToast();
  const qc = useQueryClient();
  const locations = useLocations();
  const [locationId, setLocationId] = useState("");
  const [secret, setSecret] = useState("");
  // Key lifecycle is owner/admin only; other roles would just get a 403.
  const keys = useQuery({ queryKey: ["api-keys"], queryFn: () => api<{ data: ApiKey[] }>("/v1/api-keys"), enabled: allowed });
  const create = useMutation({
    mutationFn: () => api<ApiKey & { secret: string }>("/v1/api-keys", { method: "POST", body: { scopes: ["orders:write", "daily_sales:write"], location_id: locationId } }),
    onSuccess: async (key) => { setSecret(key.secret); await qc.invalidateQueries({ queryKey: ["api-keys"] }); },
    onError: (e) => toast(errorMessage(e), "error"),
  });
  const revoke = useMutation({
    mutationFn: (id: string) => api<ApiKey>(`/v1/api-keys/${id}`, { method: "DELETE" }),
    onSuccess: async () => { await qc.invalidateQueries({ queryKey: ["api-keys"] }); toast("API key revoked.", "success"); },
    onError: (e) => toast(errorMessage(e), "error"),
  });
  if (!allowed) return null;
  return <Card>
    <CardHeader overline="External sales" title="Push API keys" description="Issue a location-scoped key for a totals-only sales client. The secret is shown once." />
    <CardBody className="space-y-4">
      {allowed && <div className="flex flex-wrap items-end gap-3"><Field label="Location">{(props) => <Select {...props} value={locationId} onChange={(e) => setLocationId(e.target.value)}><option value="">Choose a location</option>{locations.data?.map((loc) => <option key={loc.id} value={loc.id}>{loc.name}</option>)}</Select>}</Field><Button onClick={() => create.mutate()} loading={create.isPending} disabled={!locationId}>Create key</Button></div>}
      {secret && <div className="rounded-lg border border-border p-3"><p className="text-sm font-medium">Copy this secret now</p><code className="mt-2 block break-all text-xs">{secret}</code><Button className="mt-2" variant="outline" onClick={() => { void navigator.clipboard.writeText(secret); toast("Secret copied.", "success"); }}>Copy secret</Button><Button className="ml-2 mt-2" variant="outline" onClick={() => setSecret("")}>Done</Button></div>}
      {keys.data?.data.map((key) => <div key={key.id} className="flex items-center justify-between gap-3 border-t border-border pt-3 text-sm"><span><code>{key.prefix}…</code> · {key.scopes.join(", ")} · {keyStatus(key)}</span>{allowed && keyStatus(key) === "Active" && <Button variant="outline" onClick={() => revoke.mutate(key.id)}>Revoke</Button>}</div>)}
    </CardBody>
  </Card>;
}
