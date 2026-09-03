"use client";

import { useEffect, useState, useCallback } from "react";
import { Star, ChevronLeft, ChevronRight, Quote } from "lucide-react";

interface Testimonial {
  id: number;
  name: string;
  role: string;
  avatar: string;
  rating: number;
  quote: string;
}

const testimonials: Testimonial[] = [
  {
    id: 1,
    name: "Priya Sharma",
    role: "Software Developer",
    avatar: "PS",
    rating: 5,
    quote: "AgentPay helped me find the perfect laptop for coding. The AI understood exactly what I needed - lightweight, great keyboard, and excellent battery life.",
  },
  {
    id: 2,
    name: "Rahul Verma",
    role: "Music Producer",
    avatar: "RV",
    rating: 5,
    quote: "I was overwhelmed by audio options. The comparison feature made it crystal clear which headphones matched my studio needs. Saved me hours of research.",
  },
  {
    id: 3,
    name: "Ananya Patel",
    role: "Content Creator",
    avatar: "AP",
    rating: 4,
    quote: "Love how the AI assistant asks the right questions. It recommended a monitor setup I never would have found on my own. Delivery was super fast too!",
  },
  {
    id: 4,
    name: "Vikram Singh",
    role: "Startup Founder",
    avatar: "VS",
    rating: 5,
    quote: "Finally, a shopping experience that respects my time. No endless scrolling - just clear recommendations with honest trade-offs explained upfront.",
  },
  {
    id: 5,
    name: "Meera Krishnan",
    role: "Graphic Designer",
    avatar: "MK",
    rating: 5,
    quote: "The product comparison tools are brilliant. I could see exactly how different phones stacked up on the features that matter to me. No hidden surprises.",
  },
];

export function TestimonialCarousel() {
  const [active, setActive] = useState(0);
  const [isPaused, setIsPaused] = useState(false);

  const next = useCallback(() => {
    setActive((prev) => (prev + 1) % testimonials.length);
  }, []);

  const prev = useCallback(() => {
    setActive((prev) => (prev - 1 + testimonials.length) % testimonials.length);
  }, []);

  useEffect(() => {
    if (isPaused) return;
    const interval = setInterval(next, 5000);
    return () => clearInterval(interval);
  }, [isPaused, next]);

  return (
    <div
      className="relative"
      onMouseEnter={() => setIsPaused(true)}
      onMouseLeave={() => setIsPaused(false)}
    >
      {/* Background decorative elements */}
      <div className="absolute -left-4 -top-4 h-24 w-24 rounded-full bg-[#174c3c] opacity-[0.04] blur-2xl" />
      <div className="absolute -bottom-4 -right-4 h-32 w-32 rounded-full bg-[#e87544] opacity-[0.06] blur-2xl" />

      <div className="relative overflow-hidden rounded-[28px] border border-[#e6e8df] bg-white/80 p-6 backdrop-blur-sm sm:p-8 lg:p-10">
        <Quote className="absolute right-6 top-6 h-12 w-12 text-[#174c3c] opacity-[0.06] sm:h-16 sm:w-16" />

        <div className="relative min-h-[220px] sm:min-h-[200px]">
          {testimonials.map((testimonial, index) => (
            <div
              key={testimonial.id}
              className={`absolute inset-0 flex flex-col justify-between transition-all duration-500 ${
                index === active
                  ? "opacity-100 translate-x-0"
                  : index < active
                  ? "opacity-0 -translate-x-8"
                  : "opacity-0 translate-x-8"
              }`}
              aria-hidden={index !== active}
            >
              {/* Stars */}
              <div className="flex gap-0.5">
                {Array.from({ length: 5 }).map((_, i) => (
                  <Star
                    key={i}
                    className={`h-4 w-4 ${
                      i < testimonial.rating
                        ? "fill-[#e8a33e] text-[#e8a33e]"
                        : "text-[#dfe4dd]"
                    }`}
                  />
                ))}
              </div>

              {/* Quote text */}
              <p className="mt-4 max-w-2xl text-base leading-7 text-[#365046] sm:text-lg sm:leading-8">
                &ldquo;{testimonial.quote}&rdquo;
              </p>

              {/* Author info */}
              <div className="mt-6 flex items-center gap-3">
                <div className="grid h-10 w-10 place-items-center rounded-full bg-gradient-to-br from-[#174c3c] to-[#1d5f4b] text-xs font-bold text-white">
                  {testimonial.avatar}
                </div>
                <div>
                  <p className="text-sm font-bold text-[#17231e]">{testimonial.name}</p>
                  <p className="text-xs text-[#68736d]">{testimonial.role}</p>
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Navigation */}
        <div className="mt-6 flex items-center justify-between">
          {/* Dots */}
          <div className="flex gap-2">
            {testimonials.map((_, index) => (
              <button
                key={index}
                onClick={() => setActive(index)}
                className={`h-2 rounded-full transition-all duration-300 ${
                  index === active
                    ? "w-6 bg-[#174c3c]"
                    : "w-2 bg-[#d4e8da] hover:bg-[#a8cfb4]"
                }`}
                aria-label={`Go to testimonial ${index + 1}`}
              />
            ))}
          </div>

          {/* Arrow buttons */}
          <div className="flex gap-2">
            <button
              onClick={prev}
              className="grid h-9 w-9 place-items-center rounded-full border border-[#dfe4dd] text-[#526058] transition-all duration-200 hover:border-[#174c3c] hover:bg-[#e5f0e9] hover:text-[#174c3c] active:scale-90"
              aria-label="Previous testimonial"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <button
              onClick={next}
              className="grid h-9 w-9 place-items-center rounded-full border border-[#dfe4dd] text-[#526058] transition-all duration-200 hover:border-[#174c3c] hover:bg-[#e5f0e9] hover:text-[#174c3c] active:scale-90"
              aria-label="Next testimonial"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
