import { Logo } from "@/components/brand";

export default function FlowLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col bg-background">
      <header className="flex h-16 items-center border-b-[1.5px] border-line bg-white px-5">
        <Logo />
      </header>
      <main className="mx-auto w-full max-w-md flex-1 px-5 py-8">{children}</main>
    </div>
  );
}
