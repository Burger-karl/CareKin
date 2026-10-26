"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ShareDialog } from "@/components/facilities/share-dialog";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchFacilities } from "@/lib/api/queries";
import { formatDistanceKm } from "@/lib/utils";

/** Pick a verified facility, then choose what to share with it. */
export function SharePicker({
  patientId,
  open,
  onOpenChange,
}: {
  patientId: string;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const [q, setQ] = useState("");
  const [chosen, setChosen] = useState<{ id: string; name: string } | null>(null);
  const term = q.trim().length >= 2 ? q.trim() : undefined;
  const results = useQuery({
    queryKey: ["facility-pick", term],
    queryFn: () => fetchFacilities({ q: term, limit: 8 }),
    enabled: open,
  });

  return (
    <>
      <Dialog open={open && !chosen} onOpenChange={onOpenChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Share with a facility</DialogTitle>
            <DialogDescription>Only facilities CareKin has verified can receive your passport.</DialogDescription>
          </DialogHeader>
          <Input
            aria-label="Search facilities"
            placeholder="Search by name or address"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="mb-3"
          />
          <ul className="space-y-2">
            {results.isPending && <Skeleton className="h-16 rounded-xl" />}
            {results.data?.items.map((r) => (
              <li key={r.facility.id}>
                <button
                  className="w-full rounded-xl border-[1.5px] border-line p-3 text-left hover:border-primary hover:bg-mint-soft"
                  onClick={() => setChosen({ id: r.facility.id, name: r.facility.name })}
                >
                  <span className="block font-bold">{r.facility.name}</span>
                  <span className="ck-meta">{[formatDistanceKm(r.distance_km), r.facility.address].filter(Boolean).join(" · ")}</span>
                </button>
              </li>
            ))}
            {results.isSuccess && results.data.items.length === 0 && (
              <li className="text-body text-muted-foreground">No verified facility found.</li>
            )}
          </ul>
        </DialogContent>
      </Dialog>
      <ShareDialog
        patientId={patientId}
        facility={chosen}
        onClose={() => {
          setChosen(null);
          onOpenChange(false);
        }}
      />
    </>
  );
}
