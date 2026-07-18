import { useEffect, useState } from "react";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

type ShopItem = {
  name: string;
  unit: string;
  price_naira: number;
  in_stock: boolean;
};
type Shop = { shop_name: string; whatsapp: string; items: ShopItem[] };

export default function App() {
  const slug = window.location.pathname.split("/shop/")[1] ?? "";
  const [shop, setShop] = useState<Shop | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    fetch(`${API}/api/shops/${slug}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then(setShop)
      .catch(() => setError(true));
  }, [slug]);

  if (error) return <p>Shop not found.</p>;
  if (!shop) return <p>Loading…</p>;

  const order = (item: ShopItem) =>
    `https://wa.me/${shop.whatsapp}?text=${encodeURIComponent(
      `I wan buy ${item.name} — how much for delivery?`,
    )}`;

  return (
    <main style={{ maxWidth: 480, margin: "0 auto", padding: 16 }}>
      <h1>{shop.shop_name}</h1>
      {shop.items.map((it) => (
        <div
          key={it.name}
          style={{
            display: "flex",
            justifyContent: "space-between",
            padding: "12px 0",
            borderBottom: "1px solid #eee",
            opacity: it.in_stock ? 1 : 0.45,
          }}
        >
          <div>
            <strong>{it.name}</strong>
            <div>
              ₦{it.price_naira.toLocaleString()} / {it.unit}
            </div>
          </div>
          {it.in_stock ? (
            <a href={order(it)}>Order on WhatsApp</a>
          ) : (
            <span>Sold out</span>
          )}
        </div>
      ))}
    </main>
  );
}
