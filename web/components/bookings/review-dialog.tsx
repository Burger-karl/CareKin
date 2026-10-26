"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Star } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/input";
import { api, errorMessage } from "@/lib/api/client";
import { STAR_LABEL } from "@/lib/booking";
import { cn } from "@/lib/utils";

/** Two-way rating after a shift. The same dialog serves families and caregivers. */
export function ReviewDialog({
  bookingId,
  shiftId,
  who,
  open,
  onOpenChange,
}: {
  bookingId: string;
  shiftId?: string;
  who: string;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const qc = useQueryClient();
  const [rating, setRating] = useState(0);
  const [comment, setComment] = useState("");

  const send = useMutation({
    mutationFn: () =>
      api(`bookings/${bookingId}/review`, {
        body: { rating, comment: comment.trim() || null, ...(shiftId && { shift_id: shiftId }) },
      }),
    onSuccess: () => {
      toast.success("Thanks for rating.");
      void qc.invalidateQueries({ queryKey: ["reviews", bookingId] });
      onOpenChange(false);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Rate {who}</DialogTitle>
          <DialogDescription>Your rating helps other families choose.</DialogDescription>
        </DialogHeader>
        <div role="radiogroup" aria-label="Rating" className="mb-1 flex justify-center gap-2">
          {[1, 2, 3, 4, 5].map((n) => (
            <button
              key={n}
              type="button"
              role="radio"
              aria-checked={rating === n}
              aria-label={`${n} star${n === 1 ? "" : "s"}`}
              onClick={() => setRating(n)}
              className="rounded-md p-1"
            >
              <Star className={cn("size-10", n <= rating ? "fill-amber text-amber" : "text-[#C5D6D4]")} />
            </button>
          ))}
        </div>
        <p className="mb-4 h-5 text-center text-meta text-muted-foreground" aria-live="polite">
          {STAR_LABEL[rating]}
        </p>
        <Textarea
          aria-label="Comment (optional)"
          placeholder="Anything you'd like to add (optional)"
          value={comment}
          maxLength={2000}
          onChange={(e) => setComment(e.target.value)}
          className="mb-4"
        />
        <Button size="lg" className="w-full" disabled={rating === 0} loading={send.isPending} onClick={() => send.mutate()}>
          Submit rating
        </Button>
      </DialogContent>
    </Dialog>
  );
}
