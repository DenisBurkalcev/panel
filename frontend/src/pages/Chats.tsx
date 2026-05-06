import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  ApiError,
  api,
  type Account,
  type ChatPreview,
  type ChatThread,
} from "../api";
import Avatar from "../components/Avatar";

export default function ChatsPage() {
  const [params, setParams] = useSearchParams();
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [accErr, setAccErr] = useState<string | null>(null);

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
  }

  function selectChat(id: string) {
    const next = new URLSearchParams(params);
    if (selectedAccountId !== null) next.set("account", String(selectedAccountId));
    next.set("chat", id);
    setParams(next, { replace: true });
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

  return (
    <div className="flex h-full min-h-0 flex-col gap-5">
      <header className="flex flex-wrap items-center gap-2">
        <h1 className="mr-3 text-2xl font-semibold tracking-wide">Chats</h1>
        <div className="flex flex-wrap gap-2">
          {enabledAccounts.map((a) => (
            <button
              key={a.id}
              onClick={() => selectAccount(a.id)}
              className={
                selectedAccountId === a.id ? "chip text-ink" : "btn-ghost"
              }
            >
              {a.label}
            </button>
          ))}
        </div>
      </header>
      {selectedAccountId !== null && (
        <div className="grid min-h-0 flex-1 grid-cols-[320px_1fr] gap-5">
          <ChatList
            key={selectedAccountId}
            accountId={selectedAccountId}
            activeChatId={chatId}
            onSelect={selectChat}
          />
          <ChatPane
            accountId={selectedAccountId}
            chatId={chatId}
            // Bumping this key forces a remount when switching threads, so we
            // don't carry stale message state into a different chat.
            key={`${selectedAccountId}-${chatId ?? ""}`}
          />
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

  async function load(force: boolean) {
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
  }

  useEffect(() => {
    void load(false);
    // Poll the chat list often enough that new threads (or new last-message
    // previews) appear without forcing the operator to click Refresh. The
    // backend `_CHAT_LIST_TTL` keeps this from hammering FunPay.
    const id = window.setInterval(() => void load(false), 10_000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accountId]);

  return (
    <div className="card flex min-h-0 flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold uppercase tracking-wider text-ink2">
          Chats
        </div>
        <button
          className="btn-ghost text-xs"
          disabled={busy}
          onClick={() => void load(true)}
          title="Refresh from FunPay"
        >
          {busy ? "…" : "Refresh"}
        </button>
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
}: {
  accountId: number;
  chatId: string | null;
}) {
  const [thread, setThread] = useState<ChatThread | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const messagesRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  // Tracks whether the user is currently parked at the bottom of the message
  // list. We only auto-scroll on poll updates if true — otherwise scrolling
  // up to read history would yank the operator back to the bottom every
  // poll tick. Initial value is `true` so the first render pins to bottom.
  const stickToBottomRef = useRef(true);

  async function load(force: boolean) {
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
  }

  useEffect(() => {
    setThread(null);
    stickToBottomRef.current = true;
    if (chatId === null) return;
    void load(false);
    // 3s poll keeps incoming buyer messages flowing into the open thread
    // without manual refresh; backend `_THREAD_TTL` is tuned to match.
    const id = window.setInterval(() => void load(false), 3_000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accountId, chatId]);

  useEffect(() => {
    const el = messagesRef.current;
    if (!el) return;
    if (stickToBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [thread]);

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

  async function send() {
    if (chatId === null || !text.trim() || sending) return;
    // Sending is an explicit user intent to bring the thread to the
    // bottom — re-pin the auto-scroll regardless of where they were.
    stickToBottomRef.current = true;
    setSending(true);
    setErr(null);
    try {
      const updated = await api.post<ChatThread>(
        `/api/accounts/${accountId}/chats/${encodeURIComponent(chatId)}/messages`,
        { text }
      );
      setThread(updated);
      setText("");
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
    <div className="card flex min-h-0 flex-col">
      <div className="mb-3 flex items-center justify-between gap-3 border-b border-line/40 pb-3">
        <div className="flex min-w-0 items-center gap-3">
          <Avatar name={peerName} src={peerAvatar} size="md" />
          <div className="min-w-0">
            <div className="truncate text-base font-semibold">
              {thread?.title || chatId}
            </div>
            <div className="text-xs text-muted">Chat #{chatId}</div>
          </div>
        </div>
        <button
          className="btn-ghost text-xs"
          onClick={() => void load(true)}
          title="Refresh from FunPay"
        >
          Refresh
        </button>
      </div>
      <div
        ref={messagesRef}
        onScroll={onMessagesScroll}
        className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto pr-1 pb-3"
      >
        {thread === null ? (
          <div className="text-sm text-muted">Loading…</div>
        ) : thread.messages.length === 0 ? (
          <div className="text-sm text-muted">No messages yet.</div>
        ) : (
          thread.messages.map((m, i) => {
            const showPeerAvatar = !m.is_me;
            const authorName = m.author ?? peerName;
            return (
              <div
                key={`${m.id ?? i}`}
                className={`flex items-end gap-2 ${
                  m.is_me ? "justify-end" : "justify-start"
                }`}
              >
                {showPeerAvatar && (
                  <Avatar name={authorName} src={peerAvatar} size="sm" />
                )}
                <div className={m.is_me ? "bubble-me" : "bubble-them"}>
                  {!m.is_me && m.author && (
                    <div className="mb-0.5 text-[10px] uppercase tracking-wider text-muted">
                      {m.author}
                    </div>
                  )}
                  <div>{m.text}</div>
                </div>
              </div>
            );
          })
        )}
      </div>
      {err && <div className="mt-2 text-sm text-danger">{err}</div>}
      <form onSubmit={onFormSubmit} className="mt-3 flex items-end gap-2">
        <textarea
          ref={inputRef}
          className="input min-h-[2.5rem] max-h-36 resize-none leading-relaxed"
          placeholder="Type a message…  (Shift+Enter — new line)"
          value={text}
          rows={1}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onInputKeyDown}
          disabled={sending}
        />
        <button className="btn-primary" disabled={sending || !text.trim()}>
          {sending ? "Sending…" : "Send"}
        </button>
      </form>
    </div>
  );
}


