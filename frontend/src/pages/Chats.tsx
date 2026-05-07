import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  ApiError,
  api,
  type Account,
  type Attachment,
  type ChatMessage,
  type ChatPreview,
  type ChatThread,
  type UploadAttachmentResult,
} from "../api";
import Avatar from "../components/Avatar";
import RightSidebar from "../components/RightSidebar";

// FunPay's chat form caps uploads at 7 MB and only allows images. We mirror
// the same rules client-side so a user gets the failure feedback before the
// payload travels to our backend.
const MAX_IMAGE_BYTES = 7 * 1024 * 1024;
const ALLOWED_IMAGE_TYPES = new Set([
  "image/png",
  "image/jpeg",
  "image/jpg",
  "image/gif",
  "image/webp",
]);

// Order numbers FunPay prints in chat are 4–16 uppercase alnum chars (e.g.
// `#EKW9ZFHL`). The lookahead/lookbehind trim avoids gluing the link onto
// adjacent words like `email#ABC123`.
const ORDER_RE = /(?:^|[^A-Z0-9])#([A-Z0-9]{4,16})(?=$|[^A-Z0-9])/g;

export default function ChatsPage() {
  const [params, setParams] = useSearchParams();
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [accErr, setAccErr] = useState<string | null>(null);
  // Right sidebar visibility persists across chat switches so the operator's
  // preference is remembered for the session.
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [orderId, setOrderId] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<Account[]>("/api/accounts")
      .then(setAccounts)
      .catch((e) =>
        setAccErr(e instanceof ApiError ? e.detail : String(e))
      );
  }, []);

  const enabledAccounts = useMemo(
    () => (accounts ?? []).filter((a) => a.enabled),
    [accounts]
  );

  const accountIdParam = params.get("account");
  const selectedAccountId = useMemo(() => {
    if (accounts === null) return null;
    if (accountIdParam) {
      const n = Number(accountIdParam);
      if (Number.isFinite(n) && enabledAccounts.some((a) => a.id === n)) {
        return n;
      }
    }
    return enabledAccounts[0]?.id ?? null;
  }, [accountIdParam, accounts, enabledAccounts]);

  const chatId = params.get("chat");

  function selectAccount(id: number) {
    const next = new URLSearchParams(params);
    next.set("account", String(id));
    next.delete("chat");
    setParams(next, { replace: true });
    setOrderId(null);
  }

  function selectChat(id: string) {
    const next = new URLSearchParams(params);
    if (selectedAccountId !== null) next.set("account", String(selectedAccountId));
    next.set("chat", id);
    setParams(next, { replace: true });
    setOrderId(null);
  }

  if (accounts === null) {
    return <div className="card text-sm text-muted">Loading accounts…</div>;
  }
  if (accErr) {
    return <div className="card text-sm text-danger">{accErr}</div>;
  }
  if (enabledAccounts.length === 0) {
    return (
      <div className="card text-sm text-muted">
        No enabled accounts. Add an account first on the{" "}
        <a className="underline hover:text-ink" href="/accounts">
          Accounts
        </a>{" "}
        tab.
      </div>
    );
  }

  // The right sidebar is meaningful only when an actual chat thread is
  // open — otherwise the chat pane just shows "Pick a chat on the left."
  // and there's nothing to surface in the sidebar. Hide both the toggle
  // button and the column itself in that state.
  const showSidebar = chatId !== null && sidebarOpen;
  const showSidebarToggle = chatId !== null;

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <header className="flex flex-wrap items-center gap-2">
        <h1 className="mr-3 text-2xl font-semibold tracking-wide">Chats</h1>
        <div className="flex flex-wrap gap-2">
          {enabledAccounts.map((a) => (
            <button
              key={a.id}
              onClick={() => selectAccount(a.id)}
              className={
                selectedAccountId === a.id
                  ? "chip normal-case tracking-normal text-ink"
                  : "btn-ghost"
              }
            >
              {a.label}
            </button>
          ))}
        </div>
        {showSidebarToggle && (
          <button
            onClick={() => setSidebarOpen((v) => !v)}
            className="sidebar-toggle ml-auto grid h-9 w-9 place-items-center rounded-xl bg-surface text-ink2 shadow-neu-sm transition-shadow hover:text-ink hover:shadow-neu-pressed"
            title={sidebarOpen ? "Hide right sidebar" : "Show right sidebar"}
            aria-label={sidebarOpen ? "Hide right sidebar" : "Show right sidebar"}
            aria-pressed={sidebarOpen}
            data-open={sidebarOpen ? "true" : "false"}
          >
            {/* IDE-style sidebar-toggle glyph: a rectangle with a vertical
                divider on the right. The filled-cell highlight scales in/out
                so the icon matches the sidebar's open/closed state with a
                short animation. */}
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="none"
              width="18"
              height="18"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="sidebar-toggle-icon"
            >
              <rect x="3" y="4" width="18" height="16" rx="2" />
              <line x1="15" y1="4" x2="15" y2="20" />
              <rect
                className="sidebar-toggle-fill"
                x="15"
                y="4"
                width="6"
                height="16"
                rx="0"
                fill="currentColor"
              />
            </svg>
          </button>
        )}
      </header>
      {selectedAccountId !== null && (
        <div
          className="chat-grid min-h-0 flex-1 gap-4"
          data-sidebar={showSidebar ? "open" : "closed"}
        >
          <ChatList
            accountId={selectedAccountId}
            activeChatId={chatId}
            onSelect={selectChat}
          />
          <ChatPane
            accountId={selectedAccountId}
            chatId={chatId}
            onOpenOrder={(id) => setOrderId(id)}
            // Bumping this key forces a remount when switching threads, so we
            // don't carry stale message state into a different chat.
            key={`${selectedAccountId}-${chatId ?? ""}`}
          />
          {/*
            The sidebar column is always present in the grid (when a chat
            is open) so its width can transition smoothly via
            `grid-template-columns`. The card itself stays mounted while
            the close animation runs and only stops polling FunPay via the
            `paused` flag.
          */}
          <div
            className="chat-grid__sidebar min-h-0"
            aria-hidden={!showSidebar}
          >
            {chatId !== null && (
              <RightSidebar
                accountId={selectedAccountId}
                chatId={chatId}
                orderId={orderId}
                onCloseOrder={() => setOrderId(null)}
                paused={!sidebarOpen}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function ChatList({
  accountId,
  activeChatId,
  onSelect,
}: {
  accountId: number;
  activeChatId: string | null;
  onSelect: (id: string) => void;
}) {
  const [chats, setChats] = useState<ChatPreview[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(
    async (force: boolean) => {
      setBusy(true);
      setErr(null);
      try {
        const url = `/api/accounts/${accountId}/chats${force ? "?fresh=true" : ""}`;
        setChats(await api.get<ChatPreview[]>(url));
      } catch (e) {
        setErr(e instanceof ApiError ? e.detail : String(e));
      } finally {
        setBusy(false);
      }
    },
    [accountId]
  );

  useEffect(() => {
    void load(false);
    // Poll the chat list often enough that new threads (or new last-message
    // previews) appear without forcing the operator to click Refresh. The
    // backend `_CHAT_LIST_TTL` keeps this from hammering FunPay. Skip ticks
    // while the tab is hidden so a backgrounded panel doesn't pin a CPU.
    const id = window.setInterval(() => {
      if (document.visibilityState === "hidden") return;
      void load(false);
    }, 10_000);
    return () => window.clearInterval(id);
  }, [load]);

  return (
    <div className="card flex min-h-0 flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold uppercase tracking-wider text-ink2">
          Chats
        </div>
        {busy && <div className="text-xs text-muted">syncing…</div>}
      </div>
      {err && <div className="text-sm text-danger">{err}</div>}
      <div className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto pr-1">
        {chats === null ? (
          <div className="text-sm text-muted">Loading…</div>
        ) : chats.length === 0 ? (
          <div className="text-sm text-muted">No chats yet.</div>
        ) : (
          chats.map((c) => {
            const active = c.id === activeChatId;
            return (
              <button
                key={c.id}
                onClick={() => onSelect(c.id)}
                className={active ? "chat-row-active" : "chat-row"}
              >
                <Avatar name={c.title || c.id} src={c.avatar_url} size="md" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <div className="truncate text-sm font-medium">
                      {c.title || c.id}
                    </div>
                    {c.unread && (
                      <span className="ml-auto h-2 w-2 flex-shrink-0 rounded-full bg-ink" />
                    )}
                  </div>
                  {c.last_message && (
                    <div className="truncate text-xs text-muted">
                      {c.last_message}
                    </div>
                  )}
                </div>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}

function ChatPane({
  accountId,
  chatId,
  onOpenOrder,
}: {
  accountId: number;
  chatId: string | null;
  onOpenOrder: (orderId: string) => void;
}) {
  const [thread, setThread] = useState<ChatThread | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [pendingPreview, setPendingPreview] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const messagesRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  // Tracks whether the user is currently parked at the bottom of the message
  // list. We only auto-scroll on poll updates if true — otherwise scrolling
  // up to read history would yank the operator back to the bottom every
  // poll tick. Initial value is `true` so the first render pins to bottom.
  const stickToBottomRef = useRef(true);

  const load = useCallback(
    async (force: boolean) => {
      if (chatId === null) return;
      try {
        const url = `/api/accounts/${accountId}/chats/${encodeURIComponent(chatId)}${
          force ? "?fresh=true" : ""
        }`;
        const t = await api.get<ChatThread>(url);
        setThread(t);
        setErr(null);
      } catch (e) {
        setErr(e instanceof ApiError ? e.detail : String(e));
      }
    },
    [accountId, chatId]
  );

  useEffect(() => {
    setThread(null);
    stickToBottomRef.current = true;
    if (chatId === null) return;
    void load(false);
    // 3s poll keeps incoming buyer messages flowing into the open thread
    // without manual refresh; backend `_THREAD_TTL` is tuned to match. Pause
    // polling while the tab is hidden so we don't generate FunPay traffic
    // for a panel nobody is looking at.
    const id = window.setInterval(() => {
      if (document.visibilityState === "hidden") return;
      void load(false);
    }, 3_000);
    return () => window.clearInterval(id);
  }, [load, chatId]);

  useEffect(() => {
    const el = messagesRef.current;
    if (!el) return;
    if (stickToBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [thread]);

  // Free the object URL we generated for the preview when the file changes.
  useEffect(() => {
    if (!pendingFile) {
      setPendingPreview(null);
      return;
    }
    const url = URL.createObjectURL(pendingFile);
    setPendingPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [pendingFile]);

  function onMessagesScroll(e: React.UIEvent<HTMLDivElement>) {
    const el = e.currentTarget;
    // 80px tolerance so a couple of pixels of scroll-jitter still counts as
    // "at the bottom" — same threshold popular messengers use.
    stickToBottomRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }

  function autosizeInput() {
    const ta = inputRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    // Cap at ~6 lines (≈144px) before the textarea gives up vertical growth
    // and starts scrolling internally; otherwise a 200-line message would
    // hide the entire conversation.
    ta.style.height = `${Math.min(ta.scrollHeight, 144)}px`;
  }

  useEffect(() => {
    autosizeInput();
  }, [text]);

  function pickFile(file: File | null) {
    if (!file) return;
    if (!ALLOWED_IMAGE_TYPES.has(file.type)) {
      setErr(
        "FunPay accepts only PNG/JPG/GIF/WebP images. " +
          "For other files paste a download URL into the message text."
      );
      return;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      setErr(
        `Image is ${(file.size / 1024 / 1024).toFixed(1)} MB — FunPay caps uploads at 7 MB.`
      );
      return;
    }
    setErr(null);
    setPendingFile(file);
  }

  function clearPendingFile() {
    setPendingFile(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  async function send() {
    if (chatId === null || sending) return;
    if (!text.trim() && !pendingFile) return;
    // Sending is an explicit user intent to bring the thread to the
    // bottom — re-pin the auto-scroll regardless of where they were.
    stickToBottomRef.current = true;
    setSending(true);
    setErr(null);
    try {
      let imageId: string | null = null;
      if (pendingFile) {
        const upload = await api.upload<UploadAttachmentResult>(
          `/api/accounts/${accountId}/chats/${encodeURIComponent(chatId)}/attachments`,
          pendingFile
        );
        imageId = upload.image_id;
      }
      const body: { text: string; image_id?: string } = { text };
      if (imageId) body.image_id = imageId;
      const updated = await api.post<ChatThread>(
        `/api/accounts/${accountId}/chats/${encodeURIComponent(chatId)}/messages`,
        body
      );
      setThread(updated);
      setText("");
      clearPendingFile();
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setSending(false);
    }
  }

  function onFormSubmit(e: React.FormEvent) {
    e.preventDefault();
    void send();
  }

  function onInputKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends, Shift+Enter inserts a newline. Mirrors Slack/Telegram so
    // multi-line buyer-style replies feel natural without losing the fast
    // single-line case.
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send();
    }
  }

  function onPaste(e: React.ClipboardEvent<HTMLTextAreaElement>) {
    // Paste-from-screenshot: capture the first image in the clipboard, drop
    // it into the pending-file slot. Lets the user Win+Shift+S a region and
    // immediately Ctrl-V into the chat — the same flow as Slack/Discord.
    const item = Array.from(e.clipboardData.items).find((it) =>
      it.type.startsWith("image/")
    );
    if (!item) return;
    const file = item.getAsFile();
    if (file) {
      e.preventDefault();
      pickFile(file);
    }
  }

  function onDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) pickFile(file);
  }

  if (chatId === null) {
    return (
      <div className="card grid place-items-center text-sm text-muted">
        Pick a chat on the left.
      </div>
    );
  }

  const peerAvatar = thread?.peer_avatar_url ?? null;
  const peerName = thread?.title || chatId;

  return (
    <div
      className={`card relative flex min-h-0 flex-col ${
        dragOver ? "ring-2 ring-ink/30" : ""
      }`}
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes("Files")) {
          e.preventDefault();
          setDragOver(true);
        }
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={onDrop}
    >
      {dragOver && (
        <div className="pointer-events-none absolute inset-0 z-10 grid place-items-center rounded-2xl bg-surface/80 text-sm text-ink">
          Drop image to attach
        </div>
      )}
      <div className="mb-3 flex items-center justify-between gap-3 border-b border-line/40 pb-3">
        <div className="flex min-w-0 items-center gap-3">
          <Avatar name={peerName} src={peerAvatar} size="md" />
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <div className="truncate text-base font-semibold">
                {thread?.title || chatId}
              </div>
              {thread?.peer_online === true && (
                <span
                  className="inline-flex items-center gap-1 text-[11px] text-emerald-600 dark:text-emerald-400"
                  title="Online on FunPay"
                >
                  <span className="h-2 w-2 rounded-full bg-emerald-500" />
                  online
                </span>
              )}
              {thread?.peer_online === false && (
                <span
                  className="inline-flex items-center gap-1 text-[11px] text-muted"
                  title="Offline on FunPay"
                >
                  <span className="h-2 w-2 rounded-full bg-muted/60" />
                  offline
                </span>
              )}
            </div>
            <div className="text-xs text-muted">Chat #{chatId}</div>
          </div>
        </div>
      </div>
      <div
        ref={messagesRef}
        onScroll={onMessagesScroll}
        className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto pr-1 pb-3"
      >
        {thread === null ? (
          <div className="text-sm text-muted">Loading…</div>
        ) : thread.messages.length === 0 ? (
          <div className="text-sm text-muted">No messages yet.</div>
        ) : (
          thread.messages.map((m, i) => (
            <MessageRow
              key={`${m.id ?? i}`}
              message={m}
              prev={i > 0 ? thread.messages[i - 1] : null}
              peerAvatar={peerAvatar}
              peerName={peerName}
              onOpenOrder={onOpenOrder}
            />
          ))
        )}
      </div>
      {err && <div className="mt-2 text-sm text-danger">{err}</div>}
      {pendingFile && pendingPreview && (
        <div className="mt-2 flex items-center gap-3 rounded-xl bg-surface2 p-2 shadow-neu-inset">
          <img
            src={pendingPreview}
            alt={pendingFile.name}
            className="h-12 w-12 rounded-lg object-cover"
          />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm">{pendingFile.name}</div>
            <div className="text-xs text-muted">
              {(pendingFile.size / 1024).toFixed(0)} KB
            </div>
          </div>
          <button
            type="button"
            onClick={clearPendingFile}
            className="grid h-8 w-8 place-items-center rounded-lg text-ink2 hover:text-ink"
            title="Remove attachment"
            aria-label="Remove attachment"
          >
            ✕
          </button>
        </div>
      )}
      <form onSubmit={onFormSubmit} className="mt-3 flex items-end gap-2">
        <input
          ref={fileInputRef}
          type="file"
          accept="image/png,image/jpeg,image/gif,image/webp"
          className="hidden"
          onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
        />
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          className="grid h-10 w-10 flex-shrink-0 place-items-center rounded-xl bg-surface text-ink2 shadow-neu-sm transition-shadow hover:text-ink hover:shadow-neu-pressed"
          title="Attach image"
          aria-label="Attach image"
          disabled={sending}
        >
          {/* Paperclip glyph mirrors FunPay's own attach button */}
          📎
        </button>
        <textarea
          ref={inputRef}
          className="input min-h-[2.5rem] max-h-36 resize-none leading-relaxed"
          placeholder="Type a message…"
          value={text}
          rows={1}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onInputKeyDown}
          onPaste={onPaste}
          disabled={sending}
        />
        <button
          className="btn-primary"
          disabled={sending || (!text.trim() && !pendingFile)}
        >
          {sending ? "Sending…" : "Send"}
        </button>
      </form>
    </div>
  );
}

// Visual styling for the small role tag rendered above non-regular messages.
// Translated to Russian so the panel matches FunPay's labels (опов., автоответ,
// поддержка) without requiring an i18n layer for a single feature.
const KIND_BADGE: Record<
  Exclude<ChatMessage["kind"], "regular">,
  { label: string; cardClass: string; pillClass: string }
> = {
  system: {
    label: "Сообщение от системы",
    cardClass: "msg-card msg-card-system",
    pillClass: "msg-card-system",
  },
  support: {
    label: "Сообщение от поддержки FunPay",
    cardClass: "msg-card msg-card-support",
    pillClass: "msg-card-support",
  },
  autoreply: {
    // Autoreply is rendered as an inline pill above the seller's bubble,
    // not as a centered card — it's a regular message with extra context.
    label: "Автоответ",
    cardClass: "",
    pillClass: "msg-card-autoreply-pill",
  },
};

function MessageRow({
  message,
  prev,
  peerAvatar,
  peerName,
  onOpenOrder,
}: {
  message: ChatMessage;
  prev: ChatMessage | null;
  peerAvatar: string | null;
  peerName: string;
  onOpenOrder: (orderId: string) => void;
}) {
  // Increase vertical spacing whenever the kind changes between adjacent
  // messages — that's what creates a visible gap between buyer/seller
  // exchanges and the system/support announcements that FunPay interleaves
  // with them.
  const kindChanged = !prev || prev.kind !== message.kind;
  const extraSpacing =
    (kindChanged && (message.kind !== "regular" || (prev && prev.kind !== "regular")))
      ? "mt-4"
      : message.is_group_first
        ? "mt-2"
        : "";

  if (message.kind === "system" || message.kind === "support") {
    const meta = KIND_BADGE[message.kind];
    return (
      <div className={`flex justify-center px-4 ${extraSpacing}`}>
        <div className={meta.cardClass}>
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider opacity-80">
            {meta.label}
            {message.label && (
              <>
                <span className="mx-1.5 opacity-50">•</span>
                <span>{message.label}</span>
              </>
            )}
          </div>
          <MessageContent message={message} onOpenOrder={onOpenOrder} />
        </div>
      </div>
    );
  }

  const mine = message.is_me;
  const showAvatar = !mine && message.is_group_last;
  const showAuthor = !mine && message.is_group_first && message.kind !== "autoreply";

  const bubbleClass = [
    mine ? "bubble-me" : "bubble-them",
    message.is_group_last ? (mine ? "bubble-tail-me" : "bubble-tail-them") : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={`flex w-full ${mine ? "justify-end" : "justify-start"} ${extraSpacing}`}>
      <div
        className={`flex max-w-[80%] items-end gap-2 ${
          mine ? "flex-row-reverse" : ""
        }`}
      >
        {!mine && (
          <div className="flex h-9 w-9 flex-shrink-0 items-end">
            {showAvatar ? (
              <Avatar name={message.author || peerName} src={peerAvatar} size="md" />
            ) : (
              // Empty placeholder keeps grouped bubbles aligned with the
              // bottom-most one that does carry the avatar.
              <span className="h-9 w-9" aria-hidden />
            )}
          </div>
        )}
        <div className={`flex min-w-0 flex-col gap-1 ${mine ? "items-end" : "items-start"}`}>
          {showAuthor && (
            <div className="px-1 text-xs font-medium text-ink2">
              {message.author || peerName}
            </div>
          )}
          {message.kind === "autoreply" && message.is_group_first && (
            <span
              className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${KIND_BADGE.autoreply.pillClass}`}
            >
              {KIND_BADGE.autoreply.label}
            </span>
          )}
          <div className={bubbleClass}>
            <MessageContent message={message} onOpenOrder={onOpenOrder} />
          </div>
        </div>
      </div>
    </div>
  );
}

function MessageContent({
  message,
  onOpenOrder,
}: {
  message: ChatMessage;
  onOpenOrder: (orderId: string) => void;
}) {
  return (
    <>
      {message.attachments.map((att, i) => (
        <AttachmentView key={`${att.href}-${i}`} attachment={att} />
      ))}
      {message.text && (
        <div className="leading-relaxed">
          <RichText text={message.text} onOpenOrder={onOpenOrder} />
        </div>
      )}
    </>
  );
}

function AttachmentView({ attachment }: { attachment: Attachment }) {
  if (attachment.kind === "image") {
    return (
      <a
        href={attachment.href}
        target="_blank"
        rel="noreferrer"
        className="block overflow-hidden rounded-md bg-surface2"
        title={attachment.name ?? "Open image"}
      >
        <img
          src={attachment.src}
          alt={attachment.name ?? ""}
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          className="block max-h-[320px] max-w-full object-contain"
        />
      </a>
    );
  }
  return null;
}

/**
 * Render text with order-number links (`#XXXXXXXX`) made clickable. Order
 * clicks open the right sidebar with that order's details. Anything else
 * is rendered as plain text — FunPay already inlines URLs as `<a>` in the
 * raw HTML, but our parser strips those tags; restoring URL auto-linking
 * is intentionally out of scope for this change to keep the renderer
 * simple and XSS-free (we never inject HTML, only render plain text).
 */
function RichText({
  text,
  onOpenOrder,
}: {
  text: string;
  onOpenOrder: (orderId: string) => void;
}) {
  const out: React.ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  ORDER_RE.lastIndex = 0;
  while ((m = ORDER_RE.exec(text)) !== null) {
    const [full, id] = m;
    const lead = full.startsWith("#") ? "" : full[0];
    const matchStart = m.index + lead.length;
    const matchEnd = matchStart + 1 + id.length;
    if (matchStart > last) out.push(text.slice(last, matchStart));
    out.push(
      <button
        key={`${id}-${matchStart}`}
        type="button"
        onClick={() => onOpenOrder(id)}
        className="rounded bg-surface2 px-1 font-mono text-[12px] text-ink underline-offset-2 hover:underline"
        title={`Open order #${id}`}
      >
        #{id}
      </button>
    );
    last = matchEnd;
  }
  if (last < text.length) out.push(text.slice(last));
  return <>{out}</>;
}
