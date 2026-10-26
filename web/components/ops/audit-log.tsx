"use client";

import { useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api/client";
import { formatDate, formatTime } from "@/lib/utils";
import type { AuditPage } from "@/lib/types";

export function AuditLog() {
  const [action, setAction] = useState("");
  const [applied, setApplied] = useState("");
  const q = useInfiniteQuery({
    queryKey: ["audit", applied],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) =>
      api<AuditPage>("audit-log", { query: { action: applied || undefined, before_id: pageParam, limit: 50 } }),
    getNextPageParam: (last) => last.next_before_id ?? undefined,
  });
  const rows = q.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <div className="space-y-4">
      <form
        className="flex max-w-md gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          setApplied(action.trim());
        }}
      >
        <Input aria-label="Filter by action" placeholder="Filter by action, e.g. booking.create" value={action} onChange={(e) => setAction(e.target.value)} />
        <Button type="submit" variant="secondary">
          Filter
        </Button>
      </form>
      <Card className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left">
            <thead>
              <tr className="border-b-[1.5px] border-line text-[13px] text-muted-foreground">
                <th className="px-5 py-2 font-medium">When</th>
                <th className="px-3 py-2 font-medium">Who</th>
                <th className="px-3 py-2 font-medium">Action</th>
                <th className="px-3 py-2 font-medium">Record</th>
              </tr>
            </thead>
            <tbody>
              {q.isPending && (
                <tr>
                  <td colSpan={4} className="p-5">
                    <Skeleton className="h-24" />
                  </td>
                </tr>
              )}
              {rows.map((r) => (
                <tr key={r.id} className="border-b border-line text-[14px] last:border-0">
                  <td className="whitespace-nowrap px-5 py-2.5">
                    {formatDate(r.created_at)} {formatTime(r.created_at)}
                  </td>
                  <td className="px-3 py-2.5">
                    {r.actor_name ?? "System"}
                    {r.actor_role && <span className="ck-meta"> · {r.actor_role}</span>}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-[13px]">{r.action}</td>
                  <td className="px-3 py-2.5 ck-meta">
                    {r.entity_type ?? ""} {r.entity_id ? r.entity_id.slice(0, 8) : ""}
                  </td>
                </tr>
              ))}
              {q.isSuccess && rows.length === 0 && (
                <tr>
                  <td colSpan={4} className="p-6 text-center text-body text-muted-foreground">
                    No entries.
                  </td>
                </tr>
              )}
              {q.isError && (
                <tr>
                  <td colSpan={4} className="p-6 text-center text-body text-crisis-ink">
                    {q.error.message}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
      {q.hasNextPage && (
        <Button variant="secondary" onClick={() => q.fetchNextPage()} loading={q.isFetchingNextPage}>
          Load older
        </Button>
      )}
    </div>
  );
}
