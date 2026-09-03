"use client";

import React, { createContext, useContext, useEffect, useState } from "react";
import { ProductItem } from "@/data/products";
import { bootstrapSession } from "@/lib/api";

export interface CartItem {
  product: ProductItem;
  quantity: number;
}

export interface OrderRecord {
  orderId: string;
  paymentId: string;
  items: CartItem[];
  totalMinor: number;
  currency: string;
  status: "confirmed" | "preparing" | "shipped" | "delivered";
  createdAt: string;
  deliveryDate: string;
  policySummary: string;
}

export interface ReturnRequest {
  returnId: string;
  orderId: string;
  productId: string;
  reason: string;
  resolution: "refund" | "replacement";
  status: "submitted" | "approved" | "completed";
  createdAt: string;
  merchantPolicyNote: string;
}

export interface StructuredIntent {
  queryText: string;
  category?: string | null;
  budget_max?: number | null; // In minor units
  brands?: string[];
  weight_preference?: "light" | "standard" | null;
  priority?: "battery" | "performance" | "price" | "display" | "balanced" | null;
  min_memory_gb?: number | null;
  delivery_max_days?: number | null;
}

export interface UserAIPreferences {
  aiRecommendations: boolean;
  useReviewInsights: boolean;
  useWebResearch: boolean;
  askBeforePurchases: boolean;
  autoApprovalLimitMinor: number; // e.g. 500000 = ₹5,000
  preferredBrands: string[];
}

interface StoreContextType {
  sessionId: string;
  cart: CartItem[];
  wishlist: string[]; // product IDs
  compareList: string[]; // product IDs
  orders: OrderRecord[];
  returns: ReturnRequest[];
  currentIntent: StructuredIntent;
  userPreferences: UserAIPreferences;
  isAiDrawerOpen: boolean;
  aiDrawerContext: {
    pageType: "home" | "product" | "compare" | "checkout" | "search";
    product?: ProductItem;
    customPrompt?: string;
  };
  highlightedProductId: string | null;
  sortBy: string;
  failureSimulation: "NONE" | "PRICE_CHANGED" | "PAYMENT_UNCERTAIN" | "POLICY_BLOCKED" | "OFFER_EXPIRED";

  addToCart: (product: ProductItem, quantity?: number) => void;
  removeFromCart: (productId: string) => void;
  updateCartQuantity: (productId: string, quantity: number) => void;
  switchCartItem: (oldProductId: string, newProduct: ProductItem) => void;
  clearCart: () => void;
  toggleWishlist: (productId: string) => void;
  toggleCompare: (productId: string) => void;
  openAiDrawer: (context?: Partial<StoreContextType["aiDrawerContext"]>) => void;
  closeAiDrawer: () => void;
  placeOrder: (order: Omit<OrderRecord, "orderId" | "createdAt" | "status" | "deliveryDate"> & { orderId?: string }) => OrderRecord;
  submitReturn: (orderId: string, productId: string, reason: string, resolution: "refund" | "replacement") => ReturnRequest;
  getProductById: (id: string) => ProductItem | undefined;
  updateIntent: (partialIntent: Partial<StructuredIntent>) => void;
  removeIntentConstraint: (key: keyof StructuredIntent, itemToRemove?: string) => void;
  clearIntent: () => void;
  setSortBy: (sort: string) => void;
  setHighlightedProductId: (id: string | null) => void;
  updateUserPreferences: (prefs: Partial<UserAIPreferences>) => void;
  setFailureSimulation: (sim: StoreContextType["failureSimulation"]) => void;
}

const StoreContext = createContext<StoreContextType | undefined>(undefined);

export function StoreProvider({ children }: { children: React.ReactNode }) {
  const [sessionId] = useState(() => `sess_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`);
  const [cart, setCart] = useState<CartItem[]>([]);
  const [wishlist, setWishlist] = useState<string[]>([]);
  const [compareList, setCompareList] = useState<string[]>([]);
  const [orders, setOrders] = useState<OrderRecord[]>([]);
  const [returns, setReturns] = useState<ReturnRequest[]>([]);
  const [highlightedProductId, setHighlightedProductId] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState("recommended");
  const [failureSimulation, setFailureSimulation] = useState<StoreContextType["failureSimulation"]>("NONE");

  const [currentIntent, setCurrentIntent] = useState<StructuredIntent>({
    queryText: "",
    category: null,
    budget_max: null,
    brands: [],
    weight_preference: null,
    priority: null,
    min_memory_gb: null,
    delivery_max_days: null,
  });

  const [userPreferences, setUserPreferences] = useState<UserAIPreferences>({
    aiRecommendations: true,
    useReviewInsights: true,
    useWebResearch: true,
    askBeforePurchases: true,
    autoApprovalLimitMinor: 500000, // ₹5,000
    preferredBrands: ["Lenovo", "Dell", "Sony"],
  });

  const [isAiDrawerOpen, setIsAiDrawerOpen] = useState(false);
  const [aiDrawerContext, setAiDrawerContext] = useState<StoreContextType["aiDrawerContext"]>({
    pageType: "home",
  });

  const [hydrated, setHydrated] = useState(false);

  // Load persistence from localStorage
  useEffect(() => {
    try {
      const savedCart = localStorage.getItem("agentpay_cart");
      if (savedCart) setCart(JSON.parse(savedCart));
      const savedWishlist = localStorage.getItem("agentpay_wishlist");
      if (savedWishlist) setWishlist(JSON.parse(savedWishlist));
      const savedCompareList = localStorage.getItem("agentpay_compare_list");
      if (savedCompareList) setCompareList(JSON.parse(savedCompareList));
      const savedOrders = localStorage.getItem("agentpay_orders");
      if (savedOrders) setOrders(JSON.parse(savedOrders));
      const savedPrefs = localStorage.getItem("agentpay_user_prefs");
      if (savedPrefs) setUserPreferences(JSON.parse(savedPrefs));
    } catch (e) {
      console.warn("Storage sync note:", e);
    }
    setHydrated(true);
  }, []);

  // Automatic browser session bootstrap so console & search APIs authenticate cleanly
  useEffect(() => {
    bootstrapSession();
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    localStorage.setItem("agentpay_cart", JSON.stringify(cart));
  }, [cart, hydrated]);

  useEffect(() => {
    if (!hydrated) return;
    localStorage.setItem("agentpay_wishlist", JSON.stringify(wishlist));
  }, [wishlist, hydrated]);

  useEffect(() => {
    if (!hydrated) return;
    localStorage.setItem("agentpay_compare_list", JSON.stringify(compareList));
  }, [compareList, hydrated]);

  useEffect(() => {
    if (!hydrated) return;
    localStorage.setItem("agentpay_orders", JSON.stringify(orders));
  }, [orders, hydrated]);

  useEffect(() => {
    if (!hydrated) return;
    localStorage.setItem("agentpay_user_prefs", JSON.stringify(userPreferences));
  }, [userPreferences, hydrated]);

  const addToCart = (product: ProductItem, quantity = 1) => {
    setCart((prev) => {
      const existing = prev.find((item) => item.product.id === product.id);
      if (existing) {
        return prev.map((item) =>
          item.product.id === product.id
            ? { ...item, quantity: item.quantity + quantity }
            : item
        );
      }
      return [...prev, { product, quantity }];
    });
  };

  const removeFromCart = (productId: string) => {
    setCart((prev) => prev.filter((item) => item.product.id !== productId));
  };

  const switchCartItem = (oldProductId: string, newProduct: ProductItem) => {
    setCart((prev) =>
      prev.map((item) =>
        item.product.id === oldProductId
          ? { product: newProduct, quantity: item.quantity }
          : item
      )
    );
  };

  const updateCartQuantity = (productId: string, quantity: number) => {
    if (quantity <= 0) {
      removeFromCart(productId);
      return;
    }
    setCart((prev) =>
      prev.map((item) =>
        item.product.id === productId ? { ...item, quantity } : item
      )
    );
  };

  const clearCart = () => setCart([]);

  const toggleWishlist = (productId: string) => {
    setWishlist((prev) =>
      prev.includes(productId)
        ? prev.filter((id) => id !== productId)
        : [...prev, productId]
    );
  };

  const toggleCompare = (productId: string) => {
    setCompareList((prev) => {
      if (prev.includes(productId)) {
        return prev.filter((id) => id !== productId);
      }
      if (prev.length >= 3) {
        return [...prev.slice(1), productId];
      }
      return [...prev, productId];
    });
  };

  const openAiDrawer = (context?: Partial<StoreContextType["aiDrawerContext"]>) => {
    if (context) {
      setAiDrawerContext((prev) => ({ ...prev, ...context }));
    }
    setIsAiDrawerOpen(true);
  };

  const closeAiDrawer = () => setIsAiDrawerOpen(false);

  const updateIntent = (partialIntent: Partial<StructuredIntent>) => {
    setCurrentIntent((prev) => ({ ...prev, ...partialIntent }));
  };

  const removeIntentConstraint = (key: keyof StructuredIntent, itemToRemove?: string) => {
    setCurrentIntent((prev) => {
      if (key === "brands" && itemToRemove && prev.brands) {
        return {
          ...prev,
          brands: prev.brands.filter((b) => b.toLowerCase() !== itemToRemove.toLowerCase()),
        };
      }
      return {
        ...prev,
        [key]: null,
      };
    });
  };

  const clearIntent = () => {
    setCurrentIntent({
      queryText: "",
      category: null,
      budget_max: null,
      brands: [],
      weight_preference: null,
      priority: null,
      min_memory_gb: null,
      delivery_max_days: null,
    });
  };

  const updateUserPreferences = (prefs: Partial<UserAIPreferences>) => {
    setUserPreferences((prev) => ({ ...prev, ...prefs }));
  };

  const placeOrder = (orderData: Omit<OrderRecord, "orderId" | "createdAt" | "status" | "deliveryDate"> & { orderId?: string }) => {
    const orderId = orderData.orderId || `ord_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
    const now = new Date();
    const delivery = new Date(now.getTime() + 2 * 24 * 60 * 60 * 1000);
    const newOrder: OrderRecord = {
      ...orderData,
      orderId,
      status: "confirmed",
      createdAt: now.toISOString(),
      deliveryDate: delivery.toLocaleDateString("en-IN", {
        weekday: "long",
        day: "numeric",
        month: "short",
      }),
    };
    setOrders((prev) => [newOrder, ...prev]);
    clearCart();
    return newOrder;
  };

  const submitReturn = (
    orderId: string,
    productId: string,
    reason: string,
    resolution: "refund" | "replacement"
  ) => {
    const returnId = `ret_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
    const newReturn: ReturnRequest = {
      returnId,
      orderId,
      productId,
      reason,
      resolution,
      status: "submitted",
      createdAt: new Date().toISOString(),
      merchantPolicyNote: "Eligible under verified return policy. Courier pickup scheduled within 24 hours.",
    };
    setReturns((prev) => [newReturn, ...prev]);
    return newReturn;
  };

  const getProductById = (id: string) => {
    const fromCart = cart.find((item) => item.product.id === id || item.product.slug === id)?.product;
    if (fromCart) return fromCart;
    for (const order of orders) {
      const fromOrder = order.items.find((item) => item.product.id === id || item.product.slug === id)?.product;
      if (fromOrder) return fromOrder;
    }
    return undefined;
  };

  return (
    <StoreContext.Provider
      value={{
        sessionId,
        cart,
        wishlist,
        compareList,
        orders,
        returns,
        currentIntent,
        userPreferences,
        isAiDrawerOpen,
        aiDrawerContext,
        highlightedProductId,
        sortBy,
        failureSimulation,
        addToCart,
        removeFromCart,
        updateCartQuantity,
        switchCartItem,
        clearCart,
        toggleWishlist,
        toggleCompare,
        openAiDrawer,
        closeAiDrawer,
        placeOrder,
        submitReturn,
        getProductById,
        updateIntent,
        removeIntentConstraint,
        clearIntent,
        setSortBy,
        setHighlightedProductId,
        updateUserPreferences,
        setFailureSimulation,
      }}
    >
      {children}
    </StoreContext.Provider>
  );
}

export function useStore() {
  const context = useContext(StoreContext);
  if (!context) {
    throw new Error("useStore must be used within a StoreProvider");
  }
  return context;
}
