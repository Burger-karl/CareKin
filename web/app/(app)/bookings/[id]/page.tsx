import type { Metadata } from "next";
import { BookingDetail } from "@/components/bookings/booking-detail";

export const metadata: Metadata = { title: "Booking" };

export default function BookingPage({ params }: { params: { id: string } }) {
  return <BookingDetail id={params.id} />;
}
