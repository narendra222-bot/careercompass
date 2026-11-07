import type { Metadata } from "next";
import { ChatApp } from "@/components/ChatApp";

export const metadata: Metadata = { title: "Chat · CareerCompass" };

export default function ChatPage() {
  return <ChatApp />;
}
