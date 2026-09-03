export interface CustomerReview {
  id: string;
  author: string;
  verified: boolean;
  rating: number;
  date: string;
  title: string;
  comment: string;
  helpfulCount: number;
  tags: string[];
}

export interface ProductItem {
  id: string;
  slug: string;
  title: string;
  /**
   * The category identifier. Widened from a fixed union to a plain string because
   * the catalog stores its own vocabulary (`laptop`, `smartphone`, `monitor`,
   * `audio`, `computer_accessory`) and a record adapted from a real offer carries
   * whatever the catalog holds rather than a value chosen here.
   */
  category: string;
  categoryLabel: string;
  brand: string;
  priceMinor: number;
  originalPriceMinor: number;
  currency: string;
  rating: number;
  reviewCount: number;
  stock: number;
  deliveryDays: number;
  returnDays: number;
  imageUrl: string;
  gallery: string[];
  aiBadge: string;
  shortSpecs: string;
  weightKg?: number;
  batteryHours?: number;
  whyFitsYou: {
    summary: string;
    pros: string[];
    warnings: string[];
  };
  specsGrouped: {
    performance: Record<string, string>;
    display?: Record<string, string>;
    connectivity: Record<string, string>;
    batteryOrPower?: Record<string, string>;
  };
  sentiment: {
    performancePct: number;
    batteryPct: number;
    buildQualityPct: number;
    valuePct: number;
    displayPct?: number;
    customerLikes: string[];
    customerConcerns: string[];
  };
  reviews: CustomerReview[];
  qa: Array<{ question: string; answer: string; source: string }>;
  merchant: {
    id: string;
    name: string;
    verified: boolean;
    rating: number;
  };
  crossSell: {
    id: string;
    title: string;
    priceMinor: number;
    imageUrl: string;
    alternativeSavingsMinor?: number;
    alternativeTitle?: string;
  };

  // -------------------------------------------------------------------------
  // Fields present only on a record adapted from a live catalog offer.
  //
  // The buyer screens hold their cart, wishlist, and comparison list in the
  // browser (`StoreContext`), and that state is typed as `ProductItem`. A record
  // that came from the API therefore has to fit this shape -- but it must also
  // stay distinguishable from the static demo entries below, and it has to carry
  // the offer identity, because an offer id is what the checkout, offer, and
  // validation endpoints are keyed by. These optional fields carry exactly that.
  // Their absence means "this is a static demo record", which is a fact a screen
  // is allowed to state.
  // -------------------------------------------------------------------------

  /** `OfferV1.offer_id`. Present only for a record resolved from the catalog. */
  offerId?: string;
  /** `OfferV1.offer_version`, for optimistic revalidation before checkout. */
  offerVersion?: number;
  /** `OfferV1.merchant_id`, as the catalog reports it. */
  merchantId?: string;
  /** `OfferV1.pricing_source`. Generated or merchant-configured; never scraped. */
  pricingSource?: "synthetic_band_random" | "merchant_configured";
  /** Which catalog answered when this record was resolved. */
  catalogSource?: "postgresql" | "seed_fixture";
  /** `OfferV1.expires_at`. An offer is not valid past this instant. */
  offerExpiresAt?: string;
  /** True when this record was read from the API rather than from the list below. */
  fromCatalog?: boolean;
  /** False when the catalog holds no image for the product. */
  hasCatalogImage?: boolean;
  /** The raw specifications object the catalog holds, unmodified. */
  catalogSpecs?: Record<string, unknown>;
}

export const ALL_PRODUCTS: ProductItem[] = [
  {
    id: "prd_dell_xps_15",
    slug: "dell-xps-15-oled",
    title: "Dell XPS 15 9530 (13th Gen Intel i7-13700H, 16GB DDR5, 1TB SSD, RTX 4060, 15.6\" 3.5K OLED Touch)",
    category: "laptops",
    categoryLabel: "Laptops & Computers",
    brand: "Dell",
    priceMinor: 14999900,
    originalPriceMinor: 17499900,
    currency: "INR",
    rating: 4.8,
    reviewCount: 342,
    stock: 8,
    deliveryDays: 1,
    returnDays: 10,
    weightKg: 1.86,
    batteryHours: 10,
    imageUrl: "https://images.unsplash.com/photo-1593642632823-8f785ba67e45?auto=format&fit=crop&w=1000&q=80",
    gallery: [
      "https://images.unsplash.com/photo-1593642632823-8f785ba67e45?auto=format&fit=crop&w=1000&q=80",
      "https://images.unsplash.com/photo-1588872657578-7efd1f1555ed?auto=format&fit=crop&w=1000&q=80",
      "https://images.unsplash.com/photo-1517336714731-489689fd1ca8?auto=format&fit=crop&w=1000&q=80",
    ],
    aiBadge: "✦ Best for Creative & Programming",
    shortSpecs: "Core i7-13700H • 16GB DDR5 • 1TB SSD • RTX 4060 • 3.5K OLED",
    whyFitsYou: {
      summary: "Matches your programming & CAD workflow with high single-thread CPU speed and dual external 4K monitor support.",
      pros: [
        "14-Core Intel i7 with high compile throughput",
        "100% DCI-P3 color accurate 3.5K OLED panel",
        "Dual Thunderbolt 4 ports with 96W charging support",
        "1-Day Express delivery guaranteed",
      ],
      warnings: [
        "Fans become audible under sustained full-load gaming (45dB)",
      ],
    },
    specsGrouped: {
      performance: {
        "CPU": "13th Gen Intel Core i7-13700H (14 cores, 20 threads, up to 5.0 GHz)",
        "RAM": "16GB DDR5 4800MHz (expandable to 64GB)",
        "Storage": "1TB M.2 PCIe Gen4 NVMe SSD",
        "GPU": "NVIDIA GeForce RTX 4060 8GB GDDR6",
      },
      display: {
        "Size": "15.6 inches",
        "Resolution": "3.5K (3456 x 2160) OLED Touch",
        "Brightness": "400 nits, 100% DCI-P3 color gamut",
        "Glass": "Edge-to-edge Corning Gorilla Glass 6",
      },
      connectivity: {
        "Ports": "2x Thunderbolt 4 (USB-C), 1x USB-C 3.2 Gen 2, 1x Full-Size SD Card Reader v6.0",
        "Wireless": "Intel Killer Wi-Fi 6E 1675 (AX211) 2x2 + Bluetooth 5.3",
      },
      batteryOrPower: {
        "Battery": "6-Cell 86Whr integrated battery",
        "Adapter": "130W Type-C AC Power Adapter",
      },
    },
    sentiment: {
      performancePct: 94,
      batteryPct: 81,
      buildQualityPct: 96,
      valuePct: 88,
      displayPct: 98,
      customerLikes: [
        "Gorgeous 3.5K OLED screen with deep blacks",
        "Blazing fast code compilation and multitasking",
        "CNC machined aluminum chassis with carbon fiber palm rest",
      ],
      customerConcerns: [
        "No full-sized USB-A ports (requires included Type-C dongle)",
      ],
    },
    reviews: [
      {
        id: "rev_1",
        author: "Devendra K.",
        verified: true,
        rating: 5,
        date: "14 August 2026",
        title: "Spectacular machine for full-stack development",
        comment: "Docker containers spin up in seconds, and Webpack builds that took 45s on my old laptop take 8s here. The OLED panel is mesmerizing for coding in dark themes.",
        helpfulCount: 48,
        tags: ["Performance", "Display", "Programming"],
      },
      {
        id: "rev_2",
        author: "Priya S.",
        verified: true,
        rating: 4,
        date: "2 August 2026",
        title: "Great power, battery is decent",
        comment: "I get around 7-8 hours of real browser + VS Code work. Fans kick in when training ML models locally, but chassis remains comfortable.",
        helpfulCount: 22,
        tags: ["Battery", "Thermals"],
      },
      {
        id: "rev_3",
        author: "Rohan M.",
        verified: true,
        rating: 5,
        date: "28 July 2026",
        title: "Keyboard and trackpad are top tier",
        comment: "Key travel is very tactile and comfortable for 8+ hour writing sessions. Solid build quality with zero keyboard flex.",
        helpfulCount: 15,
        tags: ["Build Quality", "Keyboard"],
      },
    ],
    qa: [
      {
        question: "Can it power two external 4K monitors at 60Hz simultaneously?",
        answer: "Yes, both Thunderbolt 4 ports support DisplayPort 1.4 alt mode capable of driving two 4K 60Hz displays or one 8K display.",
        source: "Official Dell Documentation & Hardware Specs",
      },
      {
        question: "Is the RAM upgradable?",
        answer: "Yes, features 2x SO-DIMM slots supporting up to 64GB DDR5 memory.",
        source: "Service Manual Verified",
      },
    ],
    merchant: {
      id: "mer_dell_direct",
      name: "Dell Official Flagship",
      verified: true,
      rating: 4.9,
    },
    crossSell: {
      id: "prd_dell_sleeve",
      title: "Dell Premier 15 Neoprene Waterproof Sleeve",
      priceMinor: 199900,
      imageUrl: "https://images.unsplash.com/photo-1544816155-12df9643f363?auto=format&fit=crop&w=400&q=80",
      alternativeSavingsMinor: 50000,
      alternativeTitle: "EcoShield 15.6 Ultra-Light Sleeve (Save ₹500)",
    },
  },
  {
    id: "prd_lenovo_ideapad_slim_5",
    slug: "lenovo-ideapad-slim-5-ryzen-7",
    title: "Lenovo IdeaPad Slim 5 16\" (AMD Ryzen 7 7730U, 16GB DDR4, 512GB SSD, FHD IPS 300 nits, Arctic Grey)",
    category: "laptops",
    categoryLabel: "Laptops & Computers",
    brand: "Lenovo",
    priceMinor: 6499900,
    originalPriceMinor: 7289000,
    currency: "INR",
    rating: 4.6,
    reviewCount: 1284,
    stock: 12,
    deliveryDays: 2,
    returnDays: 10,
    weightKg: 1.62,
    batteryHours: 12,
    imageUrl: "https://images.unsplash.com/photo-1588872657578-7efd1f1555ed?auto=format&fit=crop&w=1000&q=80",
    gallery: [
      "https://images.unsplash.com/photo-1588872657578-7efd1f1555ed?auto=format&fit=crop&w=1000&q=80",
      "https://images.unsplash.com/photo-1593642632823-8f785ba67e45?auto=format&fit=crop&w=1000&q=80",
    ],
    aiBadge: "✦ Best for Programming under ₹70k",
    shortSpecs: "Ryzen 7 7730U • 16GB RAM • 512GB SSD • 1.6kg • 12hr Battery",
    whyFitsYou: {
      summary: "Meets 16GB RAM requirement under ₹70k budget with strong 8-core multi-threaded CPU and 2-day delivery.",
      pros: [
        "8-Core AMD Ryzen 7 handles multiple Docker containers smoothly",
        "Lightweight 1.6kg aluminum body for daily college/office travel",
        "56.6Wh battery delivers full working day battery life (10-12 hrs)",
        "Includes full-size HDMI 1.4b port and USB-C Power Delivery",
      ],
      warnings: [
        "Integrated AMD Radeon Graphics (not intended for AAA 4K gaming)",
      ],
    },
    specsGrouped: {
      performance: {
        "CPU": "AMD Ryzen 7 7730U (8 Cores, 16 Threads, 2.0 GHz Base, up to 4.5 GHz Boost)",
        "RAM": "16GB DDR4 3200MHz Dual Channel",
        "Storage": "512GB M.2 2242 PCIe 4.0x4 NVMe SSD",
        "GPU": "Integrated AMD Radeon Graphics",
      },
      display: {
        "Size": "16.0 inches",
        "Resolution": "WUXGA (1920 x 1200) IPS Anti-Glare",
        "Brightness": "300 nits, 45% NTSC, TÜV Low Blue Light",
      },
      connectivity: {
        "Ports": "2x USB-C 3.2 Gen 1 (Power Delivery & DP), 2x USB-A 3.2 Gen 1, 1x HDMI 1.4b, 1x SD card reader, 3.5mm audio",
        "Wireless": "Wi-Fi 6 (802.11ax 2x2) + Bluetooth 5.1",
      },
      batteryOrPower: {
        "Battery": "3-Cell 56.6Wh with Rapid Charge Boost (15 mins = 2 hours)",
        "Adapter": "65W USB-C Wall Adapter",
      },
    },
    sentiment: {
      performancePct: 92,
      batteryPct: 89,
      buildQualityPct: 88,
      valuePct: 96,
      displayPct: 82,
      customerLikes: [
        "Exceptional price-to-performance ratio for coding and office work",
        "Quiet thermals even under sustained multi-core compilation",
        "Comfortable keyboard with dedicated numeric keypad",
      ],
      customerConcerns: [
        "Screen colors are fine for text/code, but not for professional color grading",
      ],
    },
    reviews: [
      {
        id: "rev_len_1",
        author: "Karthik R.",
        verified: true,
        rating: 5,
        date: "10 August 2026",
        title: "Best coding laptop under ₹70k hands down",
        comment: "Ryzen 7 7730U is a beast for Linux / WSL2 dev. I run Node, Postgres, and VS Code with 30 Chrome tabs and zero lag.",
        helpfulCount: 64,
        tags: ["Programming", "Performance", "Value"],
      },
      {
        id: "rev_len_2",
        author: "Ananya B.",
        verified: true,
        rating: 4,
        date: "3 August 2026",
        title: "Great battery life and lightweight",
        comment: "Carrying this to university daily is a breeze at 1.6kg. Battery easily lasts 9-10 hours of lectures and coding.",
        helpfulCount: 31,
        tags: ["Battery", "Portability"],
      },
    ],
    qa: [
      {
        question: "Does this laptop have an HDMI port?",
        answer: "Yes, it includes a dedicated full-size HDMI 1.4b port supporting up to 4K 30Hz.",
        source: "Lenovo PSREF Verified Specification",
      },
      {
        question: "Can it charge via USB Type-C power banks?",
        answer: "Yes, both USB-C ports support USB Power Delivery 3.0 (minimum 45W recommended).",
        source: "Lenovo User Manual",
      },
    ],
    merchant: {
      id: "mer_lenovo_india",
      name: "Lenovo Authorized Store",
      verified: true,
      rating: 4.8,
    },
    crossSell: {
      id: "prd_wireless_mouse_erg",
      title: "Logitech M330 Silent Plus Wireless Ergonomic Mouse",
      priceMinor: 99900,
      imageUrl: "https://images.unsplash.com/photo-1615663245857-ac93bb7c39e7?auto=format&fit=crop&w=400&q=80",
      alternativeSavingsMinor: 20000,
      alternativeTitle: "Rapoo M100 Multi-Mode Silent Mouse (Save ₹200)",
    },
  },
  {
    id: "prd_macbook_pro_14",
    slug: "apple-macbook-pro-14-m3",
    title: "Apple MacBook Pro 14\" (M3 Pro Chip, 18GB Unified Memory, 512GB SSD, 14.2\" Liquid Retina XDR 120Hz, Space Black)",
    category: "laptops",
    categoryLabel: "Laptops & Computers",
    brand: "Apple",
    priceMinor: 18990000,
    originalPriceMinor: 19990000,
    currency: "INR",
    rating: 4.9,
    reviewCount: 890,
    stock: 14,
    deliveryDays: 1,
    returnDays: 14,
    weightKg: 1.61,
    batteryHours: 18,
    imageUrl: "https://images.unsplash.com/photo-1517336714731-489689fd1ca8?auto=format&fit=crop&w=1000&q=80",
    gallery: [
      "https://images.unsplash.com/photo-1517336714731-489689fd1ca8?auto=format&fit=crop&w=1000&q=80",
      "https://images.unsplash.com/photo-1611186871348-b1ce696e52c9?auto=format&fit=crop&w=1000&q=80",
    ],
    aiBadge: "✦ Best Battery & Portability",
    shortSpecs: "Apple M3 Pro • 18GB RAM • 512GB SSD • 120Hz XDR • 18hr Battery",
    whyFitsYou: {
      summary: "Industry-leading battery endurance with sustained performance that never drops on battery power.",
      pros: [
        "Up to 18 hours real-world battery life",
        "1600 nits peak HDR Liquid Retina display with 120Hz ProMotion",
        "Silent fan operation even during heavy Docker builds",
      ],
      warnings: [
        "Memory and storage are non-upgradable after purchase",
      ],
    },
    specsGrouped: {
      performance: {
        "Chip": "Apple M3 Pro (11-core CPU with 5 performance cores and 6 efficiency cores, 14-core GPU)",
        "Memory": "18GB Unified Memory with 150GB/s bandwidth",
        "Storage": "512GB High-Speed NVMe SSD (up to 6000 MB/s read)",
      },
      display: {
        "Size": "14.2-inch Liquid Retina XDR",
        "Resolution": "3024 x 1964 native resolution at 254 pixels per inch",
        "Refresh Rate": "ProMotion technology for adaptive refresh rates up to 120Hz",
        "Brightness": "1000 nits sustained full-screen, 1600 nits peak (HDR content)",
      },
      connectivity: {
        "Ports": "3x Thunderbolt 4 (USB-C), HDMI port, SDXC card slot, MagSafe 3 port, 3.5mm headphone jack",
        "Wireless": "Wi-Fi 6E (802.11ax) + Bluetooth 5.3",
      },
      batteryOrPower: {
        "Battery": "70-watt-hour lithium-polymer battery",
        "Charging": "70W USB-C Power Adapter (fast charge capable with 96W adapter)",
      },
    },
    sentiment: {
      performancePct: 98,
      batteryPct: 99,
      buildQualityPct: 99,
      valuePct: 86,
      displayPct: 99,
      customerLikes: [
        "Unbeatable battery life (easily lasts full 2 days of dev work)",
        "Stunning display quality and speakers",
        "Space Black anodization resists fingerprints",
      ],
      customerConcerns: [
        "Apple upgrade costs for additional RAM/Storage",
      ],
    },
    reviews: [
      {
        id: "rev_app_1",
        author: "Sameer V.",
        verified: true,
        rating: 5,
        date: "11 August 2026",
        title: "Battery life feels like magic",
        comment: "I worked from 9am to 6pm in a coffee shop without taking out my charger once. Compiles iOS and Rust apps with zero stutter.",
        helpfulCount: 82,
        tags: ["Battery", "Performance", "MacOS"],
      },
    ],
    qa: [
      {
        question: "Does it support external displays?",
        answer: "The M3 Pro chip supports up to two external displays with up to 6K resolution at 60Hz over Thunderbolt.",
        source: "Apple Official Tech Specs",
      },
    ],
    merchant: {
      id: "mer_apple_auth",
      name: "Apple Premium Partner",
      verified: true,
      rating: 5.0,
    },
    crossSell: {
      id: "prd_apple_mouse",
      title: "Apple Magic Mouse (USB-C) Space Black",
      priceMinor: 850000,
      imageUrl: "https://images.unsplash.com/photo-1615663245857-ac93bb7c39e7?auto=format&fit=crop&w=400&q=80",
    },
  },
  {
    id: "prd_sony_wh1000xm5",
    slug: "sony-wh1000xm5-wireless-anc",
    title: "Sony WH-1000XM5 Wireless Active Noise Cancelling Headphones (30hr Battery, Multi-Point LDAC Hi-Res Audio)",
    category: "audio",
    categoryLabel: "Headphones & Audio",
    brand: "Sony",
    priceMinor: 2699000,
    originalPriceMinor: 3499000,
    currency: "INR",
    rating: 4.9,
    reviewCount: 1420,
    stock: 25,
    deliveryDays: 1,
    returnDays: 10,
    weightKg: 0.25,
    batteryHours: 30,
    imageUrl: "https://images.unsplash.com/photo-1546435770-a3e426bf472b?auto=format&fit=crop&w=1000&q=80",
    gallery: [
      "https://images.unsplash.com/photo-1546435770-a3e426bf472b?auto=format&fit=crop&w=1000&q=80",
      "https://images.unsplash.com/photo-1505740420928-5e560c06d30e?auto=format&fit=crop&w=1000&q=80",
    ],
    aiBadge: "✦ #1 Best Noise Cancellation",
    shortSpecs: "Auto NC Optimizer • 30h Battery • 8 Mics • LDAC Hi-Res • Multipoint",
    whyFitsYou: {
      summary: "Industry benchmark for flight and office silence with superior call clarity and multipoint switching.",
      pros: [
        "Dual-processor Auto NC Optimizer eliminates voice and engine rumble",
        "Ultra-lightweight 250g soft-fit synthetic leather headband",
        "3-minute quick charge yields 3 hours of playback",
      ],
      warnings: [
        "Earcups do not fold inward like older XM4 (rotates flat only)",
      ],
    },
    specsGrouped: {
      performance: {
        "Noise Cancellation": "HD Noise Cancelling Processor QN1 + Integrated Processor V1 with 8 microphones",
        "Driver Unit": "30mm carbon fiber composite dome driver",
        "Frequency Response": "4 Hz - 40,000 Hz (Hi-Res Audio Certified)",
      },
      connectivity: {
        "Bluetooth": "Version 5.2 (Range approx. 10m)",
        "Codecs": "LDAC, AAC, SBC",
        "Multipoint": "Connects to 2 devices simultaneously with auto-switch",
      },
      batteryOrPower: {
        "Battery Life": "Max 30 hours (ANC On), Max 40 hours (ANC Off)",
        "Charging": "USB-PD Fast Charging (3 min = 3 hours)",
      },
    },
    sentiment: {
      performancePct: 97,
      batteryPct: 95,
      buildQualityPct: 92,
      valuePct: 91,
      customerLikes: [
        "Incredible ANC silence for plane and cafe working",
        "Clear microphone isolation for Zoom/Teams calls",
        "Flawless multipoint switching between laptop and phone",
      ],
      customerConcerns: [
        "Carrying case is slightly larger than XM4 case",
      ],
    },
    reviews: [
      {
        id: "rev_sony_1",
        author: "Vikram N.",
        verified: true,
        rating: 5,
        date: "5 August 2026",
        title: "Total silence in crowded airports",
        comment: "Flew Bengaluru to London with these. Crying babies and jet engine hum vanished completely. Multipoint between phone and iPad worked seamlessly.",
        helpfulCount: 75,
        tags: ["Noise Cancellation", "Comfort", "Travel"],
      },
    ],
    qa: [
      {
        question: "Can I use them wired with ANC turned on?",
        answer: "Yes, a 3.5mm gold-plated audio cable is included. When powered on, ANC works over the wired connection.",
        source: "Sony Official User Guide",
      },
    ],
    merchant: {
      id: "mer_sony_official",
      name: "Sony Center Authorized",
      verified: true,
      rating: 5.0,
    },
    crossSell: {
      id: "prd_headphone_stand",
      title: "Matte Aluminum Desktop Headphone Stand with Cable Organizer",
      priceMinor: 129900,
      imageUrl: "https://images.unsplash.com/photo-1583394838336-acd977736f90?auto=format&fit=crop&w=400&q=80",
    },
  },
  {
    id: "prd_keychron_k2_pro",
    slug: "keychron-k2-pro-mechanical-keyboard",
    title: "Keychron K2 Pro QMK/VIA Wireless Custom Mechanical Keyboard (Gateron Jupiter Brown, RGB Hot-Swappable, PBT Caps)",
    category: "keyboards",
    categoryLabel: "Keyboards & Peripherals",
    brand: "Keychron",
    priceMinor: 899900,
    originalPriceMinor: 1049900,
    currency: "INR",
    rating: 4.9,
    reviewCount: 489,
    stock: 19,
    deliveryDays: 2,
    returnDays: 10,
    weightKg: 0.94,
    imageUrl: "https://images.unsplash.com/photo-1587829741301-dc798b83add3?auto=format&fit=crop&w=1000&q=80",
    gallery: [
      "https://images.unsplash.com/photo-1587829741301-dc798b83add3?auto=format&fit=crop&w=1000&q=80",
      "https://images.unsplash.com/photo-1618384887929-16ec33fab9ef?auto=format&fit=crop&w=1000&q=80",
    ],
    aiBadge: "✦ Top Developer Pick",
    shortSpecs: "75% Compact • Gateron Brown Tactile • Hot-Swap • QMK/VIA • Mac/Win",
    whyFitsYou: {
      summary: "Tactile typing feedback optimized for programmers with full open-source key remapping stored in hardware.",
      pros: [
        "Pre-lubed Gateron Jupiter Brown switches with gentle bump",
        "Hot-swappable PCB supports any 3-pin or 5-pin MX switch",
        "Dedicated physical Mac/Windows toggle switch with included replacement keycaps",
      ],
      warnings: [
        "Thick chassis benefits from using a wrist rest for long sessions",
      ],
    },
    specsGrouped: {
      performance: {
        "Layout": "75% Compact (84 keys)",
        "Switches": "Gateron Jupiter Brown (Pre-Lubed Tactile, 55±15gf operating force)",
        "Keycaps": "Double-shot PBT with OSA profile (non-shine through)",
        "Hot-Swap": "Yes, compatible with Cherry, Gateron, Kailh, Panda switches",
      },
      connectivity: {
        "Modes": "Bluetooth 5.1 (connects 3 devices) + Type-C Wired (1000Hz polling rate)",
        "Compatibility": "macOS, Windows, Linux, iOS, Android",
      },
      batteryOrPower: {
        "Battery": "4000mAh rechargeable li-polymer",
        "Battery Life": "Up to 300 hours (backlight off) or 100 hours (RGB on)",
      },
    },
    sentiment: {
      performancePct: 96,
      batteryPct: 91,
      buildQualityPct: 98,
      valuePct: 94,
      customerLikes: [
        "Satisfying deep sound profile thanks to internal sound-absorbing foam",
        "Instant Bluetooth device switching between Mac and PC",
        "QMK/VIA programming allows infinite shortcuts",
      ],
      customerConcerns: [
        "Keyboard height requires an ergonomic palm rest for comfort",
      ],
    },
    reviews: [
      {
        id: "rev_key_1",
        author: "Abhishek T.",
        verified: true,
        rating: 5,
        date: "8 August 2026",
        title: "Brown switches feel sublime",
        comment: "The Jupiter browns have a crisp tactile bump without loud clicks that disturb coworkers. Wireless connection to my Mac is rock solid.",
        helpfulCount: 39,
        tags: ["Typing", "Switches", "Wireless"],
      },
    ],
    qa: [
      {
        question: "Can I remap keys without keeping software running in the background?",
        answer: "Yes, VIA flushes key remappings and macros directly into the onboard microcontroller memory.",
        source: "Keychron Technical Manual",
      },
    ],
    merchant: {
      id: "mer_keychron_india",
      name: "Keychron India Official",
      verified: true,
      rating: 4.9,
    },
    crossSell: {
      id: "prd_walnut_wrist_rest",
      title: "Solid Walnut Wood Ergonomic Palm Rest for 75% Keyboard",
      priceMinor: 199900,
      imageUrl: "https://images.unsplash.com/photo-1544816155-12df9643f363?auto=format&fit=crop&w=400&q=80",
    },
  },
  {
    id: "prd_samsung_s24_ultra",
    slug: "samsung-galaxy-s24-ultra-5g",
    title: "Samsung Galaxy S24 Ultra 5G (12GB RAM, 256GB Storage, Snapdragon 8 Gen 3 for Galaxy, 200MP Camera, Titanium Gray)",
    category: "phones",
    categoryLabel: "Flagship Smartphones",
    brand: "Samsung",
    priceMinor: 11999900,
    originalPriceMinor: 12999900,
    currency: "INR",
    rating: 4.8,
    reviewCount: 940,
    stock: 9,
    deliveryDays: 1,
    returnDays: 10,
    weightKg: 0.23,
    imageUrl: "https://images.unsplash.com/photo-1610945415295-d9bbf067e59c?auto=format&fit=crop&w=1000&q=80",
    gallery: [
      "https://images.unsplash.com/photo-1610945415295-d9bbf067e59c?auto=format&fit=crop&w=1000&q=80",
    ],
    aiBadge: "✦ Best Camera & Display",
    shortSpecs: "Snapdragon 8 Gen 3 • 200MP Quad Cam • 2600 nits Anti-Glare • S-Pen",
    whyFitsYou: {
      summary: "Top-tier photography with anti-reflective glass display and built-in productivity stylus.",
      pros: [
        "Gorilla Armor glass eliminates 75% of ambient screen reflections",
        "200MP sensor with 5x 50MP periscope zoom captures incredible detail",
        "7 years of major OS and security software updates guaranteed",
      ],
      warnings: [
        "Large 6.8-inch size with squared titanium corners requires large pockets",
      ],
    },
    specsGrouped: {
      performance: {
        "Processor": "Qualcomm Snapdragon 8 Gen 3 for Galaxy (4nm)",
        "RAM": "12GB LPDDR5X",
        "Storage": "256GB UFS 4.0",
      },
      display: {
        "Screen": "6.8\" Dynamic AMOLED 2X Quad HD+ (3120 x 1440)",
        "Brightness": "2600 nits peak brightness, 1-120Hz LTPO refresh rate",
        "Protection": "Corning Gorilla Armor (Anti-Reflective)",
      },
      connectivity: {
        "5G": "Sub-6 and mmWave Dual SIM (eSIM + Physical)",
        "Wireless": "Wi-Fi 7 (802.11be), Bluetooth 5.3, Ultra-Wideband (UWB)",
      },
      batteryOrPower: {
        "Battery": "5000mAh with 45W Fast Charging and 15W Fast Wireless Charging 2.0",
      },
    },
    sentiment: {
      performancePct: 97,
      batteryPct: 92,
      buildQualityPct: 98,
      valuePct: 89,
      displayPct: 99,
      customerLikes: [
        "Anti-reflective screen makes outdoor visibility phenomenal",
        "5x periscope zoom is tack sharp even in low light",
        "S-Pen is unbeatable for quick notes and signing documents",
      ],
      customerConcerns: [
        "Charging brick is not included in box",
      ],
    },
    reviews: [
      {
        id: "rev_sam_1",
        author: "Manish G.",
        verified: true,
        rating: 5,
        date: "9 August 2026",
        title: "The anti-glare screen is a game changer",
        comment: "Using this outdoors in bright Bangalore sunlight with zero reflections is unbelievable. S-Pen is very responsive.",
        helpfulCount: 51,
        tags: ["Display", "Camera", "Stylus"],
      },
    ],
    qa: [
      {
        question: "Does the anti-reflective coating wear off over time?",
        answer: "No, Corning Gorilla Armor is an integrated ion-exchange molecular layer baked into the glass surface.",
        source: "Corning Technical Whitepaper",
      },
    ],
    merchant: {
      id: "mer_samsung_official",
      name: "Samsung Smart Café Official",
      verified: true,
      rating: 4.9,
    },
    crossSell: {
      id: "prd_samsung_45w_adapter",
      title: "Samsung 45W USB-C Super Fast Power Adapter with 5A Cable",
      priceMinor: 299900,
      imageUrl: "https://images.unsplash.com/photo-1583863788434-e58a36330cf0?auto=format&fit=crop&w=400&q=80",
    },
  },
  {
    id: "prd_lg_ultrafine_27_4k",
    slug: "lg-27-4k-uhd-monitor",
    title: "LG 27UP850N-W 27\" 4K UHD IPS Monitor (3840x2160, DCI-P3 95%, HDR400, USB-C 96W PD Power Delivery, Ergo Stand)",
    category: "monitors",
    categoryLabel: "Monitors & Displays",
    brand: "LG",
    priceMinor: 3199000,
    originalPriceMinor: 4200000,
    currency: "INR",
    rating: 4.8,
    reviewCount: 630,
    stock: 7,
    deliveryDays: 2,
    returnDays: 10,
    imageUrl: "https://images.unsplash.com/photo-1527443224154-c4a3942d3acf?auto=format&fit=crop&w=1000&q=80",
    gallery: [
      "https://images.unsplash.com/photo-1527443224154-c4a3942d3acf?auto=format&fit=crop&w=1000&q=80",
    ],
    aiBadge: "✦ Best All-In-One Mac/PC Monitor",
    shortSpecs: "4K IPS • 96W USB-C Power • DCI-P3 95% • HDR400 • Height/Pivot Stand",
    whyFitsYou: {
      summary: "Single-cable USB-C connection charges your laptop at 96W while carrying 4K video, data, and audio.",
      pros: [
        "96W USB-C Power Delivery powers 15\" and 16\" laptops without extra adapter",
        "Crisp 4K 163 PPI text rendering with 95% DCI-P3 wide color gamut",
        "Height, tilt, and 90-degree vertical pivot adjustable stand included",
      ],
      warnings: [
        "60Hz refresh rate (not designed for competitive esports gaming)",
      ],
    },
    specsGrouped: {
      performance: {
        "Resolution": "4K Ultra HD (3840 x 2160) at 60Hz",
        "Panel": "IPS (In-Plane Switching) 178° viewing angles",
        "Color Gamut": "DCI-P3 95% (CIE1976), sRGB 99%",
        "HDR": "VESA DisplayHDR 400 with HDR10 support",
      },
      connectivity: {
        "USB-C": "1x USB-C (96W Power Delivery, DisplayPort 1.4 alt mode, Data)",
        "HDMI": "2x HDMI 2.0 ports",
        "DisplayPort": "1x DisplayPort 1.4",
        "USB Hub": "2x USB 3.0 Type-A downstream ports",
      },
      batteryOrPower: {
        "Speakers": "2x 5W built-in stereo speakers with MaxxAudio",
        "Power": "External adapter with smart energy saving mode",
      },
    },
    sentiment: {
      performancePct: 95,
      batteryPct: 90,
      buildQualityPct: 93,
      valuePct: 96,
      displayPct: 97,
      customerLikes: [
        "Single USB-C cable keeps desk clean and charges MacBook Pro rapidly",
        "Super sharp text clarity eliminates eye strain during coding",
        "Pivot mode makes reviewing code and reading documentation effortless",
      ],
      customerConcerns: [
        "Speakers are adequate for video calls but not audiophile quality",
      ],
    },
    reviews: [
      {
        id: "rev_lg_1",
        author: "Deepak J.",
        verified: true,
        rating: 5,
        date: "29 July 2026",
        title: "Perfect companion for MacBook Pro",
        comment: "96W charging powers my 16\" M3 Max without breaking a sweat. Colors are accurate right out of the box and vertical pivot is great for reading code.",
        helpfulCount: 44,
        tags: ["Display", "USB-C", "Productivity"],
      },
    ],
    qa: [
      {
        question: "Does it charge a 16-inch MacBook Pro or Dell XPS under full CPU load?",
        answer: "Yes, the upgraded 96W power delivery provides full continuous power without draining laptop battery.",
        source: "LG Electronics Hardware Specification",
      },
    ],
    merchant: {
      id: "mer_lg_official",
      name: "LG Electronics Official",
      verified: true,
      rating: 4.8,
    },
    crossSell: {
      id: "prd_hdmi_21_cable",
      title: "Ultra High-Speed HDMI 2.1 48Gbps 2-Meter Braided Cable",
      priceMinor: 99900,
      imageUrl: "https://images.unsplash.com/photo-1544816155-12df9643f363?auto=format&fit=crop&w=400&q=80",
    },
  },
];
