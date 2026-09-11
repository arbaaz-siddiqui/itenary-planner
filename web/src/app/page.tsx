import { redirect } from "next/navigation";

export default function Home() {
  // /chat itself decides authed vs. not and bounces to /login if needed.
  redirect("/chat");
}
