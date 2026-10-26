import type { Metadata } from "next";
import { BookingList } from "@/components/bookings/booking-list";

export const metadata: Metadata = { title: "Bookings" };

export default function BookingsPage() {
  return <BookingList />;
}
