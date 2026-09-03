import Link from "next/link";
import { ShieldCheck, Sparkles, Truck, Undo2 } from "lucide-react";

const columns = [
  { title: "Shop", links: [["Laptops", "/category/laptops"], ["Phones", "/category/phones"], ["Audio", "/category/audio"], ["Workspace", "/category/monitors"], ["Best deals", "/search?deals=true"]] },
  { title: "Your experience", links: [["Saved products", "/search?wishlist=true"], ["Compare products", "/compare"], ["Your bag", "/cart"], ["Checkout", "/checkout"]] },
  { title: "For merchants", links: [["Merchant workspace", "/merchant"], ["Campaigns", "/merchant/campaigns"], ["Catalog", "/merchant/catalog"], ["Policy controls", "/merchant/policy"], ["Audit trail", "/merchant/audit"]] },
];

export function Footer() {
  const promises = [[ShieldCheck, "Secure checkout", "Clear, verified payments"], [Truck, "Reliable delivery", "Live availability and timing"], [Undo2, "Easy returns", "Simple, transparent policies"], [Sparkles, "Helpful AI", "Advice that keeps you in control"]] as const;
  return <footer className="mt-6 bg-[#102d23] text-[#d9e6dc]"><div className="mx-auto max-w-[1440px] px-4 sm:px-6 lg:px-10"><div className="grid gap-6 border-b border-white/10 py-8 sm:grid-cols-2 lg:grid-cols-4">{promises.map(([Icon, title, detail]) => <div key={title} className="flex gap-3"><Icon className="h-5 w-5 shrink-0 text-[#a9d1b6]" /><div><p className="text-sm font-bold text-white">{title}</p><p className="mt-1 text-xs text-[#aac1b1]">{detail}</p></div></div>)}</div><div className="grid gap-10 py-12 md:grid-cols-[1.5fr_repeat(3,1fr)]"><div><Link href="/" className="flex items-center gap-2.5"><span className="grid h-9 w-9 place-items-center rounded-xl bg-[#e5f0e9] text-sm font-black text-[#174c3c]">A</span><span className="font-display text-xl font-extrabold text-white">agentpay</span></Link><p className="mt-4 max-w-sm text-sm leading-6 text-[#aac1b1]">A more human way to discover, compare and buy technology. AI guidance is always optional, decisions are always yours.</p><p className="mt-7 text-xs text-[#769b85]">© {new Date().getFullYear()} AgentPay</p></div>{columns.map((column) => <div key={column.title}><h3 className="text-xs font-bold uppercase tracking-[.14em] text-[#a9d1b6]">{column.title}</h3><ul className="mt-4 space-y-2.5">{column.links.map(([label, href]) => <li key={label}><Link href={href} className="text-sm text-[#d9e6dc] transition hover:text-white">{label}</Link></li>)}</ul></div>)}</div></div></footer>;
}
