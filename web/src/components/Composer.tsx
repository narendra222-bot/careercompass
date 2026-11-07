"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Icon } from "./Icons";
import { toast } from "./Toast";

type Props = {
  onSend: (text: string) => void;
  placeholder: string;
  disabled?: boolean;
  autoFocus?: boolean;
  id: string;
  compact?: boolean;
};

/** Message box: Enter sends, Shift+Enter adds a line, grows with its text. */
export function Composer({ onSend, placeholder, disabled, autoFocus, id, compact }: Props) {
  const [text, setText] = useState("");
  const ta = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = ta.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [text]);

  useEffect(() => {
    if (autoFocus) ta.current?.focus();
  }, [autoFocus]);

  const send = () => {
    const t = text.trim();
    if (!t || disabled) return;
    if (t.length > 2000) {
      toast("Keep each message under 2,000 characters.");
      return;
    }
    onSend(t);
    setText("");
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  };

  return (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault();
        send();
      }}
    >
      <textarea
        id={id}
        ref={ta}
        rows={1}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKey}
        placeholder={placeholder}
        aria-label={placeholder}
      />
      <div className="crow">
        <button
          type="button"
          className="cbtn"
          aria-label="Attach resume"
          onClick={() => toast("Resume upload is coming soon")}
        >
          <Icon name="plus" />
        </button>
        {!compact && (
          <span className="model" title="Plans are matched against O*NET 31.0">
            Compass · O*NET 31
          </span>
        )}
        <button
          className="send"
          aria-label="Send"
          disabled={!text.trim() || disabled}
          style={compact ? { marginLeft: "auto" } : undefined}
        >
          <Icon name="up" />
        </button>
      </div>
    </form>
  );
}
