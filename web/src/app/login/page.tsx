"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, MOCK_MODE } from "@/lib/api";
import { AuthShell } from "@/components/auth/AuthShell";
import { AuthChecking, Field, FormError, SubmitButton } from "@/components/auth/Field";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [checking, setChecking] = useState(true);
  const emailRef = useRef<HTMLInputElement>(null);

  // Already signed in? Skip the form.
  useEffect(() => {
    let live = true;
    api
      .me()
      .then(() => {
        if (live) router.replace("/chat");
      })
      .catch(() => {
        if (live) setChecking(false);
      });
    return () => {
      live = false;
    };
  }, [router]);

  useEffect(() => {
    if (!checking) emailRef.current?.focus();
  }, [checking]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await api.login(email.trim(), password);
      router.replace("/chat");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign in failed");
      setSubmitting(false);
    }
  }

  if (checking) return <AuthChecking />;

  return (
    <AuthShell
      title="Welcome back"
      subtitle="Sign in to pick up your trips where you left them."
      footer={
        <>
          New to GujjuTours?{" "}
          <Link href="/signup" className="font-medium text-link underline-offset-4 hover:underline">
            Create an account
          </Link>
        </>
      }
    >
      <form onSubmit={onSubmit} className="space-y-4">
        <Field
          ref={emailRef}
          label="Email"
          type="email"
          autoComplete="email"
          inputMode="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
        />
        <Field
          label="Password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <FormError message={error} />
        <div className="pt-1">
          <SubmitButton busy={submitting} busyLabel="Signing in">
            Sign in
          </SubmitButton>
        </div>
      </form>

      {MOCK_MODE && (
        <p className="mt-6 rounded-xl border border-border bg-surface px-3.5 py-2.5 text-xs text-fg-muted">
          <span className="font-medium text-fg">Mock mode.</span> Any email and password works. Use password{" "}
          <code className="font-mono">wrong</code> to see the error state.
        </p>
      )}
    </AuthShell>
  );
}
