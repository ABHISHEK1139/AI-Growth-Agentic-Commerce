"use client";

const brands = [
  "Sony",
  "Apple",
  "Samsung",
  "Bose",
  "Lenovo",
  "OnePlus",
  "Dell",
  "HP",
  "Sennheiser",
  "Asus",
  "Acer",
  "LG",
  "JBL",
  "Realme",
  "Nothing",
  "Google",
];

export function BrandMarquee() {
  return (
    <div className="relative overflow-hidden rounded-2xl border border-[#e6e8df] bg-white/60 py-5 backdrop-blur-sm">
      {/* Gradient fade edges */}
      <div className="pointer-events-none absolute inset-y-0 left-0 z-10 w-16 bg-gradient-to-r from-white/90 to-transparent sm:w-24" />
      <div className="pointer-events-none absolute inset-y-0 right-0 z-10 w-16 bg-gradient-to-l from-white/90 to-transparent sm:w-24" />

      {/* Scrolling content */}
      <div className="animate-marquee flex w-max gap-8 sm:gap-12">
        {/* Render brands twice for seamless loop */}
        {[...brands, ...brands].map((brand, index) => (
          <span
            key={`${brand}-${index}`}
            className="flex shrink-0 items-center gap-2 text-sm font-bold text-[#68736d] transition-colors duration-300 hover:text-[#174c3c] sm:text-base"
          >
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-[#eef1eb] text-xs font-black text-[#174c3c] sm:h-9 sm:w-9">
              {brand[0]}
            </span>
            {brand}
          </span>
        ))}
      </div>
    </div>
  );
}
