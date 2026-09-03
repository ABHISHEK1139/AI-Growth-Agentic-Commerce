import { Suspense } from "react";

export default function RazorpayReturnLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <Suspense>{children}</Suspense>;
}
