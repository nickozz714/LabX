/**
 * contexts/AuthContext.tsx
 *
 * Single-admin auth (see backend authentication.py — there is no user table,
 * the account IS the server config). Token lives in sessionStorage so it
 * doesn't survive a browser restart, matching lovely-landing-project's
 * AuthContext pattern.
 */
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { getToken, setToken } from "@/lib/api";

interface AuthContextValue {
  username: string | null;
  isAuthenticated: boolean;
  login: (username: string, password: string) => Promise<void>;
  setup: (username: string, password: string) => Promise<void>;
  adoptSession: (token: string, username: string) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [username, setUsername] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const token = getToken();
    if (token) {
      try {
        const payload = JSON.parse(atob(token.split(".")[1]));
        setUsername(payload.sub || "admin");
      } catch {
        setToken(null);
      }
    }
    setReady(true);
  }, []);

  async function login(user: string, password: string) {
    const res = await api.post<{ access_token: string; username: string }>("/auth/login", {
      username: user,
      password,
    });
    setToken(res.access_token);
    setUsername(res.username);
  }

  async function setup(user: string, password: string) {
    const res = await api.post<{ access_token: string; username: string }>("/auth/setup", {
      username: user,
      password,
    });
    setToken(res.access_token);
    setUsername(res.username);
  }

  /** Adopt a fresh token issued by another endpoint (e.g. after a
   * credentials change, which returns a token for the new username). */
  function adoptSession(token: string, user: string) {
    setToken(token);
    setUsername(user);
  }

  function logout() {
    setToken(null);
    setUsername(null);
  }

  if (!ready) return null;

  return (
    <AuthContext.Provider value={{ username, isAuthenticated: !!username, login, setup, adoptSession, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
