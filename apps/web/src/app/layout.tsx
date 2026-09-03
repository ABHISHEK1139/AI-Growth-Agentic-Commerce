import "./globals.css";
import React from "react";
import { StoreProvider } from "@/context/StoreContext";
import { Navbar } from "@/components/Navbar";
import { AIAssistantDrawer } from "@/components/AIAssistantDrawer";
import { ToastContainer } from "@/components/ToastContainer";
import { Footer } from "@/components/Footer";
import { MobileNav } from "@/components/MobileNav";

export const metadata = {
  title: "AgentPay — AI-Native Shopping & Autonomous Commerce Gateway",
  description: "Shop the way you think with conversational search, web research, and Razorpay standard checkout.",
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
      </body>
    </html>
  );
}
