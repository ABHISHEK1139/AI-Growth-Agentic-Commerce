"use client";

import { MessageSquare, GitCompareArrows, ShoppingBag } from "lucide-react";
import { ScrollReveal } from "./ScrollReveal";

const steps = [
  {
    number: "01",
    title: "Ask",
    description: "Tell us what you need in plain language. No jargon, no filters to figure out.",
    Icon: MessageSquare,
    color: "bg-[#e5f0e9]",
    iconColor: "text-[#174c3c]",
  },
  {
    number: "02",
    title: "Compare",
    description: "See honest trade-offs side by side. We highlight what actually matters for your use case.",
    Icon: GitCompareArrows,
    color: "bg-[#fef3ec]",
    iconColor: "text-[#e87544]",
  },
  {
    number: "03",
    title: "Buy",
    description: "Decide with confidence. Secure checkout, clear pricing, and delivery you can count on.",
    Icon: ShoppingBag,
    color: "bg-[#e5f0e9]",
    iconColor: "text-[#174c3c]",
  },
];

export function HowItWorks() {
  return (
    <div className="grid gap-6 sm:gap-8 lg:grid-cols-3 lg:gap-6">
      {steps.map((step, index) => (
        <ScrollReveal key={step.number} delay={index * 150} direction="up">
          <div className="group relative flex flex-col items-start rounded-[24px] border border-[#e6e8df] bg-white/70 p-6 backdrop-blur-sm transition-all duration-300 hover:-translate-y-1 hover:shadow-soft hover:border-[#c8d4cc] sm:p-8">
            {/* Connector line for desktop */}
            {index < steps.length - 1 && (
              <div className="pointer-events-none absolute -right-3 top-1/2 hidden h-[2px] w-6 bg-gradient-to-r from-[#d4e8da] to-transparent lg:block" />
            )}

            {/* Step number */}
            <span className="mb-4 text-xs font-bold text-[#68736d] tracking-widest">
              STEP {step.number}
            </span>

            {/* Icon */}
            <div
              className={`mb-4 grid h-12 w-12 place-items-center rounded-2xl ${step.color} transition-transform duration-300 group-hover:scale-110`}
            >
              <step.Icon className={`h-5 w-5 ${step.iconColor}`} />
            </div>

            {/* Content */}
            <h3 className="text-xl font-extrabold text-[#17231e] font-display">
              {step.title}
            </h3>
            <p className="mt-2 text-sm leading-6 text-[#526058]">
              {step.description}
            </p>

            {/* Step indicator dot */}
            <div className="mt-6 flex items-center gap-2">
              {steps.map((_, dotIndex) => (
                <div
                  key={dotIndex}
                  className={`h-1.5 rounded-full transition-all duration-300 ${
                    dotIndex === index
                      ? "w-6 bg-[#174c3c]"
                      : "w-1.5 bg-[#d4e8da]"
                  }`}
                />
              ))}
            </div>
          </div>
        </ScrollReveal>
      ))}
    </div>
  );
}
