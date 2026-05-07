import { useEffect, useState } from "react";
import { ApiError, api, type OrderInfo, type ProductInfo } from "../api";

type Props = {
  accountId: number;
  chatId: string | null;
  /** Order id whose details should be visible right now, or null for the
   *  default "currently viewed product" pane. */
  orderId: string | null;
  /** User clicked the X — close the order pane and revert to product info. */
  onCloseOrder: () => void;
};

/**
 * IDE-style right sidebar with two states:
 *   1. order pane — when a message order link was clicked (`orderId !== null`).
 *      Shows order details + an X button to return to the default pane.
 *   2. product pane — fetches the product the buyer is currently viewing
 *      from FunPay (refreshed every 15 s, polling pauses while tab hidden).
 *
 * Both states share the same outer card so toggling between them doesn't
 * shift the chat width or the page layout.
 */
export default function RightSidebar({
  accountId,
  chatId,
  orderId,
  onCloseOrder,
}: Props) {
  if (orderId !== null) {
    return (
      <OrderPane
        accountId={accountId}
        orderId={orderId}
        onClose={onCloseOrder}
      />
    );
  }
  return <ProductPane accountId={accountId} chatId={chatId} />;
}

function ProductPane({
  accountId,
  chatId,
}: {
  accountId: number;
  chatId: string | null;
}) {
  const [info, setInfo] = useState<ProductInfo | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setInfo(null);
    setErr(null);
    if (chatId === null) return;
    let cancelled = false;
    async function load() {
      try {
        const data = await api.get<ProductInfo>(
          `/api/accounts/${accountId}/chats/${encodeURIComponent(chatId!)}/product`
        );
        if (!cancelled) {
          setInfo(data);
          setErr(null);
        }
      } catch (e) {
        if (!cancelled) {
          setErr(e instanceof ApiError ? e.detail : String(e));
        }
      }
    }
    void load();
    // 15 s poll matches the "currently viewing" cadence FunPay's own JS uses
    // for the chat-panel-user runner request — fast enough to feel live, but
    // slow enough not to thrash their backend.
    const id = window.setInterval(() => {
      if (document.visibilityState === "hidden") return;
      void load();
    }, 15_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [accountId, chatId]);

  return (
    <div className="card flex min-h-0 flex-col gap-3">
      <div className="text-sm font-semibold uppercase tracking-wider text-ink2">
        Currently viewing
      </div>
      {err && <div className="text-sm text-danger">{err}</div>}
      {info === null && !err && (
        <div className="text-sm text-muted">Loading…</div>
      )}
      {info !== null && !info.available && (
        <div className="text-sm text-muted">
          Buyer isn't browsing any of your offers right now.
        </div>
      )}
      {info !== null && info.available && (
        <div className="flex min-h-0 flex-col gap-2 overflow-y-auto pr-1">
          {info.title && (
            <div className="text-base font-semibold leading-tight">
              {info.title}
            </div>
          )}
          {info.price && (
            <div className="text-sm text-ink2">
              <span className="text-muted">Price · </span>
              {info.price}
            </div>
          )}
          {info.description && (
            <div className="text-sm text-muted leading-relaxed whitespace-pre-wrap break-words">
              {info.description}
            </div>
          )}
          {info.url && (
            <a
              href={info.url}
              target="_blank"
              rel="noreferrer"
              className="text-sm text-ink underline-offset-2 hover:underline"
            >
              Open offer on FunPay ↗
            </a>
          )}
        </div>
      )}
    </div>
  );
}

function OrderPane({
  accountId,
  orderId,
  onClose,
}: {
  accountId: number;
  orderId: string;
  onClose: () => void;
}) {
  const [info, setInfo] = useState<OrderInfo | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setInfo(null);
    setErr(null);
    let cancelled = false;
    async function load() {
      try {
        const data = await api.get<OrderInfo>(
          `/api/accounts/${accountId}/orders/${encodeURIComponent(orderId)}`
        );
        if (!cancelled) {
          setInfo(data);
          setErr(null);
        }
      } catch (e) {
        if (!cancelled) {
          setErr(e instanceof ApiError ? e.detail : String(e));
        }
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [accountId, orderId]);

  return (
    <div className="card flex min-h-0 flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm font-semibold uppercase tracking-wider text-ink2">
            Order #{orderId}
          </div>
          {info?.status && (
            <div className="mt-0.5 text-xs text-muted">{info.status}</div>
          )}
        </div>
        <button
          onClick={onClose}
          aria-label="Close order details"
          title="Back to product info"
          className="grid h-8 w-8 place-items-center rounded-xl bg-surface text-ink2 shadow-neu-sm transition-shadow hover:text-ink hover:shadow-neu-pressed"
        >
          ✕
        </button>
      </div>
      {err && <div className="text-sm text-danger">{err}</div>}
      {info === null && !err && (
        <div className="text-sm text-muted">Loading…</div>
      )}
      {info && (
        <div className="flex min-h-0 flex-col gap-2 overflow-y-auto pr-1">
          {info.buyer && (
            <div className="text-sm">
              <span className="text-muted">Buyer · </span>
              <span className="text-ink">{info.buyer}</span>
            </div>
          )}
          {info.total && (
            <div className="text-sm">
              <span className="text-muted">Total · </span>
              <span className="text-ink font-medium">{info.total}</span>
            </div>
          )}
          {info.items.length > 0 && (
            <div className="mt-1 grid gap-2">
              {info.items.map((it, i) => (
                <div key={`${it.label}-${i}`} className="text-sm leading-snug">
                  <div className="text-[11px] uppercase tracking-wider text-muted">
                    {it.label}
                  </div>
                  <div className="text-ink2 whitespace-pre-wrap break-words">
                    {it.value}
                  </div>
                </div>
              ))}
            </div>
          )}
          <a
            href={info.url}
            target="_blank"
            rel="noreferrer"
            className="mt-2 text-sm text-ink underline-offset-2 hover:underline"
          >
            Open order on FunPay ↗
          </a>
        </div>
      )}
    </div>
  );
}
