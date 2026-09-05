import "./globals.css";
import React from "react";
import Script from "next/script";
import { StoreProvider } from "@/context/StoreContext";
import { Navbar } from "@/components/Navbar";
import { AIAssistantDrawer } from "@/components/AIAssistantDrawer";
import { CartDrawer } from "@/components/CartDrawer";
import { ToastContainer } from "@/components/ToastContainer";
import { Footer } from "@/components/Footer";
import { MobileNav } from "@/components/MobileNav";

export const metadata = {
  title: "AgentPay | AI Growth & Agentic Commerce",
  description: "Merchant-side AI commerce gateway and storefront for agentic commerce, featuring autonomous purchasing, dynamic catalog, and merchant console.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-[#f7f7f2] text-[#17231e] flex flex-col antialiased">
        <StoreProvider>
          {/* Consumer Global Header */}
          <Navbar />

          {/* Contextual AI Assistant Drawer */}
          <AIAssistantDrawer />

          {/* E-Commerce Slide-Over Cart Drawer */}
          <CartDrawer />

          {/* Interactive Global Toast Notification System */}
          <ToastContainer />

          {/* Main Application Container */}
          <main className="flex-1 w-full max-w-[1440px] mx-auto px-4 pb-24 sm:px-6 sm:py-10 md:pb-10 lg:px-10">
            {children}
          </main>

          {/* Shopper Footer */}
          <Footer />
          <MobileNav />
        </StoreProvider>
        <Script src="https://checkout.razorpay.com/v1/checkout.js" strategy="lazyOnload" />
      </body>
    </html>
  );
}
