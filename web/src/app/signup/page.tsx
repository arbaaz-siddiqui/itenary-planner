"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, MOCK_MODE } from "@/lib/api";
import { AuthShell } from "@/components/auth/AuthShell";
import { AuthChecking, Field, FormError, SubmitButton } from "@/components/auth/Field";

// The API rejects anything shorter, so check here too rather than letting the
// customer submit and read a 422 they cannot act on.
const MIN_PASSWORD = 8;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

type Key = "first" | "email" | "password" | "confirm";

export default function SignupPage() {
  const router = useRouter();
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  // A field speaks up only once the customer has left it (or tried to
  // submit), so nobody is told off while still typing.
  const [touched, setTouched] = useState<Partial<Record<Key, boolean>>>({});
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [checking, setChecking] = useState(true);
  const firstRef = useRef<HTMLInputElement>(null);

  // Already signed in? Skip the form, same as the login page.
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
    if (!checking) firstRef.current?.focus();
  }, [checking]);

  const problems: Record<Key, string | null> = {
    first: firstName.trim() ? null : "Enter your first name",
    email: EMAIL.test(email.trim()) ? null : "Enter a valid email, like name@example.com",
    password: password.length >= MIN_PASSWORD ? null : `Use at least ${MIN_PASSWORD} characters`,
    confirm: confirm && confirm === password ? null : "Passwords do not match",
  };
  const show = (k: Key) => (touched[k] ? problems[k] : null);
  const blur = (k: Key) => () => setTouched((t) => ({ ...t, [k]: true }));

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    // Validate before the request so a typo costs no round trip.
    setTouched({ first: true, email: true, password: true, confirm: true });
    const first = (Object.keys(problems) as Key[]).find((k) => problems[k]);
    if (first) {
      document.querySelector<HTMLInputElement>(`[data-field="${first}"]`)?.focus();
      return;
    }

    setSubmitting(true);
    try {
      await api.register(email.trim(), password, firstName.trim(), lastName.trim());
      // register signs the customer in, so go straight to chat rather than
      // bouncing them through login again.
      router.replace("/chat");
    } catch (err) {
      // The API returns 409 with a readable message when the email is taken.
      setError(err instanceof Error ? err.message : "Could not create the account");
      setSubmitting(false);
    }
  }

  if (checking) return <AuthChecking />;

  return (
    <AuthShell
      title="Create your account"
      subtitle="Plan flights, hotels, tours and visas for Dubai in one conversation."
      footer={
        <>
          Already have an account?{" "}
          <Link href="/login" className="font-medium text-link underline-offset-4 hover:underline">
            Sign in
          </Link>
        </>
      }
    >
      <form onSubmit={onSubmit} className="space-y-4" noValidate>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 sm:gap-3">
          <Field
            ref={firstRef}
            data-field="first"
            label="First name"
            autoComplete="given-name"
            required
            maxLength={80}
            value={firstName}
            onChange={(e) => setFirstName(e.target.value)}
            onBlur={blur("first")}
            error={show("first")}
          />
          <Field
            label="Last name"
            autoComplete="family-name"
            maxLength={80}
            value={lastName}
            onChange={(e) => setLastName(e.target.value)}
          />
        </div>
        <Field
          data-field="email"
          label="Email"
          type="email"
          inputMode="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          onBlur={blur("email")}
          error={show("email")}
          placeholder="you@example.com"
        />
        <Field
          data-field="password"
          label="Password"
          type="password"
          autoComplete="new-password"
          required
          minLength={MIN_PASSWORD}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onBlur={blur("password")}
          error={show("password")}
          hint={{ text: `At least ${MIN_PASSWORD} characters`, met: password.length >= MIN_PASSWORD }}
        />
        <Field
          data-field="confirm"
          label="Confirm password"
          type="password"
          autoComplete="new-password"
          required
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          onBlur={blur("confirm")}
          error={show("confirm")}
          hint={confirm && confirm === password ? { text: "Passwords match", met: true } : null}
        />
        <FormError message={error} />
        <div className="pt-1">
          <SubmitButton busy={submitting} busyLabel="Creating your account">
            Create account
          </SubmitButton>
        </div>
      </form>

      {MOCK_MODE && (
        <p className="mt-6 text-center text-xs text-fg-muted">Mock mode. Any email and password works.</p>
      )}
    </AuthShell>
  );
}
